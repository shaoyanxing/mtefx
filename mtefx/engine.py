"""
mtefx 核心引擎 —— MathType OLE → MathML 的快路径。

设计要点（均由实测得出，见 build_assets.py 与 README）：

1. **加速 XSLT**：绕开 mathtypejx 内置样式表中每公式重载 348 KB fontmap 的行为，
   33x 提速且输出字节级一致。

2. **空壳哨兵**：mathtypejx 对 MTEF v3 会返回 ``<math ... />`` 这种**空壳**，
   既不抛异常也不返回 None。调用方常见的 ``if result:`` 判断会直接放行，
   造成公式静默丢失。本模块显式检测并判定为失败，交由 refine 层兜底。
   实测 transpect 的 11 个 v3 样本中有 10 个命中此情形。

3. **分诊**：先读 MTEF 头一个字节拿到版本，v3 直接标记走兜底，不浪费一次解析。
"""

from __future__ import annotations

import hashlib
import io
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from lxml import etree

from mtefx._xslt import transform as _xslt_transform_element

_ASSETS = Path(__file__).resolve().parent / "assets"
_XSLT_FAST = _ASSETS / "xslt_fast"

# transpect 仓库自带的真实样本，用作回归基准
_FIXTURES = (
    Path(__file__).resolve().parent.parent
    / "vendor/mathtype-extension/ruby/mathtype-0.0.7.5/spec/fixtures"
)

# OLE 里 MathType 公式流的三种常见别名
_STREAM_NAMES = ("Equation Native", "EquationNative", "Equation")

# MathML 命名空间
_MATHML_NS = "http://www.w3.org/1998/Math/MathML"

STATUS_OK = "ok"
STATUS_EMPTY = "empty"          # 解析"成功"但产出空壳 —— 静默失败
STATUS_ERROR = "error"
STATUS_NO_STREAM = "no_stream"
STATUS_BAD_VERSION = "bad_version"


@dataclass
class FormulaResult:
    """单个公式的转换结果。"""

    status: str
    mathml: str | None = None
    mtef_version: int | None = None
    digest: str = ""
    reason: str = ""
    elapsed_ms: float = 0.0
    from_cache: bool = False
    refined: bool = False
    pua_fixed: int = 0        # 经 fontmap 兜底修复的私用区字符数
    pua_unresolved: int = 0   # 仍未能映射的私用区字符数

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    @property
    def needs_fallback(self) -> bool:
        """是否应交给 refine 层（transpect/Saxon）兜底。"""
        return self.status in (STATUS_EMPTY, STATUS_ERROR)


# ────────────────────────────── XSLT 加速安装 ──────────────────────────────

_xslt_cache: dict[str, object] = {}


def _install_fast_xslt() -> bool:
    """把 mathtypejx 的 XSLT 执行替换为「加速版 + 单次编译」。

    幂等；返回 True 表示已生效。若加速资产缺失则保持原样返回 False。
    """
    if not (_XSLT_FAST / "transform.xsl").exists():
        return False

    import mathtypejx.mtef.mathml as M
    from lxml import etree

    if getattr(M._xslt_transform, "_mtefx_patched", False):
        return True

    if not hasattr(M, "_xslt_transform"):  # pragma: no cover - 上游改名时的保护
        return False

    def _fast(xml_root):
        xslt = _xslt_cache.get("t")
        if xslt is None:
            xslt = etree.XSLT(etree.parse(str(_XSLT_FAST / "transform.xsl")))
            _xslt_cache["t"] = xslt
        return etree.tostring(xslt(xml_root), encoding="unicode", pretty_print=False)

    _fast._mtefx_patched = True  # type: ignore[attr-defined]
    M._xslt_transform = _fast
    return True


# ────────────────────────────── OLE / MTEF 处理 ──────────────────────────────


