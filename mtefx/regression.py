"""mtefx 统一回归门禁 —— 把「硬结构覆盖」检查收拢成单条 CI 命令。

历史上本引擎的 MTEF→OMML 全链路只在**简单中考语料**和**合成 MathML** 上
被测过，MTEF 二进制解析器从未见过 MATRIX(5)/PILE(4)/TM_INTEG/TM_SUM/TM_LDIV
这类硬记录的真实字节。本门禁覆盖这一盲区：

  1. 合成硬结构 OLE 电池  (mtefx._synthesize_ole, 18 例)
     逐字节逆向编码真实 MTEF v5 记录（matrix/pile/integ/sum/prod/ldiv/root/
     subsup），包成真实 OLE，喂 `extract_mtef` → `convert_fused` 全链路，
     校验：二进制记录解码器确实看到硬结构、OMML 形状正确、无退化空壳。
  2. 真实 OLE 二进制 fixture (mtefx.test_web_ole_fixtures, 3 例)
     mathtypejx 仓库公开的真实高考物理 OLE，验证引擎能从真实二进制解出
     MTEF 并产出合法可重解析的 OMML。

更重的「全量语料 OMML md5 摘要零回归」由 `mtefx._regress_corpus` 单独跑
（35 zip / 34233 公式，修复 matrix.xsl 后摘要 = f9799cae303778344028c5fcf20a2362）。

用法：
    python -m mtefx.regression            # 跑全部
    python -m mtefx.regression --no-synth # 跳过合成电池
    python -m mtefx.regression --no-web  # 跳过真实 OLE fixture
退出码：0 = 全绿，1 = 任一子检查失败。
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="mtefx 统一回归门禁")
    ap.add_argument("--no-synth", action="store_true", help="跳过合成硬结构 OLE 电池")
    ap.add_argument("--no-web", action="store_true", help="跳过真实 OLE fixture")
    args = ap.parse_args(argv)

    failures: list[str] = []

    if not args.no_synth:
        from mtefx import _synthesize_ole
        print("=" * 72)
        print("① 合成硬结构 OLE 电池")
        print("=" * 72)
        try:
            _rows, bad = _synthesize_ole.main()
        except Exception as exc:  # 不要让子模块异常吞掉整个门禁
            failures.append(f"合成电池异常: {type(exc).__name__}: {exc}")
        else:
            if bad:
                failures.append(f"合成电池 {len(bad)} 例失败: {bad}")

    if not args.no_web:
        from mtefx import test_web_ole_fixtures
        print("\n" + "=" * 72)
        print("② 真实 OLE 二进制 fixture")
        print("=" * 72)
        try:
            rc = test_web_ole_fixtures.main()
        except Exception as exc:
            failures.append(f"真实 OLE 异常: {type(exc).__name__}: {exc}")
        else:
            if rc != 0:
                failures.append("真实 OLE fixture 存在 FAIL")

    print("\n" + "=" * 72)
    if failures:
        print("REGRESSION FAILED:")
        for f in failures:
            print("  ✗", f)
        return 1
    print("REGRESSION PASSED —— 合成硬结构电池 + 真实 OLE fixture 全部绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
