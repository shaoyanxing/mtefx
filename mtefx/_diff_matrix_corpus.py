"""Find every corpus formula that contains a MATRIX record and dump its OMML.

Used to A/B the matrix.xsl fix on real data: run once per stylesheet variant
with a different output tag, then diff the two JSON files.

    python -m mtefx._diff_matrix_corpus old
    python -m mtefx._diff_matrix_corpus new
"""

from __future__ import annotations

import glob
import hashlib
import io
import json
import os
import sys
import zipfile

ROOT = r"D:\moved_data2\中考"
HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "_synth_fixtures")

# only the zips whose digest moved when matrix.xsl changed
TARGET_HINTS = ("26226493", "26226504", "26226509", "26226512")


def _has_matrix(blob: bytes) -> tuple[bool, int, int]:
    """True if the MTEF record tree contains a MATRIX(5), plus its shape."""
    from mathtypejx.mtef.stream import ByteStream
    from mathtypejx.mtef.records5 import parse_equation
    from mathtypejx.mtef.mathml import _skip_stream_header
    from mtefx import extract_mtef

    payload = extract_mtef(blob)
    if not payload or payload[0] != 5:
        return False, 0, 0
    try:
        st = ByteStream(payload)
        _skip_stream_header(st, 5)
        eq = parse_equation(st)
    except Exception:
        return False, 0, 0

    found = []

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "matrix":
                found.append((n.get("rows", 0), n.get("cols", 0)))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(eq.get("equation"))
    if not found:
        return False, 0, 0
    return True, found[0][0], found[0][1]


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "run"
    from lxml import etree
    from mtefx._fused import convert_fused

    out = {}
    shapes = {}
    for zp in sorted(glob.glob(os.path.join(ROOT, "*.zip"))):
        if not any(h in os.path.basename(zp) for h in TARGET_HINTS):
            continue
        with zipfile.ZipFile(zp) as zo:
            for name in sorted(zo.namelist()):
                if not name.lower().endswith(".docx") or name.startswith("~"):
                    continue
                with zipfile.ZipFile(io.BytesIO(zo.read(name))) as dz:
                    for b in sorted(n for n in dz.namelist()
                                    if n.startswith("word/embeddings/")
                                    and n.lower().endswith(".bin")):
                        blob = dz.read(b)
                        hit, r, c = _has_matrix(blob)
                        if not hit:
                            continue
                        key = hashlib.md5(blob).hexdigest()
                        if key in out:
                            continue
                        shapes[key] = f"{r}x{c}"
                        try:
                            st, omml, _f, _u, _ms = convert_fused(blob)
                            out[key] = (etree.tostring(omml, encoding="unicode")
                                        if omml is not None else f"<{st}>")
                        except Exception as exc:
                            out[key] = f"<EXC {type(exc).__name__}>"

    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, f"matrix_omml_{tag}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"shapes": shapes, "omml": out}, fh, ensure_ascii=False, indent=1)
    from collections import Counter
    print(f"formulas containing MATRIX: {len(out)}")
    print("shape histogram:", dict(Counter(shapes.values())))
    print("saved ->", path)


if __name__ == "__main__":
    main()
