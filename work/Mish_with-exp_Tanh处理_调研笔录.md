# Mish with-exp Tanh Temp Buffer 处理调研笔录

> 调研日期: 2026-06-15
> 数据源: with-exp (sid: cc1f8c68-e830-4e6d-9867-3d1ce55947df) 主会话 + subagent 完整 JSONL 及源代码
> 对比参考: without-exp (同一 mish/level1 目录下的对照组)

---

## 1. with-exp 完整 Buffer 分配方案（源代码级）

### 1.1 Tiling 结构体 (`op_kernel/mish_tiling.h`)

```cpp
constexpr uint32_t DOUBLE_BUFFER = 2;

struct MishTilingData {
    uint32_t blockNum;
    uint64_t totalLength;
    uint64_t numPerCore;       // base elements per core
    uint64_t tailNumLastCore;  // unused
    uint32_t ubFormer;         // elements per tile in UB
    uint32_t tileNumPerCore;   // base tiles per core
    uint32_t tailTileNum;      // unused
    uint32_t tanhTmpSize;      // Tanh API temp buffer size in bytes
    uint32_t extraTiles;       // cores with +1 tile
    uint32_t tileNumExtraCore; // tiles per core for extra cores
};
```

### 1.2 Buffer 声明 (`op_kernel/mish_kernel.asc`)

```cpp
TQue<TPosition::VECIN, 1> inQueueX_;     // QDepth=1
TQue<TPosition::VECOUT, 1> outQueueY_;    // QDepth=1
TBuf<TPosition::VECCALC> tmpBuf1_;
TBuf<TPosition::VECCALC> tmpBuf2_;
TBuf<TPosition::VECCALC> tmpBuf3_;
// + cmpBuf_  (Select 阶段引入)
// + tanhTmpBuf_
```

### 1.3 Buffer 初始化 (`Init` 函数)

```cpp
pipe_->InitBuffer(inQueueX_, DOUBLE_BUFFER, ubFormer_ * typeSize);
pipe_->InitBuffer(outQueueY_, DOUBLE_BUFFER, ubFormer_ * typeSize);
pipe_->InitBuffer(tmpBuf1_, ubFormer_ * sizeof(float));
pipe_->InitBuffer(tmpBuf2_, ubFormer_ * sizeof(float));
pipe_->InitBuffer(tmpBuf3_, ubFormer_ * sizeof(float));
pipe_->InitBuffer(tanhTmpBuf_, tiling_->tanhTmpSize);
```

**要点**:
- `inQueueX_` 和 `outQueueY_` 使用 `DOUBLE_BUFFER=2`（即 depth=2, 双缓冲）
- 3 个 FP32 TBuf 使用单缓冲 (depth=1)
- `tanhTmpBuf_` 使用 `tiling_->tanhTmpSize` 字节（由 Host 侧 `GetTanhMaxMinTmpSize` 动态计算），单缓冲

### 1.4 Tanh 调用

```cpp
LocalTensor<uint8_t> tanhTmp = tanhTmpBuf_.Get<uint8_t>();
Tanh<float, false, highPrecisionConfig>(buf2, buf3, tanhTmp, calCount);
//                           (dst,     src,    tmp,        count)
```

**Tanh 签名**: `Tanh(dst, src, sharedTmpBuffer, count)` —— **dst 和 src 是独立的 Buffer**（`buf2` 与 `buf3`），不使用 `dst==src` 的原地计算。

---

## 2. Tanh Temp Buffer 大小确定方法

### 2.1 核心策略：两步法（避免循环依赖）

with-exp 采用**两步法**（"两步法，避免循环依赖"），完整逻辑在 `op_host/mish.asc` 的 `ComputeTiling()` 中：

