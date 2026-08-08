"""真实 OLE 二进制回归测试。

fixture 来源：mathtypejx 仓库（针对中国高考物理试卷开发的 MathType OLE→OMML 引擎）
的公开测试样本 tests/fixtures/oleObject{1,2,3}.bin —— 是**真实 MathType OLE 复合文档**
（3584 字节 OLE 包裹 ~250 字节 MTEF v5），用于验证我们引擎能从真实二进制里解出
MTEF 并产出合法 OMML（而非仅对合成 MathML 有效）。

运行：python -m mtefx.test_web_ole_fixtures
"""
from __future__ import annotations

import glob
import os

from lxml import etree

from mtefx.engine import extract_mtef, probe_version
from mtefx._fused import convert_fused, _is_degenerate_omml

_FIX_DIR = os.path.join(os.path.dirname(__file__), "_web_fixtures")


def _run_one(path: str):
    b = open(path, "rb").read()
    payload = extract_mtef(b)
    assert payload is not None, f"{os.path.basename(path)}: extract_mtef 失败"
    ver = probe_version(payload)
    assert ver in (3, 5), f"{os.path.basename(path)}: 意外 MTEF 版本 {ver}"

    status, omml_el, _fixed, _unres, _ms = convert_fused(b)
    assert status == "ok", f"{os.path.basename(path)}: convert_fused status={status}"
    assert omml_el is not None, f"{os.path.basename(path)}: OMML 为 None"
    assert not _is_degenerate_omml(omml_el), f"{os.path.basename(path)}: OMML 退化空壳"
    # 产物必须是合法 <m:oMath>，可再次解析
    s = etree.tostring(omml_el, encoding="unicode")
    reparsed = etree.fromstring(s.encode("utf-8"))
    assert etree.QName(reparsed).localname == "oMath"
    return ver, len(s), _ms


def main():
    bins = sorted(glob.glob(os.path.join(_FIX_DIR, "oleObject*.bin")))
    assert bins, "未找到真实 OLE fixture，请先下载到 _web_fixtures/"
    print(f"真实 OLE fixture 数: {len(bins)}\n")
    all_ok = True
    for p in bins:
        try:
            ver, nbytes, ms = _run_one(p)
            print(f"  PASS  {os.path.basename(p):20s}  ver={ver}  OMML={nbytes}B  {ms:.1f}ms")
        except AssertionError as e:
            all_ok = False
            print(f"  FAIL  {os.path.basename(p):20s}  {e}")
    print("\n结果:", "全部 PASS" if all_ok else "存在 FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
