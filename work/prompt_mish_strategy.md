# Prompt: 开发 Mish 算子的 Tanh TmpBuffer 大小策略对比实验（FP32 版本）

## 目标

基于现有 Mish 算子（Single Buffer 模式），开发一个**四策略对比实验**。不同策略之间**只有一处差异**：`ComputeTmpBufSize()` 函数中 Tanh sharedTmpBuffer 的大小计算方式。其余所有代码（kernel 实现、Buffer 分配、CopyIn/CopyOut 逻辑、多核切分、NaN 防护、UB 对齐）完全相同。

实验的核心问题：**对于 Vector 算子，sharedTmpBuffer 和 I/O queue 共享 UB 是零和博弈。盲目最大化 tmpBuffer 是否会挤占 I/O 带宽，反而拖慢端到端性能？最优的 tmpSize 到底是多少？**

开发路径：`/data/ypx-dev/workspace/rushdog-claude/`

## 参考代码

现有 Mish Single Buffer 算子位于：
- `/data/ypx-dev/workspace/claude-code-log/output/mish/src/with-exp/op_kernel/mish_tiling.h` — Host 端 Tiling 逻辑
- `/data/ypx-dev/workspace/claude-code-log/output/mish/src/with-exp/op_kernel/mish_kernel.asc` — Kernel 实现

请仔细阅读这两个文件，理解其完整结构和 Buffer 布局，然后再开始修改。

---

## 通用常量与 UB 布局（FP32）

```cpp
constexpr uint32_t UB_SIZE = 192 * 1024;     // 192KB (Atlas A2/A3)
constexpr uint32_t BANK_PADDING = 512;        // per VECCALC buf, 防止 bank conflict
constexpr uint32_t BUFFER_NUM = 1;            // Single Buffer
constexpr uint32_t ONE_REPEAT_BYTES = 256;    // 一次 Vector Repeat 的字节数
constexpr uint32_t ONE_BLK_SIZE = 32;         // 对齐单元

// 公共常量（统一放在全局作用域，避免编译错误）
constexpr uint32_t TOTAL_PADDING = 4 * BANK_PADDING;           // 2048
constexpr uint32_t BUFFER_DIVISOR = 16;                         // bytes per element
constexpr uint32_t ALIGN_FACTOR = ONE_REPEAT_BYTES / sizeof(float);  // 64
constexpr uint32_t TANH_TYPE_SIZE = sizeof(float);             // 4
```

### Buffer 布局（FP32 路径）

```
inQ       = ubFormer * sizeof(float) + BANK_PADDING = ubFormer * 4 + 512
outQ      = ubFormer * sizeof(float) + BANK_PADDING = ubFormer * 4 + 512
spBuf     = ubFormer * sizeof(float) + BANK_PADDING = ubFormer * 4 + 512
thBuf     = ubFormer * sizeof(float) + BANK_PADDING = ubFormer * 4 + 512
tmpBuf    = tmpBufSize                                              // ← 策略差异点
────────────────────────────────────────
Total     = ubFormer * 16 + 2048 + tmpBufSize
```

**关键说明**：
- `spBuf` = Softplus 计算结果（中间结果1）
- `thBuf` = Tanh 输入 buffer（中间结果2）
- `tmpBuf` = Tanh 内部 sharedTmpBuffer（策略变量）
- Tanh 输入类型是 `float`，因此 `TANH_TYPE_SIZE = 4`

---

## 架构设计：单一算子框架 + 枚举策略分发

**核心原则**：只编译一次 Kernel，通过枚举类在 Host 端动态切换策略，确保"单一变量原则"。

### 策略枚举定义

```cpp
// mish_tiling.h
enum class MishTilingStrategy {
    MULTI_SAMPLE = 0,  // 策略 A: 多值采样（需要额外传入 sampleTmpSize）
    THIRD_UB     = 1,  // 策略 B: 经验基线
    TILE_MATCH   = 2,  // 策略 C: 不动点迭代
    DIRECT_SOLVE = 3   // 策略 D: 闭式解
};
```

### 文件结构

