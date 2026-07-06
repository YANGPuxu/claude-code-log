# CANN 官方算子 UB 分配方式调研报告

> 调研日期: 2026-06-15
> CANN 版本: ascend-toolkit 8.2.RC1
> 调研目标: 验证关于 Vector 算子 UB 分配方式（Tiling 配置）的猜想

---

## 一、调研范围

以 `/usr/local/Ascend/ascend-toolkit/8.2.RC1/` 下的 CANN 算子库为调研范围，覆盖：

- **High-Level API 层**: `aarch64-linux/ascendc/include/highlevel_api/`（C++ 模板库，声明算子 API 和 Tiling 接口）
- **Kernel 实现层**: `opp/built-in/op_impl/ai_core/tbe/impl/ascendc/`（实际算子的 DAG/手动内核实现）
- **ATP 框架层**: `opp/built-in/op_impl/ai_core/tbe/impl/ascendc/common/op_kernel/atvoss/`（DAG 调度框架的底层实现）
- **闭源库**: `atc/lib64/libtiling_api.a` 中的符号表（Tiling API 的实际实现）
- **开源社区**: Gitee Ascend samples, AtomGit ops-transformer, 官方开发指南（Host 端 Tiling 代码）

---

## 二、待验证的猜想

原始猜想：

```
f = f_shared + f_tmp + BF * (f_input + f_output)
tileNum = AlignDown(UB_SIZE / f, ALIGN_PARAM)
```

其中：
- `f_shared`: 通过 `GetXxxTmpBufferFactorSize` 求得的一份 tile 的 sharedTmpBuffer 空间消耗乘数
- `f_tmp`: 一份 tile 的中间计算结果临时空间
- `f_input + f_output`: Input/Output Buffer 空间
- `BF = BUFFER_NUM = 2`: Double Buffer 倍数

---

## 三、核心发现：猜想需要修正

### 3.1 实际公式（从开源 Host 端 Tiling 代码中验证）

通过搜索 Gitee Ascend Samples、CANN 官方开发指南和 AtomGit 生产代码，找到了 Host 端 Tiling 的标准实现。实际公式为：

```
Step 1: ubSize        = GetCoreMemSize(UB)                           // 硬件查询
Step 2: tmpBufferSize = clamp(GetXxxMaxMinTmpSize, [tmpMin, tmpMax]) // 从 [min, max] 中选择
Step 3: ubForQueues   = ubSize - systemReserve - tmpBufferSize       // 减去固定开销
Step 4: ubPerBuffer   = ubForQueues / BUFFER_NUM                     // Double Buffer 分半
Step 5: tileLength    = AlignDown(ubPerBuffer / typeSize, 32/typeSize) // 32B 对齐
```

**官方示例代码**（来自 CANN 8.1 开发指南 / Gitee Ascend Samples）：

```cpp
static ge::graphStatus TilingFunc(gert::TilingContext* context) {
    // 1. 获取硬件参数
    auto ascendcPlatform = platform_ascendc::PlatformAscendC(context->GetPlatformInfo());
    uint64_t ubSize;
    ascendcPlatform.GetCoreMemSize(platform_ascendc::CoreMemType::UB, ubSize);
    uint32_t coreNum = ascendcPlatform.GetCoreNumAiv();

    // 2. 获取 shape
    uint32_t totalLength = context->GetInputShape(0)->GetShapeSize();
    uint32_t typeSize = 2;  // half

    // 3. 查询临时缓冲区需求
    uint32_t tmpMin = 0, tmpMax = 0;
    AscendC::GetAsinMaxMinTmpSize(shape, typeSize, false, tmpMax, tmpMin);
    auto tmpSize = ubSize >= tmpMax ? tmpMax : ubSize;

    // 4. 计算可用 UB
    uint32_t ubAvailable = (ubSize - 1024 - tmpSize) / 2;  // systemReserve ~1KB

    // 5. tileSize = AlignDown(ubAvailable / typeSize, 32/typeSize)
    uint32_t alignNum = 32 / typeSize;
    uint32_t tileLength = (ubAvailable / typeSize / alignNum) * alignNum;

    // 6. 多核分配（former/tail）
    uint32_t blockLength = (totalLength + coreNum - 1) / coreNum;
    uint32_t tileNum = (blockLength + tileLength - 1) / tileLength;
    ...
}
```

