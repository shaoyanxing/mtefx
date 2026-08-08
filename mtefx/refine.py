"""
transpect 精修层 —— 出版级 MathML 后处理（可选）。

transpect/mathtype-extension 顶层 ``xsl/`` 有 6 个 mathtypejx **没有**的后处理
样式表，负责把结构正确但略显粗糙的 MathML 打磨到出版质量：

===================  ====================================================
whitespace-handle    空白节点归一
split-elements       拆分多字符 token（如 ``<mi>sin</mi>`` 的处理）
combine-elements     合并相邻同类 token（数字、标识符）
operator-elements    ``<mi>`` / ``<mo>`` 判定修正，补运算符属性
repair-subsup        修复上下标嵌套结构（唯一的 XSLT 2.0）
clean-up             移除冗余 mrow / mstyle
===================  ====================================================

⚠️ 重要实测结论：这些样式表虽然声明 ``version="1.0"``，实际使用了
``mode="#current"``、``select="@*, node()"`` 等 XSLT 2.0+ 语法，
**lxml 全部编译失败**，必须用 Saxon（Java）执行。

性能约束：JVM 冷启动约 200–400 ms。所以本模块**只用批量目录模式** ——
一次 JVM 调用处理整个目录的 N 个文件，6 个阶段共 6 次 JVM。
绝不 per-formula 起进程（那是 Ruby gem 包装方案的经典性能灾难）。

因此精修层适合**离线批处理 / 出版流水线**，不适合单公式在线请求。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SAXON = _ROOT / "vendor" / "saxon"
_DEFAULT_XSL = _ROOT / "vendor" / "mathtype-extension" / "xsl"

# 精修管线顺序（与 transpect XProc 中的阶段顺序一致）
PIPELINE = (
    "whitespace-handle",
    "split-elements",
    "combine-elements",
    "operator-elements",
    "repair-subsup",
    "clean-up",
)

# 阶段文件名 → transpect XProc 里对应的 initial-mode
# （见 xpl/mathtype2mml-declaration-internal.xpl 的各 p:xslt initial-mode）。
# 注意：whitespace-handle.xsl 的入口 mode 是 "handle-whitespace"，文件名≠mode，
# 必须用此表，不能简单用 xsl.stem 当 mode。
STAGE_MODE = {
    "whitespace-handle": "handle-whitespace",
    "split-elements": "split-elements",
    "combine-elements": "combine-elements",
    "operator-elements": "operator-elements",
    "repair-subsup": "repair-subsup",
    "clean-up": "clean-up",
}


@dataclass
class RefineStatus:
    available: bool
    reason: str = ""
    saxon_jars: tuple[str, ...] = ()
    stages: tuple[str, ...] = ()


class SaxonRefiner:
    """基于 Saxon 的批量 MathML 精修器。"""

    def __init__(
        self,
        saxon_dir: str | Path = _DEFAULT_SAXON,
        xsl_dir: str | Path = _DEFAULT_XSL,
        java: str = "java",
        stages: tuple[str, ...] = PIPELINE,
    ):
        self.saxon_dir = Path(saxon_dir)
        self.xsl_dir = Path(xsl_dir)
        self.java = java
        self.stages = stages

    # ── 环境检查 ──────────────────────────────────────────

    def _jars(self) -> list[Path]:
        if not self.saxon_dir.is_dir():
            return []
        return sorted(self.saxon_dir.glob("*.jar"))

    def _classpath(self) -> str:
        # Windows 用 ';'，POSIX 用 ':'
        import os

        return os.pathsep.join(str(j) for j in self._jars())

    def status(self) -> RefineStatus:
        jars = self._jars()
        if not jars:
            return RefineStatus(
                False,
                f"未找到 Saxon jar（预期在 {self.saxon_dir}）。"
                "下载：https://repo1.maven.org/maven2/net/sf/saxon/Saxon-HE/",
            )
        if not any("saxon" in j.name.lower() for j in jars):
            return RefineStatus(False, "jar 目录中缺少 Saxon-HE")
        missing = [s for s in self.stages if not (self.xsl_dir / f"{s}.xsl").exists()]
        if missing:
            return RefineStatus(
                False, f"缺少 transpect 样式表: {', '.join(missing)}（{self.xsl_dir}）"
            )
        if shutil.which(self.java) is None:
            return RefineStatus(False, f"未找到 java 可执行文件: {self.java}")
        return RefineStatus(
            True,
            "就绪",
            tuple(j.name for j in jars),
            tuple(self.stages),
        )

    def available(self) -> bool:
        return self.status().available

    # ── 批量精修 ──────────────────────────────────────────

    def _run_stage(self, xsl: Path, src: Path, dst: Path, timeout: int) -> None:
        dst.mkdir(parents=True, exist_ok=True)
        # 关键：transpect 的每个后处理阶段都有专属入口 mode（见
        # xpl/mathtype2mml-declaration-internal.xpl 的 p:xslt initial-mode）。
        # 若用 Saxon CLI 的默认 #default mode，会因只命中恒等模板而“空转”，
        # 6 个阶段里 4 个根本不执行逻辑。必须显式 -im:<阶段名>。
        mode = STAGE_MODE.get(xsl.stem, xsl.stem)
        cmd = [
            self.java,
            "-cp",
            self._classpath(),
            "net.sf.saxon.Transform",
            f"-im:{mode}",
            f"-s:{src}",
            f"-o:{dst}",
            f"-xsl:{xsl}",
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Saxon 阶段 {xsl.stem} 失败: {(proc.stderr or proc.stdout)[:300]}"
            )

    def refine_batch(
        self,
        mathml_list: list[str],
        *,
        timeout: int = 300,
        skip_on_error: bool = True,
    ) -> list[str]:
        """批量精修。一次 JVM 处理整批，共 len(stages) 次 JVM 调用。

        Args:
            mathml_list: 待精修的 MathML 字符串列表。
            timeout: 单阶段超时（秒）。
            skip_on_error: 某阶段失败时保留上一阶段结果继续，而非整批失败。

        Returns:
            与输入等长的结果列表；某项精修失败则回退为原值。
        """
        if not mathml_list:
            return []
        st = self.status()
        if not st.available:
            raise RuntimeError(st.reason)

        # 临时根用系统 Temp（沙箱 safe-delete 在工作区内会 FAIL_CLOSED 阻断 rmtree，
        # 故不放在工作区）。仅用 2 个目录 s0/s1 乒乓交替，避免 6 阶段 × N 文件产生
        # 大量零散小文件拖慢清理。
        with tempfile.TemporaryDirectory(prefix="mtefx_rb_") as tmp:
            tmp = Path(tmp)
            cur = tmp / "s0"
            cur.mkdir()
            nxt = tmp / "s1"
            names = []
            for i, ml in enumerate(mathml_list):
                fn = f"f{i:06d}.xml"
                names.append(fn)
                (cur / fn).write_text(ml, encoding="utf-8")

            for stage in self.stages:
                nxt.mkdir()
                try:
                    self._run_stage(self.xsl_dir / f"{stage}.xsl", cur, nxt, timeout)
                except Exception:
                    if not skip_on_error:
                        raise
                    # 本阶段失败：丢弃半成品，保留 cur 不变、跳过该阶段
                    shutil.rmtree(nxt, ignore_errors=True)
                    nxt.mkdir()
                    continue
                # 交换：旧 cur 变下一轮的 nxt，先清空其残留
                cur, nxt = nxt, cur
                shutil.rmtree(nxt, ignore_errors=True)

            out = []
            for i, fn in enumerate(names):
                f = cur / fn
                try:
                    out.append(f.read_text(encoding="utf-8") if f.exists() else mathml_list[i])
                except Exception:
                    out.append(mathml_list[i])
            return out
