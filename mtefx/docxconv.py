"""docx 级 OLE 公式 → OMML 转换。

只处理真正的内嵌 MathType 公式（``<w:object>`` 内含
``<o:OLEObject ProgID="Equation.*" Type="Embed">``），其余 OLE 对象原样保留。

流水线：
    bin(OLE2) → MTEF → MathML(mtefx) → OMML(MML2OMML.XSL)
    把承载公式的 ``<w:r><w:object>…</w:object></w:r>`` 整体替换为行内
    ``<m:oMath>…</m:oMath>``（行内数学，合法挂在 ``<w:p>`` 下）。

提供：
    convert_docx_bytes(docx_bytes, *, cache=None) -> DocxReport
    convert_docx_file(src, dst, *, cache=None)     -> DocxReport
    convert_zip_of_docx(zip_path, out_dir, *, cache=None) -> list[DocxReport]
"""

from __future__ import annotations

import copy
import io
import posixpath
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lxml import etree

import mtefx
from mtefx.engine import extract_mtef, digest_of
from mtefx.omml import mathml_to_omml_element, M_NS
from mtefx._fused import convert_fused

_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "o": "urn:schemas-microsoft-com:office:office",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
}
_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

_W_PARSER = etree.XMLParser(resolve_entities=False, recover=True, huge_tree=True)

# 进程级跨文档去重缓存：每个 worker 进程在 import 时得到自己独立的一份 ``{}``。
# 同一公式（MTEF 净荷摘要相同）在多个 docx 中高频重复时，命中即跳过「解析 + XSLT」
# 整段开销。放在模块级而非传参，是为了让 ProcessPoolExecutor 的 worker 进程**自动**
# 持有跨 docx 的去重缓存而无需 ``Manager.dict`` 的 IPC 序列化开销——进程内去重零成本，
# 仅「跨进程」的重复会漏掉（绝大多数重复是文档内的，已被覆盖）。
_PER_PROCESS_CACHE: dict = {}


@dataclass
class DocxReport:
    name: str = ""
    total: int = 0
    ok: int = 0
    skipped: int = 0          # 非 Equation 的 OLE（原样保留）
    failed: int = 0           # 转换失败（原样保留）
    dedup_hits: int = 0
    elapsed_ms: float = 0.0
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def converted(self) -> int:
        return self.ok


def _clean_omml(omml_el: etree._Element) -> etree._Element:
    """复制成干净的 ``<m:oMath>``，去掉可能残留的 mml 命名空间声明。"""
    new = etree.Element(f"{{{M_NS}}}oMath")
    for child in omml_el:
        new.append(copy.deepcopy(child))
    return new


def _find_equation_objects(root: etree._Element):
    """返回 (w:object 元素, OLEObject 元素, r:id, bin 目标) 列表。"""
    out = []
    for obj in root.findall(".//w:object", _NS):
        ole = obj.find("o:OLEObject", _NS)
        if ole is None:
            continue
        prog = (ole.get("ProgID") or "").lower()
        if "equation" not in prog:        # 只处理 MathType 公式
            continue
        if (ole.get("Type") or "").lower() != "embed":
            continue
        rid = ole.get(f"{{{_NS['r']}}}id")
        if not rid:
            continue
        out.append((obj, ole, rid))
    return out


