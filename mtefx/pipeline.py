"""
批处理管线 —— 去重 + 进程池 + docx zip 直读。

三条吞吐优化，收益从大到小：

1. **内容级去重**：教辅/试卷语料里公式高度重复（``v``、``t``、``Δx``、``m/s``）。
   以剥壳后 MTEF 净荷的 blake2b 摘要为 key 缓存，命中率常见 30–60%。
2. **进程池**：MTEF 解析是纯 Python CPU-bound，受 GIL 限制，必须用进程而非线程。
   每个进程在 initializer 里各自编译一份 XSLT（lxml 的 XSLT 对象跨线程不安全）。
3. **zip 直读**：docx 就是 zip，直接 ``read()`` 拿 bytes，不解压落盘。
   没有 ``word/embeddings/`` 条目的文件整个跳过。
"""

from __future__ import annotations

import os
import zipfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from mtefx.engine import FormulaResult, convert, digest_of, extract_mtef

# docx 内嵌 OLE 对象的存放位置
_EMBED_PREFIX = "word/embeddings/"
_OLE_SUFFIXES = (".bin",)


@dataclass
class DocReport:
    """单个 docx 的转换报告。"""

    path: str
    total: int = 0
    ok: int = 0
    failed: int = 0
    cache_hits: int = 0
    elapsed_ms: float = 0.0
    by_status: Counter = field(default_factory=Counter)
    by_version: Counter = field(default_factory=Counter)
    pua_fixed: int = 0
    pua_unresolved: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    error: str | None = None

    @property
    def success_rate(self) -> float:
        return self.ok / self.total if self.total else 0.0


def iter_docx_oles(docx_path: str | Path) -> Iterator[tuple[str, bytes]]:
    """从 docx 里直接读出内嵌 OLE 对象，不解压落盘。"""
    with zipfile.ZipFile(docx_path) as zf:
        names = [
            n
            for n in zf.namelist()
            if n.startswith(_EMBED_PREFIX) and n.lower().endswith(_OLE_SUFFIXES)
        ]
        for n in names:  # 短路：没有 embeddings 的文档自然产出空序列
            yield n, zf.read(n)


def convert_docx(
    docx_path: str | Path,
    *,
    cache: dict[str, FormulaResult] | None = None,
    **kw,
) -> DocReport:
    """转换单个 docx 里的全部 MathType 公式。"""
    import time

    rep = DocReport(path=str(docx_path))
    t0 = time.perf_counter()
    local_cache = cache if cache is not None else {}

    try:
        items = list(iter_docx_oles(docx_path))
    except Exception as exc:
        rep.error = f"{type(exc).__name__}: {exc}"
        rep.elapsed_ms = (time.perf_counter() - t0) * 1000
        return rep

    for name, blob in items:
        rep.total += 1

        # 去重：用剥壳后的净荷做 key
        payload = extract_mtef(blob)
        key = digest_of(payload) if payload else None
        if key and key in local_cache:
            res = local_cache[key]
            rep.cache_hits += 1
        else:
            res = convert(blob, **kw)
            if key:
                local_cache[key] = res

        rep.by_status[res.status] += 1
        if res.mtef_version:
            rep.by_version[f"v{res.mtef_version}"] += 1
        rep.pua_fixed += res.pua_fixed
        rep.pua_unresolved += res.pua_unresolved

        if res.ok:
            rep.ok += 1
        else:
            rep.failed += 1
            if len(rep.failures) < 20:
                rep.failures.append((name, res.reason or res.status))

    rep.elapsed_ms = (time.perf_counter() - t0) * 1000
    return rep


def _worker_init() -> None:
    """每个子进程各自编译一份 XSLT，避免跨进程共享不安全对象。"""
    from mtefx.engine import _install_fast_xslt

    _install_fast_xslt()


def _worker(path: str) -> DocReport:
    return convert_docx(path)


def convert_many(
    paths: Iterable[str | Path],
    *,
    workers: int | None = None,
) -> list[DocReport]:
    """多进程批量转换。

    Args:
        paths: docx 路径序列。
        workers: 进程数，默认取 CPU 核数。
    """
    paths = [str(p) for p in paths]
    if not paths:
        return []

    n = workers or os.cpu_count() or 4
    if n <= 1 or len(paths) == 1:
        _worker_init()
        return [convert_docx(p) for p in paths]

    # chunksize 调大以减少 IPC 往返
    chunk = max(1, min(16, len(paths) // (n * 4) or 1))
    with ProcessPoolExecutor(max_workers=n, initializer=_worker_init) as ex:
        return list(ex.map(_worker, paths, chunksize=chunk))


def summarize(reports: list[DocReport]) -> dict:
    """汇总多份报告。"""
    total = sum(r.total for r in reports)
    ok = sum(r.ok for r in reports)
    hits = sum(r.cache_hits for r in reports)
    status: Counter = Counter()
    version: Counter = Counter()
    for r in reports:
        status.update(r.by_status)
        version.update(r.by_version)
    return {
        "files": len(reports),
        "formulas": total,
        "ok": ok,
        "failed": total - ok,
        "success_rate": ok / total if total else 0.0,
        "cache_hits": hits,
        "cache_hit_rate": hits / total if total else 0.0,
        "elapsed_ms": sum(r.elapsed_ms for r in reports),
        "by_status": dict(status),
        "by_version": dict(version),
        "pua_fixed": sum(r.pua_fixed for r in reports),
        "pua_unresolved": sum(r.pua_unresolved for r in reports),
        "file_errors": [r.path for r in reports if r.error],
    }