### 3.2 猜想与实际的关键差异

| 维度 | 原始猜想 | 实际公式 |
|------|---------|---------|
| `f_shared`（sharedTmpBuffer） | 作为乘数因子加在 f 中 | 作为**固定字节开销**从 UB_SIZE 中直接减去 |
| `f_tmp`（中间结果） | 作为独立项加在 f 中 | 在直调模式中不单独存在（中间结果要么在 VREG 中，要么在 tmpBuffer 中） |
| BF | 乘以 `(f_in + f_out)` | 除以整个 `(UB_SIZE - overhead - tmpBuffer)` |
| 公式形式 | `UB_SIZE / (f_shared + f_tmp + 2*typeSize)` | `(UB_SIZE - overhead - tmpBuffer) / 2 / typeSize` |

**核心洞察**：`tmpBufferSize` 不是每次 tile 迭代都重新分配的——它是在算子初始化时**一次性分配**，所有 tile 的迭代**复用**同一块 sharedTmpBuffer。因此它不在 BF 因子内，而是直接从一个 core 的总 UB 中减去。这是 High-Level API 比 DAG 模式的 trade-off：High-Level API 需要一个持久的 scratch space（每个 core 一份），而 input/output queue 是 ping-pong 的（每个 core 两份）。

---

## 四、三种 UB 分配模式

CANN 算子不遵循统一的 UB 分配范式。根据实现方式的不同，存在三种分配模式：

### 模式 1: High-Level API 模式（无 Double Buffer）

**代表算子**: Tanh、Exp、Erf、Sigmoid、Gelu 等（High-Level API 版本）

**特征**:
- 标记为 `#pragma begin_pipe(V)`（Vector 算子），`highlevel_api/lib/` 下所有 123 处均为 V
- 提供 `sharedTmpBuffer` 参数的 API 重载，也提供自动 `PopStackBuffer` 的无参重载
- **不处理 GM↔UB 数据搬运**，只接受 `LocalTensor`（已在 UB 中的数据）
- **不使用 Double Buffer**

**UB 分配方式** (以 Tanh 为例，来自 `tanh_common_impl.h`):

```
// float16 (half):
stackSize = tmpBufferSize / maxLiveNodeCount / 32 * 32
         = tmpBufferSize / 2 / 32 * 32
// 将 sharedTmpBuffer 拆分为 tempTensorConv (float cast) + tmpClip 两份

// float32:
stackSize = tmpBufferSize / 1 / 32 * 32
// sharedTmpBuffer 全部用于 tmpClip
```

**结论**: 此模式满足 `tileSize = tmpBufferSize / maxLiveNodeCount`（对齐后），但**没有 Double Buffer**——此模式不负责数据搬运。

---

### 模式 2: ATP DAG 框架模式（自动 Double Buffer）

**代表算子**: Tanh、Exp、Erf、Gelu 等（Kernel 版本，使用 `ElementwiseSch` 调度器）

**特征**:
- 使用 `DAGSch<Outputs, void, MemCfg>` 编译期 DAG 描述
- 使用 `MemOptCfg<MemLevel::LEVEL_2>` 启用 Level 2 内存优化
- **自动应用 Double Buffer**，但 BufferNum 不是固定的 2
- **每个中间计算节点占用恰好一个 blockLen 大小的 UB buffer slot**
- 中间计算在 VREG（向量寄存器）中完成，不额外消耗 UB

**UB 分配方式** (来自 `elementwise_sch.h:55` 和 `dag.h:362-367`):

```cpp
// 总 UB 分配
TotalUB = ubFormer * MaxDtypeBytes * BufferNum

// BufferNum 计算公式 (Level 2, 对简单 DAG 为默认)
BufferNum = tempCalcNodeSize + InputSizeWoScalar * BUF_PING_PONG + lvl12Mte3Count * BUF_PING_PONG
//        = tempCalcNodeSize + InputSizeWoScalar * 2       + lvl12Mte3Count * 2
```

