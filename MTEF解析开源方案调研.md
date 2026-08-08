# MTEF（MathType 公式）解析开源方案调研

> 约束：**拒绝 COM 调用**（不允许 Word.Application / MathType MTAPI / OLE 激活），**优化批量处理效率**
> 调研时间：2026-08-06

---

## 0. 一句话结论

- 三层依赖里**没有一层真的需要 COM**：OLE2 复合文档是纯文件格式；`Equation Native` 流是纯二进制；`MML2OMML.XSL` 是一个静态 XSL 文件。全部可用纯解析库替代。
- **Python 栈首选 `mathtypejx`**（MIT，2026-05 仍在更新，MTEF v3/v5 全覆盖，内置并行）；
  **出版级保真度选 `transpect/mathtype-extension`**（2026-02 仍在提交，唯一还支持 `.wmf` 内嵌 MTEF 的）；
  **极致吞吐选 `mtef-go` 思路**（直出 LaTeX，但需自行补记录类型）。
- 性能量级：直出 LaTeX ≈ **0.05–0.3 ms/公式**；走 XSLT ≈ **2–10 ms/公式**；COM ≈ **100–500 ms/公式**。
  叠加**字节级去重 + 进程池**，万级公式可从「小时级」压到「秒级」。

---

## 1. 开源库全景对比

