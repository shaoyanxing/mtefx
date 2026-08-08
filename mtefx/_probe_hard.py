"""全语料难度探针 + 硬样本 OMML 级对拍。

阶段一（全量，~90s）：
  遍历 D:\\moved_data2\\中考\\ 35 个 zip → 各 docx → word/embeddings/*.bin，
  对每个 Equation OLE 公式跑 convert_fused，采集难度特征，按难度打分。
  产出：all_formulas.jsonl（全量特征）、summary.txt（分布）、
        hard_top.json（Top-150）、hard_bins/*.bin（Top-40 原始字节，内存保留避免重扫）。

阶段二（Top-150，快）：
  对最硬样本做 OMML 级一致性对拍：
    融合路径  convert_fused(bin)        → omml_el  → canonical(A)
    参考路径  convert(bin).mathml
             → mathml_to_omml_element  → omml_el  → canonical(B)
  A != B 即真实 bug（生产融合路径偏离参考）。同时记录退化 OMML / 参考路径失败。

用法：python -m mtefx._probe_hard
"""

from __future__ import annotations

import io
import json
import posixpath
import time
import zipfile
from pathlib import Path

from lxml import etree

from mtefx.engine import (
    extract_mtef,
    digest_of,
    probe_version,
    convert,
)
from mtefx._fused import convert_fused
from mtefx.omml import mathml_to_omml_element, M_NS

_CORPUS = Path(r"D:\moved_data2\中考")
_OUT = Path(__file__).resolve().parent / "_probe_out"
_OUT.mkdir(exist_ok=True)
_BIN_DIR = _OUT / "hard_bins"
_BIN_DIR.mkdir(exist_ok=True)

_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "o": "urn:schemas-microsoft-com:office:office",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _wp():
    return etree.XMLParser(resolve_entities=False, recover=True, huge_tree=True)


def _iter_equation_bins(outer_zip: Path):
    """yield (docx_name, bin_path, bin_bytes)。"""
    with zipfile.ZipFile(outer_zip) as zo:
        docx_names = [
            n for n in zo.namelist()
            if n.lower().endswith(".docx") and not n.startswith("~")
        ]
        for dn in docx_names:
            try:
                data = zo.read(dn)
            except Exception:
                continue
            try:
                zi = zipfile.ZipFile(io.BytesIO(data))
            except Exception:
                continue
            names = zi.namelist()
            doc_name = next((n for n in names if n.lower().endswith("word/document.xml")), None)
            rels_name = next((n for n in names if n.lower().endswith("word/_rels/document.xml.rels")), None)
            if doc_name is None:
                zi.close()
                continue
            try:
                root = etree.fromstring(zi.read(doc_name), parser=_wp())
            except Exception:
                zi.close()
                continue
            rels_target = {}
            if rels_name:
                try:
                    rr = etree.fromstring(zi.read(rels_name), parser=_wp())
                    for rel in rr.findall(f"{{{_RELS_NS}}}Relationship"):
                        rels_target[rel.get("Id")] = rel.get("Target")
                except Exception:
                    pass
            doc_dir = doc_name.rsplit("/", 1)[0] + "/" if "/" in doc_name else ""

            def _resolve(t):
                if not t:
                    return ""
                if t.startswith("/"):
                    return t.lstrip("/")
                return posixpath.normpath(doc_dir + t)

            for obj in root.findall(".//w:object", _NS):
                ole = obj.find("o:OLEObject", _NS)
                if ole is None:
                    continue
                prog = (ole.get("ProgID") or "").lower()
                if "equation" not in prog:
                    continue
                if (ole.get("Type") or "").lower() != "embed":
                    continue
                rid = ole.get(f"{{{_NS['r']}}}id")
                if not rid:
                    continue
                tgt = rels_target.get(rid)
                if not tgt:
                    continue
                bp = _resolve(tgt)
                if not bp.lower().endswith(".bin"):
                    continue
                try:
                    b = zi.read(bp)
                except KeyError:
                    continue
                yield dn, bp, b
            zi.close()


def _omml_features(el):
    if el is None:
        return dict(node_count=0, has_matrix=False, has_delim=False,
                    has_nary=False, has_stack=False, has_script=False,
                    depth=0, omml_bytes=0, degenerate=True)
    nodes = list(el.iter())
    nc = len(nodes)
    has = lambda tag: el.find(f".//{{{M_NS}}}{tag}") is not None
    depth = 0
    for n in nodes:
        d = 0
        p = n.getparent()
        while p is not None:
            d += 1
            p = p.getparent()
        depth = max(depth, d)
    degenerate = nc <= 1 or (nc == 2 and not list(el))
    return dict(
        node_count=nc,
        has_matrix=has("m"),
        has_delim=has("d"),
        has_nary=has("nary"),
        has_stack=has("stack"),
        has_script=has("sSub") or has("sSup") or has("sSubSup"),
        depth=depth,
        omml_bytes=len(etree.tostring(el)),
        degenerate=degenerate,
    )


def _score(row):
    s = row["payload_len"] / 50.0
    if row["ver"] == 3:
        s += 300
    s += row["pua_unresolved"] * 100
    if row["has_matrix"]:
        s += 200
    if row["has_nary"]:
        s += 120
    if row["has_stack"]:
        s += 120
    if row["has_delim"]:
        s += 80
    if row["has_script"]:
        s += 80
    s += max(0.0, 300 - row["omml_bytes"] / 10.0)
    return s