def convert_docx_bytes(
    docx_bytes: bytes,
    *,
    cache: Optional[dict] = None,
    fast: bool = True,
    fix_v3: bool = True,
    repair_chars: bool = True,
) -> tuple[bytes, DocxReport]:
    """把 docx 字节里的 MathType OLE 公式转成 OMML，返回 (新 docx 字节, 报告)。

    Args:
        docx_bytes: 原始 docx（zip）字节。
        cache: 跨调用共享的指纹→OMML 字符串缓存（dict），用于大批量去重。
        fast/fix_v3/repair_chars: 透传给 mtefx.convert。
    """
    t0 = time.perf_counter()
    rep = DocxReport()
    if cache is None:
        cache = {}

    zin = zipfile.ZipFile(io.BytesIO(docx_bytes))
    names = zin.namelist()

    doc_name = next((n for n in names if n.lower().endswith("word/document.xml")), None)
    rels_name = next(
        (n for n in names if n.lower().endswith("word/_rels/document.xml.rels")), None
    )
    if doc_name is None:
        raise ValueError("docx 内无 word/document.xml")

    doc_bytes = zin.read(doc_name)
    root = etree.fromstring(doc_bytes, _W_PARSER)

    # 文档部件所在目录（用于解析相对 Target，如 word/embeddings/..）
    doc_dir = doc_name.rsplit("/", 1)[0] + "/" if "/" in doc_name else ""

    # 关系表
    rels_root = None
    rels_target_of = {}
    if rels_name:
        rels_root = etree.fromstring(zin.read(rels_name), _W_PARSER)
        for rel in rels_root.findall(f"{{{_RELS_NS}}}Relationship"):
            rels_target_of[rel.get("Id")] = rel.get("Target")

    def _resolve(target: str) -> str:
        if not target:
            return ""
        if target.startswith("/"):
            return target.lstrip("/")
        return posixpath.normpath(doc_dir + target)

    objs = _find_equation_objects(root)
    rep.total = len(objs)

    removed_rids: set[str] = set()
    removed_files: set[str] = set()

    for obj, ole, rid in objs:
        # 1) 定位 bin
        target = rels_target_of.get(rid)
        if not target:
            rep.skipped += 1
            continue
        bin_path = _resolve(target)
        if not bin_path.lower().endswith(".bin"):
            rep.skipped += 1
            continue
        try:
            bin_bytes = zin.read(bin_path)
        except KeyError:
            rep.skipped += 1
            continue

        # 2) 指纹去重
        payload = extract_mtef(bin_bytes)
        if payload is None:
            rep.skipped += 1
            continue
        dig = digest_of(payload)

        # 3) 转 OMML（优先走缓存）
        omml_str = cache.get(dig)
        if omml_str is None:
            # 融合路径：transform → Element 级 NS 归一化 + PUA 修复 → 直接喂
            # MML2OMML，省去中间 MathML 字符串 tostring + repair_pua 字符串扫描
            # + omml 入口 fromstring 两次序列化往返（实测 +18% 提速，OMML 级零回归）。
            status, omml_el, _pua_fixed, _pua_unresolved, _ = convert_fused(
                bin_bytes, fix_v3=fix_v3, repair_chars=repair_chars
            )
            if status != "ok" or omml_el is None:
                rep.failed += 1
                rep.failures.append((bin_path, status))
                continue
            omml_str = etree.tostring(_clean_omml(omml_el), encoding="unicode")
            cache[dig] = omml_str
        else:
            rep.dedup_hits += 1
            omml_el = etree.fromstring(omml_str.encode("utf-8"))

        # 4) 替换承载公式的整个 <w:r> 为行内 <m:oMath>
        #    向上回溯找到真正的 run（OLE 有时被 v:shape / w:drawing 包着）
        w_r = obj
        while w_r is not None and w_r.tag != f"{{{_NS['w']}}}r":
            w_r = w_r.getparent()
        if w_r is None:
            rep.failed += 1
            rep.failures.append((bin_path, "未找到承载公式的 w:r"))
            continue
        new_el = _clean_omml(omml_el)
        parent = w_r.getparent()
        idx = parent.index(w_r)
        parent.insert(idx, new_el)
        parent.remove(w_r)
        rep.ok += 1

        # 5) 收集待清理的关系与文件（OLE + 同 object 内的 VML imagedata）
        removed_rids.add(rid)
        for img in obj.iter(f"{{{_NS['v']}}}imagedata"):
            ir = img.get(f"{{{_NS['r']}}}id")
            if ir:
                removed_rids.add(ir)

    # 清理关系表
    if rels_root is not None:
        for rel in list(rels_root):
            if rel.get("Id") in removed_rids:
                tgt = _resolve(rel.get("Target") or "")
                if tgt:
                    removed_files.add(tgt)
                rels_root.remove(rel)

    # 仅当文件不再被任何剩余关系引用时才删除
    live = set()
    if rels_root is not None:
        for r in rels_root:
            tgt = _resolve(r.get("Target") or "")
            if tgt:
                live.add(tgt)
    removed_files = {f for f in removed_files if f and f not in live}

    # 重新打包
    # 把 OMML 命名空间声明提升到根（避免每个 <m:oMath> 都带 xmlns:m，且保证前缀绑定）
    etree.cleanup_namespaces(root)
    new_doc_bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    new_rels_bytes = (
        etree.tostring(rels_root, xml_declaration=True, encoding="UTF-8")
        if rels_root is not None
        else None
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            if n in removed_files:
                continue
                # 删除孤儿 bin / 显示位图
            if n == doc_name:
                zout.writestr(n, new_doc_bytes)
            elif n == rels_name:
                if new_rels_bytes is not None:
                    zout.writestr(n, new_rels_bytes)
            else:
                zout.writestr(n, zin.read(n))
    zin.close()

    rep.elapsed_ms = (time.perf_counter() - t0) * 1000
    return buf.getvalue(), rep


def convert_docx_file(
    src: str | Path, dst: str | Path, *, cache: Optional[dict] = None
) -> DocxReport:
    """转换单个 docx 文件（磁盘路径），结果写到 dst。"""
    src, dst = Path(src), Path(dst)
    data = src.read_bytes()
    new_bytes, rep = convert_docx_bytes(data, cache=cache)
    dst.write_bytes(new_bytes)
    rep.name = src.name
    return rep


def convert_zip_of_docx(
    zip_path: str | Path,
    out_dir: str | Path,
    *,
    cache: Optional[dict] = None,
    keep_name: bool = True,
) -> list[DocxReport]:
    """解一个 zip，把里面每个 docx 转成 OMML 版，写到 out_dir。

    返回每个 docx 的报告列表。zip 本身不动。
    """
    zip_path, out_dir = Path(zip_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if cache is None:
        cache = {}

    reports: list[DocxReport] = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".docx") or name.startswith("~"):
                continue
            data = zf.read(name)
            stem = Path(name).stem
            dst = out_dir / (f"{stem}.docx" if keep_name else Path(name).name)
            new_bytes, rep = convert_docx_bytes(data, cache=cache)
            dst.write_bytes(new_bytes)
            rep.name = name
            reports.append(rep)
    return reports


def _parallel_worker(data: bytes) -> tuple[bytes, "DocxReport"]:
    """模块级 worker：每个子进程复用自己的 ``_PER_PROCESS_CACHE`` 实现跨 docx 去重。

    必须是模块级函数（ProcessPoolExecutor 在 Windows ``spawn`` 下需 pickle），
    不能是 ``convert_many_docx`` 内部闭包——闭包无法被 pickle。
    """
    return convert_docx_bytes(data, cache=_PER_PROCESS_CACHE)


def _parallel_worker_with_cache(data: bytes, cache: dict) -> tuple[bytes, "DocxReport"]:
    """模块级 worker：使用调用方传入的共享缓存（如 multiprocessing.Manager().dict）。"""
    return convert_docx_bytes(data, cache=cache)


def convert_many_docx(
    docx_bytes_list: list[bytes],
    *,
    workers: int | None = None,
    cache: Optional[dict] = None,
) -> list[tuple[bytes, DocxReport]]:
    """并行转换一批 docx 字节，返回 [(新 docx 字节, 报告), ...]，顺序与输入一致。

    性能要点：
    - 融合路径（convert_fused）默认开启，比字符串路径约 +18.5%。
    - 去重缓存：
        * cache=None（默认）→ 每个 worker 进程用自己的 ``_PER_PROCESS_CACHE``，
          零 IPC 实现「进程内跨 docx 去重」；
        * cache=某全局 dict（如 multiprocessing.Manager().dict()）→ 跨进程全局去重，
          换取 IPC 序列化开销。
    - 去重命中跳过的是「解析 + XSLT」整段开销，是并行之外最大的加速杠杆。
    """
    import os
    from concurrent.futures import ProcessPoolExecutor
    from functools import partial

    if workers is None:
        workers = min(os.cpu_count() or 4, 12)
    if workers <= 1 or len(docx_bytes_list) <= 1:
        return [convert_docx_bytes(d, cache=cache) for d in docx_bytes_list]

    fn = (
        partial(_parallel_worker_with_cache, cache=cache)
        if cache is not None
        else _parallel_worker
    )

    with ProcessPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(fn, docx_bytes_list))
    return results