```
operators/mish/
  kernel/
    mish_kernel.asc              # kernel 实现（四策略共用）
  include/
    mish_tiling_common.h        # 公共函数：ComputeUbFormer, ComputeUbFormerWithTanhCheck
    mish_tiling_strategies.h    # 各策略的 ComputeTmpBuf 实现
    mish_tiling.h               # 主入口：ComputeMishTiling(strategy, sampleTmpSize, ...)
  op_host/
    mish.asc                    # Host 端主程序（策略调度 + 性能打点）
  CMakeLists.txt
  scripts/
    gen_test_data.py            # 生成测试数据
    verify.py                   # 验证输出正确性
    run_all.sh                  # 一键运行所有策略
  DESIGN.md                     # 设计文档 + 实测指标表
```

**架构优势**：
1. 避免 header 符号冲突
2. 核心修复（如 NaN 防护）无差别覆盖所有策略
3. Host 端统一调度，输出结构化 JSON 报告
4. 便于 Agent 自动化分析和经验沉淀

---

## 公共函数

### ubFormer 计算（不考虑 TanhTmpBuffer 约束）

```cpp
uint32_t ComputeUbFormer(uint32_t tmpBufSize) {
    uint32_t ubAvailable = UB_SIZE - tmpBufSize - TOTAL_PADDING;
    return (ubAvailable / BUFFER_DIVISOR / ALIGN_FACTOR) * ALIGN_FACTOR;
}
```

### ubFormer 合法性校验（同时考虑 UB 容量和 TanhTmpBuffer 约束）

```cpp
uint32_t ComputeUbFormerWithTanhCheck(uint32_t tmpBufSize, uint32_t maxLiveNodeCount, uint32_t extraBuf) {
    // 防御性检查
    if (maxLiveNodeCount == 0) return 0;
    if (tmpBufSize <= extraBuf) return 0;
    if (tmpBufSize + TOTAL_PADDING >= UB_SIZE) return 0;

    // UB容量能支持的ubFormer
    uint32_t ubBySpace = ComputeUbFormer(tmpBufSize);

    // TanhTmpBuffer能支撑的ubFormer
    // tmpBuf >= extraBuf + maxLiveNodeCount * ubFormer * 4
    // => ubFormer <= (tmpBuf - extraBuf) / (maxLiveNodeCount * 4)
    uint32_t ubByTanh = (tmpBufSize - extraBuf) / (maxLiveNodeCount * TANH_TYPE_SIZE);
    ubByTanh = (ubByTanh / ALIGN_FACTOR) * ALIGN_FACTOR;

    return std::min(ubBySpace, ubByTanh);
}
```

---

## 四策略定义

### 策略对照表（FP32，192KB UB）

| 策略 | 名称 | 枚举值 | tmpBufSize (KB) | 实际 ubFormer | 说明 |
|------|------|--------|----------------|---------------|------|
| **A** | **MultiSample** | MULTI_SAMPLE | 多值采样 | 变化 | 扫描性能曲线，覆盖极小→极大全范围 |
| **B** | **ThirdUB** | THIRD_UB | 64 | 8064 | 经验基线：UB/3 |
| **C** | **TileMatch** | TILE_MATCH | ~38.9 | ~9728 | 不动点迭代 |
| **D** | **DirectSolve** | DIRECT_SOLVE | ~38.9 | 9728 | 闭式解 |

**关键预期**（需实验验证）：
- B ≠ C/D：B 是经验值 64KB，C/D 是数学自洽值 ~39KB
- C ≈ D：两者基于同一数学模型，数值应接近。由于C可能震荡收敛（取较小值），与D的绝对最优解可能有合理差异（< 512B）
- A 的曲线可能显示从"Tanh tmp 不够"到"I/O 空间被挤占"的转折

---

## 策略详解与实现

### 策略 A: MultiSample — 多值采样

**核心思想**: "在合理范围内采样多个 tmpBufSize 值，绘制性能曲线。采样点包括极小值、经验值、数学解、极大值。"

**采样点设计**（已包含数学解 38912B）:
```cpp
constexpr std::array<uint32_t, 9> SAMPLE_POINTS = {
    256,        // 极小值
    4096,       // 4KB
    16384,      // 16KB
    32768,      // 32KB
    38912,      // ~38KB，数学解（C/D 的结果）
    65536,      // 64KB，经验值（B 的结果）
    98304,      // 96KB
    131072,     // 128KB
    190464,     // ~190KB，极端大 tmp
};
```

