"""刁钻/高难度公式测试电池（构造式压力测试）。

本语料（中考）结构过于简单（全 v5、0 矩阵/积分/堆叠、100% 成功），
无法暴露覆盖率缺口。因此这里**构造**一批本语料缺失的高难度 MathML 构造，
逐一喂给转换引擎，找出 OMML 层 + 融合路径在进阶结构上的真实弱点：

  参考路径  mathml_to_omml_element(mathml)  → omml_ref
  融合核心  _normalize_element + repair_pua_element + MML2OMML → omml_fused
  比对：两者 canonical 是否一致；任一方是否失败/退化。

覆盖构造：矩阵/行列式、分段(cases)、定积分/求和/连乘(带极限)、
多行对齐(eqArr)、深度嵌套分式/根式、上下标组合、重音、上下限(lim)、
特殊字体 PUA(黑板粗体/花体/手写)、混合 CJK+数学、mphantom/mspace、
menclose(长除/精算)、根号带次数、二项式系数、张量多指标等。

用法：python -m mtefx._probe_hardmath
"""

from __future__ import annotations

from lxml import etree

from mtefx._fused import _normalize_element, convert_fused
from mtefx.fontmap import repair_pua_element
from mtefx.omml import mathml_to_omml_element, _get_transform, M_NS

_MATH = "http://www.w3.org/1998/Math/MathML"

# 每个用例: (名称, MathML 字符串)
CASES = []


def _add(name, body_inner, row=True):
    """body_inner 是 <math> 的子内容；row=False 时整段即 <math> 子节点集合。"""
    mm = f'<math xmlns="{_MATH}">{body_inner}</math>'
    CASES.append((name, mm))


# 1. 2x2 矩阵
_add("matrix_2x2",
     '<mrow><mi>A</mi><mo>=</mo>'
     '<mrow><mo>[</mo>'
     '<mtable><mtr><mtd><mi>a</mi></mtd><mtd><mi>b</mi></mtd></mtr>'
     '<mtr><mtd><mi>c</mi></mtd><mtd><mi>d</mi></mtd></mtr></mtable>'
     '<mo>]</mo></mrow></mrow>')

# 2. 3x3 矩阵含分式
_add("matrix_3x3_frac",
     '<mtable>'
     '<mtr><mtd><mfrac><mi>a</mi><mi>b</mi></mfrac></mtd><mtd><mi>x</mi></mtd><mtd><mn>1</mn></mtd></mtr>'
     '<mtr><mtd><mi>y</mi></mtd><mtd><mfrac><mn>2</mn><mn>3</mn></mfrac></mtd><mtd><mi>z</mi></mtd></mtr>'
     '<mtr><mtd><mn>0</mn></mtd><mtd><mi>w</mi></mtd><mtd><mi>k</mi></mtd></mtr>'
     '</mtable>')

# 3. 分段函数 cases（左大括号）
_add("piecewise_cases",
     '<mrow><mi>f</mi><mo>(</mo><mi>x</mi><mo>)</mo><mo>=</mo>'
     '<mrow><mo>{</mo>'
     '<mtable columnalign="left">'
     '<mtr><mtd><msup><mi>x</mi><mn>2</mn></msup></mtd><mtd><mtext>if&#160;</mtext><mi>x</mi><mo>&#x2265;</mo><mn>0</mn></mtd></mtr>'
     '<mtr><mtd><mo>&#x2212;</mo><mi>x</mi></mtd><mtd><mtext>if&#160;</mtext><mi>x</mi><mo>&lt;</mo><mn>0</mn></mtd></mtr>'
     '</mtable></mrow></mrow>')

# 4. 定积分（带上下限）
_add("integral_limits",
     '<mrow><msubsup><mo>&#x222B;</mo><mn>0</mn><mn>1</mn></msubsup>'
     '<msup><mi>e</mi><mrow><mo>&#x2212;</mo><msup><mi>x</mi><mn>2</mn></msup></mrow></msup>'
     '<mi>d</mi><mi>x</mi></mrow>')

# 5. 求和（带上下限）
_add("sum_limits",
     '<mrow><munderover><mo>&#x2211;</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow>'
     '<mi>n</mi></munderover><msub><mi>a</mi><mi>i</mi></msub></mrow>')

