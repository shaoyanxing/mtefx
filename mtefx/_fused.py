"""
融合 MVP —— MTEF → OMML 一次 Element 串联（验证用，非生产代码）。

当前路径：convert() 产 MathML 字符串 → docxconv 调 mathml_to_omml_element(str)
          → 内部 fromstring 再走 MML2OMML。中间经过 _normalize_root 的 tostring
          + repair_pua 的字符串扫描 + omml 入口的 fromstring，共 2~3 次序列化/解析。

本模块：transform 输出的 MathML 直接持有为 lxml Element，在 Element 上做完
命名空间归一化（复刻 _normalize_root 的精确语义）与 PUA 修复（repair_pua_element，
已存在的 Element 版），**直接喂 MML2OMML XSLT**，省去
  · _normalize_root 的 etree.tostring
  · repair_pua 的全字符串扫描
  · mathml_to_omml_element 入口的 fromstring
两次序列化往返 + 一次字符串扫描。

目的：拿到「融合」的真实 OMML 级一致性与端到端提速数据，供方案取舍决策。
OMML 级一致性是硬闸门——只要 33/33 逐字节一致，即证明融合在 MML2OMML 视角
与目标等价；提速数据则回答「值不值得」。
"""

from __future__ import annotations

import time
from lxml import etree

from mtefx.engine import (
    extract_mtef,
    probe_version,
    _mtef_to_mathml_str,
    _strip_ns_inplace,
    _MATHML_NS,
)
from mtefx.v3fix import mtef_v3_to_mathml
from mtefx.fontmap import repair_pua_element
from mtefx.omml import _get_transform, M_NS


def _mathml_str_to_element(mathml: str) -> etree._Element | None:
    """MathML 字符串 → Element，单解析。失败时返回 None。"""
    try:
        return etree.fromstring(mathml.encode("utf-8"))
    except Exception:
        return None


def _normalize_element(root: etree._Element) -> etree._Element:
    """在 Element 上复刻 _normalize_root 的精确命名空间语义（不序列化）。

    ⚠️ 关键：必须给**所有元素（含子元素）在内存中统一设 MathML NS 的 URI**，
    不能只在根上设默认 NS 然后指望裸子元素继承。原因是 libxslt 的 XSLT 按元素
    **实际命名空间 URI** 匹配 ``match="mml:*"``，而「默认命名空间继承」只是 XML
    解析/序列化时的概念——在 lxml 内存模型里，把裸子元素移进默认 NS 根后，子
    元素的 URI 仍是 ``None``，导致 MML2OMML 全部失配、OMML 输出为空。

    因此这里遍历整棵树，把每个元素的 tag 统一成 ``{MathML}local`` 并删除所有
    ``xmlns`` 声明属性——这与当前路径 ``fromstring(_normalize_root 字符串)`` 后
    的内存状态逐字节等价（根 + 子全在 MathML NS，无任何 xmlns 属性声明）。
    """
    for el in root.iter():
        if isinstance(el.tag, str):
            local = el.tag.split("}", 1)[1] if "}" in el.tag else el.tag
            el.tag = f"{{{_MATHML_NS}}}{local}"
        for attr in list(el.attrib):
            if attr == "xmlns" or attr.startswith("xmlns:"):
                del el.attrib[attr]
    return root


_UNSUPPORTED_MENCLOSE = {"longdiv", "actuarial"}


def _unwrap_unsupported_menclose(root: etree._Element) -> etree._Element:
    """MML2OMML 完全不支持 menclose 的 longdiv/actuarial 记号，会产出空 <m:oMath/>。

    这里在 XSLT 前把这类 menclose **原地展开**为包其内容的 <mrow>，保住内部数学
    （宁可丢失「长除号/精算括号」装饰，也不要把整段公式变成空 OMML 静默丢弃）。
    circle/box/strike/radical 等 MML2OMML 能处理，保持不动。
    """
    for m in list(root.iter(f"{{{_MATHML_NS}}}menclose")):
        nota = set((m.get("notation") or "").split())
        if nota & _UNSUPPORTED_MENCLOSE:
            parent = m.getparent()
            if parent is None:
                continue
            repl = etree.Element(f"{{{_MATHML_NS}}}mrow")
            if (m.text or "").strip():
                repl.text = m.text
            for k in list(m):
                repl.append(k)
            parent.replace(m, repl)
    return root


def _is_degenerate_omml(el: etree._Element) -> bool:
    """OMML 是否退化（空壳）：无子元素且无文本，即 <m:oMath/>。"""
    return el is not None and len(list(el)) == 0 and not (el.text or "").strip()


def convert_fused(
    blob: bytes, *, fix_v3: bool = True, repair_chars: bool = True
) -> tuple[str, etree._Element | None, int, int, float]:
    """MTEF/OLE 字节 → OMML <m:oMath> Element，一次 Element 串联。

    Returns:
        (status, omml_element|None, pua_fixed, pua_unresolved, elapsed_ms)
        status ∈ {ok, empty, error, no_stream}
    """
    t0 = time.perf_counter()

    payload = extract_mtef(blob)
    if payload is None:
        return ("no_stream", None, 0, 0, (time.perf_counter() - t0) * 1000)

    ver = probe_version(payload)
    try:
        if ver == 3 and fix_v3:
            mathml = mtef_v3_to_mathml(payload)
        else:
            mathml = _mtef_to_mathml_str(payload)
    except Exception:
        return ("error", None, 0, 0, (time.perf_counter() - t0) * 1000)
    if not mathml:
        return ("error", None, 0, 0, (time.perf_counter() - t0) * 1000)

    root = _mathml_str_to_element(mathml)
    if root is None:
        return ("error", None, 0, 0, (time.perf_counter() - t0) * 1000)

    # 空壳判定（与 convert 同语义）
    if len(root) == 0 and not (root.text or "").strip():
        return ("empty", None, 0, 0, (time.perf_counter() - t0) * 1000)

    # 命名空间归一化（Element 级，复刻 _normalize_root）
    root = _normalize_element(root)
    # PUA 修复（Element 级，省字符串扫描）
    fixed = unresolved = 0
    if repair_chars:
        fixed, unresolved = repair_pua_element(root)
    # 展开 MML2OMML 不支持的 menclose 记号（longdiv/actuarial），保住内部数学
    _unwrap_unsupported_menclose(root)

    try:
        result = _get_transform()(root)
    except etree.XSLTError:
        return ("error", None, fixed, unresolved, (time.perf_counter() - t0) * 1000)

    omml_root = result.getroot()
    if omml_root is None or omml_root.tag != f"{{{M_NS}}}oMath":
        found = (
            omml_root.find(f".//{{{M_NS}}}oMath") if omml_root is not None else None
        )
        omml_root = found
    if omml_root is None:
        return ("error", None, fixed, unresolved, (time.perf_counter() - t0) * 1000)

    # 退化守卫：MML2OMML 对某些构造（如 menclose longdiv/actuarial）会产出空
    # <m:oMath/>。若不拦截，docxconv 会把「空 OMML」当成成功写回，导致公式被
    # 静默丢弃。这里判为 empty，使上层保留原始 OLE 而非写入空壳。
    if _is_degenerate_omml(omml_root):
        return ("empty", None, fixed, unresolved, (time.perf_counter() - t0) * 1000)

    return ("ok", omml_root, fixed, unresolved, (time.perf_counter() - t0) * 1000)
