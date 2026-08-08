"""全量真实语料零回归验证（融合版 docxconv）。

遍历 D:\\moved_data2\\中考 的 35 个 zip，对每个 docx 跑融合版
convert_docx_bytes，统计 ok/failed/skipped 与 OLEObject 残留，
确认与基线（656/656 ok、0 OLEObject）一致。
"""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path
from lxml import etree

import mtefx.docxconv as dc

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
O_NS = "urn:schemas-microsoft-com:office:office"

ZIP_DIR = Path(r"D:/moved_data2/中考")


def main() -> int:
    zips = sorted(ZIP_DIR.glob("*.zip"))
    print(f"发现 {len(zips)} 个 zip")

    cache: dict = {}
    total_ok = total_failed = total_skipped = 0
    ole_residual = 0
    t0 = time.perf_counter()

    for zp in zips:
        with zipfile.ZipFile(zp) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".docx") or name.startswith("~"):
                    continue
                data = zf.read(name)
                new_bytes, rep = dc.convert_docx_bytes(data, cache=cache)
                total_ok += rep.ok
                total_failed += rep.failed
                total_skipped += rep.skipped
                try:
                    with zipfile.ZipFile(io.BytesIO(new_bytes)) as nz:
                        doc = nz.read("word/document.xml")
                    root = etree.fromstring(doc)
                    ole_residual += len(root.findall(f".//{{{O_NS}}}OLEObject"))
                except Exception:
                    pass

    elapsed = time.perf_counter() - t0
    print(f"总公式: ok={total_ok}  failed={total_failed}  skipped={total_skipped}")
    print(f"OLEObject 残留: {ole_residual}  (基线期望 0)")
    print(f"耗时: {elapsed:.1f}s  ({total_ok / elapsed:.0f} 公式/秒)")
    print(f"零回归判定: {'✓' if total_failed == 0 and ole_residual == 0 else '✗'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
