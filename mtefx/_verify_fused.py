"""
融合 MVP 验证：OMML 级一致性（硬闸门）+ 端到端提速（决策数据）。

对比两条链路在 33 个 transpect 真实样本上的 OMML 产出：
  · 当前：convert() → mathml_to_omml_element(str)   （docxconv 实际用的路径）
  · 融合：convert_fused(blob)                        （Element 串联，本 MVP）

OMML 级逐字节（canonical）一致 = 0 回归；再量化端到端提速。
"""

from __future__ import annotations

import time

from lxml import etree

from mtefx.engine import convert, iter_fixtures
from mtefx.omml import mathml_to_omml_element
from mtefx._fused import convert_fused


def _canon(el: etree._Element) -> str:
    return etree.canonicalize(el)


def main() -> int:
    samples = list(iter_fixtures("all"))
    if not samples:
        print("未找到样本")
        return 2

    n = len(samples)
    mism = []          # OMML 级不一致的样本
    cur_ok = fused_ok = 0

    N = 5  # 每条链路重复 N 次取端到端均值
    t_cur_total = 0.0
    t_fused_total = 0.0

    for name, blob in samples:
        # ── 当前路径 ──
        cur_el = None
        for _ in range(N):
            t0 = time.perf_counter()
            r = convert(blob)
            if r.ok:
                try:
                    el = mathml_to_omml_element(r.mathml)
                    cur_el = el
                except Exception:
                    cur_el = None
            t_cur_total += time.perf_counter() - t0
        if cur_el is not None:
            cur_ok += 1

        # ── 融合路径 ──
        fused_el = None
        for _ in range(N):
            t0 = time.perf_counter()
            status, el, _, _, _ = convert_fused(blob)
            if status == "ok" and el is not None:
                fused_el = el
            t_fused_total += time.perf_counter() - t0
        if fused_el is not None:
            fused_ok += 1

        # ── OMML 级比对（硬闸门）──
        if cur_el is None and fused_el is None:
            continue  # 两边都失败/空壳，等价
        if cur_el is None or fused_el is None:
            mism.append((name, "单边缺失", cur_el is not None, fused_el is not None))
            continue
        if _canon(cur_el) != _canon(fused_el):
            mism.append((name, "OMML 不一致", True, True))

    # ── 汇总 ──
    print(f"样本数: {n}")
    print(f"  当前路径 OMML 成功: {cur_ok}/{n}")
    print(f"  融合路径 OMML 成功: {fused_ok}/{n}")
    print(f"  OMML 级不一致(硬闸门): {len(mism)} 个")
    for m in mism[:20]:
        print(f"    · {m[0]}: {m[1]} (cur={m[2]} fused={m[3]})")

    if n:
        cur_per = (t_cur_total / N) / n * 1000  # ms/公式
        fused_per = (t_fused_total / N) / n * 1000
        speedup = cur_per / fused_per if fused_per else 0
        print(f"\n端到端(含 parse+build+XSLT+OMML, {N}轮均值):")
        print(f"  当前路径: {cur_per:.2f} ms/公式")
        print(f"  融合路径: {fused_per:.2f} ms/公式")
        print(f"  提速: {speedup:.3f}x  ({(1 - fused_per/cur_per)*100:+.1f}%)")

    passed = len(mism) == 0
    print(f"\n{'✓ 融合 OMML 级零回归' if passed else '✗ 融合存在 OMML 级回归'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
