"""
文档级吞吐基准 —— 验证去重缓存与进程池的真实收益。

仓库里只有裸 ``.bin`` 样本，没有 docx。本模块用这些样本**合成** docx
（fixtures 本身就是合法的 OLE 复合文档，可以直接放进 ``word/embeddings/``），
从而在没有真实语料的情况下端到端压测 :func:`mtefx.convert_docx` /
:func:`mtefx.convert_many`。

    python -m mtefx.bench                    # 默认 24 份文档 × 40 公式
    python -m mtefx.bench --docs 60 --per-doc 80 --dup 0.7

``--dup`` 模拟教辅语料的公式重复率：0.7 表示 70% 的公式抽自一个很小的
高频集合（``v``、``t``、``Δx`` 这类），用来体现去重缓存的价值。
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from mtefx.engine import iter_fixtures
from mtefx.pipeline import convert_many, summarize

# 最小可用的 docx 骨架 —— 只需能被 zipfile 打开且含 word/embeddings/
_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="bin" ContentType="application/vnd.openxmlformats-officedocument.oleObject"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOCUMENT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>mtefx bench</w:t></w:r></w:p></w:body>
</w:document>"""


def make_docx(path: Path, oles: list[bytes]) -> None:
    """合成一个含 N 个内嵌 OLE 公式的 docx。"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("word/document.xml", _DOCUMENT)
        for i, blob in enumerate(oles, 1):
            zf.writestr(f"word/embeddings/oleObject{i}.bin", blob)


def build_corpus(
    dest: Path, docs: int, per_doc: int, dup_ratio: float, seed: int = 42
) -> list[Path]:
    """生成合成语料。``dup_ratio`` 越高，高频公式占比越大。"""
    rng = random.Random(seed)
    pool = [b for _, b in iter_fixtures("all")]
    if not pool:
        raise RuntimeError("未找到样本，请先克隆 transpect/mathtype-extension 到 vendor/")

    hot = pool[: max(1, len(pool) // 8)]  # 高频小集合
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for d in range(docs):
        oles = [
            rng.choice(hot) if rng.random() < dup_ratio else rng.choice(pool)
            for _ in range(per_doc)
        ]
        p = dest / f"doc{d:03d}.docx"
        make_docx(p, oles)
        out.append(p)
    return out


def _run(paths: list[Path], workers: int) -> tuple[dict, float]:
    t0 = time.perf_counter()
    reps = convert_many(paths, workers=workers)
    return summarize(reps), (time.perf_counter() - t0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="mtefx 文档级吞吐基准")
    ap.add_argument("--docs", type=int, default=24)
    ap.add_argument("--per-doc", type=int, default=40)
    ap.add_argument("--dup", type=float, default=0.6, help="公式重复率 0~1")
    ap.add_argument("--workers", type=int, default=0, help="0=CPU 核数")
    ap.add_argument("--keep", metavar="DIR", help="保留合成语料到指定目录")
    args = ap.parse_args(argv)

    tmp = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="mtefx_bench_"))
    try:
        print(
            f"合成语料: {args.docs} 份 docx × {args.per_doc} 公式 "
            f"= {args.docs * args.per_doc} 个，重复率 {args.dup:.0%}"
        )
        paths = build_corpus(tmp, args.docs, args.per_doc, args.dup)
        size = sum(p.stat().st_size for p in paths) / 1e6
        print(f"语料体积 {size:.1f} MB  路径 {tmp}\n")

        n_cpu = os.cpu_count() or 4
        workers = args.workers or n_cpu
        print(f"{'配置':<16}{'耗时 s':>9}{'公式/秒':>11}{'去重命中':>11}{'成功率':>9}")
        print("-" * 58)

        s1, t1 = _run(paths, 1)
        print(
            f"{'单进程':<16}{t1:9.2f}{s1['formulas'] / t1:11.0f}"
            f"{s1['cache_hit_rate']:10.0%}{s1['success_rate']:10.0%}"
        )

        sn, tn = _run(paths, workers)
        print(
            f"{f'进程池 ×{workers}':<16}{tn:9.2f}{sn['formulas'] / tn:11.0f}"
            f"{sn['cache_hit_rate']:10.0%}{sn['success_rate']:10.0%}"
        )

        print("-" * 58)
        print(f"并行加速比 {t1 / tn:.2f}x（CPU {n_cpu} 核）")
        print(f"公式总数 {s1['formulas']}，唯一解析 {s1['formulas'] - s1['cache_hits']}")
        print(f"版本分布 {s1['by_version']}   状态分布 {s1['by_status']}")
        print(
            f"PUA 修复 {s1['pua_fixed']} 个，残留 {s1['pua_unresolved']} 个"
            + (f"，文件级错误 {len(s1['file_errors'])}" if s1["file_errors"] else "")
        )

        # 去重关掉会慢多少：用重复率 0 的语料做对照
        alt = tmp / "nodup"
        alt_paths = build_corpus(alt, max(2, args.docs // 3), args.per_doc, 0.0, seed=7)
        s0, t0_ = _run(alt_paths, workers)
        print(
            f"\n对照（重复率 0%）: {s0['formulas']} 公式 / {t0_:.2f}s "
            f"= {s0['formulas'] / t0_:.0f} 公式/秒，命中 {s0['cache_hit_rate']:.0%}"
        )
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
