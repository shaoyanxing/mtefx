"""Synthesize *real* MathType OLE binaries containing hard structures.

Why this exists
---------------
The 中考 corpus (34233 formulas) is structurally trivial: zero matrices, zero
integrals, zero piles.  The public mathtypejx fixtures are three fractions.
So the engine's **MTEF binary parser** has literally never seen a MATRIX(5) or
PILE(4) record, nor a tmINTEG/tmSUM/tmLDIV template, coming off real bytes.

Every "hard formula" battery so far (``_probe_hardmath``, ``_probe_hardmath2``)
entered the pipeline at the *MathML* layer, skipping ``extract_mtef`` and the
record decoder entirely.  That is the last blind spot.

This module closes it by encoding MTEF v5 records byte-for-byte (mirroring
``mathtypejx.mtef.records5``), wrapping them in a genuine OLE compound file
with a correct 28-byte ``EQNOLEFILEHDR``, and feeding them to the full
``extract_mtef`` -> ``convert_fused`` pipeline.

Stream layout produced here (verified against oleObject1.bin)::

    EQNOLEFILEHDR  28B : cbHdr=28, version, cf, cbObject, reserved[4]
    MTEF header    12B : ver=5, platform, product, prod_ver, prod_sub,
                         appkey "DSMT6\\0", equation_options(1 byte)
    records            : LINE/CHAR/TMPL/PILE/MATRIX ... terminated by END
"""

from __future__ import annotations

import io
import json
import os
import struct
import sys
from pathlib import Path

import olefile

# ── make the package importable no matter how this file is launched ──
# As a script (`python mtefx/_synthesize_ole.py`) the interpreter only puts
# the `mtefx/` directory on sys.path, so `import mtefx` fails.  As a module
# (`python -m mtefx._synthesize_ole`) the workspace root is already there.
# Inserting the parent dir covers both, so this doubles as a CI gate.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── record tags (records5) ────────────────────────────────────────────
END, LINE, CHAR, TMPL, PILE, MATRIX = 0, 1, 2, 3, 4, 5

# ── typefaces (value stored on the wire is value-128 as int8) ─────────
TF_TEXT = 1
TF_FUNCTION = 2
TF_VARIABLE = 3
TF_LCGREEK = 4
TF_UCGREEK = 5
TF_SYMBOL = 6
TF_VECTOR = 7
TF_NUMBER = 8

# ── template selectors ────────────────────────────────────────────────
TM_PAREN, TM_BRACE, TM_BRACK = 1, 2, 3
TM_ROOT, TM_FRACT = 10, 11
TM_INTEG, TM_SUM, TM_PROD = 15, 16, 17
TM_LDIV = 26
TM_SUB, TM_SUP, TM_SUBSUP = 27, 28, 29

# variation flags
TV_FENCE_L, TV_FENCE_R = 1, 2
TV_ROOT_NTH = 1
TV_INT_1 = 1
TV_BO_LOWER, TV_BO_UPPER = 16, 32


# ── encoders ──────────────────────────────────────────────────────────
def enc_variation(var_code: int) -> bytes:
    """1-2 byte variation field.  Parser: var1&0x80 -> (var1&0x7F)|(var2<<8)."""
    if var_code < 0x80:
        return bytes([var_code])
    return bytes([0x80 | (var_code & 0x7F), (var_code >> 8) & 0xFF])


def char(mt_code: int, typeface: int = TF_VARIABLE) -> bytes:
    """CHAR(2): options + int8(typeface-128) + mtef16 mt_code (LE)."""
    return bytes([CHAR, 0x00, (typeface - 128) & 0xFF]) + struct.pack("<H", mt_code)


def text(s: str, typeface: int | None = None) -> bytes:
    """Convenience: a run of ASCII chars, digits as NUMBER, letters as VARIABLE."""
    out = b""
    for ch in s:
        tf = typeface
        if tf is None:
            tf = TF_NUMBER if ch.isdigit() else (TF_VARIABLE if ch.isalpha() else TF_TEXT)
        out += char(ord(ch), tf)
    return out