**实现**:
```cpp
uint32_t ComputeTmpBuf_MultiSample(uint32_t sampleTmpSize) {
    // Host 端直接传入采样值
    return sampleTmpSize;
}
```

### 策略 B: ThirdUB — 经验基线

**核心思想**: "知识库经验证实：UB_SIZE/3 是 Mish 的 Tanh 经验最优 tmpBufSize。"

**实现**:
```cpp
uint32_t ComputeTmpBuf_ThirdUB(uint32_t ubSize) {
    uint32_t targetSize = ubSize / 3;                      // 65536 = 64KB
    targetSize = std::max(std::min(targetSize, ubSize / 2), (uint32_t)256);
    return AlignDown(targetSize, ONE_BLK_SIZE);           // 65536
}
```

### 策略 C: TileMatch — 不动点迭代（鲁棒版）

**核心思想**: "让 tmpBufSize 和 ubFormer 互相自洽——tmpBuf 正好够处理一个 tile，一个 tile 正好用满剩余的 UB。"

**数学挑战**：
- `ubFormer` 计算包含向下 64 对齐（`AlignDown`）
- `newTmpBufSize` 计算包含向上 32 对齐（`AlignUp`）
- 两个离散阶跃函数互相咬合时，可能陷入 2 周期震荡：`A → B → A → B`
- **数学上无法保证绝对收敛**（`newTmpBufSize == tmpBufSize` 可能永远不满足）

**实现**（同时捕获绝对收敛和震荡收敛）：
```cpp
uint32_t ComputeTmpBuf_TileMatch(uint32_t maxLiveNodeCount, uint32_t extraBuf) {
    // 初始猜测：用最小可能值启动
    uint32_t tmpBufSize = ONE_REPEAT_BYTES * maxLiveNodeCount + extraBuf;
    uint32_t lastTmpBufSize = tmpBufSize;

    // 20 次对于字节级别的离散对齐跳跃绝对足够触底
    for (int i = 0; i < 20; ++i) {
        uint32_t ubFormer = ComputeUbFormer(tmpBufSize);
        uint32_t newTmpBufSize = extraBuf + maxLiveNodeCount * ubFormer * TANH_TYPE_SIZE;
        newTmpBufSize = AlignUp(newTmpBufSize, ONE_BLK_SIZE);

        // 场景 1: 绝对收敛（命中真正的数学不动点）
        if (newTmpBufSize == tmpBufSize) {
            break;
        }

        // 场景 2: 震荡收敛（陷入 A → B → A 的 2 周期循环）
        if (newTmpBufSize == lastTmpBufSize) {
            // 防御性策略：在两个震荡值中取较小的一个，确保总 UB 空间绝不超载
            tmpBufSize = std::min(tmpBufSize, newTmpBufSize);
            break;
        }

        lastTmpBufSize = tmpBufSize;
        tmpBufSize = newTmpBufSize;
    }

    return tmpBufSize;
}
```

**数学含义**: 解方程组
```
ubFormer = (UB_SIZE - TOTAL_PADDING - tmpBuf) / 16
tmpBuf   = extraBuf + maxLiveNodeCount * ubFormer * 4
```

**预期行为**：
- 大多数情况：2-3 次迭代内收敛（绝对或震荡）
- 震荡时取较小值：保证 UB 不超载，性能可能略逊于最优解但安全

### 策略 D: DirectSolve — 闭式解

**核心思想**: "将迭代转为不等式约束，利用 ubFormer 已对齐到 64 的事实，直接求最优解。"

**实现**（修正版，无需 -31）:
```cpp
uint32_t ComputeTmpBuf_DirectSolve(uint32_t maxLiveNodeCount, uint32_t extraBuf) {
    // 利用 ubFormer 已对齐到 64，tmpBuf 必然是 256 的倍数，天然满足 32 字节对齐
    // 无需减去误差项，直接使用精确公式
    uint32_t alignedExtra = AlignUp(extraBuf, ONE_BLK_SIZE);
    uint32_t ubCapacity = UB_SIZE - TOTAL_PADDING - alignedExtra;
    uint32_t ubFormerMax = ubCapacity / (BUFFER_DIVISOR + maxLiveNodeCount * TANH_TYPE_SIZE);
    uint32_t ubFormer = (ubFormerMax / ALIGN_FACTOR) * ALIGN_FACTOR;

    // 回代求 tmpBufSize
    return AlignUp(extraBuf + maxLiveNodeCount * ubFormer * TANH_TYPE_SIZE, ONE_BLK_SIZE);
}
```

