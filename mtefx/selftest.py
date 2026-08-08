"""
mtefx 端到端自检 —— 在 transpect 的 33 个真实样本上跑完整融合链路。

    python -m mtefx.selftest            # 全量自检
    python -m mtefx.selftest --no-refine  # 跳过 Saxon 精修层
    python -m mtefx.selftest --dump out/  # 同时导出 MathML

检查项：
  1. convert() 整合路径（快速 XSLT + v3 修复 + fontmap 兜底）逐样本状态
  2. 对照组：关掉 fix_v3 / repair_chars，量化两项改进的真实增量
  3. 去重缓存命中率
  4. Saxon 精修层可用性与批量精修
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from mtefx.engine import (
    STATUS_OK,
    FormulaResult,
    convert,
    digest_of,
    extract_mtef,
    iter_fixtures,
    probe_version,
)

_OK = "\u2713"
_NO = "\u2717"


def _c(s: str, code: str) -> str:
    return f"\033[{code}m{s}\033[0m" if sys.stdout.isatty() else s


def _hr(title: str = "") -> None:
    if title:
        print(f"\n{'─' * 4} {title} {'─' * max(0, 66 - len(title))}")
    else:
        print("─" * 72)


# ────────────────────────── 1. 整合路径逐样本 ──────────────────────────


def run_matrix(samples: list[tuple[str, bytes]]) -> dict:
    """跑三组配置，量化每项改进的增量贡献。"""
    configs = {
        "baseline": dict(fast=True, fix_v3=False, repair_chars=False),
        "v3fix": dict(fast=True, fix_v3=True, repair_chars=False),
        "full": dict(fast=True, fix_v3=True, repair_chars=True),
    }
    out: dict[str, list[tuple[str, FormulaResult]]] = {}
    for name, kw in configs.items():
        t0 = time.perf_counter()
        rows = [(n, convert(b, **kw)) for n, b in samples]
        out[name] = rows
        ok = sum(1 for _, r in rows if r.ok)
        ms = (time.perf_counter() - t0) * 1000
        print(
            f"  {name:<9} 成功 {ok:>2}/{len(rows)}   "
            f"总耗时 {ms:7.1f} ms   均摊 {ms / max(1, len(rows)):5.2f} ms/公式"
        )
    return out


def print_detail(rows: list[tuple[str, FormulaResult]]) -> None:
    print(f"\n  {'样本':<26} {'ver':>3} {'状态':<10} {'PUA修复':>7} {'耗时ms':>7}")
    print("  " + "-" * 62)
    for name, r in rows:
        mark = _c(_OK, "32") if r.ok else _c(_NO, "31")
        pua = str(r.pua_fixed) if r.pua_fixed else "-"
        print(
            f"  {mark} {name:<24} {r.mtef_version or '?':>3} "
            f"{r.status:<10} {pua:>7} {r.elapsed_ms:7.2f}"
        )
        if not r.ok and r.reason:
            print(f"      └─ {r.reason[:60]}")


# ────────────────────────── 2. 去重缓存 ──────────────────────────


def check_quality(rows: list[tuple[str, FormulaResult]]) -> int:
    """校验产出 MathML 的合法性 —— 良构、根命名空间、无 xmlns="" 污染。"""
    from lxml import etree

    ns = "http://www.w3.org/1998/Math/MathML"
    bad = 0
    for name, r in rows:
        if not r.ok or not r.mathml:
            continue
        problems = []
        try:
            root = etree.fromstring(r.mathml.encode("utf-8"))
        except Exception as exc:
            print(f"  {_NO} {name}: 非良构 XML — {exc}")
            bad += 1
            continue
        if not root.tag.endswith("math"):
            problems.append(f"根元素为 {root.tag}")
        if ns not in r.mathml[:200]:
            problems.append("根缺 MathML 命名空间")
        if 'xmlns=""' in r.mathml:
            problems.append('存在 xmlns="" 污染')
        inner_ns = sum(1 for e in root.iter() if e is not root and "xmlns" in e.attrib)
        if inner_ns:
            problems.append(f"内层残留 {inner_ns} 处 xmlns 声明")
        if problems:
            print(f"  {_NO} {name}: {'; '.join(problems)}")
            bad += 1
    total = sum(1 for _, r in rows if r.ok)
    print(f"  {_OK if not bad else _NO} 合法 {total - bad}/{total}")
    return bad


def check_dedup(samples: list[tuple[str, bytes]]) -> None:
    seen: dict[str, str] = {}
    dup = 0
    for name, blob in samples:
        payload = extract_mtef(blob)
        if payload is None:
            continue
        d = digest_of(payload)
        if d in seen:
            dup += 1
            print(f"  重复内容: {name}  ==  {seen[d]}")
        else:
            seen[d] = name
    print(f"  唯一公式 {len(seen)} / 输入 {len(samples)}，命中去重 {dup} 次")


# ────────────────────────── 3. Saxon 精修层 ──────────────────────────


def check_refine(rows: list[tuple[str, FormulaResult]], enabled: bool) -> None:
    from mtefx.refine import SaxonRefiner

    r = SaxonRefiner()
    st = r.status()
    print(f"  可用性: {_OK if st.available else _NO}  {st.reason}")
    if st.saxon_jars:
        print(f"  jar   : {', '.join(st.saxon_jars)}")
        print(f"  阶段  : {' → '.join(st.stages)}")
    if not (st.available and enabled):
        print("  (跳过实跑)")
        return

    payload = [r_.mathml for _, r_ in rows if r_.ok and r_.mathml]
    if not payload:
        print("  无可精修输入")
        return

    # 两种批量规模，用于分离「JVM 固定开销」与「每公式边际成本」
    for mult in (1, 8):
        batch = payload * mult
        t0 = time.perf_counter()
        refined = r.refine_batch(batch)
        ms = (time.perf_counter() - t0) * 1000
        changed = sum(1 for a, b in zip(batch, refined) if a != b)
        print(
            f"  批量 {len(batch):>4} 条 → {ms:7.0f} ms  "
            f"（{ms / len(batch):6.2f} ms/公式，变更 {changed}/{len(batch)}）"
        )
        if mult == 1:
            first = refined
            base_ms, base_n = ms, len(batch)
        else:
            # 线性外推分离固定/边际成本
            per = (ms - base_ms) / max(1, len(batch) - base_n)
            fixed = base_ms - per * base_n
            print(
                f"  → JVM 固定开销 ≈ {fixed:.0f} ms（{len(st.stages)} 次冷启动），"
                f"边际成本 ≈ {per:.2f} ms/公式"
            )

    ok_rows = [(n, x) for n, x in rows if x.ok and x.mathml]
    shown = 0
    for (name, _), before, after in zip(ok_rows, payload, first):
        if before != after and shown < 3:
            print(f"    · {name}: {len(before)} → {len(after)} 字节")
            shown += 1


# ────────────────────────── main ──────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="mtefx 端到端自检")
    ap.add_argument("--no-refine", action="store_true", help="跳过 Saxon 精修实跑")
    ap.add_argument("--dump", metavar="DIR", help="导出 MathML 到目录")
    ap.add_argument("--detail", action="store_true", help="打印逐样本明细")
    args = ap.parse_args(argv)

    samples = list(iter_fixtures("all"))
    if not samples:
        print("未找到测试样本，请先 git clone transpect/mathtype-extension 到 vendor/")
        return 2

    v3 = sum(1 for n, b in samples if probe_version(extract_mtef(b) or b"") == 3)
    print(f"样本集: {len(samples)} 个（v3 {v3} 个 / v5 {len(samples) - v3} 个）")

    _hr("1. 三组配置对照")
    matrix = run_matrix(samples)

    base_ok = sum(1 for _, r in matrix["baseline"] if r.ok)
    v3_ok = sum(1 for _, r in matrix["v3fix"] if r.ok)
    full = matrix["full"]
    full_ok = sum(1 for _, r in full if r.ok)
    pua_total = sum(r.pua_fixed for _, r in full)
    pua_left = sum(r.pua_unresolved for _, r in full)

    _hr("2. 改进增量")
    print(f"  v3 结构修复      : 成功数 {base_ok} → {v3_ok}  (+{v3_ok - base_ok})")
    print(f"  fontmap PUA 兜底 : 修复 {pua_total} 个私用区字符，残留 {pua_left} 个")
    print(f"  最终成功率       : {full_ok}/{len(samples)}")

    if args.detail:
        _hr("3. 逐样本明细")
        print_detail(full)

    _hr("4. 输出合法性")
    bad = check_quality(full)

    _hr("5. 去重缓存")
    check_dedup(samples)

    _hr("6. Saxon 精修层")
    check_refine(full, not args.no_refine)

    if args.dump:
        d = Path(args.dump)
        d.mkdir(parents=True, exist_ok=True)
        n = 0
        for name, r in full:
            if r.ok and r.mathml:
                (d / (name.replace("/", "_") + ".mml")).write_text(
                    r.mathml, encoding="utf-8"
                )
                n += 1
        print(f"\n已导出 {n} 个 MathML 到 {d}")

    _hr()
    passed = full_ok == len(samples) and bad == 0
    print(_c(f"{_OK} 自检通过", "32") if passed else _c(f"{_NO} 存在失败样本", "31"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
