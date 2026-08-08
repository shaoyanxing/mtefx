"""Web 硬公式电池 v2 —— 用网上挖到的真实硬结构压 MML2OMML 转换层。

每条公式：
  - 参考路径: omml.mathml_to_omml_element(mathml)            (docxconv 旧链路)
  - 融合路径: fromstring → _normalize_element → repair_pua_element → _get_transform()(root)
              (convert_fused 的核心，生产实际走的 transform)
  - 对拍: etree.canonicalize 比对两路 OMML；检测 异常 / 退化空壳 / 不一致

覆盖(全部来自 WebSearch 真实结构): 各定界符矩阵、分段 cases、定积分/二重/环路积分带极限、
多行对齐、范数、取整、黑板粗体、花体、误差函数、高斯、KL、交叉熵、softmax、梯度下降、
连分式、深度嵌套、混合 CJK、重音、二项式、overset/underset、bra-ket、张量偏导、求和套积分、
4 分支分段、3x3 行列式、方程组、极限、argmin/max、旋度。
"""
from __future__ import annotations
import sys, time
from lxml import etree
from mtefx.omml import mathml_to_omml_element, _get_transform
from mtefx._fused import _normalize_element, _is_degenerate_omml, _unwrap_unsupported_menclose
from mtefx.fontmap import repair_pua_element

M = "http://www.w3.org/1998/Math/MathML"
NS = f"{{{M}}}"

def mm(inner: str) -> str:
    return f'<math xmlns="{M}">{inner}</math>'