# 6. 连乘
_add("prod_limits",
     '<mrow><munderover><mo>&#x220F;</mo><mrow><mi>k</mi><mo>=</mo><mn>1</mn></mrow>'
     '<mi>n</mi></munderover><mo>(</mo><mi>x</mi><mo>&#x2212;</mo><mi>k</mi><mo>)</mo></mrow>')

# 7. 多行对齐（eqArr）
_add("multiline_eqarr",
     '<mtable columnalign="right center left">'
     '<mtr><mtd><mi>y</mi></mtd><mtd><mo>=</mo></mtd><mtd><msup><mi>x</mi><mn>2</mn></msup></mtd></mtr>'
     '<mtr><mtd></mtd><mtd><mo>=</mo></mtd><mtd><mrow><mi>x</mi><mo>&#x00B7;</mo><mi>x</mi></mrow></mtd></mtr>'
     '</mtable>')

# 8. 深度嵌套分式
_add("deep_frac",
     '<mfrac><mn>1</mn><mrow><mfrac><mn>1</mn><mrow><mfrac><mn>1</mn>'
     '<mrow><mfrac><mn>1</mn><mi>x</mi></mfrac></mrow></mfrac></mrow></mfrac></mrow></mfrac>')

# 9. 嵌套根式
_add("nested_radical",
     '<msqrt><mi>x</mi><mo>+</mo><msqrt><mi>y</mi><mo>+</mo>'
     '<msqrt><mi>z</mi></msqrt></msqrt></msqrt>')

# 10. 上下标组合
_add("subsup_combo",
     '<msubsup><mi>T</mi><mrow><mi>i</mi><mi>j</mi></mrow>'
     '<mrow><mi>k</mi><mi>l</mi></mrow></msubsup>')

# 11. 重音（向量/上划线）
_add("accent_vec",
     '<mover><mi>v</mi><mo>&#x2192;</mo></mover>')

# 12. 上极限 lim
_add("lim_upper",
     '<munder><mo>lim</mo><mrow><mi>x</mi><mo>&#x2192;</mo><mn>0</mn></mrow></munder>'
     '<mfrac><mrow><mi>sin</mi><mi>x</mi></mrow><mi>x</mi></mfrac>')

# 13. 黑板粗体 ℝ（PUA 特殊字体）
_add("bbold_R",
     '<mrow><mi>&#xF74D;</mi><mo>=</mo><mrow><mo>{</mo><mi>x</mi><mo>&#x2208;</mo>'
     '<mi>&#xF74B;</mi><mo>|</mo><mi>x</mi><mo>&#x2265;</mo><mn>0</mn><mo>}</mo></mrow></mrow>')

# 14. 花体/手写字母
_add("fraktur_g",
     '<mrow><mi>&#xF761;</mi><mo>(</mo><mi>x</mi><mo>)</mo></mrow>')

# 15. 混合 CJK + 数学
_add("mixed_cjk_math",
     '<mrow><mtext>当</mtext><mi>a</mi><mo>&gt;</mo><mn>0</mn>'
     '<mtext>时，方程</mtext><msup><mi>x</mi><mn>2</mn></msup>'
     '<mo>=</mo><mi>a</mi><mtext>有解</mtext></mrow>')

# 16. mphantom
_add("mphantom",
     '<mrow><msub><mi>x</mi><mphantom><mi>i</mi></mphantom></msub>'
     '<msub><mi>y</mi><mi>i</mi></msub></mrow>')

# 17. mspace
_add("mspace",
     '<mrow><mi>a</mi><mspace width="1em"/><mi>b</mi></mrow>')

# 18. menclose 长除
_add("menclose_longdiv",
     '<mrow><menclose notation="longdiv"><mi>x</mi><mo>+</mo><mn>1</mn></menclose></mrow>')

# 19. 根号带次数（立方根）
_add("root_with_degree",
     '<mroot><mi>x</mi><mn>3</mn></mroot>')

# 20. 二项式系数
_add("binom",
     '<mrow><mo>(</mo><mfrac linethickness="0"><mi>n</mi><mi>k</mi></mfrac>'
     '<mo>)</mo></mrow>')