def line(*objs: bytes, options: int = 0) -> bytes:
    """LINE(1): options + object_list + END."""
    return bytes([LINE, options]) + b"".join(objs) + bytes([END])


def tmpl(selector: int, variation: int, *slots: bytes, tso: int = 0) -> bytes:
    """TMPL(3): options + int8 selector + variation + tso + object_list + END."""
    return (
        bytes([TMPL, 0x00, selector & 0xFF])
        + enc_variation(variation)
        + bytes([tso])
        + b"".join(slots)
        + bytes([END])
    )


def pile(*lines: bytes, halign: int = 2, valign: int = 1) -> bytes:
    """PILE(4): options + int8 halign + int8 valign + object_list + END."""
    return bytes([PILE, 0x00, halign & 0xFF, valign & 0xFF]) + b"".join(lines) + bytes([END])


def matrix(rows: int, cols: int, *cells: bytes, valign: int = 1, h_just: int = 2,
           v_just: int = 1) -> bytes:
    """MATRIX(5): options + valign + h_just + v_just + rows + cols
    + row_parts((rows+4)//4 bytes) + col_parts((cols+4)//4 bytes) + cells + END."""
    n_row_bytes = (rows + 4) // 4
    n_col_bytes = (cols + 4) // 4
    return (
        bytes([MATRIX, 0x00, valign & 0xFF, h_just & 0xFF, v_just & 0xFF,
               rows & 0xFF, cols & 0xFF])
        + bytes(n_row_bytes)
        + bytes(n_col_bytes)
        + b"".join(cells)
        + bytes([END])
    )


# ── OLE container ─────────────────────────────────────────────────────
MTEF_HEADER = bytes([5, 1, 0, 7, 8]) + b"DSMT6\x00" + bytes([0x0B])

HERE = os.path.dirname(os.path.abspath(__file__))
FIXDIR = os.path.join(HERE, "_web_fixtures")
OUTDIR = os.path.join(HERE, "_synth_fixtures")
# A genuine MathType OLE container, reused as a shell.  olefile can only
# overwrite an existing stream and demands an identical byte length, so the
# payload is zero-padded to the template's stream size (harmless: cbObject
# bounds the real data and the record parser stops at the top-level END).
TEMPLATE = os.path.join(FIXDIR, "oleObject1.bin")


def _template_stream_size() -> int:
    with open(TEMPLATE, "rb") as fh:
        ole = olefile.OleFileIO(io.BytesIO(fh.read()))
    size = ole.get_size("Equation Native")
    ole.close()
    return size


def wrap_ole(body: bytes) -> bytes:
    """Wrap a top-level record stream into a genuine OLE compound file."""
    mtef = MTEF_HEADER + body + bytes([END])
    cb_object = len(mtef)
    eqn_hdr = struct.pack("<H I H I I I I I", 28, 0x00020000, 0, cb_object, 0, 0, 0, 0)
    stream = eqn_hdr + mtef

    target = _template_stream_size()
    if len(stream) > target:
        raise ValueError(f"payload {len(stream)}B exceeds template stream {target}B")
    stream += bytes(target - len(stream))

    with open(TEMPLATE, "rb") as fh:
        buf = io.BytesIO(fh.read())
    ole = olefile.OleFileIO(buf, write_mode=True)
    ole.write_stream("Equation Native", stream)
    ole.close()
    return buf.getvalue()


# ── cases ─────────────────────────────────────────────────────────────
def case_matrix_2x2() -> bytes:
    return line(matrix(2, 2,
                       line(text("a")), line(text("b")),
                       line(text("c")), line(text("d"))))


def case_matrix_3x3_paren() -> bytes:
    cells = [line(text(str(n))) for n in range(1, 10)]
    return line(tmpl(TM_PAREN, TV_FENCE_L | TV_FENCE_R,
                     line(matrix(3, 3, *cells))))


def case_matrix_of_fractions() -> bytes:
    def frac(a, b):
        return tmpl(TM_FRACT, 0, line(text(a)), line(text(b)))
    return line(tmpl(TM_BRACK, TV_FENCE_L | TV_FENCE_R,
                     line(matrix(2, 2,
                                 line(frac("1", "2")), line(frac("3", "4")),
                                 line(frac("5", "6")), line(frac("7", "8"))))))


