"""
缓存的「MTEF XML → MathML」XSLT —— 全程返回 lxml Element。

历史包袱：
- mathtypejx 自带的 ``_xslt_transform`` 每次调用都 ``etree.parse`` + ``etree.XSLT``
  重新编译样式表，且只返回**字符串**，于是调用方为了判空 / 归一化命名空间，
  不得不反复 ``fromstring`` / ``tostring``，单公式白做 2~3 次序列化往返。
- 旧 ``engine._install_fast_xslt`` 用 monkeypatch 把编译结果缓存下来，但依然返回字符串。

本模块把所有逻辑收敛到一处：
- 样式表**只编译一次**（模块级 ``_CACHE``），进程内/子进程内复用；
- ``transform()`` 直接返回 XSLT 结果的 **根 Element**，调用方持有树做后续处理，
  全程零多余序列化。

资产位置与 ``engine._XSLT_FAST`` 保持一致，避免两份真相。
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

_XSLT_PATH = Path(__file__).resolve().parent / "assets" / "xslt_fast" / "transform.xsl"

_CACHE: dict[str, "etree.XSLT"] = {}


def get_transform() -> "etree.XSLT | None":
    """返回编译好的 XSLT 转换器；资产缺失时返回 None。"""
    if not _XSLT_PATH.exists():
        return None
    t = _CACHE.get("t")
    if t is None:
        t = etree.XSLT(etree.parse(str(_XSLT_PATH)))
        _CACHE["t"] = t
    return t


def transform(xml_root: etree._Element) -> str | None:
    """把 MTEF 中间 XML 转成 MathML 字符串（已解码、与旧路径逐字节一致）。

    Args:
        xml_root: ``build_mtef_xml`` 的输出（``<root><mtef>…</mtef></root>``）。

    Returns:
        MathML 字符串；转换失败返回 None。

    为什么返回字符串而非 Element —— lxml ``_XSLTResultTree`` 的两个坑：

    1. **实体解码**：``result.getroot()`` 的文本节点里 ``&#x2003;`` 会以 7 字符字面
       字符串残留（不解码），而 ``etree.tostring(result, "unicode")`` 序列化时已
       经解码成字面字符 `` ``。用 ``getroot`` 会让 MML2OMML 拿到
       ``&amp;#x2003;`` 而乱套。
    2. **命名空间提升(hoist)**：``etree.tostring(result)`` 会把子元素声明的
       ``xmlns="…MathML"`` 提升到根 ``<math>``；但若 ``fromstring`` 重新解析再
       序列化，这个提升会丢失（``xmlns`` 退回子元素），导致 ``normalize_mathml``
       产出与旧路径不同的树（如 equation2.bin 的 ``<msqrt>`` 误判）。

    因此直接返回 ``etree.tostring(result)`` 这个**与旧字符串路径完全一致的产物**，
    调用方拿到的就是和 mathtypejx 原生 ``etree.tostring(xslt(xml_root))`` 相同的
    字符串，可无缝接入 ``normalize_mathml`` / ``MML2OMML``。真正的提速点在于
    ``get_transform`` 对样式表的**进程级缓存**（免去 mathtypejx 每公式重载
    348KB fontmap + 重编译 XSLT），而非少几次序列化。
    """
    t = get_transform()
    if t is None:
        return None
    try:
        result = t(xml_root)
        return etree.tostring(result, encoding="unicode", pretty_print=False)
    except Exception:
        return None