# 21. 张量多指标
_add("tensor_indices",
     '<msubsup><mi>R</mi>'
     '<mrow><mi>&#x03BC;</mi><mi>&#x03BD;</mi></mrow>'
     '<mrow><mi>&#x03B1;</mi><mi>&#x03B2;</mi></mrow></msubsup>')

# 22. 行列式（带竖线）
_add("determinant",
     '<mrow><mo>|</mo>'
     '<mtable><mtr><mtd><mi>a</mi></mtd><mtd><mi>b</mi></mtd></mtr>'
     '<mtr><mtd><mi>c</mi></mtd><mtd><mi>d</mi></mtd></mtr></mtable>'
     '<mo>|</mo></mrow>')

# 23. 大运算符 movablelimits（max）
_add("max_movable",
     '<mrow><munder><mo>max</mo><mrow><mi>x</mi><mo>&#x2208;</mo><mi>S</mi></mrow></munder>'
     '<mi>f</mi><mo>(</mo><mi>x</mi><mo>)</mo></mrow>')

# 24. 连分数
_add("continued_frac",
     '<mfrac><mn>1</mn><mrow><mi>a</mi><mo>+</mo>'
     '<mfrac><mn>1</mn><mrow><mi>b</mi><mo>+</mo>'
     '<mfrac><mn>1</mn><mi>c</mi></mfrac></mrow></mfrac></mrow></mfrac>')

# 25. 希腊字母 + 运算符密集
_add("greek_dense",
     '<mrow><mi>&#1071;</mi><mo>(</mo><mi>&#x03B1;</mi>'
     '<mo>,</mo><mi>&#x03B2;</mi><mo>)</mo><mo>=</mo>'
     '<munderover><mo>&#x222B;</mo><mn>0</mn><mi>&#x03C0;</mi></munderover>'
     '<mi>&#x03B1;</mi><mi>d</mi><mi>&#x03B2;</mi></mrow>')


def _fused_core(mathml: str):
    root = etree.fromstring(mathml.encode("utf-8"))
    _normalize_element(root)
    repair_pua_element(root)
    res = _get_transform()(root)
    return res.getroot()


def main():
    print(f"测试 {len(CASES)} 个高难度构造 …\n")
    header = f"{'用例':<18} {'参考':<6} {'融合':<6} {'一致':<5} 备注"
    print(header)
    print("-" * 70)
    fails = []
    divs = []
    for name, mm in CASES:
        # 参考路径
        try:
            ref_el = mathml_to_omml_element(mm)
            ref_canon = etree.canonicalize(ref_el)
            ref_ok = True
        except Exception as exc:
            ref_ok = False
            ref_canon = None
            ref_err = f"{type(exc).__name__}"
        # 融合核心
        try:
            fused_el = _fused_core(mm)
            fused_canon = etree.canonicalize(fused_el) if fused_el is not None else None
            fused_ok = fused_el is not None
        except Exception as exc:
            fused_ok = False
            fused_canon = None
            fused_err = f"{type(exc).__name__}"
        match = ref_ok and fused_ok and ref_canon == fused_canon
        note = ""
        if not ref_ok:
            note = f"参考失败:{ref_err}"
            fails.append((name, "ref", ref_err))
        if not fused_ok:
            note = (note + " | " if note else "") + f"融合失败:{fused_err}"
            fails.append((name, "fused", fused_err))
        if ref_ok and fused_ok and not match:
            note = (note + " | " if note else "") + "两路径不一致"
            divs.append(name)
        # 退化检测
        if ref_ok and ref_el is not None:
            nc = len(list(ref_el.iter()))
            if nc <= 1:
                note = (note + " | " if note else "") + "参考退化"
        print(f"{name:<18} {('OK' if ref_ok else 'FAIL'):<6} "
              f"{('OK' if fused_ok else 'FAIL'):<6} {('✓' if match else '✗'):<5} {note}")

    print("-" * 70)
    print(f"总计 {len(CASES)} | 参考失败 {sum(1 for f in fails if f[1]=='ref')} "
          f"| 融合失败 {sum(1 for f in fails if f[1]=='fused')} "
          f"| 不一致 {len(divs)}")
    if fails:
        print("\n失败明细:")
        for n, which, e in fails:
            print(f"  [{which}] {n}: {e}")
    if divs:
        print("\n不一致用例:", ", ".join(divs))


if __name__ == "__main__":
    main()