def extract_mtef(ole_bytes: bytes) -> bytes | None:
    """从 OLE 复合文档中取出 MTEF 净荷（已剥离 28 字节 EQNOLEFILEHDR）。

    直接传入裸 MTEF 字节也能正确处理。
    """
    import olefile

    data = ole_bytes
    if olefile.isOleFile(io.BytesIO(data)):
        try:
            ole = olefile.OleFileIO(io.BytesIO(data))
        except Exception:
            return None
        raw = None
        for name in _STREAM_NAMES:
            try:
                if ole.exists(name):
                    raw = ole.openstream(name).read()
                    break
            except Exception:
                continue
        ole.close()
        if raw is None:
            return None
        data = raw

    # EQNOLEFILEHDR: cbHdr(2) version(4) cf(2) cbObject(4) reserved(16)
    if len(data) > 2:
        cb_hdr = int.from_bytes(data[:2], "little")
        if cb_hdr == 28 and len(data) > cb_hdr:
            data = data[cb_hdr:]
    return data or None


def probe_version(mtef_payload: bytes) -> int | None:
    """读 MTEF 头首字节得到格式版本（通常为 3 或 5）。"""
    if not mtef_payload:
        return None
    v = mtef_payload[0]
    return v if v in (2, 3, 4, 5) else None


def digest_of(mtef_payload: bytes) -> str:
    """内容指纹，用于去重缓存。必须基于**剥壳后的净荷**。"""
    return hashlib.blake2b(mtef_payload, digest_size=16).hexdigest()


def is_empty_mathml(mathml: str | None) -> bool:
    """判断是否为「空壳」MathML —— mathtypejx 静默失败的标志。

    形如 ``<math display="block" xmlns="..."/>``：根元素下无任何实质子节点。
    """
    if not mathml:
        return True
    s = mathml.strip()
    if not s:
        return True
    # 自闭合根元素
    if s.endswith("/>") and s.count("<") == 1:
        return True
    try:
        from lxml import etree

        root = etree.fromstring(s.encode("utf-8"))
    except Exception:
        # 解析不了但有内容，交给下游判断，不武断判空
        return False
    if len(root) == 0 and not (root.text or "").strip():
        return True
    return False


def _mtef_to_mathml_str(mtef_payload: bytes) -> str | None:
    """MTEF 净荷 → MathML 字符串（走模块级缓存的加速 XSLT，不重复解 OLE）。

    等价于 ``mathtypejx.mtef.mathml.mtef_to_mathml`` 的解析部分，但直接吃
    **已剥壳的净荷** ``mtef_payload``（跳过 ``convert()`` 里已做过一次的 OLE
    解包），且 XSLT 由 :mod:`mtefx._xslt` 进程级缓存，免去 mathtypejx 每公式
    重载 348KB fontmap + 重编译样式表的开销。

    保留 mathtypejx 的「版本兜底」：主版本解析抛异常时，换另一版本定义再试一次。
    """
    from mathtypejx.mtef.builder import build_mtef_xml
    from mathtypejx.mtef.chars import replace as chars_replace
    from mathtypejx.mtef.mathml import _skip_stream_header
    from mathtypejx.mtef.mover import move as mover_move
    from mathtypejx.mtef.records3 import parse_equation_v3
    from mathtypejx.mtef.records5 import parse_equation
    from mathtypejx.mtef.stream import ByteStream

    if len(mtef_payload) < 2:
        return None
    ver = mtef_payload[0]
    if ver not in (3, 5):
        return None

    stream = ByteStream(mtef_payload)
    _skip_stream_header(stream, ver)
    eq = None
    try:
        eq = parse_equation(stream) if ver == 5 else parse_equation_v3(stream)
    except Exception:
        try:
            stream = ByteStream(mtef_payload)
            _skip_stream_header(stream, 5 if ver == 3 else 3)
            eq = parse_equation_v3(stream) if ver == 3 else parse_equation(stream)
        except Exception:
            return None
    if eq is None:
        return None
    eq.setdefault("mtef_version", ver)

    xml_root = build_mtef_xml(eq)
    mover_move(xml_root)
    chars_replace(xml_root)
    return _xslt_transform_element(xml_root)


# ────────────────────────────── 主转换入口 ──────────────────────────────