def main():
    t0 = time.perf_counter()
    rows = []
    bins = {}  # dig16 -> bytes（仅保留用于后续保存/对拍，峰值内存 ~70MB）
    stats = dict(total=0, ok=0, empty=0, error=0, no_stream=0,
                 v3=0, pua_unresolved_gt0=0, degenerate=0,
                 has_matrix=0, has_nary=0, has_stack=0, has_delim=0, has_script=0)

    zips = sorted(_CORPUS.glob("*.zip"))
    print(f"[scan] {len(zips)} 个 zip …", flush=True)

    for zi, zp in enumerate(zips, 1):
        for dn, bp, b in _iter_equation_bins(zp):
            payload = extract_mtef(b)
            if payload is None:
                stats["no_stream"] += 1
                continue
            dig = digest_of(payload)  # 已为 hex 字符串
            dig16 = dig[:16]
            ver = probe_version(payload)
            status, omml_el, pua_fixed, pua_unresolved, _ = convert_fused(b)
            feat = _omml_features(omml_el)
            row = dict(
                zip=zp.name, docx=dn, bin=bp, dig=dig16,
                ver=ver, payload_len=len(payload),
                status=status, pua_fixed=pua_fixed, pua_unresolved=pua_unresolved,
                **feat,
            )
            stats["total"] += 1
            if status == "ok":
                stats["ok"] += 1
            elif status == "empty":
                stats["empty"] += 1
            elif status == "error":
                stats["error"] += 1
            if ver == 3:
                stats["v3"] += 1
            if pua_unresolved and pua_unresolved > 0:
                stats["pua_unresolved_gt0"] += 1
            if feat["degenerate"]:
                stats["degenerate"] += 1
            for k in ("has_matrix", "has_nary", "has_stack", "has_delim", "has_script"):
                if feat[k]:
                    stats[k] += 1
            bins[dig16] = b
            rows.append(row)
        print(f"[scan] {zi}/{len(zips)} {zp.name}  累计 {stats['total']} 公式", flush=True)

    for r in rows:
        r["score"] = _score(r)
    rows.sort(key=lambda r: r["score"], reverse=True)

    with open(_OUT / "all_formulas.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    top = rows[:150]
    with open(_OUT / "hard_top.json", "w", encoding="utf-8") as f:
        json.dump(top, f, ensure_ascii=False, indent=1)

    saved = 0
    for r in rows[:40]:
        b = bins.get(r["dig"])
        if b is not None:
            (_BIN_DIR / f"{r['dig']}.bin").write_bytes(b)
            saved += 1
        if saved >= 40:
            break

    # 阶段二：Top-150 对拍
    print(f"[cmp] 对拍 Top-{len(top)} 硬样本 …", flush=True)
    cmp_rows = []
    incons = 0
    ref_fail = 0
    for r in top:
        b = bins.get(r["dig"])
        if b is None:
            continue
        _, fused_el, _, _, _ = convert_fused(b)
        fused_canon = etree.canonicalize(fused_el) if fused_el is not None else None
        ref = convert(b, fix_v3=True, repair_chars=True)
        ref_canon = None
        ref_status = ref.status
        if ref.status == "ok" and ref.mathml:
            try:
                ref_el = mathml_to_omml_element(ref.mathml)
                ref_canon = etree.canonicalize(ref_el)
            except Exception as exc:
                ref_status = f"ref_error:{type(exc).__name__}"
                ref_fail += 1
        match = (fused_canon is not None and ref_canon is not None
                 and fused_canon == ref_canon)
        if not match:
            incons += 1
        cmp_rows.append(dict(
            dig=r["dig"], ver=r["ver"], fused_status=r["status"],
            ref_status=ref_status, match=bool(match),
            score=r["score"], node_count=r["node_count"],
            degenerate=r["degenerate"], pua_unresolved=r["pua_unresolved"],
        ))

    with open(_OUT / "hard_cmp.json", "w", encoding="utf-8") as f:
        json.dump(cmp_rows, f, ensure_ascii=False, indent=1)

    elapsed = time.perf_counter() - t0
    lines = []
    lines.append("===== 全语料难度探针 summary =====")
    lines.append(f"总公式数: {stats['total']}")
    lines.append(f"status: ok={stats['ok']} empty={stats['empty']} error={stats['error']} no_stream={stats['no_stream']}")
    lines.append(f"v3 占比: {stats['v3']} ({100*stats['v3']/max(1,stats['total']):.1f}%)")
    lines.append(f"含矩阵 m:m     : {stats['has_matrix']}")
    lines.append(f"含定积分 m:d   : {stats['has_delim']}")
    lines.append(f"含 nary        : {stats['has_nary']}")
    lines.append(f"含 stack       : {stats['has_stack']}")
    lines.append(f"含上下标 script: {stats['has_script']}")
    lines.append(f"PUA 未解析>0   : {stats['pua_unresolved_gt0']}")
    lines.append(f"退化 OMML      : {stats['degenerate']}")
    lines.append("")
    lines.append(f"阶段二 Top-{len(top)} 对拍:")
    lines.append(f"  融合 vs 参考 不一致: {incons}")
    lines.append(f"  参考路径失败     : {ref_fail}")
    lines.append(f"  总耗时: {elapsed:.1f}s")
    lines.append("")
    lines.append("Top-15 最硬样本:")
    for r in rows[:15]:
        lines.append(f"  score={r['score']:.0f} ver={r['ver']} status={r['status']} "
                     f"nodes={r['node_count']} pay={r['payload_len']} "
                     f"M={int(r['has_matrix'])}D={int(r['has_delim'])}N={int(r['has_nary'])}"
                     f"S={int(r['has_stack'])}pua={r['pua_unresolved']} {r['dig']} {r['zip'][:22]}…{r['bin']}")
    txt = "\n".join(lines)
    (_OUT / "summary.txt").write_text(txt, encoding="utf-8")
    print(txt, flush=True)


if __name__ == "__main__":
    main()