```cpp
uint32_t ubSize = 192 * 1024; // 192KB for A2/A3

// bufferDivisor = 4 * typeSize + 3 * sizeof(float)
//                (inQ+outQ双缓冲各有2份) (tmp1+tmp2+tmp3各1份)
// 注意: typeSize 对于 FP16/BF16=2, FP32=4
// 对于 FP32: bufferDivisor = 4*4 + 3*4 = 28
// 对于 FP16: bufferDivisor = 4*2 + 3*4 = 20
uint32_t bufferDivisor = 4 * typeSize + 3 * sizeof(float);

// Step 1: 粗估，向 GetTanhMaxMinTmpSize 查询 tanhTmpSize
uint32_t alignFactor = 256 / sizeof(float); // 64 elements = 256 bytes
uint32_t initialTileEstimate = ubSize / (bufferDivisor + 1);
// 对齐后去做一次查询
AscendC::GetTanhMaxMinTmpSize(shape, sizeof(float), false, tanhMaxSize, tanhMinSize);
uint32_t tanhTmpSize = tanhMaxSize;

// Step 2: 扣除 tanhTmpSize 后精算 ubFormer
uint32_t availableForBuffers = ubSize - tanhTmpSize;
// 若 tanhTmp 太大则放弃
if (tanhTmpSize > availableForBuffers) {
    tanhTmpSize = 0;
    availableForBuffers = ubSize;
}
uint32_t maxElemNum = availableForBuffers / bufferDivisor;
uint32_t ubFormer = (maxElemNum / alignFactor) * alignFactor;

// Step 3: 用最终 ubFormer 重新查询 tanh 临时空间
// 如果更大的话重新计算
AscendC::GetTanhMaxMinTmpSize(finalShape, sizeof(float), false, finalTanhMaxSize, finalTanhMinSize);
if (finalTanhMaxSize > tanhTmpSize) {
    tanhTmpSize = finalTanhMaxSize;
    availableForBuffers = ubSize - tanhTmpSize;
    maxElemNum = availableForBuffers / bufferDivisor;
    ubFormer = (maxElemNum / alignFactor) * alignFactor;
}
tanhTmpSize = finalTanhMaxSize;
```

### 2.2 GetTanhMaxMinTmpSize API 说明

**函数签名**:
```cpp
void GetTanhMaxMinTmpSize(const ge::Shape& srcShape, const uint32_t typeSize,
                          const bool isVectorMode, uint32_t& maxSize, uint32_t& minSize);
```

**语义**（摘自官方文档）:
- kernel 侧 Tanh 接口的计算需要开发者预留/申请临时空间
- 本接口用于在 host 侧获取预留/申请的最大和最小临时空间大小
- 为保证功能正确，预留/申请的临时空间大小**不能小于 minSize**
- 在 minSize~maxSize 范围内，随临时空间增大性能会有一定优化提升
- **with-exp 使用 maxSize** 作为 tanhTmpSize（优先性能）

### 2.3 典型输出值

不同的 dtype 对 `ubFormer` 和 `tanhTmpSize` 的影响:

| dtype | typeSize | bufferDivisor | ubFormer | tanhTmpSize | UB 总占用 |
|-------|----------|---------------|----------|-------------|-----------|
| FP32  | 4        | 28            | 5440     | 87040 (85KB)| ~239KB -> 溢出 |
| FP16  | 2        | 20            | 6144     | 98304 (96KB)| ~221KB -> 溢出 |
| BF16  | 2        | 20            | 6144     | 98304 (96KB)| ~221KB -> 溢出 |

**重要**：with-exp 的 DOUBLE_BUFFER=2 导致 UB 占用超过 192KB！代码审查发现此问题（Issue #1: DOUBLE_BUFFER 与 TQue 深度不匹配导致 UB 溢出风险）。

修复方案是改为**单缓冲（DOUBLE_BUFFER=1）**，host 侧 bufferDivisor 相应调整。

### 2.4 UB 空间验证逻辑（修复后）

```cpp
// 8KB reserve for Select mode 2 temp space
uint32_t selectReserve = 8 * 1024;

// Verify it fits
if (2 * typeSize * ubFormer + 3 * sizeof(float) * ubFormer + tanhTmpSize + selectReserve > ubSize) {
    while (ubFormer >= alignFactor) {
        tanhTmpSize = ubFormer * sizeof(float) * 4;
        uint32_t totalNeeded = 2 * typeSize * ubFormer + 3 * sizeof(float) * ubFormer + tanhTmpSize + selectReserve;
        if (totalNeeded <= ubSize) break;
        ubFormer -= alignFactor;
    }
}
```

其中 `tanhTmpSize = ubFormer * sizeof(float) * 4` 是一个保守回退公式——当 `GetTanhMaxMinTmpSize` 查询不可用时，tanhTmpSize 粗略估计为 `ubFormer * 16` 字节。

---

## 3. 知识库中关于 Tanh/Buffer/UB 的约束记录

### 3.1 `opt_fp16_activation_upcast_fp32`
- **问题**: FP16 激活函数应上提到 FP32 计算
- **代价**: 额外 Cast 开销和更大 UB 占用（bufferDivisor 10→24）
- **当前 with-exp 的 bufferDivisor**: FP32 28, FP16/BF16 20（含 Double Buffer 乘数）