def _strip_ns_inplace(root: etree._Element) -> None:
    """原地去掉前缀与内层 xmlns 声明（仅保留根 ``<math>`` 的 MathML 命名空间）。

    在**已反解析的普通 lxml 树**上操作是安全的（注意：绝不要对
    ``_XSLTResultTree.getroot()`` 直接做这件事——lxml 的 XSLT 结果树在实体
    解码与命名空间提升上有怪癖，见 :mod:`mtefx._xslt`）。
    """
    for el in root.iter():
        tag = el.tag
        if isinstance(tag, str) and "}" in tag:
            el.tag = tag.split("}", 1)[1]
        for attr in list(el.attrib):
            if attr == "xmlns":
                if el is not root or el.attrib[attr] == "":
                    del el.attrib[attr]
            elif attr.startswith("xmlns:"):
                del el.attrib[attr]


def _serialize_mathml(root: etree._Element) -> str:
    """把归一化后的根树序列化成最终 MathML 字符串，并兜底根 ``<math>`` 的 xmlns。

    判定依据：序列化后**根开标签是否已含 xmlns**——lxml 不会把默认 xmlns 暴露
    在 ``root.attrib`` 里（不能查 attrib），也不能用「整串是否含 xmlns」（XSLT
    可能把声明落在子元素如 ``<msqrt>``，根开标签仍缺 xmlns，整串却已含 xmlns，
    会导致 MML2OMML 按 URI 匹配 ``<m:math>`` 失败）。这里只看根开标签。
    """
    out = etree.tostring(root, encoding="unicode", pretty_print=False)
    out = re.sub(r'\sxmlns=""', "", out)
    if not re.search(r'^<math\b[^>]*\bxmlns=', out):
        if out.startswith("<math "):
            out = out.replace("<math ", f'<math xmlns="{_MATHML_NS}" ', 1)
        elif out.startswith("<math>"):
            out = out.replace("<math>", f'<math xmlns="{_MATHML_NS}">', 1)
    return out


def _normalize_root(root: etree._Element) -> str:
    """对**已反解析的普通 lxml 树**做命名空间归一化并返回字符串。

    与 ``normalize_mathml`` 共用同一套逻辑，区别只是入口是 Element 而非字符串，
    供 ``convert()`` 在**单次反解析**的树上直接复用，省掉一次 ``fromstring``。

    注意：务必传入「普通 lxml 树」，不要直接喂 ``_XSLTResultTree.getroot()``——
    lxml 的 XSLT 结果树在实体解码与命名空间提升上有怪癖（见 :mod:`mtefx._xslt`）。
    """
    _strip_ns_inplace(root)
    return _serialize_mathml(root)


def normalize_mathml(mathml: str) -> str:
    """命名空间归一化 —— 与 mathtypejx 原生后处理等价（字符串入口）。

    直接调用 XSLT（v3 修复路径）会绕过 mathtypejx 的后处理，产出诸如
    ``<msqrt xmlns="…MathML"><mrow xmlns="">`` 的命名空间污染：部分子树带
    MathML 命名空间，其余被 ``xmlns=""`` 强制拽回无命名空间。这种文档在
    MathJax / MML2OMML.XSL 下会渲染失败。

    本函数去掉所有前缀与内层 xmlns 声明，只在根 ``<math>`` 上保留一个。
    """
    if not mathml:
        return mathml
    cleaned = re.sub(r"<\?xml[^?]*\?>", "", mathml).strip()
    try:
        root = etree.fromstring(cleaned.encode("utf-8"))
    except Exception:
        return mathml
    return _normalize_root(root)