以 **Tanh DAG** 为例:
```
DAG 拓扑: CopyIn(half) -> Cast(to float) -> TanhCustom -> Cast(to half) -> CopyOut
InputSizeWoScalar = 1
lvl12Mte3Count    = 1
tempCalcNodeSize  = 3 (Cast + TanhCustom + Cast，均为节点计数)

BufferNum = 3 + 1*2 + 1*2 = 7
TotalUB = ubFormer * 4 * 7 = ubFormer * 28 bytes
```

**关键特征**:
- `BUF_PING_PONG = 2` 固定，但 **仅作用于数据搬运节点**
- **Intermediate nodes are single-buffered**（不乘 2）
- `tempCalcNodeSize` 是**节点计数**（不是字节数）——这是编译期从 DAG 拓扑推导的
- DAG 模式中，每个被调度节点（如 `TanhCustom`）继承自 `ElemwiseUnaryOP`（`TempSize=0`），中间计算全在 VREG 中，所以一个节点 = 一个 UB slot
- **无 sharedTmpBuffer**——所有 buffer 统一在 `tensorPool` 中管理

---

### 模式 3: 手动管理 / 直调模式（显式 Double Buffer + sharedTmpBuffer）

**代表算子**: `weight_quant_batch_matmul_v2`、`nsa_compress_attention_infer`、`swin_transformer_ln_qkv_quant`、以及大量 `clip_by_value`、`cast`、`add_rms_norm` 等直调算子

**特征**:
- 不通过 DAG 框架，手动调用 `pipe_->InitBuffer()` 逐个分配 buffer
- 使用 `TQue<QuePosition::VECIN, BUFFER_NUM>` 管理数据搬运
- 使用 `TBuf<>` 或偏移量布局 sharedTmpBuffer
- 调用 High-Level API（如 `SoftMax()`, `LayerNorm()`）时需要预留 sharedTmpBuffer
- **亲自处理 GM↔UB 数据搬运 → 必须使用 Double Buffer**

**UB 分配方式**（来自开源示例中的官方范式）:

```cpp
// Host 端 Tiling 中计算的总 UB 约束:
tileLength * typeSize = (UB_SIZE - systemReserve - tmpBufferSize) / BUFFER_NUM

// Device 端:
pipe->InitBuffer(inQueue,  BUFFER_NUM, tileLength * sizeof(T));   // 输入 (双缓冲)
pipe->InitBuffer(outQueue, BUFFER_NUM, tileLength * sizeof(T));   // 输出 (双缓冲)
pipe->InitBuffer(tmpBuf,   tileLength * sizeof(float));           // sharedTmpBuffer (单份)
```

**实例** — RopeMatrix（来自 AtomGit ops-transformer，生产级代码）:
```cpp
constexpr int32_t DOUBLE_BUFFER = 2;
pipe->InitBuffer(inQueueX,   DOUBLE_BUFFER, sinSize);       // 10KB, 双缓冲
pipe->InitBuffer(outQueueY,  DOUBLE_BUFFER, sinSize);       // 10KB, 双缓冲
pipe->InitBuffer(inQueueCos, DOUBLE_BUFFER, sinSize);       // 10KB, 双缓冲
pipe->InitBuffer(inQueueSin, DOUBLE_BUFFER, sinSize);       // 10KB, 双缓冲
pipe->InitBuffer(xBuf,       sinSize32);   // 20KB, TBuf (VECCALC, 单份)
pipe->InitBuffer(cosBuf,     sinSize32);   // 20KB, TBuf (单份)
pipe->InitBuffer(sinBuf,     sinSize32);   // 20KB, TBuf (单份)
```

**核心洞察——sharedTmpBuffer 的生命周期**:

`tmpBufferSize`（sharedTmpBuffer）**不是按 tile 分配，而是按 core 分配一次**。所有 tile 迭代**复用**同一块 sharedTmpBuffer。这是因为它只是 High-Level API 内部的 scratch space——每个 tile 的处理纯粹是顺序的，前一 tile 的结果不会保留到后一 tile。因此它不在 BF 因子内，而 input/output queue 是 ping-pong 的（需要两份来实现流水线掩盖搬运延迟）。