### 3.2 `design_ub_temporal_reuse_tbuf`
- **核心思想**: 单个 TBuf UB 分配中，不同流水线阶段的 buffer 可在 UB 内时间复用（地址重叠）
- **利用**: CopyIn/Compute/CopyOut 的阶段互斥性节省 UB 空间
- **影响**: with-exp 设计中明确将 tanhTmpBuf 标为"时间复用"——Tanh 写入 buf2 后 tanhTmpBuf 即可释放
- **with-exp 的状态**: 提到"tanhTmpBuf 仅在 Tanh 计算时使用，采用时间复用策略：Tanh 计算完成后结果已写入 tmpBuf2，tanhTmpBuf 空间即可释放，因此 tanhTmpBuf 可与后续不再使用的 Buffer 空间共享，不额外占用实际 UB 空间"

### 3.3 `design_bf16_cast_round_mode`
- **规则**: bf16->fp32 用 CAST_NONE（无损宽化），fp32->bf16 用 CAST_ROUND
- **影响**: with-exp 设计文档将此应用于 Cast RoundMode
- **与 Buffer 关系**: Cast 的 RoundMode 选择影响精度，但不直接涉及 Buffer 分配

### 3.4 `design_double_buffer_ub_overflow` (with-exp 代码审查时加载的经验)
- **问题**: 归约/归一化算子启用 Double Buffer 时 UB 空间翻倍
- **标签**: keywords=[DoubleBuffer, UB, InitBuffer, TQue, bufferDivisor]
- **影响**: 这是 with-exp 代码审查时加载的相关经验，指出 DoubleBuffer=2 导致 bufferDivisor 需要翻倍

### 3.5 `bug_ub_bufferdivisor_double_buffer`（被引用的相关经验）
- 与上述 3.4 同组，记录了 bufferDivisor 未考虑 Double Buffer 导致 UB 溢出的 Bug
- with-exp 代码审查报告 Issue #1 就是此 Bug 的实例：
  > "TQue 队列深度声明为 1 但 InitBuffer 使用 DOUBLE_BUFFER=2，FP32 场景实际占用 239KB 超过 192KB UB 限制"

### 3.6 `design_bf16_elementwise_upcast_fp32`
- **策略**: BF16 elementwise 算子中标量计算 API 不支持 BF16 时，Cast 到 FP32 计算再 Cast 回 BF16
- **影响**: with-exp 全部 dtype 统一 FP32 计算的理论基础，这也是需要 3 个 FP32 TBuf + 2 个 typeSize 双缓冲 + tanhTmp 的根本原因

---

## 4. with-exp 与 without-exp 的关键差异点

### 4.1 整体 Buffer 策略对比

| 维度 | with-exp | without-exp |
|------|----------|-------------|
| **总 Buffer 份数** | 6 (inQ+outQ+tmp1~3+tanhTmp+cmp) | 4 (inQ+outQ+tmp1+tmp2) |
| **双缓冲** | DOUBLE_BUFFER=2 (后改为1) | 单缓冲 (depth=1) |
| **FP32 升精度** | 全部 dtype 统一 FP32 计算 | 仅 FP32 计算，不支持 FP16/BF16 |
| **Tanh 临时 Buffer** | 独立 `tanhTmpBuf_`, 通过 `GetTanhMaxMinTmpSize` 动态分配 | 复用 `outQueueY` 作为 Tanh tmpBuf |
| **bufferDivisor** | FP32: 28, FP16/BF16: 20 | 16 (`BUFFER_DIVISOR = 16`) |
| **输入/输出类型** | 模板化 `<T>`, 支持 FP16/FP32/BF16 | 硬编码 `float` |
| **初次开发** | 使用 DOUBLE_BUFFER=2 失败 | 第1版就用单缓冲，成功一次性编译通过 |

#### 代码级对比

**with-exp 的 InitBuffer**（初始版，后因 UB 溢出被审查指出）:
```cpp
pipe_->InitBuffer(inQueueX_, DOUBLE_BUFFER, ubFormer_ * typeSize);
pipe_->InitBuffer(outQueueY_, DOUBLE_BUFFER, ubFormer_ * typeSize);
pipe_->InitBuffer(tmpBuf1_, ubFormer_ * sizeof(float));
pipe_->InitBuffer(tmpBuf2_, ubFormer_ * sizeof(float));
pipe_->InitBuffer(tmpBuf3_, ubFormer_ * sizeof(float));
pipe_->InitBuffer(tanhTmpBuf_, tiling_->tanhTmpSize);
```