| # | 项目 | 语言 | 路线 | 输出 | 许可 | 最后更新 | COM | 评价 |
|---|------|------|------|------|------|----------|-----|------|
| 1 | [transpect/mathtype-extension](https://github.com/transpect/mathtype-extension) | Java + XProc/XSLT + JRuby | B | MathML | 开源（le-tex） | **2026-02**，225 commits | 无 | ⭐ **最成熟、生产级**。德国出版社（Carl Hanser、VDE、STM）赞助维护。唯一支持 `.wmf` 内嵌 MTEF。附带 `fontmaps` 字体映射表和 XSpec 测试，是整个生态里质量最高的资产 |
| 2 | [a917470154/mathtypejx](https://github.com/a917470154/mathtypejx) | Python | B | MathML → OMML，直接改写 docx | MIT | **2026-05** | 无* | ⭐ **中文试卷场景最对口**。在 344 份高考物理文档、7888 个 MathType 公式上验证，7886/7888 与 Ruby 基线一致。内置 `parallel=True, max_workers=N` |
| 3 | [jure/mathtype](https://github.com/jure/mathtype) + [mathtype_to_mathml](https://github.com/jure/mathtype_to_mathml) | Ruby + XSLT | B | MTEF-XML / MathML | MIT | 77 commits，维护中 | 无 | 事实上的**参考实现**，上面 1、2 都源自它。XSLT 部分（89% 代码量）值得直接复用；Ruby 运行时是性能包袱 |
| 4 | [zhexiao/mtef-go](https://github.com/zhexiao/mtef-go)（fork: SmallCream） | Go | A | LaTeX | 有 LICENSE | 2020-02，仅 8 commits | 无 | 极快、代码短、易读。但覆盖不全（README 示例输出 `\sqrt[]{...}` 就带瑕疵），矩阵/多行/装饰符支持弱。**适合当"内核骨架"自己扩** |
| 5 | [AndyQsmart/MTEF-py](https://github.com/AndyQsmart/MTEF-py) | Python | A | LaTeX | — | 2022-04，3 commits | 无 | mtef-go 的 Python 移植，自带 OLE 解析。API 极简：`MTEF.OpenBytes(b).Translate()`。适合"只要近似 LaTeX"的场景 |
| 6 | [hiro93/mtef-rs](https://github.com/hiro93/mtef-rs) | Rust | A | LaTeX | **GPL-3.0** | 2019-02，最后一次 commit 就叫 `WIP` | 无 | 半成品。性能天花板最高但没写完，且 GPL 有传染性，商业项目慎入 |
| 7 | [chulei926/MtefParser](https://github.com/chulei926/MtefParser)（fork danielrendall/Metaphor） | Java | B | MTEF-XML | Apache-2.0（拟） | 2021-10 | 无 | 作者自述 "currently doesn't do very much at all"。**不建议直接用**，可作 Java 栈的起点参考 |
| 8 | LibreOffice `starmath/source/mathtype.cxx` | C++ | — | StarMath | MPL-2.0 / Apache-2.0 | 持续维护 | 无 | 唯一**工业级长期维护**的 C++ 实现，跨平台、无 windows.h。**致命限制：只解析 `nVersion <= 3`**，代码里明写 `if (nVersion > 3) return false;` —— MathType 4+ 写出的 MTEF v5（`DSMT4`）直接拒绝。所以 `soffice --convert-to` 对现代 MathType 公式无效 |
| 9 | [rtf2latex2e](https://sourceforge.net/projects/rtf2latex2e/)（[CTAN](https://www.ctan.org/tex-archive/support/rtf2latex2e)） | C | A | LaTeX | GPL | 2.2.3 | 无 | 官方特性写明 "Converts MathType Equations **through version 5**"。2000 年由 MacKichan（Scientific Word 厂商）的 Steve Swanson 贡献 MTEF 支持，是**最老牌的完整 C 实现**。同站点托管着 MTEF 规范文档 |
| 10 | [docx-to-html-mathml-v2](https://www.npmjs.com/package/docx-to-html-mathml-v2) | Node | B | HTML+MathML | — | — | 无 | ⚠️ 底层仍 shell 调 Ruby gem，**每公式一次进程 fork**，性能灾难。不推荐 |
| 11 | `@wiris/mathtype-*` npm 全家桶 | JS | — | — | MIT | 活跃 | — | ❌ **踩坑预警**：这是 Wiris 官方的前端**编辑器**插件，跟 MTEF 二进制解析毫无关系 |

\* mathtypejx 的 OMML 步骤需要 `MML2OMML.XSL`，但那是随 Office 安装的**静态 XSL 文件**（如 `C:\Program Files\Microsoft Office\root\Office16\MML2OMML.XSL`），用 lxml/Saxon 执行即可，**不启动 Office 进程、不走 COM**。仅输出 MathML 时完全不需要它。

### 规范文档（自研必读）
- MTEF v5 规范：https://rtf2latex2e.sourceforge.net/MTEF5.html
- MTEF v3 规范：https://rtf2latex2e.sourceforge.net/MTEF3.html

要点摘录：
- 头部：`版本(1) + 平台(1) + 产品(1) + 主版本(1) + 次版本(1) + app key(变长, null 结尾, 如 "DSMT4") + 方程选项(1)`
- 记录类型：`0=END, 1=LINE, 2=CHAR, 3=TMPL, 4=PILE, 5=MATRIX, 6=EMBELL, 7=RULER, 8=FONT_STYLE_DEF, 9~14=SIZE 系列, 15=COLOR, 16=COLOR_DEF, 17=FONT_DEF, 18=EQN_PREFS, 19=ENCODING_DEF, >=100=FUTURE(带长度前缀)`
- 尺寸单位 = 1/32 印刷点
- ⚠️ 规范里**不含** OLE 存储层。`Equation Native` 流前面还有 28 字节的 `EQNOLEFILEHDR`（`cbHdr(2)=0x1C, version(4), cf(2), cbObject(4), reserved(16)`），要按 `cbHdr` 跳过后才是 MTEF 字节

---

## 2. 按场景选型

| 你的场景 | 推荐 | 理由 |
|---------|------|------|
| Python 栈，docx 批量转原生 Word 公式 | **mathtypejx** | 开箱即用，端到端，有质量校验和失败回退 |
| Python 栈，只要 MathML/LaTeX | **mathtypejx**（截断到 MathML）或 **MTEF-py** | 前者保真，后者轻量 |
| 出版/排版流水线，要求最高保真 | **transpect** | fontmaps + XSpec 测试 + 持续维护；可只抽 `xsl/` + `fontmaps/` 复用 |
| 需要处理 `.wmf` 里的公式 | **transpect**（唯一支持） | 其余方案都只认 `.bin` |
| 高并发服务、追求极限吞吐 | **mtef-go 骨架自行扩展**，做常驻服务 | 无 GC 压力的流式解析，可做到亚毫秒 |
| C/C++ 嵌入 | **rtf2latex2e 的 MTEF 模块** | 完整支持到 v5；LibreOffice 那份只到 v3，不可用 |

**最省事的组合**：`mathtypejx` 打底 → 用本文第 4 节的优化手段包一层 → 对失败样本用 `transpect` 的 XSLT 兜底。

---

## 3. 为什么可以彻底拒绝 COM

常见的 COM 方案（`Word.Application` + OLE 激活 / MathType MTAPI），三个致命问题：仅 Windows、串行单进程、进程易挂死。逐层替换：

| 依赖层 | COM 做法 | 无 COM 替代 |
|--------|---------|------------|
| 读 OLE2 复合文档 | `IStorage` / OLE 激活 | Python `olefile`；Ruby `ruby-ole`；Java POI `POIFS`；Go `mscfb`；Rust `cfb` |
| 取 `Equation Native` 流 | 让 MathType 自己反序列化 | 直接读流，按 `cbHdr` 跳头，剩下就是 MTEF 字节。流名有三种别名：`Equation Native` / `EquationNative` / `Equation`，依次尝试 |
| MathML → OMML | `Word.Application` 打开再存 | 直接用 `MML2OMML.XSL` + lxml/Saxon 做 XSLT 变换 |

> ⚠️ 合规提醒：`MML2OMML.XSL` 属于 Office 分发文件。**不要把它打包进你的产品**，改为运行时让用户配置本机路径（mathtypejx 的 `--xsl` 就是这个设计）。若要完全脱离 Office，就停在 MathML 层，或自写 MathML→OMML 映射。

---

## 4. 效率优化清单（按收益排序）

### 4.1 内容级去重 —— 收益最大，务必先做
教辅/试卷语料里公式高度重复（`v`、`t`、`Δx`、`m/s` 这类）。以 **MTEF 字节的 blake2b 摘要**为 key 做缓存，命中率常见 30–60%，等比例砍掉解析 + XSLT 成本。
```python
key = hashlib.blake2b(mtef_bytes, digest_size=16).digest()
```
注意：要用**剥掉 OLE 头之后的 MTEF 净荷**做 key，OLE 容器字节会带无关差异。

### 4.2 XSLT 只编译一次
`etree.XSLT(etree.parse(xsl_path))` 的结果必须复用。每公式重新 parse XSL 是常见的 10 倍级性能损失。
lxml 的 `XSLT` 对象**跨线程不安全** → 用 `ProcessPoolExecutor(initializer=...)` 每进程建一份。

### 4.3 绝不 per-formula 起进程
Ruby gem / CLI / npm 包装方案最大的坑：每个公式 fork 一次，光进程启动就 20–80 ms。要么改常驻服务批量喂，要么换进程内库。

### 4.4 进程池而非线程池
MTEF 解析是纯 Python CPU-bound，GIL 会锁死。用 `ProcessPoolExecutor(max_workers=os.cpu_count())`，并把 `chunksize` 调到 ≥16 减少 IPC 往返。
（lxml 的 XSLT 执行会释放 GIL，混合负载下线程池也有一定收益，但主体建议进程。）

### 4.5 I/O：直接读 zip，不落盘
`docx` 就是 zip。用 `zipfile.ZipFile` 直接 `read('word/embeddings/oleObject1.bin')` 拿 bytes，`olefile.OleFileIO(BytesIO(data))` 直接解析。不要解压到临时目录。

### 4.6 记录级快速跳过
只关心结构时，`FONT_DEF` / `COLOR_DEF` / `EQN_PREFS` / `RULER` 可直接跳过。`FUTURE`（type ≥ 100）记录**自带长度前缀**，`seek` 跳过即可，别逐字节试探。
`EQN_PREFS` 是 nibble 打包的变长数据，是解析器最容易 over-consume 把公式主体吃掉的地方，务必做边界保护（mathtypejx 专门写了恢复逻辑）。

### 4.7 字符映射表预建为模块级 dict
MTCode → Unicode 的映射用 `dict` 常量，不要 if-else 链或每次构造。

### 4.8 短路优化
- docx 里没有 `word/embeddings/` 前缀条目 → 整个文件直接跳过
- OLE 流名三选一，命中即停
- MTEF 头部 version 字节不是 3 或 5 → 直接判失败，不进主循环

### 4.9 失败隔离 + 超时
单个公式解析异常不能拖垮整篇。包 try/except + 保留原 OLE 对象 + 记录 per-formula 报告（mathtypejx 就是这么设计的，可直接抄）。

### 4.10 再快一个量级：热路径下沉
把解析核心搬到 Go/Rust，做成常驻 HTTP/gRPC 服务或 Python 扩展（PyO3 / cgo）。MTEF 是流式定长记录，天然适合零拷贝解析，能到亚毫秒。

### 预期收益
| 配置 | 万级公式耗时（估算） |
|------|---------------------|
| COM 串行 | 20–80 分钟 |
| 纯解析串行（B 路线） | 20–100 秒 |
| + 去重（50% 命中） | 10–50 秒 |
| + 8 进程并行 | **2–8 秒** |
| A 路线（直出 LaTeX）+ 并行 | **< 1 秒** |

---

## 5. 落地步骤建议

1. **拿 100 个真实样本建基准集**，人工核对期望输出，作为回归用例。
2. `pip install mathtypejx`（或 `pip install .` 源码装），先跑通端到端。
3. 用 `mtef_fast_pipeline.py`（本次同时产出）套上去重 + 进程池，先测吞吐和命中率。
4. 统计失败样本的记录类型分布，针对性打补丁 —— 通常集中在矩阵、长除法、装饰符、text-mode 单位这几类。
5. 若 Python 吞吐仍不够，把 `parse_mtef` 换成 Go/Rust 常驻服务，管道其余部分不动。

---

## 6. 避坑清单

- ❌ 别用 `@wiris/mathtype-*` npm 包 —— 是前端编辑器，不解析二进制
- ❌ 别指望 LibreOffice 转 MathType 4+ 公式 —— 源码里硬编码 `nVersion > 3` 就 return false
- ❌ 别用 shell 包装 Ruby gem 的 Node 方案 —— 每公式 fork
- ⚠️ `mtef-rs` 是 GPL-3.0 且未完成
- ⚠️ `MML2OMML.XSL` 不要打包分发
- ⚠️ WMF 图片里也可能藏着 MTEF（老文档常见），只扫 `word/embeddings/*.bin` 会漏；目前只有 transpect 支持 `.wmf`
