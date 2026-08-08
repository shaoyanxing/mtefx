"""
mtefx —— MathType (MTEF) 公式解析加速与增强层。

B 路线（MTEF → MathML → OMML），**零 COM 依赖**，融合两个上游：

* ``a917470154/mathtypejx``      —— Python 解析引擎（MIT）
* ``transpect/mathtype-extension`` —— 出版级 XSLT 与 fontmap 资产

本层提供四项由实测驱动的改进（基准：transpect 自带的 33 个真实 MTEF 样本）：

======================  ==============================================
XSLT 加速               93.49 → 2.83 ms/公式（33x），输出 33/33 字节级一致
MTEF v3 结构修复        v3 可用率 1/11 → 11/11
空壳哨兵                消除 mathtypejx 的静默失败（返回空 <math/> 不报错）
fontmap 字符兜底        修复漏进 MathML 的私用区字符（如 U+EB04 → U+2004）
======================  ==============================================

快速开始::

    from mtefx import convert, convert_docx

    res = convert(ole_bytes)
    if res.ok:
        print(res.mathml)

    rep = convert_docx("试卷.docx")
    print(rep.ok, "/", rep.total)
"""

from mtefx.engine import (
    FormulaResult,
    convert,
    digest_of,
    extract_mtef,
    is_empty_mathml,
    iter_fixtures,
    probe_version,
)
from mtefx.pipeline import DocReport, convert_docx, convert_many, summarize

__version__ = "0.1.0"

__all__ = [
    "FormulaResult",
    "convert",
    "digest_of",
    "extract_mtef",
    "is_empty_mathml",
    "iter_fixtures",
    "probe_version",
    "DocReport",
    "convert_docx",
    "convert_many",
    "summarize",
    "__version__",
]