def case_definite_integral() -> bytes:
    # slot[1]=integrand, slot[2]=lower, slot[3]=upper
    return line(tmpl(TM_INTEG, TV_INT_1 | TV_BO_LOWER | TV_BO_UPPER,
                     line(text("x"), text("dx")),
                     line(text("0")),
                     line(text("1"))))


def case_indefinite_integral() -> bytes:
    return line(tmpl(TM_INTEG, TV_INT_1, line(text("f"), text("x"))))


def case_summation() -> bytes:
    return line(tmpl(TM_SUM, TV_BO_LOWER | TV_BO_UPPER,
                     line(text("n")),
                     line(text("n"), text("=1")),
                     line(text("N"))))


def case_product() -> bytes:
    return line(tmpl(TM_PROD, TV_BO_LOWER | TV_BO_UPPER,
                     line(text("k")),
                     line(text("k=1")),
                     line(text("n"))))


def case_piecewise() -> bytes:
    return line(text("f"), text("x"), text("="),
                tmpl(TM_BRACE, TV_FENCE_L,
                     line(pile(line(text("x"), text(", x>0")),
                               line(text("0"), text(", x=0")),
                               halign=1))))


def case_system_pile() -> bytes:
    return line(pile(line(text("x+y=2")), line(text("x-y=0")), halign=1))


def case_nth_root() -> bytes:
    return line(tmpl(TM_ROOT, TV_ROOT_NTH, line(text("x")), line(text("3"))))


def case_subsup() -> bytes:
    return line(text("x"), tmpl(TM_SUBSUP, 0, line(text("i")), line(text("2"))))


def case_long_division() -> bytes:
    """tmLDIV -> menclose[longdiv], the record type that used to yield an
    empty <m:oMath/>.  Now exercised through the *binary* path."""
    return line(tmpl(TM_LDIV, 0, line(text("144"))))


def case_long_division_quotient() -> bytes:
    """tmLDIV + tvLD_UPPER keeps the quotient row above the vinculum."""
    return line(tmpl(TM_LDIV, 1, line(text("144")), line(text("12"))))


def case_nested_deep() -> bytes:
    inner = tmpl(TM_FRACT, 0, line(text("1")), line(text("2")))
    lvl2 = tmpl(TM_FRACT, 0, line(inner), line(text("3")))
    lvl3 = tmpl(TM_ROOT, 0, line(lvl2))
    return line(tmpl(TM_INTEG, TV_INT_1 | TV_BO_LOWER | TV_BO_UPPER,
                     line(lvl3), line(text("a")), line(text("b"))))


def case_matrix_in_integral() -> bytes:
    cells = [line(text(c)) for c in "abcd"]
    return line(tmpl(TM_INTEG, TV_INT_1 | TV_BO_LOWER | TV_BO_UPPER,
                     line(tmpl(TM_PAREN, TV_FENCE_L | TV_FENCE_R,
                               line(matrix(2, 2, *cells)))),
                     line(text("0")),
                     line(text("t"))))


def case_matrix_4x1_column() -> bytes:
    cells = [line(text(str(n))) for n in range(1, 5)]
    return line(tmpl(TM_PAREN, TV_FENCE_L | TV_FENCE_R, line(matrix(4, 1, *cells))))


def case_matrix_1x4_row() -> bytes:
    cells = [line(text(str(n))) for n in range(1, 5)]
    return line(tmpl(TM_PAREN, TV_FENCE_L | TV_FENCE_R, line(matrix(1, 4, *cells))))


def case_matrix_2x3() -> bytes:
    cells = [line(text(str(n))) for n in range(1, 7)]
    return line(tmpl(TM_BRACK, TV_FENCE_L | TV_FENCE_R, line(matrix(2, 3, *cells))))


