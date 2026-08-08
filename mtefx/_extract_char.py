"""Dump the parsed MTEF record tree of the real web fixtures.

Purpose: harvest *valid* (typeface, mt_code) pairs and template layouts from
genuine MathType OLE binaries, so `_synthesize_ole.py` can emit records the
real parser accepts instead of guessed glyph codes.
"""
import io
import json
import os
import sys

import olefile

from mathtypejx.mtef.stream import ByteStream
from mathtypejx.mtef.records5 import parse_equation
from mathtypejx.mtef.mathml import _skip_stream_header

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "_web_fixtures")


def load_mtef(path):
    with open(path, "rb") as fh:
        data = fh.read()
    ole = olefile.OleFileIO(io.BytesIO(data))
    raw = ole.openstream("Equation Native").read()
    ole.close()
    return raw[28:]


def walk(node, out, depth=0):
    if isinstance(node, dict):
        t = node.get("type")
        if t == "char":
            out.append(
                {
                    "typeface": node.get("typeface"),
                    "mt_code": node.get("mt_code_value"),
                    "options": node.get("options"),
                    "variation": node.get("variation"),
                }
            )
        if t == "tmpl":
            out.append(
                {
                    "tmpl": node.get("selector"),
                    "variation_code": node.get("variation_code"),
                    "tso": node.get("template_specific_options"),
                    "options": node.get("options"),
                }
            )
        for v in node.values():
            walk(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            walk(v, out, depth + 1)


def main():
    for name in sorted(os.listdir(FIX)):
        if not name.endswith(".bin"):
            continue
        path = os.path.join(FIX, name)
        mtef = load_mtef(path)
        st = ByteStream(mtef)
        _skip_stream_header(st, 5)
        tree = parse_equation(st)
        found = []
        walk(tree, found)
        print("=" * 60)
        print(name, "mtef len", len(mtef), "header consumed", st.position)
        for item in found:
            print("   ", json.dumps(item, ensure_ascii=False))
        print("   full tree:", json.dumps(tree.get("equation"), ensure_ascii=False)[:1200])


if __name__ == "__main__":
    main()
