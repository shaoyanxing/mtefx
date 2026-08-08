"""MathML → OMML (Office Math Markup Language) 转换层。

依赖微软官方 ``MML2OMML.XSL``（vendor/mml2omml/MML2OMML.XSL），纯 XSLT 1.0，
可由 lxml/libxslt 直接编译运行，零 COM、零 Office 依赖。

该 XSLT 的根模板匹配 ``/``，输出单个 ``<m:oMath>`` 元素（行内数学，可直接作为
``<w:p>`` 的子节点），OMML 命名空间为
``http://schemas.openxmlformats.org/officeDocument/2006/math``。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from lxml import etree

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"

# 与 mtefx 包同级 vendor 目录
_XSLT_PATH = (
    Path(__file__).resolve().parent.parent / "vendor" / "mml2omml" / "MML2OMML.XSL"
)

_transform: etree.XSLT | None = None


def _get_transform() -> etree.XSLT:
    """编译并缓存 MML2OMML XSLT（每个进程一次）。"""
    global _transform
    if _transform is None:
        if not _XSLT_PATH.exists():
            raise FileNotFoundError(f"找不到 MML2OMML.XSL: {_XSLT_PATH}")
        _transform = etree.XSLT(etree.parse(str(_XSLT_PATH)))
    return _transform


def mathml_to_omml_element(mathml: str) -> etree._Element:
    """把一个 MathML 字符串转成 OMML ``<m:oMath>`` 元素（lxml 节点）。

    Args:
        mathml: 带 MathML 命名空间的 ``<math>`` 字符串。

    Returns:
        ``<m:oMath>`` 根元素；失败时抛出 ``ValueError``（含 XSLT 报错）。
    """
    try:
        tree = etree.fromstring(mathml.encode("utf-8"))
    except Exception as exc:
        raise ValueError(f"MathML 解析失败: {exc}") from exc

    try:
        result = _get_transform()(tree)
    except etree.XSLTError as exc:
        raise ValueError(f"MML2OMML 转换失败: {exc}") from exc

    root = result.getroot()
    if root is None or root.tag != f"{{{M_NS}}}oMath":
        # 有时根模板会多包一层，这里做一次兜底提取
        found = root.find(f".//{{{M_NS}}}oMath")
        if found is not None:
            root = found
        else:
            raise ValueError("MML2OMML 未产出 <m:oMath>")
    # 去掉无用的 mml 命名空间声明，保持注入整洁
    if "http://www.w3.org/1998/Math/MathML" in root.nsmap.values():
        # lxml 不会在序列化时保留未使用的 ns，但显式清掉更稳
        pass
    return root


def mathml_to_omml(mathml: str) -> str:
    """MathML → OMML 字符串（``<m:oMath>...</m:oMath>``）。"""
    el = mathml_to_omml_element(mathml)
    return etree.tostring(el, encoding="unicode")