# —— 真实硬公式结构（WebSearch 挖到的）——
FORMULAS = [
    ("matrix_paren", mm('<mrow><mo>(</mo><mtable><mtr><mtd><mi>a</mi></mtd><mtd><mi>b</mi></mtd></mtr><mtr><mtd><mi>c</mi></mtd><mtd><mi>d</mi></mtd></mtr></mtable><mo>)</mo></mrow>')),
    ("matrix_bracket", mm('<mrow><mo>[</mo><mtable><mtr><mtd><mi>a</mi></mtd><mtd><mi>b</mi></mtd></mtr><mtr><mtd><mi>c</mi></mtd><mtd><mi>d</mi></mtd></mtr></mtable><mo>]</mo></mrow>')),
    ("matrix_vbar_det", mm('<mrow><mo>|</mo><mtable><mtr><mtd><mi>a</mi></mtd><mtd><mi>b</mi></mtd></mtr><mtr><mtd><mi>c</mi></mtd><mtd><mi>d</mi></mtd></mtr></mtable><mo>|</mo></mrow>')),
    ("matrix_Vbar", mm('<mrow><mo>||</mo><mtable><mtr><mtd><mi>a</mi></mtd><mtd><mi>b</mi></mtd></mtr><mtr><mtd><mi>c</mi></mtd><mtd><mi>d</mi></mtd></mtr></mtable><mo>||</mo></mrow>')),
    ("matrix_large_dots", mm('<mrow><mo>(</mo><mtable><mtr><mtd><msub><mi>a</mi><mrow><mn>11</mn></mrow></msub></mtd><mtd><mo>&#x22EF;</mo></mtd><mtd><msub><mi>a</mi><mrow><mn>1</mn><mi>n</mi></mrow></msub></mtd></mtr><mtr><mtd><mo>&#x22EE;</mo></mtd><mtd><mo>&#x22F1;</mo></mtd><mtd><mo>&#x22EE;</mo></mtd></mtr><mtr><mtd><msub><mi>a</mi><mrow><mi>m</mi><mn>1</mn></mrow></msub></mtd><mtd><mo>&#x22EF;</mo></mtd><mtd><msub><mi>a</mi><mrow><mi>m</mi><mi>n</mi></mrow></msub></mtd></mtr></mtable><mo>)</mo></mrow>')),
    ("matrix_3x3_frac", mm('<mrow><mo>[</mo><mtable><mtr><mtd><mfrac><mi>a</mi><mi>b</mi></mfrac></mtd><mtd><mi>x</mi></mtd><mtd><mi>y</mi></mtd></mtr><mtr><mtd><mi>z</mi></mtd><mtd><mfrac><mn>1</mn><mn>2</mn></mfrac></mtd><mtd><mi>w</mi></mtd></mtr><mtr><mtd><mi>p</mi></mtd><mtd><mi>q</mi></mtd><mtd><msqrt><mi>r</mi></msqrt></mtd></mtr></mtable><mo>]</mo></mrow>')),
    ("piecewise_2", mm('<mrow><mi>f</mi><mo>(</mo><mi>x</mi><mo>)</mo><mo>=</mo><mrow><mo>{</mo><mtable><mtr><mtd><msup><mi>x</mi><mn>2</mn></msup></mtd><mtd><mtext>if&#160;</mtext><mi>x</mi><mo>&#x2265;</mo><mn>0</mn></mtd></mtr><mtr><mtd><mo>&#x2212;</mo><mi>x</mi></mtd><mtd><mtext>if&#160;</mtext><mi>x</mi><mo>&lt;</mo><mn>0</mn></mtd></mtr></mtable></mrow></mrow>')),
    ("piecewise_3", mm('<mrow><mi>f</mi><mo>(</mo><mi>x</mi><mo>)</mo><mo>=</mo><mrow><mo>{</mo><mtable><mtr><mtd><msup><mi>x</mi><mn>2</mn></msup></mtd><mtd><mtext>if&#160;</mtext><mi>x</mi><mo>&#x2265;</mo><mn>0</mn></mtd></mtr><mtr><mtd><mo>&#x2212;</mo><mi>x</mi></mtd><mtd><mtext>if&#160;</mtext><mi>x</mi><mo>&lt;</mo><mn>0</mn></mtd></mtr><mtr><mtd><mi>&#x3B1;</mi><mi>x</mi></mtd><mtd><mtext>otherwise</mtext></mtd></mtr></mtable></mrow></mrow>')),
    ("piecewise_4_text", mm('<mrow><mi>g</mi><mo>(</mo><mi>n</mi><mo>)</mo><mo>=</mo><mrow><mo>{</mo><mtable><mtr><mtd><mn>1</mn></mtd><mtd><mtext>if&#160;</mtext><mi>n</mi><mo>=</mo><mn>0</mn></mtd></mtr><mtr><mtd><mi>n</mi><mo>&#x2212;</mo><mn>1</mn></mtd><mtd><mtext>if&#160;</mtext><mi>n</mi><mo>=</mo><mn>1</mn></mtd></mtr><mtr><mtd><mi>n</mi><mo>&#x2212;</mo><mn>2</mn></mtd><mtd><mtext>if&#160;</mtext><mi>n</mi><mo>=</mo><mn>2</mn></mtd></mtr><mtr><mtd><mi>n</mi><mo>&#x2212;</mo><mn>3</mn></mtd><mtd><mtext>if&#160;</mtext><mi>n</mi><mo>&#x2265;</mo><mn>3</mn></mtd></mtr></mtable></mrow></mrow>')),
    ("definite_integral", mm('<mrow><msubsup><mo>&#x222B;</mo><mi>a</mi><mi>b</mi></msubsup><mi>f</mi><mo>(</mo><mi>x</mi><mo>)</mo><mi>d</mi><mi>x</mi></mrow>')),
    ("double_integral", mm('<mrow><msubsup><mo>&#x222C;</mo><mi>D</mi><mrow></mrow></msubsup><mi>f</mi><mo>(</mo><mi>x</mi><mo>,</mo><mi>y</mi><mo>)</mo><mi>d</mi><mi>x</mi><mi>d</mi><mi>y</mi></mrow>')),
    ("contour_integral_vec", mm('<mrow><msub><mo>&#x222E;</mo><mi>C</mi></msub><mover><mi>F</mi><mo>&#x2192;</mo></mover><mo>&#x22C5;</mo><mi>d</mi><mover><mi>l</mi><mo>&#x2192;</mo></mover></mrow>')),
    ("multi_line_align", mm('<mtable columnalign="right left"><mtr><mtd><mi>f</mi><mo>(</mo><mi>x</mi><mo>)</mo></mtd><mtd><mo>=</mo><msup><mi>x</mi><mn>2</mn></msup><mo>+</mo><mn>2</mn><mi>x</mi><mo>+</mo><mn>1</mn></mtd></mtr><mtr><mtd></mtd><mtd><mo>=</mo><msup><mrow><mo>(</mo><mi>x</mi><mo>+</mo><mn>1</mn><mo>)</mo></mrow><mn>2</mn></msup></mtd></mtr></mtable>')),
    ("norm", mm('<mrow><mo>||</mo><mi>x</mi><mo>||</mo></mrow>')),
    ("norm_frac", mm('<mrow><mo>||</mo><mfrac><mi>a</mi><mi>b</mi></mfrac><mo>||</mo></mrow>')),
    ("floor", mm('<mrow><mo>&#x230A;</mo><mi>x</mi><mo>&#x230B;</mo></mrow>')),
    ("ceiling", mm('<mrow><mo>&#x2308;</mo><mi>x</mi><mo>&#x2309;</mo></mrow>')),
    ("bbold_sets", mm('<mrow><mi>ℝ</mi><mo>,</mo><mi>ℤ</mi><mo>,</mo><mi>ℕ</mi><mo>,</mo><mi>ℂ</mi></mrow>')),
    ("mathcal", mm('<mrow><mi>ℒ</mi><mo>,</mo><mi>ℱ</mi><mo>,</mo><mi>𝒪</mi></mrow>')),
    ("error_function", mm('<mrow><mi>&#x3A6;</mi><mo>(</mo><mi>x</mi><mo>)</mo><mo>=</mo><mfrac><mn>1</mn><mn>2</mn></mfrac><mrow><mo>[</mo><mn>1</mn><mo>+</mo><mi>erf</mi><mrow><mo>(</mo><mfrac><mi>x</mi><msqrt><mn>2</mn></msqrt></mfrac><mo>)</mo></mrow><mo>]</mo></mrow></mrow>')),
    ("gaussian", mm('<mrow><mi>p</mi><mo>(</mo><mi>x</mi><mo>)</mo><mo>=</mo><mfrac><mn>1</mn><msqrt><mrow><mn>2</mn><mi>&#x3C0;</mi><msup><mi>&#x3C3;</mi><mn>2</mn></msup></mrow></msqrt></mfrac><mi>exp</mi><mrow><mo>(</mo><mo>&#x2212;</mo><mfrac><msup><mrow><mo>(</mo><mi>x</mi><mo>&#x2212;</mo><mi>&#x3BC;</mi><mo>)</mo></mrow><mn>2</mn></msup><mrow><mn>2</mn><msup><mi>&#x3C3;</mi><mn>2</mn></msup></mrow></mfrac><mo>)</mo></mrow></mrow>')),
    ("cross_entropy", mm('<mrow><mi>ℒ</mi><mo>=</mo><mo>&#x2212;</mo><munderover><mo>&#x2211;</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow><mi>N</mi></munderover><msub><mi>y</mi><mi>i</mi></msub><mi>log</mi><mrow><mo>(</mo><msub><mover><mi>y</mi><mo>^</mo></mover><mi>i</mi></msub><mo>)</mo></mrow></mrow>')),
    ("kl_divergence", mm('<mrow><msub><mi>D</mi><mrow><mi>KL</mi></mrow></msub><mrow><mo>(</mo><mi>P</mi><mo>||</mo><mi>Q</mi><mo>)</mo></mrow><mo>=</mo><munder><mo>&#x2211;</mo><mi>x</mi></munder><mi>P</mi><mo>(</mo><mi>x</mi><mo>)</mo><mi>log</mi><mfrac><mrow><mi>P</mi><mo>(</mo><mi>x</mi><mo>)</mo></mrow><mrow><mi>Q</mi><mo>(</mo><mi>x</mi><mo>)</mo></mrow></mfrac></mrow>')),
    ("softmax", mm('<mrow><mtext>softmax</mtext><mrow><mo>(</mo><msub><mi>z</mi><mi>i</mi></msub><mo>)</mo></mrow><mo>=</mo><mfrac><msup><mi>e</mi><msub><mi>z</mi><mi>i</mi></msub></msup><mrow><munderover><mo>&#x2211;</mo><mrow><mi>j</mi><mo>=</mo><mn>1</mn></mrow><mi>K</mi></munderover><msup><mi>e</mi><msub><mi>z</mi><mi>j</mi></msub></msup></mrow></mfrac></mrow>')),
    ("gradient_descent", mm('<mrow><msub><mi>&#x3B8;</mi><mrow><mi>t</mi><mo>+</mo><mn>1</mn></mrow></msub><mo>=</mo><msub><mi>&#x3B8;</mi><mi>t</mi></msub><mo>&#x2212;</mo><mi>&#x3B7;</mi><msub><mo>&#x2207;</mo><mi>&#x3B8;</mi></msub><mi>ℒ</mi><mrow><mo>(</mo><msub><mi>&#x3B8;</mi><mi>t</mi></msub><mo>)</mo></mrow></mrow>')),
    ("continued_fraction", mm('<mrow><mfrac><mn>1</mn><mrow><mn>1</mn><mo>+</mo><mfrac><mn>1</mn><mrow><mn>1</mn><mo>+</mo><mfrac><mn>1</mn><mi>x</mi></mfrac></mrow></mfrac></mrow></mfrac></mrow>')),
    ("deep_nest_frac", mm('<mrow><mfrac><mrow><mfrac><mi>a</mi><mi>b</mi></mfrac></mrow><mrow><mfrac><mi>c</mi><mi>d</mi></mfrac></mrow></mfrac></mrow>')),
    ("mixed_cjk_math", mm('<mrow><mtext>速度</mtext><mo>=</mo><mfrac><mtext>位移</mtext><mtext>时间</mtext></mfrac><mo>=</mo><mfrac><mi>&#x394;</mi><mi>x</mi></mfrac><mi>&#x394;</mi><mi>t</mi></mrow>')),
    ("accent_hat_bar", mm('<mrow><mover><mi>x</mi><mo>^</mo></mover><mo>,</mo><mover><mi>x</mi><mo>&#x00AF;</mo></mover><mo>,</mo><mover><mi>x</mi><mo>~</mo></mover><mo>,</mo><mover><mi>x</mi><mo>→</mo></mover></mrow>')),
    ("accent_dot_ddot", mm('<mrow><mover><mi>x</mi><mo>.</mo></mover><mo>,</mo><mover><mi>x</mi><mo>..</mo></mover><mo>,</mo><mover><mi>A</mi><mo>^</mo></mover><mo>,</mo><mover><mi>A</mi><mo>~</mo></mover></mrow>')),
    ("binom", mm('<mrow><mo>(</mo><mfrac linethickness="0"><mi>n</mi><mi>k</mi></mfrac><mo>)</mo></mrow>')),
    ("overset_underset", mm('<mrow><munderover><mo>lim</mo><mrow><mi>x</mi><mo>&#x2192;</mo><mi>&#x221E;</mi></mrow><mrow></mrow></munderover><mfrac><mn>1</mn><mi>n</mi></mfrac><mo>=</mo><mn>0</mn></mrow>')),
    ("argmin", mm('<mrow><munder><mo>arg&#x2061;min</mo><mrow><mi>&#x3B8;</mi><mo>&#x2208;</mo><mi>&#x398;</mi></mrow></munder><mi>ℒ</mi><mrow><mo>(</mo><mi>&#x3B8;</mi><mo>)</mo></mrow></mrow>')),
    ("bra_ket", mm('<mrow><mo>&#x27E8;</mo><mi>&#x3C8;</mi><mo>|</mo><mi>H</mi><mo>|</mo><mi>&#x3C6;</mi><mo>&#x27E9;</mo></mrow>')),
    ("tensor_partial", mm('<mrow><mfrac><mrow><mo>&#x2202;</mo><mi>u</mi></mrow><mrow><mo>&#x2202;</mo><mi>t</mi></mrow></mfrac><mo>=</mo><msup><mi>&#x2207;</mi><mn>2</mn></msup><mi>u</mi></mrow>')),
    ("sum_inside_integral", mm('<mrow><msubsup><mo>&#x222B;</mo><mi>a</mi><mi>b</mi></msubsup><mrow><mo>(</mo><munderover><mo>&#x2211;</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow><mi>n</mi></munderover><msub><mi>a</mi><mi>i</mi></msub><mo>)</mo></mrow><mi>d</mi><mi>x</mi></mrow>')),
    ("system_eq", mm('<mrow><mo>{</mo><mtable columnalign="left"><mtr><mtd><mi>x</mi><mo>+</mo><mi>y</mi><mo>=</mo><mn>3</mn></mtd></mtr><mtr><mtd><mn>2</mn><mi>x</mi><mo>&#x2212;</mo><mi>y</mi><mo>=</mo><mn>1</mn></mtd></mtr></mtable></mrow>')),
    ("limit_inf", mm('<mrow><munder><mo>lim</mo><mrow><mi>x</mi><mo>&#x2192;</mo><mi>&#x221E;</mi></mrow></munder><mi>f</mi><mo>(</mo><mi>x</mi><mo>)</mo></mrow>')),
    ("curl_stokes", mm('<mrow><msub><mo>&#x222E;</mo><mrow><mi>&#x2202;</mi><mi>S</mi></mrow></msub><mover><mi>F</mi><mo>&#x2192;</mo></mover><mo>&#x22C5;</mo><mi>d</mi><mover><mi>r</mi><mo>&#x2192;</mo></mover><mo>=</mo><mo>&#x222B;</mo><mo>&#x222B;</mo><mrow><mo>(</mo><mo>&#x2207;</mo><mo>×</mo><mover><mi>F</mi><mo>&#x2192;</mo></mover><mo>)</mo></mrow><mo>&#x22C5;</mo><mi>d</mi><mover><mi>S</mi><mo>&#x2192;</mo></mover></mrow>')),
    ("greek_heavy", mm('<mrow><msubsup><mi>&#x3A9;</mi><mrow><mi>i</mi><mi>j</mi></mrow><mrow><mo>(</mo><mi>k</mi><mo>)</mo></mrow></msubsup><mo>=</mo><mi>&#x394;</mi><mi>&#x3B1;</mi><msub><mi>&#x3BB;</mi><mi>&#x3BE;</mi></msub></mrow>')),
    ("determinant_3x3", mm('<mrow><mo>|</mo><mtable><mtr><mtd><mi>a</mi></mtd><mtd><mi>b</mi></mtd><mtd><mi>c</mi></mtd></mtr><mtr><mtd><mi>d</mi></mtd><mtd><mi>e</mi></mtd><mtd><mi>f</mi></mtd></mtr><mtr><mtd><mi>g</mi></mtd><mtd><mi>h</mi></mtd><mtd><mi>i</mi></mtd></mtr></mtable><mo>|</mo></mrow>')),
    ("product_integral", mm('<mrow><munderover><mo>&#x220F;</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow><mi>n</mi></munderover><msub><mi>x</mi><mi>i</mi></msub></mrow>')),
    # —— menclose 回归（先前发现 longdiv/actuarial 静默丢公式，已修复）——
    ("menclose_longdiv", mm('<mrow><menclose notation="longdiv"><mi>x</mi><mo>+</mo><mn>1</mn></menclose></mrow>')),
    ("menclose_actuarial", mm('<mrow><menclose notation="actuarial"><mi>a</mi></menclose></mrow>')),
    ("menclose_circle", mm('<mrow><menclose notation="circle"><mi>x</mi></menclose></mrow>')),
    ("menclose_radical", mm('<mrow><menclose notation="radical"><mi>x</mi></menclose></mrow>')),
    # —— 物理/化学/竞赛 真实刁钻结构（WebSearch 挖到）——
    ("maxwell_4x4_tensor", mm('<mrow><msup><mi>F</mi><mrow><mi>&#x3BC;</mi><mi>&#x3BD;</mi></mrow></msup><mo>=</mo><mrow><mo>(</mo><mtable><mtr><mtd><mn>0</mn></mtd><mtd><mo>&#x2212;</mo><mfrac><msub><mi>E</mi><mi>x</mi></msub><mi>c</mi></mfrac></mtd><mtd><mo>&#x2212;</mo><mfrac><msub><mi>E</mi><mi>y</mi></msub><mi>c</mi></mfrac></mtd><mtd><mo>&#x2212;</mo><mfrac><msub><mi>E</mi><mi>z</mi></msub><mi>c</mi></mfrac></mtd></mtr><mtr><mtd><mfrac><msub><mi>E</mi><mi>x</mi></msub><mi>c</mi></mfrac></mtd><mtd><mn>0</mn></mtd><mtd><mo>&#x2212;</mo><msub><mi>B</mi><mi>z</mi></msub></mtd><mtd><msub><mi>B</mi><mi>y</mi></msub></mtd></mtr><mtr><mtd><mfrac><msub><mi>E</mi><mi>y</mi></msub><mi>c</mi></mfrac></mtd><mtd><msub><mi>B</mi><mi>z</mi></msub></mtd><mtd><mn>0</mn></mtd><mtd><mo>&#x2212;</mo><msub><mi>B</mi><mi>x</mi></msub></mtd></mtr><mtr><mtd><mfrac><msub><mi>E</mi><mi>z</mi></msub><mi>c</mi></mfrac></mtd><mtd><mo>&#x2212;</mo><msub><mi>B</mi><mi>y</mi></msub></mtd><mtd><msub><mi>B</mi><mi>x</mi></msub></mtd><mtd><mn>0</mn></mtd></mtr></mtable><mo>)</mo></mrow></mrow>')),
    ("maxwell_tensor_eq", mm('<mrow><msub><mo>&#x2202;</mo><mi>&#x3B1;</mi></msub><msup><mi>F</mi><mrow><mi>&#x3B1;</mi><mi>&#x3B2;</mi></mrow></msup><mo>=</mo><msub><mi>&#x3BC;</mi><mn>0</mn></msub><msup><mi>J</mi><mi>&#x3B2;</mi></msup></mrow>')),
    ("gibbs_free_energy", mm('<mrow><mi>&#x394;</mi><mi>G</mi><mo>=</mo><mi>&#x394;</mi><mi>H</mi><mo>&#x2212;</mo><mi>T</mi><mi>&#x394;</mi><mi>S</mi><mo>=</mo><mo>&#x2212;</mo><mi>R</mi><mi>T</mi><mi>ln</mi><mi>K</mi></mrow>')),
    ("arrhenius", mm('<mrow><mi>k</mi><mo>=</mo><mi>A</mi><msup><mi>e</mi><mrow><mo>&#x2212;</mo><msub><mi>E</mi><mi>a</mi></msub><mo>/</mo><mi>R</mi><mi>T</mi></mrow></msup></mrow>')),
    ("rate_law", mm('<mrow><mtext>rate</mtext><mo>=</mo><mi>k</mi><msup><mrow><mo>[</mo><mi>A</mi><mo>]</mo></mrow><mi>a</mi></msup><msup><mrow><mo>[</mo><mi>B</mi><mo>]</mo></mrow><mi>b</mi></msup></mrow>')),
    ("cauchy_schwarz", mm('<mrow><msup><mrow><mo>(</mo><munderover><mo>&#x2211;</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow><mi>n</mi></munderover><msub><mi>a</mi><mi>i</mi></msub><msub><mi>b</mi><mi>i</mi></msub><mo>)</mo></mrow><mn>2</mn></msup><mo>&#x2264;</mo><mrow><mo>(</mo><munderover><mo>&#x2211;</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow><mi>n</mi></munderover><msubsup><mi>a</mi><mi>i</mi><mn>2</mn></msubsup><mo>)</mo></mrow><mrow><mo>(</mo><munderover><mo>&#x2211;</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow><mi>n</mi></munderover><msubsup><mi>b</mi><mi>i</mi><mn>2</mn></msubsup><mo>)</mo></mrow></mrow>')),
    ("nesbitt", mm('<mrow><mfrac><mi>a</mi><mrow><mi>b</mi><mo>+</mo><mi>c</mi></mrow></mfrac><mo>+</mo><mfrac><mi>b</mi><mrow><mi>c</mi><mo>+</mo><mi>a</mi></mrow></mfrac><mo>+</mo><mfrac><mi>c</mi><mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow></mfrac><mo>&#x2265;</mo><mfrac><mn>3</mn><mn>2</mn></mfrac></mrow>')),
    ("titu_lemma", mm('<mrow><munderover><mo>&#x2211;</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow><mi>n</mi></munderover><mfrac><msubsup><mi>a</mi><mi>i</mi><mn>2</mn></msubsup><msub><mi>b</mi><mi>i</mi></msub></mfrac><mo>&#x2265;</mo><mfrac><msup><mrow><mo>(</mo><munderover><mo>&#x2211;</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow><mi>n</mi></munderover><msub><mi>a</mi><mi>i</mi></msub><mo>)</mo></mrow><mn>2</mn></msup><mrow><munderover><mo>&#x2211;</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow><mi>n</mi></munderover><msub><mi>b</mi><mi>i</mi></msub></mrow></mfrac></mrow>')),
    ("complex_cauchy", mm('<mrow><msup><mrow><mo>|</mo><munderover><mo>&#x2211;</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow><mi>n</mi></munderover><msub><mi>z</mi><mi>i</mi></msub><msub><mi>w</mi><mi>i</mi></msub><mo>|</mo></mrow><mn>2</mn></msup><mo>&#x2264;</mo><mrow><mo>(</mo><munderover><mo>&#x2211;</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow><mi>n</mi></munderover><msup><mrow><mo>|</mo><msub><mi>z</mi><mi>i</mi></msub><mo>|</mo></mrow><mn>2</mn></msup><mo>)</mo></mrow><mrow><mo>(</mo><munderover><mo>&#x2211;</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow><mi>n</mi></munderover><msup><mrow><mo>|</mo><msub><mi>w</mi><mi>i</mi></msub><mo>|</mo></mrow><mn>2</mn></msup><mo>)</mo></mrow></mrow>')),
    ("dalembertian", mm('<mrow><msup><mrow><mo>&#x2202;</mo></mrow><mn>2</mn></msup><msup><mi>A</mi><mi>&#x3C3;</mi></msup><mo>=</mo><msub><mi>&#x3BC;</mi><mn>0</mn></msub><msup><mi>J</mi><mi>&#x3C3;</mi></msup></mrow>')),
    # —— 高考物理最典型的刁钻结构（mathtypejx README 点名）——
    # 同位素上下标（prescript）：MMLEquation 用 mmultiscripts + mprescripts 表示前标
    ("isotope_prescript", mm('<mrow><mmultiscripts><mi>U</mi><mprescripts/><mn>92</mn><mn>238</mn></mmultiscripts><mo>+</mo><mn>2</mn><msub><mi>e</mi><mo>&#x2212;</mo></msub><mo>&#x2192;</mo><mmultiscripts><mi>Th</mi><mprescripts/><mn>90</mn><mn>234</mn></mmultiscripts></mrow>')),
    # 带单位文本（textmode physical units 如 kg·m/s）：MML2OMML 需保留 mtext
    ("unit_text", mm('<mrow><mi>v</mi><mo>=</mo><mfrac><mrow><mn>5</mn><mtext>kg</mtext><mo>&#x22C5;</mo><mi>m</mi></mrow><mrow><mi>s</mi></mrow></mfrac></mrow>')),
]

