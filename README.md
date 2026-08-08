# mtefx — MathType 公式解析引擎 · 零 COM 依赖

[English Readme](https://github.com/shaoyanxing/mtefx/edit/main/README_en.md)

> 把 Word `.docx` 里的 MathType OLE 公式（`.bin`）转换为原生 OMML 可编辑公式，
> **全程零 COM、零 Word/Excel 依赖**，纯 Python + XSLT。

## 为什么做这个

Word 文档中的 MathType 公式以 OLE 对象嵌入（`word/embeddings/*.bin`），想批量转成
Word 原生 OMML 公式，传统方案只有两条路：

1. **COM 路线** — 调 Word 的 `Equation.OMath` COM 对象。需要本机安装 Office、
   交互式桌面会话、单进程串行、无法横向扩展。与服务器批量解析场景天然互斥。
2. **B 路线** - olefile + XSLT，进程内完成，配合多进程池线性扩展。

本项目走 **olefile + XSLT 路线**，在 [`mathtypejx`](https://github.com/a917470154/mathtypejx)
和 [`transpect/mathtype-extension`](https://github.com/transpect/mathtype-extension)
两条上游资产之上做加速与增强。

## 转换管线

```
.docx (zip)
  │  zipfile 直读 word/embeddings/*.bin（不解压落盘）
  ▼
OLE 二进制
  │  olefile 剥 OLE 壳 + 28 字节 EQNOLEFILEHDR 头
  ▼
MTEF 净荷（blake2b 指纹去重，语料命中率 30–60%）
  │  probe_version() 读首字节分诊
  ├─ v3 → wrap_v3_slot() 修复结构 → XSLT
  └─ v5 → mathtypejx 原生解析 → XSLT
  ▼
MathML
  │  命名空间归一 + fontmap PUA 字符修复（3648 条映射，28 张字体表）
  ▼
FormulaResult
  │  MML2OMML.XSL（lxml 编译一次缓存，进程级复用）
  ▼
OMML（原位替换 docx 里的 OLE 对象）
```

## 四项核心改进

| 改进 | 前 | 后 | 说明 |
|------|----|----|------|
| **XSLT 加速** | 93.49 ms/公式 | **2.83 ms/公式**（33×） | 剥离样式表中每公式重载 348 KB fontmap 的 `document()` 调用，改为单次编译缓存。输出字节级一致。 |
| **MTEF v3 修复** | 1/11 可用 | **11/11** | mathtypejx 对 v3 产出 `<mtef><full/><tmpl/></mtef>`，但 XSLT 期望 `<mtef><full/><slot><tmpl/></slot></mtef>`。`wrap_v3_slot()` 补上缺失包裹。 |
| **空壳哨兵** | 静默丢公式 | 显式判失败 | mathtypejx 对 v3 返回 `<math .../>` 空壳，不抛异常也不返 `None`。`is_empty_mathml()` 显式检测并标记 `status="empty"`。 |
| **fontmap 字符兜底** | 私用区字符漏进输出 | 修复 | mathtypejx 内置 fontmap 因 XSLT 1.0 下非法函数被静默跳过。改为 Python 侧 3648 条映射（28 张表），如 `U+EB04→U+2004`。 |

## 实测性能

### 真实语料（中考真题，35 个 zip / 34233 个 OLE 公式）

| 配置 | 耗时 | 吞吐 | 加速比 |
|------|------|------|--------|
| 原版串行（字符串路径） | 93.7s | 366 公式/秒 | 1.00× |
| 融合路径串行 | 64s | 535 公式/秒 | 1.46× |
| 融合 + 并行(12w) + 进程级缓存 | **13.04s** | **2625 公式/秒** | **7.18×** |

### 树莓派 4B（4 核 ARM Cortex-A72，240 个合成硬公式）

| 配置 | ms/公式 | 公式/秒 | 加速比 |
|------|---------|---------|--------|
| 原版串行 | 7.57 | 132 | 1.00× |
| 优化串行（关 logging） | 6.44 | 155 | 1.18× |
| 并行 3 workers | **2.95** | **339** | **2.57×** |

> 树莓派 4B 最优并行配置为 3 workers（4 workers 因内存带宽退化）。

### 精度

| 测试集 | 数量 | 结果 |
|--------|------|------|
| 真实 OLE (mathtypejx fixtures) | 3 | 3/3 OMML 字节级一致 |
| Web OLE fixtures | 3 | 3/3 一致 |
| 合成硬公式（矩阵/积分/求和/分段/方程组/n次根/长除法/深度嵌套） | 18 | 18/18 一致 |
| 真实语料全量 | 34233 | **0 失败、0 跳过** |
| 构造硬公式电池 | 58 | 58/58 通过 |

## 快速开始

```python
from mtefx import convert, convert_docx, convert_many, FormulaResult

# 单公式：OLE 字节（来自 docx 嵌入 / .bin / 裸 MTEF）
res: FormulaResult = convert(ole_bytes)
if res.ok:
    print(res.mathml)
    print("PUA 修复:", res.pua_fixed, "未映射:", res.pua_unresolved)

# 文档级：直接读 docx 内嵌公式
rep = convert_docx("试卷.docx")
print(f"{rep.ok}/{rep.total} 成功，去重命中 {rep.cache_hits}")

# 多文档批量处理（进程池）
reports = convert_many(["a.docx", "b.docx"], workers=4)
```

### 安装

```bash
git clone https://github.com/shaoyanxing/mtefx.git
cd mtefx
python -m venv .venv && source .venv/bin/activate
pip install -e ./mtjx_checkout
pip install lxml olefile python-docx
```

依赖：`lxml>=4.9`、`olefile>=0.46`、`python-docx>=0.8`，Python ≥ 3.10。

## 项目结构

```
mtefx/
├── mtefx/                     # 加速层 + 增强层
│   ├── __init__.py            # 公开 API（convert / convert_docx / convert_many）
│   ├── engine.py              # 核心引擎：OLE→MTEF→MathML 快路径
│   ├── v3fix.py               # MTEF v3 静默失败修复
│   ├── fontmap.py             # 3648 条 fontmap 映射与 PUA 字符修复
│   ├── omml.py                # MathML→OMML（MML2OMML.XSL，lxml 缓存）
│   ├── docxconv.py            # docx 级：OLE→OMML 原位替换
│   ├── pipeline.py            # docx zip 直读、去重缓存、进程池
│   ├── refine.py              # Saxon 批量精修层（6 阶段，含 -im 入口 mode 修复）
│   ├── _fused.py              # 融合路径：Element 串联，省序列化往返
│   ├── fused_fast.py          # 合并遍历优化版（NS+PUA+menclose 一次 iter）
│   ├── _synthesize_ole.py     # 合成硬 OLE 电池（18 种结构）
│   ├── regression.py          # 统一回归门禁
│   ├── selftest.py            # 端到端自检
│   └── assets/
│       ├── xslt_fast/         # 加速版 XSLT 样式表（单次编译）
│       └── fontmap.json       # 28 张字体表 / 3648 条映射
├── mtjx_checkout/             # 核心引擎（上游 mathtypejx）
│   └── src/mathtypejx/
│       ├── scanner.py         # docx OLE 对象扫描
│       ├── extractor.py       # OLE→MTEF 字节提取
│       ├── converter.py       # MathML→OMML 转换 + 风险分级
│       ├── replacer.py        # docx XML 中 OLE→OMML 原位替换
│       ├── validator.py       # OMML 质量检查
│       ├── service.py         # 顶层 API
│       └── mtef/              # MTEF 记录解析
│           ├── records5.py    # v5 记录解析器
│           ├── records3.py    # v3 记录解析器
│           ├── builder.py     # MTEF XML 构建
│           ├── mover.py       # 上下标移动处理
│           ├── chars.py       # 字符映射替换
│           └── stream.py      # 字节流读取器
├── vendor/
│   ├── mathtype-extension/    # transpect XSLT 样式表
│   └── mml2omml/             # 微软官方 MML2OMML.XSL
├── real_ole/                  # 真实 OLE 测试 fixtures
└── tests/                     # 对拍与回归测试
```

## 关键技术细节

### transpect 入口 mode 陷阱

transpect 的 6 个后处理样式表的主模板声明在**专属 mode** 里（如 `mode="operator-elements"`），
而 Saxon CLI 默认只跑 `#default` mode，导致 4/6 阶段空转。正确的入口 mode：

| 阶段文件 | 入口 mode |
|----------|-----------|
| `whitespace-handle.xsl` | `handle-whitespace` ⚠️ 文件名 ≠ mode |
| `split-elements.xsl` | `split-elements` |
| `combine-elements.xsl` | `combine-elements` |
| `operator-elements.xsl` | `operator-elements` |
| `repair-subsup.xsl` | `repair-subsup` |
| `clean-up.xsl` | `clean-up` |

### lxml `_XSLTResultTree` 怪癖

绝不要直接持有 `etree.XSLT(...)(xml).getroot()` 当普通树用：

1. **实体不解码** — `getroot()` 文本里 `&#x2003;` 以 7 字符字面串残留
2. **命名空间提升丢失** — `fromstring` 重解析后根 `<math>` 缺命名空间

正确做法：`etree.tostring(result, "unicode")` 直接返回字符串。

### menclose longdiv/actuarial 静默丢失

MML2OMML 对 `<menclose notation="longdiv">` 产出空 `<m:oMath/>`。
修复：XSLT 前把 `menclose[longdiv|actuarial]` 展开为 `<mrow>`，保住内部数学。

## 鸣谢

本项目站在以下开源项目的肩膀上：

- **[mathtypejx](https://github.com/a917470154/mathtypejx)** — Python MTEF 解析引擎
  （OLE→MTEF→记录树→MathML），MIT License，Copyright (c) 2026 a917470154
- **[transpect/mathtype-extension](https://github.com/transpect/mathtype-extension)** —
  出版级 XSLT 后处理样式表 + fontmap 资产，Copyright (c) 2018–2024 transpect.io，BSD-style License
- **[jure/mathtype](https://github.com/jure/mathtype)** — Ruby gem for reading MathType binaries，
  MIT License，Copyright (c) 2015 Jure Triglav
- **[jure/mathtype_to_mathml](https://github.com/jure/mathtype_to_mathml)** — Ruby/XSLT
  MTEF XML to MathML conversion，MIT License，Copyright (c) 2015 Jure Triglav
- **[sbulka/mathtype](https://github.com/sbulka/mathtype)** — jure/mathtype fork，MTEF 记录参考
- **mathtype_to_mathml_plus** — Ruby gem，mathtype gem + XSLT 组合转换流
- **Microsoft MML2OMML.XSL** — MathML→OMML 官方 XSLT 1.0 样式表

fontmap 资产（`assets/xslt_fast/xsl/fontmaps/`）来自 transpect.io，保留其 BSD-style 版权声明。

## 开源协议

- 项目代码：**[MIT License](LICENSE)**
- fontmap 资产：BSD-style License（见 `assets/xslt_fast/xsl/fontmaps/LICENSE`）
- MML2OMML.XSL：微软官方文件，按原许可使用