---

## 五、GetXxxMaxMinTmpSize 与 GetXxxTmpBufferFactorSize 完整行为模式

### 5.1 全量统计

| 模式 | 数量 | 说明 |
|------|------|------|
| **模式 A**: 同时有 FactorSize + MaxMinTmpSize | **26** | 全部有 `#pragma begin_pipe(V)` |
| **模式 B**: 仅有 MaxMinTmpSize | **~15** | 同样全部 Vector |
| **模式 C**: 零开销（直接返回 0） | **2** | silu, swish |

### 5.2 模式 A：双 API 算子（26 个）

**共同特征**: `maxTmpBuffer = maxLiveNodeCount × inputSize × typeSize + extraBuf`

即 tmpBuffer 与 tile 大小呈**线性正比**，且比例系数（`maxLiveNodeCount`）仅依赖 typeSize。

文档中发现了**两种数学等价的表述**：

| 公式变体 | 表达式 | 算子数 | 代表 |
|---------|--------|--------|------|
| 旧版（从 UB 反推） | `iterationSize = (remainFreeSpace - extraBuf) / maxLivedNodeCnt / typeSize` | 10 | acos, asin, ceil, clamp, erf, erfc, floor, fmod, frac, trunc |
| 新版（从数据正推） | `maxTmpBuffer = maxLiveNodeCount * inputSize * typeSize + extraBuf` | 22 | tanh, exp, log, sin, cos, sigmoid, geglu, swiglu, mean... |

**按类别分布**:

| 类别 | 算子 | 特殊说明 |
|------|------|---------|
| math (23) | acos, acosh, asin, asinh, atan, atanh, axpy, ceil, clamp, cos, cosh, digamma, erf, erfc, exp, floor, fmod, frac, lgamma, log/log10/log2, power, round, sign, sin, sinh, tan, tanh, trunc, xor | power: 双输入特殊6参数; round: 多 ascendcPlatform 参数; exp: MaxMinTmpSize 返回 bool |
| activation (2) | geglu, swiglu | |
| reduce (1) | mean | |

### 5.3 模式 B：仅 MaxMinTmpSize 算子（~15 个）

**共同特征**: 临时缓冲区无法化简为 `常数 × tileSize` 的形式，因为它依赖**额外参数**（归一化轴、组数、isBasicBlock 等）。

| 子类 | 算子 | 额外依赖参数 | 原因 |
|------|------|------------|------|
| 归一化类 | layernorm, batchnorm, rmsnorm, deepnorm, groupnorm, normalize, welfordfinalize, layernorm_grad, layernorm_grad_beta | axis, groupNum, isComputeRstd, isBasicBlock... | tmpBuffer 依赖归一化轴长度而非总 shape |
| SoftMax 类 | softmax (含 Flash/FlashV2/FlashV3), logsoftmax | axis（reduction axis） | tmpBuffer 依赖 reduction axis 长度 |
| 激活类 | gelu, sigmoid, reglu | —（但内部 buffer 切分复杂） | 需要复杂的子 buffer 划分 |
| Reduce 类 | reduce(6), reduce_xor_sum, sum | reduction axis 和模式 | |
| 其他 | cumsum | isLastAxis | 沿不同轴的 tiling 策略不同 |

**关键结论**: 这些算子**并非 maxLiveNodeCount=0**，而是 **maxLiveNodeCount 无法抽象为一个与 shape/axis 无关的常数**。例如 LayerNorm 的 tmpBuffer 随归一化轴长度线性增长，但如果用户改变了归一化轴，系数就变了。

### 5.4 为什么仅 Vector 算子有 FactorSize？

经调研，`highlevel_api/lib/` 下所有 123 处 `#pragma begin_pipe` 全部为 `V`。**库里没有 M（MIX）或 C（Cube）的高阶 API声明**。Cube 算子（如 MatMul）不使用 `GetXxxMaxMinTmpSize`/`GetXxxTmpBufferFactorSize` 机制——它们有自己独立的 tiling 体系（`TCubeTiling`、`MatmulTiling`）。