def convert(
    ole_bytes: bytes,
    *,
    fast: bool = True,
    fix_v3: bool = True,
    repair_chars: bool = True,
) -> FormulaResult:
    """把单个 MathType OLE 对象转成 MathML。

    Args:
        ole_bytes: OLE 复合文档字节，或裸 MTEF 字节。
        fast: 启用加速 XSLT（33x，输出等价）。
        fix_v3: 对 MTEF v3 启用 slot 结构修复（1/11 → 11/11）。
        repair_chars: 用 transpect fontmap 兜底修复私用区字符。

    Returns:
        FormulaResult；``needs_fallback`` 为 True 时应交由 refine 层处理。
    """
    t0 = time.perf_counter()

    payload = extract_mtef(ole_bytes)
    if payload is None:
        return FormulaResult(
            status=STATUS_NO_STREAM,
            reason="OLE 中未找到 Equation Native 流",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    dig = digest_of(payload)
    ver = probe_version(payload)
    if ver is None:
        return FormulaResult(
            status=STATUS_BAD_VERSION,
            digest=dig,
            reason=f"MTEF 版本字节异常: {payload[0]:#x}",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    if fast:
        _install_fast_xslt()

    try:
        if ver == 3 and fix_v3:
            # v3 走修复路径：mathtypejx 原生 v3 会产出空壳（见 v3fix.py）
            from mtefx.v3fix import mtef_v3_to_mathml

            mathml = mtef_v3_to_mathml(payload)
        else:
            mathml = _mtef_to_mathml_str(payload)
    except Exception as exc:
        return FormulaResult(
            status=STATUS_ERROR,
            mtef_version=ver,
            digest=dig,
            reason=f"{type(exc).__name__}: {exc}",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    # 单次反解析：同一棵树上做「空壳判定 + 命名空间归一化」，省掉旧路径
    # is_empty_mathml + normalize_mathml 的两次 fromstring。
    try:
        root = etree.fromstring(mathml.encode("utf-8"))
    except Exception as exc:
        return FormulaResult(
            status=STATUS_ERROR,
            mtef_version=ver,
            digest=dig,
            reason="MathML 反解析失败",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    if len(root) == 0 and not (root.text or "").strip():
        # 空壳 → v3 可换 mathtypejx 原生（绕过修复层）再试一次，否则判空交给 refine
        if ver == 3 and fix_v3:
            try:
                import mathtypejx.mtef.mathml as M

                fallback = M.mtef_to_mathml(ole_bytes)
                if not is_empty_mathml(fallback):
                    root = etree.fromstring(fallback.encode("utf-8"))
            except Exception:
                pass
        if len(root) == 0 and not (root.text or "").strip():
            return FormulaResult(
                status=STATUS_EMPTY,
                mtef_version=ver,
                digest=dig,
                reason=f"产出空壳 MathML（MTEF v{ver}）—— 静默失败，需 refine 兜底",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )

    # 命名空间归一化（与旧字符串路径完全一致，保留 mathtypejx 产出的「混合命名
    # 空间」结构——msqrt 等少数元素留在 MathML 命名空间、其余裸元素，这是
    # MML2OMML.XSL 按 URI 正确匹配的前提；底层 XSLT 已进程级缓存，免去每公式
    # 重载 348KB fontmap + 重编译的开销，是真·提速点）。复用 _normalize_root，
    # 与 normalize_mathml 共用同一套逻辑，避免漂移。
    mathml = _normalize_root(root)

    pua_fixed = pua_left = 0
    if repair_chars:
        from mtefx.fontmap import repair_pua

        mathml, pua_fixed, pua_left = repair_pua(mathml)

    return FormulaResult(
        status=STATUS_OK,
        mathml=mathml,
        mtef_version=ver,
        digest=dig,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
        pua_fixed=pua_fixed,
        pua_unresolved=pua_left,
    )


# ────────────────────────────── 测试样本 ──────────────────────────────


def iter_fixtures(which: str = "all") -> Iterator[tuple[str, bytes]]:
    """遍历 transpect 自带的真实 MTEF 样本，用作回归基准。

    Args:
        which: ``all`` / ``mathtype3`` / ``mathtype5``
    """
    base = _FIXTURES / "input"
    if not base.is_dir():
        return
    groups = ["mathtype3", "mathtype5"] if which == "all" else [which]
    for g in groups:
        d = base / g
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.bin")):
            yield f"{g}/{p.name}", p.read_bytes()
