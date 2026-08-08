"""Full-corpus OMML digest regression.

Walks every OLE equation blob in the 中考 corpus (35 zip / 34233 formulas),
converts each through the production fused path, and folds the canonical OMML
of every formula into one global digest.

Run it before and after a stylesheet change: an identical global digest is
proof of byte-level zero regression across the whole corpus, which is much
stronger than "ok/failed counts match".

    python -m mtefx._regress_corpus            # compute + print digest
    python -m mtefx._regress_corpus save NAME  # also store under _synth_fixtures
"""

from __future__ import annotations

import glob
import hashlib
import io
import json
import os
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor

ROOT = r"D:\moved_data2\中考"
HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "_synth_fixtures")


def _zip_worker(zip_path: str) -> tuple[int, int, int, str]:
    """Convert every embedded OLE equation in one zip; return counts + digest."""
    from lxml import etree
    from mtefx._fused import convert_fused, _is_degenerate_omml

    total = ok = degenerate = 0
    h = hashlib.md5()
    with zipfile.ZipFile(zip_path) as zo:
        for name in sorted(zo.namelist()):
            if not name.lower().endswith(".docx") or name.startswith("~"):
                continue
            try:
                docx = zo.read(name)
            except Exception:
                continue
            try:
                with zipfile.ZipFile(io.BytesIO(docx)) as dz:
                    bins = sorted(n for n in dz.namelist()
                                  if n.startswith("word/embeddings/")
                                  and n.lower().endswith(".bin"))
                    for b in bins:
                        blob = dz.read(b)
                        total += 1
                        try:
                            status, omml, _f, _u, _ms = convert_fused(blob)
                        except Exception as exc:
                            h.update(f"EXC:{type(exc).__name__}".encode())
                            continue
                        if status == "ok" and omml is not None:
                            ok += 1
                            if _is_degenerate_omml(omml):
                                degenerate += 1
                            h.update(etree.tostring(omml))
                        else:
                            h.update(f"ST:{status}".encode())
            except Exception:
                continue
    return total, ok, degenerate, h.hexdigest()


def main() -> dict:
    zips = sorted(glob.glob(os.path.join(ROOT, "*.zip")))
    if not zips:
        print(f"no zips under {ROOT}")
        return {}
    t0 = time.perf_counter()
    total = ok = degen = 0
    per_zip = []
    workers = min(os.cpu_count() or 4, 12)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for zp, (t, o, d, dig) in zip(zips, ex.map(_zip_worker, zips)):
            total += t
            ok += o
            degen += d
            per_zip.append((os.path.basename(zp), t, o, d, dig))
    elapsed = time.perf_counter() - t0

    glob_h = hashlib.md5()
    for name, t, o, d, dig in per_zip:
        glob_h.update(f"{name}:{t}:{o}:{d}:{dig}".encode())
    digest = glob_h.hexdigest()

    print(f"zips={len(zips)}  formulas={total}  ok={ok}  failed={total - ok}  "
          f"degenerate={degen}")
    print(f"wall={elapsed:.2f}s  workers={workers}")
    print(f"GLOBAL DIGEST = {digest}")

    result = {"zips": len(zips), "total": total, "ok": ok,
              "failed": total - ok, "degenerate": degen,
              "wall_s": round(elapsed, 2), "digest": digest,
              "per_zip": [{"zip": n, "total": t, "ok": o, "degen": d,
                           "digest": g} for n, t, o, d, g in per_zip]}

    if len(sys.argv) > 2 and sys.argv[1] == "save":
        os.makedirs(OUTDIR, exist_ok=True)
        path = os.path.join(OUTDIR, f"corpus_digest_{sys.argv[2]}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        print("saved ->", path)
    return result


if __name__ == "__main__":
    main()