Vector 算子能做到 `tmpBuffer ∝ tileSize * constant`，因为它们是 elementwise 的：不管数据如何排列，每个元素的计算开销恒定。Cube 算子的 tmpBuffer 依赖矩阵维度乘积（M×K×N），这无法简化为一个常数因子。

---

## 六、为什么 DAG 不需要 sharedTmpBuffer，但直调模式需要？

这是一个关键的理解：

### DAG 模式（如 TanhDAG）

```cpp
// TanhCustom 继承自 ElemwiseUnaryOP<T,T> (TempSize=0)
// 直接操作 VREG（向量寄存器），不调用 High-Level API
MicroAPI::RegTensor<float> vregInput, vregInputAbs, vregInputSqr, vregInputMid, vregOutput;
// 7 个 VREG 寄存器，全部中间计算在寄存器中完成
// → 不需要 sharedTmpBuffer
// → tempCalcNodeSize 只需 = 1（输出占一个 UB slot）
```

DAG 中的每个节点是**为框架特化重写**的底层实现，不通过通用 API 间接调用。

### 直调模式

```cpp
// 调用 High-Level API
AscendC::Tanh(dstLocal, srcLocal, sharedTmpBuffer, bufferSize);
// AscendC::Tanh 内部需要 tmpBuffer 来存储中间转换结果
// → 必须通过 GetXxxMaxMinTmpSize 查询并预留 sharedTmpBuffer
```

直调模式调用的是**通用 High-Level API**，它内部的中间计算（如 half→float 转换缓冲、clip 缓冲）可能超过 VREG 容量。而且多个 High-Level API 串联时（Sigmoid→Tanh→Exp），中间结果必须在 UB 中传递。

**核心法则**：
- `GetXxxMaxMinTmpSize` 用于**辅助直调模式开发者计算 sharedTmpBuffer 大小**
- DAG 模式不使用它，因为 DAG 节点是手写 MicroAPI
- sharedTmpBuffer **一次性分配，所有 tile 复用**，故不在 Double Buffer 因子内

---

## 七、对原始猜想的最终修正

原始猜想：
```
f = f_shared + f_tmp + BF * (f_in + f_out)
tileNum = AlignDown(UB_SIZE / f, ALIGN_PARAM)
```

经验证的**直调模式实际公式**：
```
tileLength = AlignDown( (UB_SIZE - systemReserve - tmpBufferSize) / BUFFER_NUM / typeSize, ALIGN_PARAM )
```

经验证的**DAG 模式实际公式**：
```
TotalUB  = ubFormer * MaxDtypeBytes * BufferNum
// BufferNum = N_temp + 2 * N_input + 2 * N_mte3_output (编译期确定)
// ubFormer 由闭源 Tiling 运行时库在 launch 时计算
```

**修正要点**：
1. `tmpBufferSize`（sharedTmpBuffer）是固定字节开销，不是乘数因子——它在直调模式的公式中直接减掉，不在 Double Buffer 因子内
2. BF=2 的确认正确——BUFFER_NUM 或 DOUBLE_BUFFER_NUM 恒为 2
3. `f_in` 和 `f_out` 在直调模式中共享同一个 buffer slot（`typeSize` 即代表），不是分别计算的
4. DAG 模式中，Double Buffer 仅作用于数据搬运节点（CopyIn/CopyOut），中间计算节点是单缓冲的

---

## 八、深度分析：tmpBuffer 的最优分配策略——"越大越好"是否正确？

### 8.1 问题的本质

调研发现一个关键矛盾：

- **官方文档**（`tanh.h:37-38`）说：*"Generally, the more space you allocate, the better performance you will achieve, and the performance reaches peak when buffer size is maximum"*
- **官方 Host 端代码**（示例中的 `TilingFunc`）中：`auto tmpSize = ubSize >= tmpMax ? tmpMax : ubSize`
- **但直调模式中**，tmpBuffer 和 InputQueue/OutputQueue 共享同一块 UB，三者**零和竞争**

那么如果 tmpMax 接近甚至超过 UB_SIZE（例如 Tanh(fp16) 面对 1M 元素输入时，tmpMax ≈ 4MB >> 192KB UB），会发生什么？