**without-exp 的 InitBuffer**（第3轮优化，始终成功编译）:
```cpp
// Round 3: 4 buffers instead of 5
// inQueueX(4) + outQueueY(4) + tmpBuf1(4) + tmpBuf2(4) = 16 bytes/elem
// outQueueY is reused as Tanh tmpBuf
pipe_->InitBuffer(inQueueX, 1, tiling->ubFormer * sizeof(float));
pipe_->InitBuffer(outQueueY, 1, tiling->ubFormer * sizeof(float));
pipe_->InitBuffer(tmpBuf1, tiling->ubFormer * sizeof(float));
pipe_->InitBuffer(tmpBuf2, tiling->ubFormer * sizeof(float));
```

### 4.2 Tanh 调用方式的差异

| 方面 | with-exp | without-exp |
|------|----------|-------------|
| **Tanh 调用** | `Tanh<float>(buf2, buf3, tanhTmp, calCount)` | `Tanh<float>(tmp1, tmp2, yLocal.ReinterpretCast<uint8_t>(), tileLen)` |
| **dst 与 src** | **独立**（buf2 ≠ buf3） | **独立**（tmp1 ≠ tmp2） |
| **tmpBuf 来源** | 专用 `tanhTmpBuf_` | `outQueueY` 的 tensor，提前 Alloc 后 ReinterpretCast |
| **数据流** | 输入在 buf2, 结果写入 buf3 | 输入在 tmp2, 结果写入 tmp1 |

**两者都不使用 `dst==src` 原地计算方案**。

### 4.3 without-exp 的 BUFFER_DIVISOR 计算

```cpp
// UB 容量 192KB (DAV_2201)
constexpr uint32_t UB_SIZE = 192 * 1024;

// Round 3: reuse outQueueY as Tanh tmpBuf
// FP32: inQueueX(4) + outQueueY(4) + tmpBuf1(4) + tmpBuf2(4) = 16
constexpr uint32_t BUFFER_DIVISOR = 16;

constexpr uint32_t ALIGN_FACTOR = 256 / sizeof(float); // 64

int64_t maxElemNum = UB_SIZE / BUFFER_DIVISOR; // 192*1024/16 = 12288
int64_t ubFormer = (maxElemNum / ALIGN_FACTOR) * ALIGN_FACTOR; // 12288
```

**without-exp 的 Tanh tmpBuf 大小逻辑**:
```cpp
// 使用 min(ubFormer * sizeof(float), tanhMaxTmpSize) 作为安全上界
tiling.tanhTmpBufSize = std::min(static_cast<uint32_t>(ubFormer * sizeof(float)), tanhMaxTmpSize);
if (tiling.tanhTmpBufSize < tanhMinTmpSize) {
    tiling.tanhTmpBufSize = tanhMinTmpSize;
}
```

注意 without-exp 将 `outQueueY` 复用为 Tanh tmpBuf，因此 `outQueueY` 的分配大小已经覆盖了 Tanh 的需求，不需要额外分配 `tanhTmpBufSize` 的 UB 空间。这里的 `tanhTmpBufSize` 仅用于记录，实际初始化时 `outQueueY` 已经存在。

### 4.4 关键差异：knowledge 的影响

| 方面 | with-exp | without-exp |
|------|----------|-------------|
| **knowledge 使用** | 是，设计阶段就检索了知识库 | 否，从头开发 |
| **引用的经验** | `opt_fp16_activation_upcast_fp32`, `design_bf16_cast_round_mode`, `design_ub_temporal_reuse_tbuf`, `design_double_buffer_ub_overflow` | 无 |
| **Double Buffer 选择** | 使用 DOUBLE_BUFFER=2（设计阶段受 Double Buffer 概念影响），后被审查指出 UB 溢出 | 全程无 Double Buffer 概念，depth=1 单缓冲 |
| **UB 切分公式** | 两步法（与 GetTanhMaxMinTmpSize 循环依赖规避） | 直接除法（UB_SIZE / BUFFER_DIVISOR） |
| **dtype 支持** | FP32+FP16+BF16（knowledge 指导上提 FP32） | 仅 FP32 |
| **Buffer 规划复杂性** | 高（6 份 Buffer + Select + 8KB reserve） | 低（4 份 Buffer） |

---

## 5. 问题回答总结

### A. with-exp 如何确定 Tanh Temp Buffer 大小？

