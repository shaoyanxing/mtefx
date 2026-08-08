"""Element 整链路原型验证（修正版）。

修正点：
- 命名空间用 _strip_ns_inplace + 一次性 _serialize_mathml（含去 xmlns=""），
  与当前字符串路径的 mathml_to_omml 输入完全对齐，保证 OMML 级一致。
- PUA 修复改在 Element 文本节点上做（真实码位），更稳。
- 省掉当前路径「normalize→tostring→repair→fromstring」的一次多余往返。
"""
from __future__ import annotations

import time
from lxml import etree
from mtefx.engine import (
    iter_fixtures, extract_mtef, probe_version,
    _strip_ns_inplace, _serialize_mathml, _install_fast_xslt,
)
from mtefx._xslt import transform as _xslt_tf
from mtefx.fontmap import repair_pua, repair_pua_element
from mtefx.omml import mathml_to_omml_element, _get_transform, M_NS

_install_fast_xslt()

samples = list(iter_fixtures("all"))
prepared = [(n, b, extract_mtef(b), probe_version(extract_mtef(b) or b""))
            for n, b in samples]
prepared = [(n, b, p, v) for n, b, p, v in prepared if p is not None]
print(f"样本数: {len(prepared)} (v3 {sum(1 for *_,v in prepared if v==3)} / "
      f"v5 {sum(1 for *_,v in prepared if v==5)})")


def build_tree(payload, ver):
    from mathtypejx.mtef.builder import build_mtef_xml
    from mathtypejx.mtef.chars import replace as cr
    from mathtypejx.mtef.mathml import _skip_stream_header
    from mathtypejx.mtef.mover import move as mv
    from mathtypejx.mtef.records3 import parse_equation_v3
    from mathtypejx.mtef.records5 import parse_equation
    from mathtypejx.mtef.stream import ByteStream
    from mtefx.v3fix import wrap_v3_slot
    s = ByteStream(payload)
    _skip_stream_header(s, ver)
    eq = parse_equation(s) if ver == 5 else parse_equation_v3(s)
    root = build_mtef_xml(eq)
    if ver == 3:
        wrap_v3_slot(root)
    mv(root)
    cr(root)
    return root


def current_pipeline(blob):
    payload = extract_mtef(blob)
    ver = probe_version(payload)
    tree = build_tree(payload, ver)
    mml = _xslt_tf(tree)
    root = etree.fromstring(mml.encode("utf-8"))
    mml = _normalize_like(root)
    mml, _, _ = repair_pua(mml)
    el = mathml_to_omml_element(mml)
    return etree.tostring(el, encoding="unicode")


def _normalize_like(root):
    from mtefx.engine import _normalize_root
    return _normalize_root(root)


def element_pipeline(blob):
    """全程 Element 树：省一次 normalize→tostring→repair→fromstring 往返。"""
    payload = extract_mtef(blob)
    ver = probe_version(payload)
    tree = build_tree(payload, ver)
    mml_str = _xslt_tf(tree)
    root = etree.fromstring(mml_str.encode("utf-8"))
    if len(root) == 0 and not (root.text or "").strip():
        return None
    _strip_ns_inplace(root)
    repair_pua_element(root)               # Element 级，真实码位，更稳
    out = _serialize_mathml(root)          # 一次性清洗（去 xmlns="" + 根兜底）
    el = mathml_to_omml_element(out)
    return etree.tostring(el, encoding="unicode")


def element_v2(blob):
    """更激进：用 cleanup_namespaces 替代正则，完全去掉 _serialize_mathml。"""
    payload = extract_mtef(blob)
    ver = probe_version(payload)
    tree = build_tree(payload, ver)
    mml_str = _xslt_tf(tree)
    root = etree.fromstring(mml_str.encode("utf-8"))
    if len(root) == 0 and not (root.text or "").strip():
        return None
    etree.cleanup_namespaces(root)
    repair_pua_element(root)
    if not root.tag.startswith("{"):
        if "http://www.w3.org/1998/Math/MathML" not in root.nsmap.values():
            root.set("xmlns", "http://www.w3.org/1998/Math/MathML")
    result = _get_transform()(root)
    omml_root = result.getroot()
    if omml_root.tag != f"{{{M_NS}}}oMath":
        f = omml_root.find(f".//{{{M_NS}}}oMath")
        if f is not None:
            omml_root = f
    return etree.tostring(omml_root, encoding="unicode")


N = 200

import statistics
def time_avg(fn, iters):
    ts = []
    for _ in range(iters):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1e3)
    return sum(ts) / len(ts)

cur_ms = time_avg(lambda: [current_pipeline(b) for _, b, _, _ in prepared], N)
el1_ms = time_avg(lambda: [element_pipeline(b) for _, b, _, _ in prepared], N)
el2_ms = time_avg(lambda: [element_v2(b) for _, b, _, _ in prepared], N)

print(f"\n── 端到端每公式平均耗时（{N}×{len(prepared)} 次）──")
print(f"  当前字符串路径        : {cur_ms:8.3f} ms")
print(f"  Element 原型 v1       : {el1_ms:8.3f} ms  省 {(cur_ms-el1_ms)/cur_ms*100:5.1f}%")
print(f"  Element 原型 v2(cleanup): {el2_ms:8.3f} ms  省 {(cur_ms-el2_ms)/cur_ms*100:5.1f}%")

# 正确性：OMML 级逐字节比对
mism1 = mism2 = err = 0
for name, blob, p, v in prepared:
    try:
        a = current_pipeline(blob)
        b1 = element_pipeline(blob)
        b2 = element_v2(blob)
    except Exception as e:
        err += 1
        print(f"  异常: {name}: {e}")
        continue
    if a != b1:
        mism1 += 1
        if mism1 <= 5:
            print(f"  v1 差异: {name}")
    if a != b2:
        mism2 += 1
        if mism2 <= 5:
            print(f"  v2 差异: {name}")
print(f"\n── 正确性（OMML 级逐字节）──")
print(f"  v1 与当前不一致: {mism1}/{len(prepared)}    异常: {err}")
print(f"  v2 与当前不一致: {mism2}/{len(prepared)}")