**数学推导**（extraBuf=0, maxLiveNodeCount=1）:
```
ubCapacity = 196608 - 2048 = 194560
ubFormerMax = 194560 / (16 + 4) = 9728
ubFormer = (9728 / 64) * 64 = 9728  ✓ 完美对齐
tmpBuf = AlignUp(0 + 1 * 9728 * 4, 32) = 38912
```

---

## 主 Tiling 入口

```cpp
// mish_tiling.h
bool ComputeMishTiling(
    MishTilingStrategy strategy,
    uint32_t sampleTmpSize,      // 仅策略 A 使用
    uint32_t totalElements,
    uint32_t blockDim,
    uint32_t& ubFormer,
    uint32_t& tmpBufSize
) {
    // 1. 获取 Tanh 参数
    uint32_t maxLiveNodeCount, extraBuf;
    GetTanhTmpBufferFactorSize(TANH_TYPE_SIZE, maxLiveNodeCount, extraBuf);
    if (maxLiveNodeCount == 0) {
        maxLiveNodeCount = 1;
        extraBuf = 0;
    }

    // 2. 策略路由（唯一变量）
    switch(strategy) {
        case MishTilingStrategy::MULTI_SAMPLE:
            tmpBufSize = sampleTmpSize;
            break;
        case MishTilingStrategy::THIRD_UB:
            tmpBufSize = ComputeTmpBuf_ThirdUB(UB_SIZE);
            break;
        case MishTilingStrategy::TILE_MATCH:
            tmpBufSize = ComputeTmpBuf_TileMatch(maxLiveNodeCount, extraBuf);
            break;
        case MishTilingStrategy::DIRECT_SOLVE:
            tmpBufSize = ComputeTmpBuf_DirectSolve(maxLiveNodeCount, extraBuf);
            break;
        default:
            return false;
    }

    // 3. 公共的 ubFormer 计算与校验
    ubFormer = ComputeUbFormerWithTanhCheck(tmpBufSize, maxLiveNodeCount, extraBuf);
    if (ubFormer == 0) return false;

    // 4. 计算 blockFormer, blockNum, tileNum...
    // （省略详细计算）

    return true;
}
```

---

## 理论预期值表（修正版）

**通用假设**：
- `UB_SIZE = 196608` (192KB)
- `TOTAL_PADDING = 2048`
- `extraBuf = 0`
- `maxLiveNodeCount = 1`

### 策略 A 采样点详细表（使用 ComputeUbFormerWithTanhCheck）

| tmpBufSize (B) | ubBySpace | ubByTanh | 实际 ubFormer | 说明 |
|---------------|-----------|----------|---------------|------|
| 256 | 12096 | 64 | **64** | Tanh tmp 限制 |
| 4096 | 11904 | 1024 | **1024** | Tanh tmp 限制 |
| 16384 | 11136 | 4096 | **4096** | Tanh tmp 限制 |
| 32768 | 10112 | 8192 | **8192** | Tanh tmp 限制 |
| 38912 | 9728 | 9728 | **9728** | 平衡点（空间=Tanh） |
| 65536 | 8064 | 16384 | **8064** | UB 空间限制 |
| 98304 | 6016 | 24576 | **6016** | UB 空间限制 |
| 131072 | 3968 | 32768 | **3968** | UB 空间限制 |
| 190464 | 256 | 47616 | **256** | UB 空间极小 |

### 策略 B/C/D 预期值

| 策略 | tmpBufSize (B) | tmpBufSize (KB) | ubFormer |
|------|---------------|----------------|----------|
| B (ThirdUB) | 65536 | 64 | 8064 |
| C (TileMatch) | ~38912 | ~38.9 | ~9728 |
| D (DirectSolve) | 38912 | 38.9 | 9728 |

