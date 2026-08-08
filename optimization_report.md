# mtefx 性能优化报告

## 一、Profiling 数据（树莓派 4B，100 个合成硬公式）

### 细粒度拆解

| 步骤 | 占比 | 单次耗时 | 可优化 |
|------|------|----------|--------|
| XSLT 转换 | 34.2% | 1.13ms | ❌ C 层 libxslt |
| OLE 剥壳 (olefile) | 20.9% | 0.69ms | ⚠️ 尝试手工解析/模板缓存，正确性无法保证 |
| XML 构建 (build_mtef_xml) | 16.1% | 0.53ms | ⚠️ _leaf 4810 次调用，固定节点数 |
| 字符替换 (chars.replace) | 14.9% | 0.49ms | ⚠️ 遍历树 |
| 上下标移动 (mover.move) | 9.4% | 0.31ms | ⚠️ 遍历树 |
| MTEF 解析 (parse_equation) | 4.6% | 0.15ms | ✅ 已够快 |

### 结论
瓶颈在 C 层（lxml XSLT 34% + olefile 21% = 55%），Python 层优化空间有限。

## 二、已落地优化

### 优化 1：关闭 olefile logging
- profiling 显示 logging.debug 被调用 11500 次（0.014s）
- `logging.getLogger("olefile").setLevel(logging.WARNING)`
- 效果：串行 7.57→6.44 ms/公式（1.18x）

### 优化 2：3-worker 并行
- 树莓派 4B 4 核，3 workers 是甜点（4 workers 因内存带宽退化）
- `ProcessPoolExecutor(max_workers=3, chunksize=15)`
- 效果：2.95 ms/公式（339 公式/秒）

### 优化 3：合并遍历（fused_fast.py）
- 原版 3 次 root.iter() 合并为 1 次（NS归一+PUA修复+menclose展开）
- OMML 输出 24/24 字节级一致
- 效果：1% 提升（Python 层不是瓶颈）

## 三、综合效果

| 配置 | ms/公式 | 公式/秒 | 加速比 |
|------|---------|---------|--------|
| 原版串行 | 7.57 | 132 | 1.00x |
| 优化串行 | 6.44 | 155 | 1.18x |
| 并行 2w | 4.38 | 229 | 1.73x |
| **并行 3w** | **2.95** | **339** | **2.57x** |
| 并行 4w | 4.64 | 216 | 1.63x（退化）|

## 四、尝试但放弃的方案

| 方案 | 加速 | 问题 |
|------|------|------|
| 手工 OLE 解析 | 1.26x | 6/24 不一致（mini stream 定位需 root entry）|
| OLE 模板缓存 | 1.31x | 239/240 不一致（MTEF offset 随文件变化）|
| 4 workers | - | 内存带宽瓶颈，比 3w 退化 |

## 五、对拍验证

24/24 OMML 字节级一致：
- 3 真实 OLE (mathtypejx fixtures)
- 3 web OLE fixtures
- 18 合成硬公式（矩阵/积分/求和/分段/方程组/n次根/上下标/长除法/深度嵌套）

## 六、下一步建议

1. Cython/cffi 加速 build_mtef_xml 和 chars.replace
2. 用真实中考/高考 docx 语料测试（需用户提供）
3. 将 logging 关闭写入 mtefx/__init__.py 默认生效
4. 将 fused_fast 替换 convert_fused 作为默认路径