**使用迭代的两步法，依赖 GetTanhMaxMinTmpSize API 动态计算**:

1. 先用粗估的 `initialTileEstimate` 调用 `GetTanhMaxMinTmpSize` 获取 tanhTmpSize
2. 扣除 tanhTmpSize 后用剩余空间计算 `ubFormer`
3. 用最终 `ubFormer` 再次查询 `GetTanhMaxMinTmpSize` 确认 tanh 临时空间
4. 若有增大则用新值重新计算 `ubFormer`
5. 使用 `tanhMaxSize`（即 maxSize，取上界）

**without-exp 对比**：同样使用 `GetTanhMaxMinTmpSize`，但只查询一次，且用 `min(ubFormer*sizeof(float), tanhMaxTmpSize)` 做安全上界裁剪。

### B. with-exp 是否尝试过 Tanh dst==src 的方案？

**否。** with-exp 始终使用独立 dst/src (`Tanh(buf2, buf3, tanhTmp, calCount)`)，dst 与 src 是不同的 TBuf。知识库中无 `dst==src` 相关约束。

without-exp 同样使用独立 dst/src (`Tanh(tmp1, tmp2, ...)`)。

### C. with-exp 的 bufferDivisor 如何计算？Double Buffer vs Single Buffer 的依据？

**bufferDivisor 计算**:
```cpp
bufferDivisor = 4 * typeSize + 3 * sizeof(float)
// inQueueX双缓冲占用2*typeSize
// outQueueY双缓冲占用2*typeSize
// tmpBuf1~3各4字节
```

由于 with-exp 使用 DOUBLE_BUFFER=2，inQueueX 和 outQueueY 各占 `2 * typeSize` 而非 `1 * typeSize`，导致 bufferDivisor 比 without-exp 大得多：
- FP32: 28 vs without-exp 的 16
- FP16/BF16: 20 vs without-exp 的 16

**Double Buffer (depth=2) vs Single Buffer (depth=1) 的决策依据**:
- with-exp 最初选择 DOUBLE_BUFFER=2 是受知识库设计模式影响（Elementwise 激活函数常用双缓冲优化流水线）
- 代码审查发现 DOUBLE_BUFFER=2 导致 UB 溢出后被指出需修复为单缓冲
- without-exp 全程使用单缓冲，开发过程无相关纠结

### D. with-exp 如何处理 FP16 + Double Buffer 的 UB 空间问题？

with-exp 在 FP16（typeSize=2）时 bufferDivisor=20，比 FP32 的 28 小，但 DOUBLE_BUFFER=2 仍然导致：
- FP16: `2*2*6144 + 3*4*6144 + 98304 = 24576 + 73728 + 98304 = 196608` → 实际加上对齐等刚好 192KB 边界

代码审查后强制将 DOUBLE_BUFFER 改为单缓冲（depth=1），对应 bufferDivisor 调整为：
- FP32: `2*4 + 3*4 = 20`（不再 4*typeSize）
- FP16/BF16: `2*2 + 3*4 = 16`

---

## 6. 参考源文件

| 文件 | 路径 |
|------|------|
| with-exp tiling.h | `/data/xyj/rushdog.claude/rushdog-cicd/runs/finished_glm51/level1/mish/with-exp/operators/mish/op_kernel/mish_tiling.h` |
| with-exp kernel.asc | `/data/xyj/rushdog.claude/rushdog-cicd/runs/finished_glm51/level1/mish/with-exp/operators/mish/op_kernel/mish_kernel.asc` |
| with-exp host.asc | `/data/xyj/rushdog.claude/rushdog-cicd/runs/finished_glm51/level1/mish/with-exp/operators/mish/op_host/mish.asc` |
| with-exp 对话日志 | `/data/xyj/rushdog.claude/rushdog-cicd/runs/finished_glm51/level1/mish/with-exp/claude-step1-readable.txt` |
| without-exp tiling.h | `/data/xyj/rushdog.claude/rushdog-cicd/runs/finished_glm51/level1/mish/without-exp/operators/mish/op_kernel/mish_tiling.h` |
| without-exp kernel.asc | `/data/xyj/rushdog.claude/rushdog-cicd/runs/finished_glm51/level1/mish/without-exp/operators/mish/op_kernel/mish_kernel.asc` |
| without-exp host.asc | `/data/xyj/rushdog.claude/rushdog-cicd/runs/finished_glm51/level1/mish/without-exp/operators/mish/op_host/mish.asc` |
