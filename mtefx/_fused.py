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


# MML2OMML 只对 <mfenced> 元素生成可撑大的 <m:d><m:dPr><m:begChr/><m:endChr/></m:dPr>
# 结构；看到 <mrow><mo>{</mo>...<mo>}</mo></mrow> 这种「裸 mo 当括号」的模式会按普通
# token 序列处理，导致：
#   1. 括号被输出为 <m:r><m:t>{</m:t></m:r>，Word 不撑大
#   2. 某些情况下闭合括号 <mo>}</mo> 会被忽略（实测真实语料：4 个左大括号，0 个右大括号）
# 修复：扫描 mrow 模式，把 fence 重写为 mfenced，让 MML2OMML 出正确的 m:d。
_FENCE_CHARS = {
    "(": (")", "(", ")"),
    "[": ("]", "[", "]"),
    "{": ("}", "{", "}"),
    "\u2329": ("\u232A", "\u2329", "\u232A"),  # ⟨⟩
    "\u230A": ("\u230B", "\u230A", "\u230B"),  # ⌊⌋
    "\u2308": ("\u2309", "\u2308", "\u2309"),  # ⌈⌉
    "\u2016": ("\u2016", "\u2016", "\u2016"),  # ‖
    "\u301A": ("\u301B", "\u301A", "\u301B"),  # 〚〛
    "|": ("|", "|", "|"),                       # 绝对值
    "\u007C": ("\u007C", "\u007C", "\u007C"),
}


def _rewrite_fence_mrow_to_mfenced(root: etree._Element) -> int:
    """把 <mrow> 中以 fence mo 起头但缺少闭合 mo 的模式改写为 <mfenced>。

    实测真实语料中 MTEF XSLT fence.xsl 经常产出如下残缺模式（闭合括号 mo 缺失）：
        <mrow><mrow><mo>{</mo><mrow><mtable>...</mtable></mrow></mrow></mrow>

    还有完整模式：
        <mrow><mo>{</mo>...<mo>}</mo></mrow>

    改写策略：所有模式都统一改写为 mfenced，让 MML2OMML 输出可撑大的 m:d。
    后续 _rewrite_group_brace_to_groupChr() 会再把「m:d 包 mtable 单 m:e（方程组）」
    的模式改写为 m:groupChr——这才 OMML 标准的「左大括号、无右括号」结构。
    返回改写数量。
    """
    n = 0
    for mrow in list(root.iter(f"{{{_MATHML_NS}}}mrow")):
        kids = list(mrow)
        if len(kids) < 2:
            continue
        first = kids[0]
        if first.tag != f"{{{_MATHML_NS}}}mo":
            continue
        open_ch = (first.text or "").strip()
        if open_ch not in _FENCE_CHARS:
            continue
        expected_close = _FENCE_CHARS[open_ch][0]

        # 检查尾部是否已有匹配的闭合 mo
        last = kids[-1]
        has_close = (
            last.tag == f"{{{_MATHML_NS}}}mo"
            and (last.text or "").strip() == expected_close
        )

        if has_close:
            middle = kids[1:-1]
        else:
            middle = kids[1:]

        if not middle:
            continue

        # 标准路径：构造 mfenced 让 MML2OMML 输出可撑大的 m:d
        if not has_close:
            closing = etree.Element(f"{{{_MATHML_NS}}}mo")
            closing.text = expected_close
            for k, v in first.attrib.items():
                closing.set(k, v)
            mrow.append(closing)

        mfenced = etree.Element(f"{{{_MATHML_NS}}}mfenced")
        mfenced.set("open", open_ch)
        mfenced.set("close", expected_close)
        # separators 设为空，避免 MML2OMML 默认 "," 分隔符
        mfenced.set("separators", "")
        stretchy = first.get("stretchy")
        if stretchy:
            mfenced.set("stretchy", stretchy)
        for k, v in first.attrib.items():
            if k != "stretchy":
                mfenced.set(k, v)
        for c in middle:
            mfenced.append(c)
        parent = mrow.getparent()
        if parent is None:
            continue
        parent.replace(mrow, mfenced)
        n += 1
    return n


def _rewrite_group_brace_to_groupChr(omml_root: etree._Element) -> int:
    """OMML 后处理：把「<m:d> 包 <m:e><m:eqArr>...</m:eqArr></m:e>」模式
    改写为 <m:groupChr>，OMML 的 groupChr 才是「只显示左大括号、不显示右大括号」
    的标准方程组/分段函数结构。

    m:d 模式（m:dPr 含 begChr 但 endChr 也是单一字符）会显示左右两个括号，
    视觉上像「{ ... }」括号包方程组——不是标准样式。
    m:groupChr 模式只显示一个撑大的 begChr 字符，右侧无 endChr。

    触发条件：
      - m:d 第一个 m:e 内只含 m:eqArr（方程组堆叠）
      - m:dPr 含 begChr（开括号字符）
    返回改写数量。
    """
    M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
    n = 0
    for md in list(omml_root.iter(f"{M}d")):
        dpr = md.find(f"{M}dPr")
        if dpr is None:
            continue
        beg = dpr.find(f"{M}begChr")
        if beg is None:
            continue
        # 检查 m:d 的第一个 m:e 内部是否就是 m:eqArr
        es = md.findall(f"{M}e")
        if len(es) != 1:
            continue  # 多 m:e（如矩阵的多个列）保持 m:d
        first_e = es[0]
        # first_e 内部必须是 m:eqArr（堆叠方程组）
        if first_e.find(f"{M}eqArr") is None:
            continue
        # 改写为 m:groupChr
        chr_val = beg.get(f"{M}val", "")
        group_chr = etree.Element(f"{M}groupChr")
        group_chr_pr = etree.SubElement(group_chr, f"{M}groupChrPr")
        chr_el = etree.SubElement(group_chr_pr, f"{M}chr")
        chr_el.set(f"{M}val", chr_val)
        # 不设置 m:pos：OMML 规范里 m:pos 缺省值就是「左侧大括号」，
        # 设了 top 会让大括号跑到内容上方（overbrace 样式）。
        # vertJc 缺省也是 center，无需显式设。
        # 把 first_e 移入 groupChr
        md.remove(first_e)
        group_chr.append(first_e)
        # 替换 m:d
        parent = md.getparent()
        if parent is None:
            continue
        parent.replace(md, group_chr)
        n += 1
    return n


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
    # 把 <mrow><mo>{</mo>...<mo>}</mo></mrow> 改写为 <mfenced>，让 MML2OMML 生成可撑大的 m:d
    _rewrite_fence_mrow_to_mfenced(root)

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

    # OMML 后处理：把「m:d 包 mtable 单 m:e（方程组）」改写为 m:groupChr，
    # 这是 OMML 标准的「只显示左大括号、无右括号」结构。
    _rewrite_group_brace_to_groupChr(omml_root)

    return ("ok", omml_root, fixed, unresolved, (time.perf_counter() - t0) * 1000)