# case -> (builder, expected OMML matrix shapes, required substrings)
CASES = {
    "matrix_2x2": (case_matrix_2x2, [[["a", "b"], ["c", "d"]]], []),
    "matrix_3x3_paren": (case_matrix_3x3_paren,
                         [[["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"]]], ["("]),
    "matrix_of_fractions": (case_matrix_of_fractions,
                            [[["12", "34"], ["56", "78"]]], ["["]),
    "matrix_2x3": (case_matrix_2x3, [[["1", "2", "3"], ["4", "5", "6"]]], ["["]),
    "matrix_4x1_column": (case_matrix_4x1_column,
                          [[["1"], ["2"], ["3"], ["4"]]], ["("]),
    "matrix_1x4_row": (case_matrix_1x4_row, [[["1", "2", "3", "4"]]], ["("]),
    "definite_integral": (case_definite_integral, [], ["\u222b", "0", "1", "dx"]),
    "indefinite_integral": (case_indefinite_integral, [], ["\u222b", "f"]),
    "summation": (case_summation, [], ["n", "N"]),
    "product": (case_product, [], ["k", "n"]),
    "piecewise": (case_piecewise, [], ["x>0", "x=0"]),
    "system_pile": (case_system_pile, [], ["x+y=2", "x-y=0"]),
    "nth_root": (case_nth_root, [], ["x", "3"]),
    "subsup": (case_subsup, [], ["x", "i", "2"]),
    "long_division": (case_long_division, [], ["144"]),
    "long_division_quotient": (case_long_division_quotient, [], ["144", "12"]),
    "nested_deep": (case_nested_deep, [], ["\u222b", "1", "2", "3", "a", "b"]),
    "matrix_in_integral": (case_matrix_in_integral,
                           [[["a", "b"], ["c", "d"]]], ["\u222b", "0", "t"]),
}


# ── verification harness ──────────────────────────────────────────────
def _mathml_of(ole_bytes):
    """The engine's own MTEF -> MathML output (mtefx assets, not mathtypejx's
    bundled stylesheets — those are a separate, unpatched copy)."""
    from mtefx.engine import extract_mtef as _ex, _mtef_to_mathml_str
    try:
        payload = _ex(ole_bytes)
        return _mtef_to_mathml_str(payload) or ""
    except Exception as exc:
        return f"<ERROR {type(exc).__name__}: {exc}>"


M_OMML = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


def _omml_matrix_shapes(omml):
    """Grid shape of every OMML matrix, as [[cell text, ...], ...].

    MML2OMML.XSL maps a *single-column* <mtable> to <m:eqArr> rather than
    <m:m> (Word renders both as a vertical stack), so an eqArr is reported
    as an n x 1 grid when no <m:m> is present.
    """
    shapes = [
        [["".join(e.itertext()) for e in row if e.tag == M_OMML + "e"]
         for row in mt if row.tag == M_OMML + "mr"]
        for mt in omml.iter(M_OMML + "m")
    ]
    if shapes:
        return shapes
    return [
        [["".join(e.itertext())] for e in arr if e.tag == M_OMML + "e"]
        for arr in omml.iter(M_OMML + "eqArr")
    ]


def _omml_alltext(omml):
    """Text plus attribute values — OMML puts operators such as the integral
    sign in <m:chr m:val="..."/> attributes, not in text nodes."""
    parts = ["".join(omml.itertext())]
    for el in omml.iter():
        parts.extend(str(v) for v in el.attrib.values())
    return "".join(parts)


def _record_tree(ole_bytes):
    """Independent reference: raw record tree straight off the binary."""
    from mathtypejx.mtef.stream import ByteStream
    from mathtypejx.mtef.records5 import parse_equation
    from mathtypejx.mtef.mathml import _skip_stream_header
    ole = olefile.OleFileIO(io.BytesIO(ole_bytes))
    raw = ole.openstream("Equation Native").read()
    ole.close()
    st = ByteStream(raw[28:])
    _skip_stream_header(st, 5)
    return parse_equation(st)


def _tree_kinds(node, acc=None):
    if acc is None:
        acc = set()
    if isinstance(node, dict):
        t = node.get("type")
        if t:
            acc.add(t)
        if t == "tmpl":
            acc.add("tmpl:" + str(node.get("selector")))
        for v in node.values():
            _tree_kinds(v, acc)
    elif isinstance(node, list):
        for v in node:
            _tree_kinds(v, acc)
    return acc