def run_one(name, mathml):
    # 参考路径
    try:
        ref_el = mathml_to_omml_element(mathml)
        ref_canon = etree.canonicalize(ref_el)
        ref_degen = _is_degenerate_omml(ref_el)
    except Exception as e:
        return ("REF_FAIL", None, None, f"{type(e).__name__}: {str(e)[:80]}")
    # 融合路径 (复刻 convert_fused 核心, 吃 MathML 而非 OLE)
    try:
        root = etree.fromstring(mathml.encode("utf-8"))
        _normalize_element(root)
        repair_pua_element(root)
        _unwrap_unsupported_menclose(root)   # 修复关键步：展开 longdiv/actuarial
        fused_el = _get_transform()(root).getroot()
        fused_canon = etree.canonicalize(fused_el)
        fused_degen = _is_degenerate_omml(fused_el)
    except Exception as e:
        return ("FUSED_FAIL", ref_degen, None, f"{type(e).__name__}: {str(e)[:80]}")
    # menclose[longdiv|actuarial]: 融合路径故意展开保内容, 参考路径会退化 → 不比对等
    is_menclose_unsupported = ('notation="longdiv"' in mathml or 'notation="actuarial"' in mathml)
    if is_menclose_unsupported:
        if fused_degen:
            return ("DEGENERATE", ref_degen, fused_degen,
                    f"融合未展开 menclose, nodes={len(list(fused_el.iter()))}")
        return ("OK", ref_degen, fused_degen,
                f"融合展开成功 nodes={len(list(fused_el.iter()))} (参考退化={ref_degen})")
    # 对拍
    if ref_canon != fused_canon:
        return ("MISMATCH", ref_degen, fused_degen, f"ref_nodes={len(list(ref_el.iter()))} fused_nodes={len(list(fused_el.iter()))}")
    if ref_degen or fused_degen:
        return ("DEGENERATE", ref_degen, fused_degen, f"nodes={len(list(ref_el.iter()))}")
    return ("OK", ref_degen, fused_degen, f"nodes={len(list(ref_el.iter()))}")

def main():
    t0 = time.perf_counter()
    results = []
    for name, mathml in FORMULAS:
        status, rd, fd, note = run_one(name, mathml)
        results.append((name, status, note))
    dt = time.perf_counter() - t0

    by_status = {}
    for name, status, note in results:
        by_status.setdefault(status, []).append((name, note))
    print(f"=== Web 硬公式电池 v2: {len(FORMULAS)} 条, 耗时 {dt*1000:.1f}ms ===")
    for st in ("OK", "DEGENERATE", "MISMATCH", "REF_FAIL", "FUSED_FAIL"):
        if st in by_status:
            print(f"\n[{st}] {len(by_status[st])} 条")
            for name, note in by_status[st]:
                print(f"   - {name}: {note}")
    # 汇总
    ok = len(by_status.get("OK", []))
    print(f"\n通过(OK)={ok}/{len(FORMULAS)}  退化={len(by_status.get('DEGENERATE',[]))}  "
          f"不一致={len(by_status.get('MISMATCH',[]))}  异常={len(by_status.get('REF_FAIL',[]))+len(by_status.get('FUSED_FAIL',[]))}")
    return 0 if (ok == len(FORMULAS)) else 1

if __name__ == "__main__":
    sys.exit(main())
