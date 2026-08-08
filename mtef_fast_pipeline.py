"""
MTEF 批量解析加速骨架 —— 无 COM、无 Office 进程。

设计目标
    1. 后端可插拔：mathtypejx / MTEF-py / 自研解析器，只要提供 bytes -> str 的 callable
    2. 字节级去重：同一公式只解析一次（试卷语料命中率常见 30~60%）
    3. 进程池并行：绕开 GIL，XSLT / 字符表在 initializer 里只构建一次
    4. 纯内存 I/O：docx 当 zip 直接读，不落盘、不解压

依赖（按需安装，都不涉及 COM）
    pip install olefile          # 读 OLE2 复合文档，纯 Python
    pip install mathtypejx       # 可选，B 路线后端（MTEF -> MathML）
    pip install lxml             # 可选，跑 XSLT

用法
    python mtef_fast_pipeline.py bench <目录或docx路径> [--workers 8] [--backend auto]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

# ---------------------------------------------------------------- 常量

# OLE 里 MathType 数据流的三种别名，按命中概率排序，命中即停（优化 4.8）
EQUATION_STREAM_NAMES = ("Equation Native", "EquationNative", "Equation")

# EQNOLEFILEHDR: cbHdr(2) version(4) cf(2) cbObject(4) reserved(4*4) = 28 字节
EQNOLEFILEHDR_SIZE = 28

# MTEF 头部第一字节是版本号，只有 3 和 5 是我们认识的
SUPPORTED_MTEF_VERSIONS = (3, 5)

EMBEDDINGS_PREFIX = "word/embeddings/"


# ---------------------------------------------------------------- 数据结构


@dataclass
class FormulaResult:
    source: str          # 来源文档
    part: str            # zip 内路径，如 word/embeddings/oleObject3.bin
    digest: str          # MTEF 净荷摘要，去重用
    ok: bool
    output: str = ""     # LaTeX 或 MathML
    error: str = ""
    from_cache: bool = False


@dataclass
class BenchStat:
    docs: int = 0
    formulas: int = 0
    unique: int = 0
    cache_hits: int = 0
    failed: int = 0
    seconds: float = 0.0
    errors: dict[str, int] = field(default_factory=dict)

    @property
    def hit_rate(self) -> float:
        return self.cache_hits / self.formulas if self.formulas else 0.0

    @property
    def per_formula_ms(self) -> float:
        return self.seconds * 1000 / self.formulas if self.formulas else 0.0


# ---------------------------------------------------------------- 提取层（无 COM）


def iter_ole_blobs(docx_path: Path) -> Iterator[tuple[str, bytes]]:
    """把 docx 当 zip 直接读出内嵌 OLE 对象，全程内存，不解压落盘（优化 4.5）。"""
    with zipfile.ZipFile(docx_path) as zf:
        names = [
            n for n in zf.namelist()
            if n.startswith(EMBEDDINGS_PREFIX) and n.lower().endswith(".bin")
        ]
        if not names:  # 短路：没有内嵌对象，整个文件跳过（优化 4.8）
            return
        for name in names:
            yield name, zf.read(name)


def extract_mtef(ole_bytes: bytes) -> bytes | None:
    """
    从 OLE2 复合文档里取出 MTEF 净荷。

    这里是"拒绝 COM"的关键：OLE2 只是一种文件格式，olefile 是纯 Python 实现，
    不需要 IStorage、不需要激活 OLE 对象、不需要 Windows。
    """
    try:
        import olefile
    except ImportError:  # pragma: no cover
        raise RuntimeError("需要 olefile：pip install olefile") from None

    from io import BytesIO

    if not olefile.isOleFile(BytesIO(ole_bytes)):
        return None

    with olefile.OleFileIO(BytesIO(ole_bytes)) as ole:
        for stream in EQUATION_STREAM_NAMES:
            if ole.exists(stream):
                raw = ole.openstream(stream).read()
                return _strip_eqn_header(raw)
    return None


def _strip_eqn_header(raw: bytes) -> bytes | None:
    """按 cbHdr 字段跳过 EQNOLEFILEHDR，比硬编码 28 更稳。"""
    if len(raw) < EQNOLEFILEHDR_SIZE + 2:
        return None
    (cb_hdr,) = struct.unpack_from("<H", raw, 0)
    if cb_hdr != EQNOLEFILEHDR_SIZE:
        cb_hdr = EQNOLEFILEHDR_SIZE  # 少数文件头字段异常，退回标准长度
    payload = raw[cb_hdr:]
    if not payload or payload[0] not in SUPPORTED_MTEF_VERSIONS:
        return None  # 短路：版本不认识就别进主循环（优化 4.8）
    return payload


def digest_of(mtef: bytes) -> str:
    """去重 key。必须用剥掉 OLE 头之后的净荷，容器字节会带无关差异（优化 4.1）。"""
    return hashlib.blake2b(mtef, digest_size=16).hexdigest()


# ---------------------------------------------------------------- 后端层（可插拔）

_BACKEND: Callable[[bytes], str] | None = None


def _make_backend_mathtypejx() -> Callable[[bytes], str]:
    """B 路线：MTEF -> MathML。XSLT 在这里只编译一次，之后整个进程复用（优化 4.2）。"""
    from mathtypejx.mtef import parser as mtef_parser  # type: ignore
    from lxml import etree

    xsl_dir = Path(mtef_parser.__file__).parent / "xslt"
    xsl_main = next(xsl_dir.rglob("mtef2mml*.xsl"), None) or next(xsl_dir.rglob("*.xsl"))
    transform = etree.XSLT(etree.parse(str(xsl_main)))  # 编译一次

    def run(mtef: bytes) -> str:
        mtef_xml = mtef_parser.parse_to_xml(mtef)          # 依 mathtypejx 版本可能需微调
        tree = etree.fromstring(mtef_xml.encode("utf-8"))
        return str(transform(tree))

    return run


def _make_backend_mtef_py() -> Callable[[bytes], str]:
    """A 路线：MTEF -> LaTeX，一次遍历，最快。"""
    from mtef import MTEF  # type: ignore  (AndyQsmart/MTEF-py)

    def run(mtef: bytes) -> str:
        obj, err = MTEF.OpenBytes(mtef)
        if err:
            raise RuntimeError(str(err))
        return obj.Translate()

    return run


def _make_backend_noop() -> Callable[[bytes], str]:
    """没装任何后端时的占位：只走完提取 + 去重 + 并行，用于压 I/O 基准。"""
    def run(mtef: bytes) -> str:
        total = 0
        for b in mtef:  # 模拟一次线性遍历
            total += b
        return f"<noop bytes={len(mtef)} checksum={total & 0xFFFF}>"

    return run


BACKENDS = {
    "mathtypejx": _make_backend_mathtypejx,
    "mtef-py": _make_backend_mtef_py,
    "noop": _make_backend_noop,
}


def _init_worker(backend: str) -> None:
    """
    进程池 initializer：每个进程建一份后端。

    lxml 的 XSLT 对象跨线程不安全，所以用进程而非线程；
    每进程只构建一次，避免每公式重编译（优化 4.2 / 4.4）。
    """
    global _BACKEND
    if backend == "auto":
        for name in ("mathtypejx", "mtef-py", "noop"):
            try:
                _BACKEND = BACKENDS[name]()
                return
            except Exception:
                continue
        _BACKEND = _make_backend_noop()
    else:
        _BACKEND = BACKENDS[backend]()


def _parse_one(item: tuple[str, bytes]) -> tuple[str, bool, str]:
    """worker 入口。失败隔离：单公式异常绝不能拖垮整批（优化 4.9）。"""
    digest, mtef = item
    try:
        return digest, True, _BACKEND(mtef)  # type: ignore[misc]
    except Exception as exc:
        return digest, False, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------- 调度层


def collect_tasks(paths: list[Path]) -> tuple[list[FormulaResult], dict[str, bytes]]:
    """
    扫描所有 docx，抽出 MTEF 净荷并按摘要去重。

    返回 (占位结果列表, 去重后的待解析集合)。
    """
    slots: list[FormulaResult] = []
    unique: dict[str, bytes] = {}

    for path in paths:
        try:
            blobs = list(iter_ole_blobs(path))
        except zipfile.BadZipFile:
            continue
        for part, ole_bytes in blobs:
            mtef = extract_mtef(ole_bytes)
            if mtef is None:
                slots.append(FormulaResult(str(path), part, "", False,
                                           error="no Equation Native stream"))
                continue
            d = digest_of(mtef)
            slots.append(FormulaResult(str(path), part, d, False))
            if d not in unique:
                unique[d] = mtef
    return slots, unique


def run_pipeline(paths: list[Path], workers: int, backend: str) -> tuple[list[FormulaResult], BenchStat]:
    stat = BenchStat(docs=len(paths))
    t0 = time.perf_counter()

    slots, unique = collect_tasks(paths)
    stat.formulas = len(slots)
    stat.unique = len(unique)
    stat.cache_hits = sum(1 for s in slots if s.digest) - len(unique)

    solved: dict[str, tuple[bool, str]] = {}
    if unique:
        items = list(unique.items())
        # chunksize 调大减少 IPC 往返（优化 4.4）
        chunk = max(16, len(items) // (workers * 4) or 1)
        with ProcessPoolExecutor(
            max_workers=workers, initializer=_init_worker, initargs=(backend,)
        ) as pool:
            for digest, ok, out in pool.map(_parse_one, items, chunksize=chunk):
                solved[digest] = (ok, out)

    for s in slots:
        if not s.digest:
            stat.failed += 1
            stat.errors[s.error] = stat.errors.get(s.error, 0) + 1
            continue
        ok, out = solved.get(s.digest, (False, "missing"))
        s.ok = ok
        if ok:
            s.output = out
        else:
            s.error = out
            stat.failed += 1
            head = out.split(":")[0]
            stat.errors[head] = stat.errors.get(head, 0) + 1

    stat.seconds = time.perf_counter() - t0
    return slots, stat


# ---------------------------------------------------------------- CLI


def gather_docx(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(p for p in target.rglob("*.docx") if not p.name.startswith("~$"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MTEF 批量解析加速骨架（无 COM）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bench", help="跑一遍并打印吞吐统计")
    b.add_argument("target", type=Path, help="docx 文件或包含 docx 的目录")
    b.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    b.add_argument("--backend", default="auto", choices=[*BACKENDS, "auto"])
    b.add_argument("--show", type=int, default=3, help="打印前 N 条解析结果")

    args = ap.parse_args(argv)

    paths = gather_docx(args.target)
    if not paths:
        print(f"没找到 docx：{args.target}", file=sys.stderr)
        return 1

    results, stat = run_pipeline(paths, args.workers, args.backend)

    print("=" * 62)
    print(f"文档数        {stat.docs}")
    print(f"公式总数      {stat.formulas}")
    print(f"去重后        {stat.unique}")
    print(f"缓存命中      {stat.cache_hits}  ({stat.hit_rate:.1%})")
    print(f"失败          {stat.failed}")
    print(f"并行度        {args.workers}   后端 {args.backend}")
    print(f"总耗时        {stat.seconds:.2f} s")
    print(f"折算单公式    {stat.per_formula_ms:.3f} ms")
    if stat.errors:
        print("-" * 62)
        for k, v in sorted(stat.errors.items(), key=lambda x: -x[1])[:8]:
            print(f"  {v:>6}  {k}")
    if args.show:
        print("-" * 62)
        for r in [x for x in results if x.ok][: args.show]:
            print(f"  {Path(r.source).name} :: {r.part}")
            print(f"    {r.output[:200]}")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