def main():
    from lxml import etree
    from mtefx import extract_mtef
    from mtefx._fused import convert_fused, _is_degenerate_omml

    os.makedirs(OUTDIR, exist_ok=True)
    rows = []
    for name, (fn, want_shapes, want_text) in CASES.items():
        rec = {"case": name}
        try:
            ole_bytes = wrap_ole(fn())
        except Exception as exc:
            rec.update(build="FAIL", error=f"{type(exc).__name__}: {exc}")
            rows.append(rec)
            continue
        path = os.path.join(OUTDIR, f"synth_{name}.bin")
        with open(path, "wb") as fh:
            fh.write(ole_bytes)
        rec["bytes"] = len(ole_bytes)

        # 1) OLE payload must be recoverable
        payload = extract_mtef(ole_bytes)
        rec["mtef_ok"] = bool(payload) and payload[0] == 5

        # 2) the binary record decoder must actually see the hard structures
        try:
            tree = _record_tree(ole_bytes)
            kinds = _tree_kinds(tree.get("equation"))
            rec["records"] = sorted(
                k for k in kinds if k in ("matrix", "pile") or k.startswith("tmpl:")
            )
        except Exception as exc:
            rec["records"] = f"ERROR {type(exc).__name__}: {exc}"

        # 3) reference MathML (mathtypejx's own path)
        mml = _mathml_of(ole_bytes)
        rec["mathml_len"] = len(mml) if isinstance(mml, str) else 0
        rec["mathml"] = (mml or "")[:400]

        # 4) the engine's fused OLE -> OMML pipeline
        try:
            status, omml, fixed, unresolved, ms = convert_fused(ole_bytes)
            rec["status"] = status
            rec["pua_unresolved"] = unresolved
            if omml is not None:
                rec["degenerate"] = _is_degenerate_omml(omml)
                rec["omml_nodes"] = sum(1 for _ in omml.iter())
                rec["omml"] = etree.tostring(omml, encoding="unicode")[:400]
                shapes = _omml_matrix_shapes(omml)
                rec["shapes"] = shapes
                flat = _omml_alltext(omml)
                rec["shape_ok"] = (shapes == want_shapes) if want_shapes else True
                missing = [t for t in want_text if t not in flat]
                rec["text_ok"] = not missing
                rec["missing"] = missing
            else:
                rec["degenerate"] = True
                rec["omml_nodes"] = 0
                rec["shape_ok"] = rec["text_ok"] = False
        except Exception as exc:
            rec["status"] = f"EXC {type(exc).__name__}: {exc}"
            rec["degenerate"] = True
            rec["shape_ok"] = rec["text_ok"] = False
        rows.append(rec)

    print("=" * 110)
    header = ("case".ljust(24) + "mtef".rjust(6) + "status".rjust(8)
              + "degen".rjust(7) + "shape".rjust(7) + "text".rjust(6)
              + "nodes".rjust(7) + "  records")
    print(header)
    print("-" * 110)
    bad = []
    for r in rows:
        ok = (r.get("mtef_ok") and r.get("status") == "ok"
              and not r.get("degenerate") and r.get("shape_ok")
              and r.get("text_ok"))
        if not ok:
            bad.append(r["case"])
        print(
            str(r.get("case")).ljust(24)
            + str(r.get("mtef_ok")).rjust(6)
            + str(r.get("status")).rjust(8)
            + str(r.get("degenerate")).rjust(7)
            + str(r.get("shape_ok")).rjust(7)
            + str(r.get("text_ok")).rjust(6)
            + str(r.get("omml_nodes", 0)).rjust(7)
            + "  " + str(r.get("records"))
            + ("  MISSING=" + str(r["missing"]) if r.get("missing") else "")
        )
    print("-" * 110)
    print(f"total={len(rows)}  problem={len(bad)}  {bad}")

    out = os.path.join(OUTDIR, "report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
    print("report ->", out)
    return rows, bad


if __name__ == "__main__":
    import sys
    _rows, _bad = main()
    sys.exit(1 if _bad else 0)
