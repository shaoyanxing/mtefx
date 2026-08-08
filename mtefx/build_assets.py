"""
资产构建器 —— 把调研结论固化成可重复执行的构建步骤。

产出两份资产：

1. ``assets/xslt_fast/``
   mathtypejx 内置 XSLT 的加速版本。原版 ``xsl/map-fonts.xsl`` 里有一个全局
   ``<xsl:variable name="font-maps">``，用 31 次 ``document()`` 加载共约 348 KB
   的 fontmap XML。它是全局变量，**每转换一个公式就全量重新加载并解析一次**。

   而这些 fontmap 实际上**从未生效**：查表语句写的是
   ``$font-maps/*[tr:symbol-map-base-uri-to-name(.) = $fontfamily][1]``，
   使用了自定义函数调用语法，但样式表声明的是 XSLT 1.0 —— 1.0 不支持自定义函数
   （其 util/symbol-map-base-uri-to-name.xsl 自己都注明"必须用 call-template"）。
   于是该 XPath 静默失配，永远走 otherwise 空分支。

   实测（transpect 自带 33 个真实样本）：剥离全部 document() 后
   93.49 ms/公式 → 2.83 ms/公式（33x），且 33/33 输出**字节级完全一致**。

2. ``assets/fontmap.json``
   把 transpect 的 fontmap 表编译成 Python dict。既然 XSLT 侧的查表是坏的，
   就把这份资产挪到 Python 侧真正用起来，作为字符映射的兜底（见 fontmap.py）。

用法::

    python -m mtefx.build_assets            # 构建
    python -m mtefx.build_assets --verify   # 构建并跑一致性校验
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"
XSLT_FAST = ASSETS / "xslt_fast"
FONTMAP_JSON = ASSETS / "fontmap.json"

# 这些字体表在 XSLT 里被加载但从不命中；即便将来修好查表，
# 它们对数学公式也无意义（装饰/图标字体），保留在 JSON 兜底表中即可。
_DOC_CALL = re.compile(r"[ \t]*<xsl:copy-of\s+select=\"document\([^)]*\)\"\s*/>\r?\n")


def _mathtypejx_xslt_dir() -> Path:
    """定位已安装 mathtypejx 的内置 XSLT 目录。"""
    try:
        import mathtypejx.mtef.mathml as _m
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "未找到 mathtypejx，请先安装：pip install mathtypejx"
        ) from exc
    d = Path(_m.__file__).resolve().parent / "xslt"
    if not (d / "transform.xsl").exists():
        raise SystemExit(f"mathtypejx 内置 XSLT 结构不符合预期：{d}")
    return d


def build_xslt_fast(verbose: bool = True) -> Path:
    """复制一份 XSLT 并剥离全部无效的 document() 加载。"""
    src = _mathtypejx_xslt_dir()
    XSLT_FAST.parent.mkdir(parents=True, exist_ok=True)
    # 用覆盖式复制而非先删后建：某些受限环境（沙箱/无回收站）下删除会失败。
    # fontmaps 目录仍需复制：fontmap.json 由它编译而来，且便于将来修复查表。
    shutil.copytree(src, XSLT_FAST, dirs_exist_ok=True)

    mf = XSLT_FAST / "xsl" / "map-fonts.xsl"
    text = mf.read_text(encoding="utf-8")
    before = text.count("document(")
    text = _DOC_CALL.sub("", text)
    after = text.count("document(")
    mf.write_text(text, encoding="utf-8")

    if verbose:
        print(f"[xslt_fast] 剥离 document() 调用 {before} -> {after}")
    if after:
        print(f"[xslt_fast] 警告：仍残留 {after} 个 document()，请检查 map-fonts.xsl")
    return XSLT_FAST


def build_fontmap(verbose: bool = True) -> Path:
    """把 transpect fontmap XML 编译成 {字体名: {码位: 字符}} 的 JSON。"""
    from lxml import etree

    fm_dir = XSLT_FAST / "xsl" / "fontmaps"
    if not fm_dir.is_dir():
        raise SystemExit("请先执行 build_xslt_fast()")

    table: dict[str, dict[str, str]] = {}
    for f in sorted(fm_dir.glob("*.xml")):
        try:
            root = etree.parse(str(f)).getroot()
        except Exception:
            continue
        # 字体名优先取 @mathtype-name，否则用文件名
        name = root.get("mathtype-name") or f.stem
        entries: dict[str, str] = {}
        for s in root.iter("symbol"):
            num, ch = s.get("number"), s.get("char")
            if num and ch:
                entries[num.upper().zfill(4)] = ch
        if entries:
            table[name] = entries

    FONTMAP_JSON.write_text(
        json.dumps(table, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    if verbose:
        total = sum(len(v) for v in table.values())
        print(f"[fontmap] {len(table)} 张字体表, {total} 条映射 -> {FONTMAP_JSON.name}")
    return FONTMAP_JSON


def verify() -> bool:
    """用 transpect 自带的 33 个真实样本校验：加速版输出必须与原版字节级一致。"""
    from mtefx.engine import _FIXTURES, iter_fixtures

    samples = list(iter_fixtures())
    if not samples:
        print(f"[verify] 跳过：未找到 fixtures（预期在 {_FIXTURES}）")
        return True

    import time

    import mathtypejx.mtef.mathml as M

    from mtefx.engine import _install_fast_xslt

    orig = M._xslt_transform
    baseline = {}
    t0 = time.perf_counter()
    for name, data in samples:
        baseline[name] = M.mtef_to_mathml(data)
    t_base = (time.perf_counter() - t0) / len(samples) * 1000

    _install_fast_xslt()
    fast = {}
    t0 = time.perf_counter()
    for name, data in samples:
        fast[name] = M.mtef_to_mathml(data)
    t_fast = (time.perf_counter() - t0) / len(samples) * 1000
    M._xslt_transform = orig

    diff = [n for n in baseline if baseline[n] != fast[n]]
    print(f"[verify] 原版 {t_base:7.2f} ms/公式")
    print(f"[verify] 加速 {t_fast:7.2f} ms/公式   提速 {t_base / max(t_fast, 1e-9):.1f}x")
    if diff:
        print(f"[verify] ✗ 输出不一致 {len(diff)}/{len(samples)}: {', '.join(diff[:8])}")
        return False
    print(f"[verify] ✓ {len(samples)}/{len(samples)} 字节级一致")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="构建 mtefx 加速资产")
    ap.add_argument("--verify", action="store_true", help="构建后跑一致性校验")
    args = ap.parse_args(argv)

    build_xslt_fast()
    build_fontmap()
    if args.verify:
        return 0 if verify() else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