```cpp
auto tmpSize = ubSize >= tmpMax ? tmpMax : ubSize;  // → 192KB (ubSize)
uint32_t ubAvailable = (ubSize - 1024 - tmpSize) / 2; // → 负数！崩溃！
```

结论：**Host 端 Tiling 公式隐含假设 tmpMax << ubSize**。当此假设不成立时，公式直接崩了。

### 8.2 两条代码路径的 UB 分配模型截然不同

理解这个矛盾的关键在于：**"越大越好"的建议和"零和竞争"的担忧，分别适用于不同的代码路径。**

| | 路径 A: High-Level API 调用 | 路径 B: 直调算子（手动 Pipeline） |
|---|---|---|
| **I/O Queue 在哪里？** | **调用者**管理，不在 API 内部 | **和 tmpBuffer 共享同一块 UB** |
| **`sharedTmpBuffer` 的角色** | **就是 compute tile 本身** | 只是三个竞争者之一 |
| **API 内部的循环** | `splitSize = tmpBufferSize / sizeof(T)` | `stackSize = tmpBufferSize / procedureCount` |
| **零和竞争？** | **不存在**（API 内只有 compute） | **存在**（三个 buf 抢一块 UB） |
| **"越大越好"成立？** | **成立**：减少 loop overhead、减少 barrier | **不成立**：挤占 I/O 带宽，导致 MTE2-bound |
| **`GetXxxMaxMinTmpSize` 使用方式** | 查 `maxValue`，用它作为 buffer 大小 | 查 `[min, max]` 范围，**开发者自行选择** |

**路径 A 的详细流程**（以 Tanh(fp16) 为例）：

```
调用者代码:
  1. 自己用 TQue VECIN/VECOUT 做 CopyIn/CopyOut (Double Buffer)
  2. 单独分配一个 TBuf<VECCALC> 作为 sharedTmpBuffer
  3. 调用 AscendC::Tanh(dst, src, sharedTmpBuffer)

AscendC::Tanh 内部 (tanh_common_impl.h:89-145):
  stackSize = tmpBufferSize / 2 / 32 * 32    // half: 2 个 scratch buffer
  将 sharedTmpBuffer 切为 tempTensorConv + tmpClip 两份
  for i in [0, calCount/stackSize]:
    对 [i*stackSize : (i+1)*stackSize] 这段数据做 Tanh 计算
```

**这里 I/O 和 compute 在调用者层面是解耦的**——调用者的 I/O queue 和 Tanh 的 tmpBuffer 互不干扰。

但调用者层面的 UB 分配**仍然面临零和约束**：

```
UB_SIZE = I/O_queues + sharedTmpBuffer + systemReserve
```

所以"越大越好"的建议对调用者来说依然有 trade-off ——只是这个 trade-off 在 API 外部。

### 8.3 Vector Core 处理能力上限：算力确实是过剩的

从 CANN 头文件 `kernel_utils_constants.h` 中提取的硬件参数：

| 参数 | 值 | 说明 |
|------|-----|------|
| Vector 寄存器位宽 | **256 bits** | 即 32B per block |
| 每 repeat 处理 fp16 元素 | **128** (=256/16) | 一个指令周期 |
| 每 repeat 处理 fp32 元素 | **64** (=256/32) | |
| 最大 repeat 次数 | **255** | `MAX_REPEAT_TIMES` |
| 单次 intrinsic 最大 fp16 元素 | **32,640** (≈65KB) | 255 × 128 |
| 单次 intrinsic 最大 fp32 元素 | **16,320** (≈65KB) | 255 × 64 |
| UB 大小 (910B) | **192 KB** | |
| UB 大小 (旧 910) | **256 KB** | |
| UB 大小 (310/dav_c300) | **248 KB** | |
| MTE 管道数 | **3** (MTE1, MTE2, MTE3) | profiler 指标 |

**关键推论**：即使在最极端的 UB 分配下——比如 I/O 各只有 8KB 的 tile（4K fp16），Vector 一次 intrinsic（MaxRepeat）可以处理 32K fp16 元素——**Vector 的算力远超单次 I/O 搬运能供给的数据量**。

