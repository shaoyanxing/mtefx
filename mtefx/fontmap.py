"""
字符兜底映射 —— 把 transpect 的 fontmap 资产在 Python 侧真正用起来。

背景：mathtypejx 的 XSLT 里虽然加载了 31 张 fontmap，但查表语句用的是
XSLT 1.0 不支持的自定义函数语法，实际从未命中（详见 build_assets.py）。
于是 chars.py 内置表覆盖不到的码位就会以**私用区（PUA）字符**原样漏进 MathML，
在 Word / 浏览器里渲染成豆腐块。

本模块把 transpect 的 3600+ 条映射编译成 dict，对产出的 MathML 做一次
PUA 扫描替换。实测有效例子（MathType MTCode 编码的间距符）::

    U+EB01 → U+200B  零宽空格
    U+EB02 → U+2009  细空格
    U+EB04 → U+2004  三分之一空格

这些间距符在中文试卷公式里非常常见。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_FONTMAP_JSON = Path(__file__).resolve().parent / "assets" / "fontmap.json"

# Unicode 私用区
_PUA_RANGES = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))

# 查表优先级：MathType 自家编码优先，其次通用数学符号字体
_PREFERRED = (
    "MathType_MTCode",
    "MT_Symbol",
    "Symbol",
    "MT_Extra",
    "Math1",
    "Euclid_Symbol",
    "Euclid_Math_One",
    "Euclid_Math_Two",
    "Euclid_Extra",
)


def is_pua(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in _PUA_RANGES)


@lru_cache(maxsize=1)
def _tables() -> dict[str, dict[str, str]]:
    if not _FONTMAP_JSON.exists():
        return {}
    try:
        return json.loads(_FONTMAP_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


@lru_cache(maxsize=1)
def _flat() -> dict[int, str]:
    """展平成 {码位: 目标字符}，按 _PREFERRED 顺序解决冲突。"""
    tables = _tables()
    order = [n for n in _PREFERRED if n in tables]
    order += [n for n in tables if n not in _PREFERRED]

    flat: dict[int, str] = {}
    for name in reversed(order):  # 低优先级先写，高优先级后覆盖
        for num, ch in tables[name].items():
            try:
                cp = int(num, 16)
            except ValueError:
                continue
            if ch:
                flat[cp] = ch
    return flat


def lookup(codepoint: int) -> str | None:
    """查单个码位的 Unicode 等价字符。"""
    return _flat().get(codepoint)


def repair_pua(text: str) -> tuple[str, int, int]:
    """替换字符串中的私用区字符。

    Returns:
        (修复后文本, 命中替换数, 仍未解决数)
    """
    if not text:
        return text, 0, 0

    table = _flat()
    out: list[str] = []
    fixed = unresolved = 0
    changed = False

    for ch in text:
        cp = ord(ch)
        if is_pua(cp):
            rep = table.get(cp)
            if rep:
                out.append(rep)
                fixed += 1
                changed = True
                continue
            unresolved += 1
        out.append(ch)

    return ("".join(out) if changed else text), fixed, unresolved


def stats() -> dict[str, int]:
    t = _tables()
    return {"tables": len(t), "entries": sum(len(v) for v in t.values())}


def repair_pua_element(root, *, _cache: dict = {}) -> tuple[int, int]:
    """在 lxml Element 上原地修复私用区字符（与 :func:`repair_pua` 等价）。

    为什么需要 Element 版：序列化后的 MathML 会把非 ASCII 字符写成
    ``&#xEB04;`` 形式，字符串版 :func:`repair_pua` 扫描的是「字符」而非
    「实体引用」，于是扫不到 PUA 码位、修复失效。Element 上字符以真实码位
    存储，修复与序列化彻底解耦，且省去一次字符串往返。

    Returns:
        (命中替换数, 仍未解决数)
    """
    if root is None:
        return 0, 0
    table = _flat()
    if not table:
        return 0, 0

    fixed = unresolved = 0

    def _fix(text: str | None) -> tuple[str | None, bool]:
        nonlocal fixed, unresolved
        if not text:
            return text, False
        out: list[str] = []
        changed = False
        for ch in text:
            cp = ord(ch)
            if is_pua(cp):
                rep = table.get(cp)
                if rep:
                    out.append(rep)
                    fixed += 1
                    changed = True
                    continue
                unresolved += 1
            out.append(ch)
        return ("".join(out) if changed else text), changed

    for el in root.iter():
        el.text, _ = _fix(el.text)
        el.tail, _ = _fix(el.tail)
    return fixed, unresolved
