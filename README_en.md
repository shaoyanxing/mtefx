# mtefx — MathType Formula Parser · Zero-COM Acceleration Layer

> Convert MathType OLE formulas (`.bin`) embedded in Word `.docx` files into
> native OMML equations — **zero COM, zero Word/Excel dependency**, pure Python + XSLT.

## Why

MathType formulas in Word documents are embedded as OLE objects
(`word/embeddings/*.bin`). Converting them to native Word OMML equations
traditionally requires either:

1. **COM route** — invoke Word's `Equation.OMath` COM object. Requires a local
   Office installation, interactive desktop session, single-process serial
   execution, and cannot scale horizontally. Fundamentally incompatible with
   server-side batch processing.
2. **B route** — olefile + XSLT, in-process, scales linearly with
   process pools.

This project takes **the olefile + XSLT route**, building acceleration and enhancements on
top of [`mathtypejx`](https://github.com/a917470154/mathtypejx) and
[`transpect/mathtype-extension`](https://github.com/transpect/mathtype-extension).

## Pipeline

```
.docx (zip)
  │  zipfile reads word/embeddings/*.bin directly (no disk extraction)
  ▼
OLE binary
  │  olefile strips OLE wrapper + 28-byte EQNOLEFILEHDR
  ▼
MTEF payload (blake2b fingerprint dedup, 30–60% hit rate on real corpora)
  │  probe_version() reads first byte for triage
  ├─ v3 → wrap_v3_slot() structural fix → XSLT
  └─ v5 → mathtypejx native parser → XSLT
  ▼
MathML
  │  Namespace normalization + fontmap PUA character repair (3648 mappings)
  ▼
FormulaResult
  │  MML2OMML.XSL (lxml compiled once, process-level cached)
  ▼
OMML (in-place replacement of OLE objects in docx)
```

## Four Key Improvements

| Improvement | Before | After | Details |
|-------------|--------|-------|---------|
| **XSLT acceleration** | 93.49 ms/formula | **2.83 ms/formula** (33×) | Eliminated per-formula reload of 348 KB fontmap via `document()` calls. Single compile + cache. Output byte-identical. |
| **MTEF v3 fix** | 1/11 usable | **11/11** | mathtypejx produces `<mtef><full/><tmpl/></mtef>` for v3, but XSLT expects `<mtef><full/><slot><tmpl/></slot></mtef>`. `wrap_v3_slot()` adds the missing wrapper. |
| **Empty shell sentinel** | Silent formula loss | Explicit failure detection | mathtypejx returns `<math .../>` empty shell for v3 without throwing. `is_empty_mathml()` explicitly detects and flags `status="empty"`. |
| **fontmap fallback** | PUA characters leak into output | Fixed | mathtypejx's built-in fontmap silently skipped due to invalid XSLT 1.0 function. Replaced with Python-side 3648 mappings (28 tables), e.g. `U+EB04→U+2004`. |

## Benchmarks

### Real corpus (Chinese middle-school exam papers, 35 zips / 34,233 OLE formulas)

| Configuration | Time | Throughput | Speedup |
|--------------|------|------------|---------|
| Original serial (string path) | 93.7s | 366 formulas/s | 1.00× |
| Fused path serial | 64s | 535 formulas/s | 1.46× |
| Fused + parallel(12w) + process cache | **13.04s** | **2,625 formulas/s** | **7.18×** |

### Raspberry Pi 4B (4-core ARM Cortex-A72, 240 synthetic hard formulas)

| Configuration | ms/formula | formulas/s | Speedup |
|---------------|------------|------------|---------|
| Original serial | 7.57 | 132 | 1.00× |
| Optimized serial (logging off) | 6.44 | 155 | 1.18× |
| Parallel 3 workers | **2.95** | **339** | **2.57×** |

> On Raspberry Pi 4B, 3 workers is optimal (4 workers degrade due to memory bandwidth).

### Accuracy

| Test set | Count | Result |
|----------|-------|--------|
| Real OLE (mathtypejx fixtures) | 3 | 3/3 OMML byte-identical |
| Web OLE fixtures | 3 | 3/3 identical |
| Synthetic hard formulas (matrix/integral/sum/piecewise/system/nth-root/long-division/nested) | 18 | 18/18 identical |
| Full real corpus | 34,233 | **0 failures, 0 skipped** |
| Constructed hard formula battery | 58 | 58/58 passed |

## Quick Start

```python
from mtefx import convert, convert_docx, convert_many, FormulaResult

# Single formula: OLE bytes (from docx embedding / .bin / raw MTEF)
res: FormulaResult = convert(ole_bytes)
if res.ok:
    print(res.mathml)
    print("PUA fixed:", res.pua_fixed, "unresolved:", res.pua_unresolved)

# Document-level: read docx embedded formulas directly
rep = convert_docx("exam.docx")
print(f"{rep.ok}/{rep.total} succeeded, dedup hits {rep.cache_hits}")

# Batch processing (process pool)
reports = convert_many(["a.docx", "b.docx"], workers=4)
```

### Installation

```bash
git clone https://github.com/shaoyanxing/mtefx.git
cd mtefx
python -m venv .venv && source .venv/bin/activate
pip install -e ./mtjx_checkout
pip install lxml olefile python-docx
```

Dependencies: `lxml>=4.9`, `olefile>=0.46`, `python-docx>=0.8`, Python ≥ 3.10.

## Project Structure

```
mtefx/
├── mtefx/                     # Acceleration + enhancement layer
│   ├── __init__.py            # Public API (convert / convert_docx / convert_many)
│   ├── engine.py              # Core engine: OLE→MTEF→MathML fast path
│   ├── v3fix.py               # MTEF v3 silent failure fix
│   ├── fontmap.py             # 3648 fontmap mappings + PUA repair
│   ├── omml.py                # MathML→OMML (MML2OMML.XSL, lxml cached)
│   ├── docxconv.py            # docx-level: OLE→OMML in-place replacement
│   ├── pipeline.py            # docx zip reader, dedup cache, process pool
│   ├── refine.py              # Saxon batch refinement (6 stages, -im mode fix)
│   ├── _fused.py              # Fused path: Element pipeline, fewer serializations
│   ├── fused_fast.py          # Merged traversal (NS+PUA+menclose in one iter)
│   ├── _synthesize_ole.py     # Synthetic hard OLE battery (18 structures)
│   ├── regression.py          # Unified regression gate
│   ├── selftest.py            # End-to-end self-test
│   └── assets/
│       ├── xslt_fast/         # Accelerated XSLT stylesheets (single compile)
│       └── fontmap.json       # 28 font tables / 3648 mappings
├── mtjx_checkout/             # Core engine (upstream mathtypejx)
│   └── src/mathtypejx/
│       ├── scanner.py         # docx OLE object scanner
│       ├── extractor.py       # OLE→MTEF byte extraction
│       ├── converter.py       # MathML→OMML conversion + risk grading
│       ├── replacer.py        # OLE→OMML in-place replacement in docx XML
│       ├── validator.py       # OMML quality validation
│       ├── service.py         # Top-level API
│       └── mtef/              # MTEF record parsing
│           ├── records5.py    # v5 record parser
│           ├── records3.py    # v3 record parser
│           ├── builder.py     # MTEF XML builder
│           ├── mover.py       # Subscript/superscript movement
│           ├── chars.py       # Character mapping replacement
│           └── stream.py      # Byte stream reader
├── vendor/
│   ├── mathtype-extension/    # transpect XSLT stylesheets
│   └── mml2omml/             # Microsoft official MML2OMML.XSL
├── real_ole/                  # Real OLE test fixtures
└── tests/                     # Differential & regression tests
```

## Technical Notes

### transpect Entry Mode Trap

transpect's 6 post-processing stylesheets declare their main templates in
**dedicated modes** (e.g. `mode="operator-elements"`), but Saxon CLI defaults
to `#default` mode only — causing 4/6 stages to no-op. Correct entry modes:

| Stage file | Entry mode |
|-----------|------------|
| `whitespace-handle.xsl` | `handle-whitespace` ⚠️ filename ≠ mode |
| `split-elements.xsl` | `split-elements` |
| `combine-elements.xsl` | `combine-elements` |
| `operator-elements.xsl` | `operator-elements` |
| `repair-subsup.xsl` | `repair-subsup` |
| `clean-up.xsl` | `clean-up` |

### lxml `_XSLTResultTree` Quirks

Never directly hold `etree.XSLT(...)(xml).getroot()` as a regular tree:

1. **Entity not decoded** — `getroot()` text retains `&#x2003;` as 7-char literal
2. **Namespace hoist lost** — `fromstring` re-parse drops namespace promotion

Correct approach: `etree.tostring(result, "unicode")` returns the string directly.

### menclose longdiv/actuarial Silent Loss

MML2OMML produces empty `<m:oMath/>` for `<menclose notation="longdiv">`.
Fix: expand `menclose[longdiv|actuarial]` to `<mrow>` before XSLT, preserving
inner math content.

## Acknowledgements

This project stands on the shoulders of these open-source projects:

- **[mathtypejx](https://github.com/a917470154/mathtypejx)** — Python MTEF parsing engine
  (OLE→MTEF→record tree→MathML), MIT License, Copyright (c) 2026 a917470154
- **[transpect/mathtype-extension](https://github.com/transpect/mathtype-extension)** —
  Publication-grade XSLT post-processing stylesheets + fontmap assets,
  Copyright (c) 2018–2024 transpect.io, BSD-style License
- **[jure/mathtype](https://github.com/jure/mathtype)** — Ruby gem for reading MathType
  binaries, MIT License, Copyright (c) 2015 Jure Triglav
- **[jure/mathtype_to_mathml](https://github.com/jure/mathtype_to_mathml)** — Ruby/XSLT
  MTEF XML to MathML conversion, MIT License, Copyright (c) 2015 Jure Triglav
- **[sbulka/mathtype](https://github.com/sbulka/mathtype)** — jure/mathtype fork, MTEF
  record reference
- **mathtype_to_mathml_plus** — Ruby gem combining mathtype gem + XSLT conversion
- **Microsoft MML2OMML.XSL** — Official MathML→OMML XSLT 1.0 stylesheet

Fontmap assets (`assets/xslt_fast/xsl/fontmaps/`) are from transpect.io under
their BSD-style license.

## License

- Project code: **[MIT License](LICENSE)**
- Fontmap assets: BSD-style License (see `assets/xslt_fast/xsl/fontmaps/LICENSE`)
- MML2OMML.XSL: Microsoft official file, used under its original terms