对于 Tanh 这种 elementwise 算子，算术强度 ≈ 8 flops / 2 bytes = 4 ops/byte（half），而 Vector Core 的 peak compute 通常在 tens of TFLOPS。**算力永远是过剩的，瓶颈永远是数据搬运。** 这印证了你的判断——优先最大化 I/O tile size。

### 8.4 最优策略：什么时候用 tmpMax？什么时候不该？

#### 场景 1：纯粹调用 High-Level API（无自定义 I/O pipeline）

如果只是 `AscendC::Tanh(dst, src, tmpBuf)` —— 且调用者的 I/O 由上层框架管理：

- **使用 tmpMax**（= 把所有可用的 UB 分配给 tmpBuffer）
- 因为调用者 I/O 不在你的 UB 竞争范围内

#### 场景 2：直调模式自定义算子（你手动管理 I/O pipeline）

这就要用优化视角看问题。tmpBuffer 和 I/O queue 都争夺 UB，而数据搬运是瓶颈：

```
目标: 最大化 MTE 吞吐量 = f(tileLength)
约束: UB_SIZE = systemReserve + tmpBufferSize + BUFFER_NUM × tileLength × typeSize
变量: tmpBufferSize ∈ [tmpMin, tmpMax]
```

**正确的分配策略应该是**：

```
第一步：确定能让 Vector 流水线化的最小 tmpBuffer（通常 stackSize ≈ 几K元素就够）
第二步：将剩余 UB 尽可能多地分配给 I/O queue（增大每次 CopyIn/CopyOut 的数据量）
第三步：如果 tmpBuffer 在 min 值处就成了瓶颈（API 内部 loop 太多 → barrier overhead 过大），逐步增大
```

**具体来说**，对于 Tanh(fp16) 直调算子（910B 192KB UB）：

```
方案 A（Official naive）: tmpSize = min(tmpMax, ubSize) ≈ 192KB
  → I/O tile ≈ 0 KB  → 算子根本跑不了

方案 B（均衡策略）: tmpSize = tmpMin ≈ 128B (= 2×1×64 for 1 fp16 element)
  → I/O tile ≈ (192KB - 1KB - 128B) / 2 ≈ 95KB  → 每次搬运 ~47K fp16 元素
  → 但 API 内部 loop 次数巨大 (if 128B buffer → stackSize = 16 fp16 → 海量 barrier)

方案 C（最优平衡点）: tmpSize = 需要配合一次 MaxRepeat 计算的大小
  → stackSize = 32,640 fp16 (≈65KB, 一个 MaxRepeat 能处理的量)
  → tmpBuffer(half) = stackSize × 2 = 130KB (TempTensorConv + TmpClip)
  → 但 130KB + I/O 双缓冲 = 超过 192KB!
  
  实际需要：
  → I/O tile = 32K fp16 = 64KB (正好一次 MaxRepeat)
  → DoubleBuffer I/O: 64KB × 2 = 128KB
  → tmpBuffer(half): 需要 stackSize = 32K fp16 → 32K × 4 + 32K × 4 = 256KB ...也不够

方案 D（更实际的平衡）:
  → tmpBuffer: stackSize = 4K fp16 (1/8 MaxRepeat, loop 8次也还好)
  → tmpBuffer(half) = 4K × 4B + 4K × 4B = 32KB
  → I/O tile = (192KB - 1KB - 32KB) / 2 / 2B ≈ 39K fp16 元素
  → 每次 CopyIn: 39K × 2B ≈ 78KB (接近单次 MaxRepeat 处理上限)
  → API 内部循环: 39K / 4K ≈ 10 次 loop
```

这个配置下，**I/O tile 达到 ~78KB（接近 UB 能分配的上限），Vector 用约 10 次循环算完，MTE 搬运是瓶颈**——这正是正确的优化方向。

#### 场景 3：DAG 自动调度（你写 DAG 描述，交给框架）

- 不需要你手动选择——**DAG 框架自动处理最优分配**（通过图着色算法 + `MemOptCfg`）

### 8.5 结论：官方建议的适用范围

