"""
MTEF v3 结构修复 —— 把 mathtypejx 的 v3 静默失败修好。

问题定位（用 transpect 自带的 11 个 v3 样本复现）：

v3 的二进制解析其实是**正确的** —— ``records3.parse_equation_v3`` 能正确解出
``tmFRACT`` 分数模板、字符码位等。真正的问题出在 ``builder.build_mtef_xml``
生成的中间 XML 结构上：

    mathtypejx 产出 :  <mtef><full/><tmpl>…</tmpl></mtef>
    XSLT 期望(v5形) :  <mtef><full/><slot><tmpl>…</tmpl></slot></mtef>

v3 分支把记录直接挂在 ``<mtef>`` 下，少了一层 ``<slot>`` 包裹。XSLT 的模板
匹配不到，于是输出 ``<math/>`` 空壳 —— 而且**不抛异常、不返回 None**。

本模块在 build 之后、XSLT 之前补上这层 ``<slot>``。
实测：v3 可用率 1/11 → **11/11**。

顺带修正被硬编码成 v5 值的头部元数据（v3 没有 application_key /
equation_options，且 platform/product 取值不同）。
"""

from __future__ import annotations

from lxml import etree

from mtefx._xslt import transform as _xslt_transform_element

# <mtef> 下属于「头部元数据」的标签，不参与 slot 包裹
_HEADER_TAGS = frozenset(
    {
        "mtef_version",
        "platform",
        "product",
        "product_version",
        "product_subversion",
        "application_key",
        "equation_options",
        "encoding_def",
        "font_def",
        "eqn_prefs",
    }
)

# XSLT 的入口模板是 mtef[equation_options = 'block' | 'inline']。
# 这个字段一旦缺失，<mtef> 就只能落到 <xsl:template match="*"/> 空模板，
# 整个转换输出**空字符串**（连 <math> 外壳都没有）。
# ⚠️ 早期版本曾把它当作「v5 专有字段」删掉，结果自断入口 —— 切勿再犯。
_DEFAULT_EQUATION_OPTIONS = "block"


def wrap_v3_slot(root: etree._Element, *, fix_header: bool = True) -> bool:
    """给 v3 的 MTEF XML 补上 ``<slot>`` 包裹。

    Args:
        root: ``build_mtef_xml`` 的返回值（``<root>`` 元素）。
        fix_header: 补齐 XSLT 入口所必需的 ``equation_options``（缺失时补 block）。
            注意这里只**补**不**删** —— 见上方 ``_DEFAULT_EQUATION_OPTIONS`` 注释。

    Returns:
        是否实际做了修改。
    """
    mtef = root.find("mtef")
    if mtef is None:
        return False

    if fix_header:
        _ensure_equation_options(mtef)

    # 已经是正确结构（v5 或已修过）则跳过
    if mtef.find("slot") is not None or mtef.find("pile") is not None:
        return False

    full = mtef.find("full")
    if full is None:
        return False

    body = [c for c in mtef if c.tag not in _HEADER_TAGS and c.tag != "full"]
    if not body:
        return False

    slot = etree.Element("slot")
    opts = etree.SubElement(slot, "options")
    opts.text = "0"

    insert_at = list(mtef).index(full) + 1
    for child in body:
        mtef.remove(child)
        if child.tag == "end":
            continue  # 原有的 end 会在 slot / mtef 末尾重建
        slot.append(child)

    etree.SubElement(slot, "end")
    mtef.insert(insert_at, slot)
    etree.SubElement(mtef, "end")
    return True


def _ensure_equation_options(mtef: etree._Element) -> None:
    """保证 ``equation_options`` 存在且取值合法，否则 XSLT 入口模板匹配不到。"""
    el = mtef.find("equation_options")
    if el is None:
        el = etree.Element("equation_options")
        # 放在头部字段之后、正文之前；找不到锚点就插到最前
        anchor = mtef.find("product_subversion")
        idx = list(mtef).index(anchor) + 1 if anchor is not None else 0
        mtef.insert(idx, el)
    if (el.text or "").strip() not in ("block", "inline"):
        el.text = _DEFAULT_EQUATION_OPTIONS


def mtef_v3_to_mathml_element(mtef_payload: bytes, *, debug: bool = False) -> str | None:
    """v3 专用转换路径：解析 → 建 XML → 补 slot → mover/chars → XSLT。

    返回 MathML **字符串**（已解码、与旧字符串路径逐字节一致，可直接喂给
    ``normalize_mathml`` / ``MML2OMML``）。底层 XSLT 由 :mod:`mtefx._xslt` 进程级
    缓存，免去 mathtypejx 每公式重载 fontmap + 重编译 XSLT 的开销。

    Args:
        mtef_payload: 已剥离 OLE 头的 MTEF 净荷。
        debug: 为 True 时不吞异常，直接抛出，便于定位。

    Returns:
        MathML 字符串；失败返回 None。失败原因记录在 :func:`last_error`。
    """
    from mathtypejx.mtef.builder import build_mtef_xml
    from mathtypejx.mtef.chars import replace as chars_replace
    from mathtypejx.mtef.mathml import _skip_stream_header
    from mathtypejx.mtef.mover import move as mover_move
    from mathtypejx.mtef.records3 import parse_equation_v3
    from mathtypejx.mtef.stream import ByteStream

    try:
        stream = ByteStream(mtef_payload)
        _skip_stream_header(stream, 3)
        eq = parse_equation_v3(stream)
        if eq is None:
            return None
        eq.setdefault("mtef_version", 3)

        xml_root = build_mtef_xml(eq)
        wrap_v3_slot(xml_root)
        mover_move(xml_root)
        chars_replace(xml_root)
        out = _xslt_transform_element(xml_root)
        if out is None:
            _set_error("XSLT 输出为空 —— 检查 mtef/equation_options 是否存在")
            return None
        _set_error("")
        return out
    except Exception as exc:
        if debug:
            raise
        _set_error(f"{type(exc).__name__}: {exc}")
        return None


_last_error = ""


def _set_error(msg: str) -> None:
    global _last_error
    _last_error = msg


def mtef_v3_to_mathml(mtef_payload: bytes, *, debug: bool = False) -> str | None:
    """v3 专用转换路径（字符串版，向后兼容 API）。

    直接返回 :func:`mtef_v3_to_mathml_element` 的 MathML 字符串。
    """
    return mtef_v3_to_mathml_element(mtef_payload, debug=debug)


def last_error() -> str:
    """返回上一次 :func:`mtef_v3_to_mathml` 失败的原因（成功则为空串）。"""
    return _last_error