---

## Host 端调度与性能打点

### Host 端伪代码

```cpp
// op_host/mish.asc
std::vector<uint32_t> samplePoints = {256, 4096, 16384, 32768, 38912, 65536, 98304, 131072, 190464};
std::vector<MishTilingStrategy> strategies = {
    MishTilingStrategy::THIRD_UB,
    MishTilingStrategy::TILE_MATCH,
    MishTilingStrategy::DIRECT_SOLVE
};

// 跑策略 A（扫参）
for (auto size : samplePoints) {
    auto result = RunMishKernel(MishTilingStrategy::MULTI_SAMPLE, size, inputData);
    LogPerformance("A-MultiSample", size, result);
}

// 跑策略 B, C, D（定参）
for (auto strategy : strategies) {
    auto result = RunMishKernel(strategy, 0, inputData);
    LogPerformance(GetStrategyName(strategy), result.tmpBufSize, result);
}
```

### 输出格式（JSON）

```json
{
  "strategy": "MULTI_SAMPLE",
  "tmpBufSize": 38912,
  "ubFormer": 9728,
  "blockDim": 32,
  "blockFormer": 9728,
  "blockNum": 27,
  "tileNum": 1,
  "duration_us": 1234,
  "tanhMaxLiveNode": 1,
  "tanhExtraBuf": 0
}
```

---

## 实验要求

### 性能测试规范

1. **预热与重复**：每个 case 至少 warmup 5 次，正式测量 30 次
2. **统计报告**：报告 median、p90、min（不仅仅是平均值）
3. **计时边界**：每次计时前后做 stream synchronize；明确 duration_us 是否包含 H2D/D2H（建议默认只测 device kernel）
4. **执行顺序**：对策略执行顺序做随机化或轮转，避免温度、缓存、频率影响
5. **数据规模**：
   - 小规模（4K）：主要观察 launch overhead，不作为主要性能结论
   - 中规模（256K）：常规测试
   - 大规模（16M）：压力测试

### 正确性验证

1. **Golden 对比**：与 PyTorch 结果对比，覆盖以下输入：
   - NaN、Inf
   - 大正数、大负数
   - 零附近输入
2. **策略一致性**：所有策略对同一输入产生相同数值输出（精度容差内）

---

## 关键注意事项

1. **NaN 防护一致性**：确保所有策略的基线代码中均已剔除 `Maxs` 保护逻辑，避免残留指令干扰性能打点

2. **常量作用域**：所有 constexpr 常量统一放在全局作用域，避免编译错误

3. **I/O buffer 口径统一**：
   - 实际 UB 占用 = `ubFormer * 4 + 512`
   - 有效 payload = `ubFormer * 4`
   - 文档中明确标注口径

4. **C/D 数值一致性**：差异应 < 256B。由于策略C可能震荡收敛（取较小值），与策略D的绝对最优解可能有合理差异

5. **符号替换策略 A**：不使用符号链接切换策略，改用枚举类在运行时分发

---

## 预期结果解读（假设性）

**注意**：以下为假设性预期，实际结果可能因平台、数据规模而异。

| 如果结果是... | 那么说明... |
|-------------|-----------|
| A 的曲线在 32-64KB 附近出现最优点 | tmpBuf 与 I/O 存在明显零和关系，存在最优平衡点 |
| B > C/D 且 B 性能最优 | 经验值给 Tanh 更多 tmp，其收益超过 I/O tile 损失 |
| C/D > B | 数学平衡点优于经验值 |
| C ≈ D（差异 < 512B） | 验证了两种算法的一致性（C可能震荡收敛取较小值） |
| A 极端点（190KB）性能极差 | 证实 tmpBuffer 过大会严重挤占 I/O |
| A 曲线单调（tmp 越大越好） | Tanh 内部优化收益 > I/O tile 损失 |
| A 曲线单调（tmp 越大越差） | I/O tile 大小是主导因素 |

**重要结论空间**：
- 若 tmpBufSize 与性能呈钟形关系 → 验证零和博弈假设
- 若 tmpBufSize 越大性能越好 → Tanh 内部优化是瓶颈
- 若数学解（C/D）优于经验值（B）→ 理论推导具有实用价值