**"越大越好，maxValue 时性能最优" —— 这个建议仅适用于 High-Level API 内部**（路径 A），即：

- 你只关心 "给定 UB 上的一段数据，调用 Tanh/Sigmoid/Exp 能多快算完"
- I/O 搬运由上层框架 / 其他代码负责
- 此时 tmpBuffer 越大 = API 内部 loop 越少 = barrier/mask/setup 固定开销摊销得越好

**对于直调模式自定义算子开发者（路径 B）**，正确的做法是：

1. `GetXxxMaxMinTmpSize` 返回 `[min, max]` 范围，这是让开发者**自行选择**的（注释原文："The developer selects a proper space size based on this range"）
2. 选择不是 "越大越好"，而是**在数据搬运吞吐量和计算流水线效率之间找最优平衡点**
3. 因为 Vector Core 算力过剩，**瓶颈永远是 MTE 数据搬运**——应该优先最大化 I/O tile size
4. 只在 I/O tile 达到 UB 容量上限后（受限于总 UB），才考虑增大 tmpBuffer 以进一步减少 API 内部 loop 次数

### 8.6 MTE2-Bound 风险的量化

回到用户举的极端例子：UB 98% → tmp，1% → I/O 各：

- 设 UB = 192KB，则 I/O tile ≈ 0.96KB ≈ 480 个 fp16
- 每次 CopyIn/CopyOut: 480 × 2B ≈ 960B（不到 1KB）
- 而 MTE 搬运本身有固定启动延迟（~cycles），搬运 960B 的开销几乎全是 latency，不是 bandwidth
- **算子必然 MTE2-bound，性能极差**

反过来，用方案 D：
- I/O tile ≈ 78KB per buffer，每次搬运 78KB
- Double Buffer 让 MTE 在 Vector 计算的同时搬运下一块数据
- 搬运的 latency 被很好地掩盖了
- **瓶颈仍然在 MTE（但这是 elementwise 算子的固有特性，不是 UB 分配失误导致的）**

---

## 九、调研局限与未解问题（续）

1. **ubFormer 的运行时计算**对于 DAG 模式仍然是闭源的——它在编译后的 Tiling 运行时库（.so）中计算，无法直接观察

2. **Cube 算子的 UB 分配**超出本次调研范围——它们不使用 High-Level API 的 sharedTmpBuffer 机制

3. **Mode 1（High-Level API）与 Mode 3（直调模式）之间的灰色地带**：当一个直调算子链式调用多个 High-Level API 时，如何分配 sharedTmpBuffer 的大小没有统一标准——官方文档建议在 `[tmpMin, tmpMax]` 范围内选择，越大性能越好

4. **推荐验证对象**：如果想通过实际编译运行来观察 UB 分配行为，建议以 **Asin/Tanh 的直调模式自定义算子**（官方示例中的 KernelLaunch 形式）为起点——它们有开源的 Host 端 Tiling 代码，并且 UB 分配逻辑完全可见

---

## 十、参考来源

### 本地代码库
- `/usr/local/Ascend/ascend-toolkit/8.2.RC1/aarch64-linux/ascendc/include/highlevel_api/` — 123个 Vector 高阶 API 声明
- `/usr/local/Ascend/ascend-toolkit/8.2.RC1/opp/built-in/op_impl/ai_core/tbe/impl/ascendc/` — DAG/直调模式内核实现
- `/usr/local/Ascend/ascend-toolkit/8.2.RC1/atc/lib64/libtiling_api.a` — 闭源 Tiling 运行库（含 75 个 `Get*MaxMinTmpSize` 实现）

### 开源社区
- Gitee Ascend Samples (v1.7-8.3.RC1): 官方 Vector 算子示例，含完整 Host 端 Tiling 代码
- AtomGit ops-transformer: 生产级算子实现 (RopeMatrix, FlashAttention 等)
- CANN 8.0.0 AscendC 算子开发接口参考 (PDF): `GetCellMaxMinTmpSize`, `GetCellTmpBufferFactorSize` 等 API 的官方文档
- CANN 8.3.RC1 多核 Tiling 开发指南
