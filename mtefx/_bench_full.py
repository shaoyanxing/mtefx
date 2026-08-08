"""全量双路径耗时对比：当前(convert+mathml_to_omml_element) vs 融合(convert_fused)。

拿「融合」在全量 34233 公式上的真实提速（33 样本测到 +18%，全量可能因大公式
XSLT 占比高而被稀释，需实测）。
"""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import mtefx
from mtefx.omml import mathml_to_omml_element
from mtefx._fused import convert_fused

ZIP_DIR = Path(r"D:/moved_data2/中考")
zips = sorted(ZIP_DIR.glob("*.zip"))

blobs = []
for zp in zips:
    with zipfile.ZipFile(zp) as zf:
        for n in zf.namelist():
            if not n.lower().endswith(".docx"):
                continue
            docx_bytes = zf.read(n)
            with zipfile.ZipFile(io.BytesIO(docx_bytes)) as dz:
                for dn in dz.namelist():
                    if dn.startswith("word/embeddings/") and dn.lower().endswith(".bin"):
                        blobs.append(dz.read(dn))
print(f"收集 {len(blobs)} 个 OLE 公式")

# 当前路径
t0 = time.perf_counter()
for b in blobs:
    r = mtefx.convert(b)
    if r.ok:
        try:
            mathml_to_omml_element(r.mathml)
        except Exception:
            pass
cur = time.perf_counter() - t0

# 融合路径
t0 = time.perf_counter()
for b in blobs:
    st, el, *_ = convert_fused(b)
fus = time.perf_counter() - t0

print(f"当前路径: {cur:.1f}s  ({len(blobs) / cur:.0f} 公式/秒)")
print(f"融合路径: {fus:.1f}s  ({len(blobs) / fus:.0f} 公式/秒)")
print(f"提速: {cur / fus:.3f}x  ({(1 - fus / cur) * 100:+.1f}%)")
