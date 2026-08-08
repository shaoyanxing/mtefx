"""诊断融合 MVP 的 OMML 级不一致根因（命名空间冗余 vs 真实结构差异）。"""
from lxml import etree

from mtefx.engine import (
    extract_mtef,
    probe_version,
    _mtef_to_mathml_str,
    _normalize_root,
    iter_fixtures,
)
from mtefx.v3fix import mtef_v3_to_mathml
from mtefx.omml import mathml_to_omml_element
from mtefx._fused import _normalize_element, convert_fused


def _mid_mathml(blob):
    payload = extract_mtef(blob)
    ver = probe_version(payload)
    mathml = (
        mte_fix(blob, ver) if False else None
    )  # placeholder, replaced below
    return mathml


def _mathml_str(blob):
    payload = extract_mtef(blob)
    ver = probe_version(payload)
    if ver == 3:
        from mtefx.v3fix import mtef_v3_to_mathml as v3

        return v3(payload)
    return _mtef_to_mathml_str(payload)


for name, blob in list(iter_fixtures("all"))[:3]:
    mathml = _mathml_str(blob)
    if not mathml:
        print(f"== {name}: 无 MathML ==")
        continue

    # 中间 MathML：当前(_normalize_root 字符串) vs 融合(_normalize_element Element)
    cur_root = etree.fromstring(mathml.encode())
    cur_m = _normalize_root(cur_root)
    cur_m_el = etree.fromstring(cur_m.encode())

    fuse_root = etree.fromstring(mathml.encode())
    fuse_root = _normalize_element(fuse_root)

    mc = etree.canonicalize(cur_m_el)
    mf = etree.canonicalize(fuse_root)
    print(f"== {name} ==")
    print(f"  中间 MathML canonical 相等: {mc == mf}")
    if mc != mf:
        print(f"    CUR M: {etree.tostring(cur_m_el, encoding='unicode')[:200]}")
        print(f"    FUS M: {etree.tostring(fuse_root, encoding='unicode')[:200]}")

    # OMML：当前 vs 融合
    cur_o = mathml_to_omml_element(cur_m)
    st, fuse_o, *_ = convert_fused(blob)
    oc = etree.canonicalize(cur_o)
    of = etree.canonicalize(fuse_o) if fuse_o is not None else ""
    print(f"  OMML canonical 相等: {oc == of}")
    if oc != of:
        s1 = etree.tostring(cur_o, encoding="unicode")
        s2 = etree.tostring(fuse_o, encoding="unicode")
        print(f"    CUR O: {s1[:200]}")
        print(f"    FUS O: {s2[:200]}")
        # 只看命名空间声明差异
        import re

        ns1 = sorted(set(re.findall(r'xmlns[^=]*="[^"]*"', s1)))
        ns2 = sorted(set(re.findall(r'xmlns[^=]*="[^"]*"', s2)))
        print(f"    CUR NS decls: {ns1}")
        print(f"    FUS NS decls: {ns2}")
    print()
