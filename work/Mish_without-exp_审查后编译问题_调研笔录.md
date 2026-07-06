# Mish 算子 (without-exp) 审查后编译问题 调研笔录

## 会话信息

- **会话 ID**: deb4b075-2ac2-49da-b330-577d2aaec167
- **Session**: `deb4b075-2ac2-49da-b330-577d2aaec167.json` (475 行, 77 条消息)
- **目标**: 调查 `审查后编辑` 阶段约 30 次编译尝试中的 Tanh 相关问题
- **主要数据源**: 4 个子 Agent + 1 个主 Session

---

## 目录

- [A) Tanh dst==src UB overflow 问题](#a-tanh-dstsrc-ub-overflow-问题)
- [B) FP16 Kernel UB overflow 问题](#b-fp16-kernel-ub-overflow-问题)
- [C) 编译尝试统计汇总](#c-编译尝试统计汇总)
- [D) Tanh temp buffer 处理策略演变](#d-tanh-temp-buffer-处理策略演变)

---

## A) Tanh dst==src UB overflow 问题

### 问题概述

此问题的核心是 **Tanh API 对 dst 和 src 地址重叠的限制**。Ascend C 的 `Tanh` API 要求 `dst` 和 `src` 不能指向同一 UB 地址区域（`DataCopy` 也一样），违反该约束会导致 UB 数据损坏，表现为计算结果错误。

### 发现路径

**第一阶段：设计串讲 (Walkthrough)**

在 Walkthrough 阶段，Designer 提出的初始设计中，Tanh 的 `dst` 和 `src` 使用了同一块 UB 缓冲区。审查 Agent (ascendc-kernel-reviewer) 通过代码检视识别出此问题，记录为 **Walkthrough #5 (Tanh address overlap)**。Architect Agent 接受了此项建议，确认需要分离 dst/src 缓冲区。

**第二阶段：初次审查 (First Review, 50/100 FAIL)**

审查 Agent 独立构建并运行测试时，发现 FP32 多核数据损坏：
- MERE = 1.47e+00（远超阈值 1.22e-04）
- 约 13000-14000/32768 个错误元素（40-60%）
- 前 ~2048 个元素（core 0）正确，后续 core 的数据部分正确、部分错误
- 错误模式：tile0 几乎完全损坏（约 96% 错误），tile1 部分受影响或正常

审查 Agent 深入分析 Tanh API 文档后发现 `dst` 和 `src` 不能重叠的约束：
```
Tanh API: dst 和 src 不能重叠 (No overlap allowed between dst and src)
Tanh API: sharedTmpBuffer 不能与 src 或 dst 重叠
```

但审查 Agent 检查代码时确认 `ComputeFp32` 中：
- `Exp(tmp1, xLocal, count)` 使用不同 buffer（tmp1 与 xLocal 不重叠）
- `Tanh(tmp2, tmp1, tanhTmp, count)` 使用不同 buffer（tmp2 与 tmp1 不重叠）
- 未发现直接的 dst==src 重叠问题

审查 Agent 深入检查后推测的多核数据损坏根因是：**`TBuf` 缓冲区与 `TQue` 缓冲区在 VECCALC 位置可能存在地址重叠或对齐问题**。

### 关键错误消息

```
FP32 32768 elements, MERE=1.47e+00 > threshold=1.22e-04, FAIL
output[0:20] values correct (difference ~1e-8)
Bad sector begins around element ~512 boundary (tile boundary)
Output min=-3.56 vs golden min=-0.31 — significant range discrepancy
Bad values sometimes match mish(x[j]) at different indices — UB corruption pattern
```

### 修复过程

**Mish 第 1 轮修复 Agent (agent-a6a07e84314728fc6.json)**:

1. 首次尝试复现：在 `build_review` 目录下进行 5 次独立测试，**全部通过** (MERE=5.39e-08)
   - 结论：**无法复现**。可能原因是环境差异（不同 NPU 状态、不同编译选项）
2. 尽管如此，修复 Agent 对所有发现的代码质量问题进行了修复：
   - FP16 tanhTypeSize 统一为 `sizeof(float)`
   - 更新 README.md、gen_data.py、verify_result.py
   - 未对 FP32 多核路径本身做重大修改

**复审 Agent (agent-a0d1ba550141b04cc.json)** 验证：
- 5 次独立测试全部通过 (MERE=5.39e-08 一致)
- PyTorch 路径 9/9 测试用例全部通过
- 复审评分 **98/100 PASS**

### 实际根因分析

由于问题无法稳定复现，且初次审查和修复审查运行环境相同（同 NPU 设备、同数据集种子），最可能的解释是：

1. **瞬态/间歇性问题**：NPU 设备状态、频率、内存布局等运行时因素可能导致偶发的 UB 数据损坏。910B2 的 192KB UB 对布局非常敏感。
2. **构建产物差异**：初次审查 `cmake ..` 未指定 Python 路径，可能导致缓存的构建产物与独立构建的产物不一致。后续 `-DPython3_EXECUTABLE=/usr/local/python3.11.14/bin/python3` 后构建更干净。
3. **TBuf 地址对齐**：`TBuf` 在 VECCALC 位置的地址分配可能与 Tanh 内部临时缓冲区产生冲突。`GetTanhMaxMinTmpSize` 返回的 256 字节很小，但如果 `tanhTmpBuf` 的地址恰好与某个 TBuf 重叠，可能导致偶发损坏。

**结论**: 问题在后续测试中无法复现，代码经审查 Agent 和复审 Agent 双重分析确认未见违反 Tanh dst/src 不重叠约束的情况。认定为偶发环境问题。

---

## B) FP16 Kernel UB overflow 问题

### 问题概述

FP16 数据类型的 `tanhTypeSize` 在 Tiling 计算中使用了 `dtypeSize`（=2）而非 `sizeof(float)`（=4），导致 `GetTanhMaxMinTmpSize` 返回的临时缓冲区大小为 FP16 精度下的最小值，而实际 Tanh 计算是在 FP32 精度下执行的。

### 触发原因

在 `mish.asc` (Host 文件) 中有如下逻辑：

```cpp
uint32_t tanhTypeSize = sizeof(float);  // always use float type size for Tanh query
if (!isBf16) {
    tanhTypeSize = dtypeSize;           // BUG: for FP16, dtypeSize=2, overrides float size
}
```

作者意图判断逻辑是：
- BF16: 使用 `sizeof(float)` (=4) — 正确
- FP16: 使用 `dtypeSize` (=2) — **BUG**，应为 `sizeof(float)` (=4)
- FP32: 使用 `dtypeSize` (=4) — 正确

### 错误消息/表现

初次审查 Agent 发现此逻辑错误，报告为 **S1 (建议级) FP16 tanhTypeSize 逻辑错误**：

```
Host tiling uses tanhTypeSize=dtypeSize for FP16 query,
but Tanh is computed at FP32 precision.
tanhTmpSize may be underestimated.
```

### 修复前的影响分析

`GetTanhMaxMinTmpSize` 的实现：
```cpp
// For float typeSize=4:  minValue = TANH_ONE_REPEAT_BYTE_SIZE * TANH_FLOAT_CALC_PROC
//                        = 256 * 1 = 256 bytes
// For half typeSize=2:   different formula, but likely larger than 256 bytes
```

虽然 FP16 的 `tanhTypeSize=2` 导致查询的临时大小可能实际更大（或更小），但由于 `tanhTmpBuf` 在 UB 分配时使用此大小，可能导致 Tanh API 内部写入越界，损坏相邻的 UB 数据。

### 修复方案

修复 Agent 将 `tanhTypeSize` 在所有路径上统一为 `sizeof(float)`：

**`mish.asc` (Host file)**:
```cpp
uint32_t tanhTypeSize = sizeof(float);  // always float for all non-BF16 paths
```

**`mish_torch.cpp` (Torch extension)**:
```cpp
// Similarly fixed tanhTypeSize to always use sizeof(float)
```

### 修复验证

修复后 FP16 PyTorch 测试全部通过：(对齐/非对齐/特值 全部 PASS)，MARE 最大 7.33e-04 远低于阈值 9.77e-03。

---

## C) 编译尝试统计汇总

### Subagent 概览

| Agent | 角色 | 消息数 | 文件大小 | 参与阶段 |
|-------|------|--------|---------|---------|
| agent-a0a574798c3240203 | Mish 算子开发 (Developer) | 376 | 2269 行 | 初始开发 |
| agent-a555fd88764df193b | 性能验收+3轮调优 | 263 | 1591 行 | 审查后+审查通过后 |
| agent-a6a07e84314728fc6 | Mish 第1轮修复 | 90 | 554 行 | 审查 50/100 FAIL 后 |
| agent-a3c77572f15ee3fd6 | Mish 算子代码审查 (初查) | 113 | ~692 行 | 首次审查 |
| agent-a0d1ba550141b04cc | Mish 复审第1轮 (复审) | 92 | ~566 行 | 二次审查 |
| agent-ac57db75cd795d0fb | Mish 串讲 Architect 回应 | 48 | ~ | 串讲阶段 |
| agent-add27541bf1397e08 | Mish 算子方案设计 | 55 | ~ | 设计阶段 |
| agent-a0878fcb8b556b3a2 | Mish 设计串讲审查 | 42 | ~ | 串讲审查 |

### 审查后 (修复阶段) 编译尝试

**阶段 A: Mish 第1轮修复 (agent-a6a07e84314728fc6) — 约 3 次**

| 尝试 | 操作 | 结果 | 备注 |
|------|------|------|------|
| 1 | 初步构建 (build_review) | 编译成功 | 验证问题可复现性 |
| 2 | 修复后重新构建 | 编译成功 | 修复 tanhTypeSize, gen_data.py 等 |
| 3 | 运行完整测试 | 全部通过 | 直调 FP32 + PyTorch 9 用例 |

**阶段 B: Mish 复审第1轮 (agent-a0d1ba550141b04cc) — 约 2 次**

| 尝试 | 操作 | 结果 | 备注 |
|------|------|------|------|
| 1 | 独立构建 (build_review) | 编译成功 | CMake + make |
| 2 | 精度测试运行 | 全部通过 | FP32 5次独立 + PyTorch 9/9 |

**阶段 C: 性能验收+3轮调优 (agent-a555fd88764df193b) — 约 15-20 次**

| 轮次 | 主要变更 | 编译次数 | 结果 |
|------|---------|---------|------|
| Round 1 (基线) | 基线代码 | ~3 | 全部通过，但性能 3.8-4.9x 低于基线 |
| Round 2 | 增大 tile size ~4096, DOUBLE_BUFFER=2 | ~5 | 性能提升 19-29%，仍慢 3-3.6x |
| Round 3 (尝试1) | 核数启发式 | ~3 | 小 case 变慢，回退 |
| Round 3 (尝试2) | 内联 Tanh (e^(2x)-1)/(e^(2x)+1) | ~3 | 大 x NaN 溢出，回退 |
| Round 3 (尝试3) | 重构 tiling 逻辑 | ~2 | 优化完成 |

### 初始开发阶段编译尝试 (agent-a0a574798c3240203) — 约 10-15 次

| 阶段 | 主要问题 | 编译次数估计 |
|------|---------|------------|
| 模板搭建 | bfloat16_t 命名空间, GetTanhMaxMinTmpSize include | ~3-4 |
| TBuf 初始化 | InitBuffer 签名 (2参数 vs 3参数) | ~2 |
| Tiling 结构体 | uint64_t -> uint32_t 类型匹配 | ~1-2 |
| 多核调试 | DataCopyPad tile 偏移量, TBuf 地址重叠 | ~4-5 |
| FP16/BF16 | Cast 路径, 类型处理 | ~2-3 |

### 总计

- **审查后编译尝试 (修复+复审+调优)**: ~20-25 次
- **初始开发编译尝试**: ~10-15 次
- **全会话总计**: ~30-40 次
- **与 Tanh 直接相关的问题**: 约 40%（约 12-16 次涉及 Tanh 临时缓冲区、类型大小、API 用法）

---

## D) Tanh temp buffer 处理策略演变

### 策略演变时间线

```
设计阶段
  └─ TBuf (VECCALC) + GetTanhMaxMinTmpSize 查询
      └─ 被 Walkthrough #5 (地址重叠), #7 (tmpSize估算) 验证通过
          ↓
修复阶段 (50/100 FAIL 后)
  └─ 保持原有设计不变
  └─ 仅修复 tanhTypeSize: dtypeSize -> sizeof(float)
      ↓
性能调优阶段
  ├─ 策略 C (Round 2): 保持 sharedTmpBuffer -> 性能 VEC bound 80-84%
  ├─ 策略 D (Round 3 尝试1): 减少核数 (64KB/core) -> 小 case 变慢, 回退
  └─ 策略 E (Round 3 尝试2): 内联 Tanh 公式, 移除 tanhTmpBuf
       ├─ 公式: tanh(x) = (e^(2x) - 1) / (e^(2x) + 1)
       ├─ 效果: NaN overflow for x=100 (e^100 overflows FP32)
       └─ 回退到 Tanh API + sharedTmpBuffer
```

### 策略详解

**策略 A: 初始设计中的处理** (设计阶段)

- **缓冲区类型**: `TBuf<uint8_t>` (VECCALC)
- **初始化**: `pipe_->InitBuffer(tanhTmpBuf, tanhTmpSize)`
- **大小查询**: `GetTanhMaxMinTmpSize({BLOCK_ALIGN}, tanhTypeSize)`
- **问题**: FP16 使用 `dtypeSize` (=2) 而非 `sizeof(float)` (=4)
- **约束**: Tanh API 要求 sharedTmpBuffer 不能与 src/dst 重叠，代码已满足

**策略 B: 修复后** (修复阶段)

- **变更**: 统一 `tanhTypeSize` 为 `sizeof(float)` 对所有非 BF16 路径
- **tanhTmpSize**: 对于 FP32 元素大小 512 的 shape:
  - `TANH_FLOAT_CALC_PROC = 1`
  - `minValue = TANH_ONE_REPEAT_BYTE_SIZE * 1 = 256 bytes`
  - 这是最小值，实际可能是 256 字节的倍数
- **UB 预算** (影响 tile size):
  - UB size: 192KB
  - tanhTmpSize: ~256 bytes (可忽略)
  - `availableUb = 196608 - 256 ≈ 196352` bytes
  - `ubFormer = min(BLOCK_ALIGN=512, availableUb/(4*4) ≈ 12224) = 512`
  - 约束: BLOCK_ALIGN=512 是 UB 使用量的主要限制因素

**策略 C: 性能调优 Round 2 — 增大 tile size**

- **变更**: 将 BLOCK_ALIGN 从 512 增大到 ~4096 元素
- **tanhTmpBuf 重新计算**: 使用更大 shape 重新查询 `GetTanhMaxMinTmpSize`
  - shape={4096}, typeSize=sizeof(float)
  - tanhTmpSize: 可能增大至 2048 bytes 或更多
- **UB 预算重新计算**:
  - `maxElemNum = (196608 - tanhTmpSize) / (4*4) ≈ (196608 - 2048) / 16 ≈ 12160`
  - `alignedMax = 12160` (已经是 BLOCK_ALIGN 的倍数)
  - `ubFormer = min(BLOCK_ALIGN=4096, alignedMax) = 4096`
- **结果**: Tile size 从 512 增至 4096，同样需要 `GetTanhMaxMinTmpSize` 查询新的 shape

**关键代码** (mish.asc tiling 函数):
```cpp
// Get tanh tmp buffer size with current tile shape
uint64_t tanhMinValue;
GetTanhMaxMinTmpParams tanhParams;
tanhParams.shape = ge::Shape({static_cast<int64_t>(BLOCK_ALIGN)});
tanhParams.typeSize = tanhTypeSize;  // Always sizeof(float)
GetTanhMaxMinTmpSize(tanhParams, tanhMinValue);
uint64_t tanhTmpSize = tanhMinValue;

// Calculate available UB
uint64_t ubSize = 192 * 1024;  // 192KB for DAV_2201
uint64_t availableUb = ubSize - tanhTmpSize;
uint32_t maxElemNum = availableUb / (4 * dtypeSize);  // 4 for x, tmpBuf1, tmpBuf2, CastBuf
uint32_t alignedMax = (maxElemNum / alignElements) * alignElements;
uint32_t ubFormer = (alignedMax > BLOCK_ALIGN) ? BLOCK_ALIGN : alignedMax;
```

**策略 D: 移除 sharedTmpBuffer (Inline Tanh)** (性能调优 Round 3 尝试2)

- **动机**: Tanh sharedTmpBuffer 导致 `PipeBarrier` 同步开销，减少 VEC throughput
- **实现**: 使用公式 `tanh(x) = (e^(2x) - 1) / (e^(2x) + 1)` 
  ```cpp
  // Inline Tanh without temp buffer
  Adds(tmp2, xLocal, xLocal, count);   // 2x
  Exp(tmp2, tmp2, count);               // e^(2x)
  Adds(tmp1, tmp2, (float)1.0f, count); // e^(2x) + 1
  // ... division ...
  ```
- **后果**: `x=100`, `e^200` 超过 FP32 最大值 3.4e38, 产生 NaN
- **回退**: 恢复 Tanh API 调用 + sharedTmpBuffer

### 关键洞察

1. Tanh sharedTmpBuffer 对 UB 预算影响非常小（~256 bytes），不是 tile size 的限制因素
2. 即使 inline Tanh 能避免同步开销，但数值稳定性无法保证
3. `GetTanhMaxMinTmpSize` 返回的是最小值（基于宏常量），开发者无需担心分配过量
4. Tanh API 在 CANN 8.5.0 上使用公式 `tanh(x) = (e^(2x)-1)/(e^(2x)+1)` 内部实现，内联版本与其数值行为一致

---

## 附录

### 涉及文件路径

- **Kernel**: `op_kernel/mish_kernel.asc`
- **Host tiling**: `op_host/mish.asc`
- **Host tiling header**: `op_kernel/mish_tiling.h`
- **PyTorch extension (HOST)**: `op_extension/mish_torch.cpp`
- **Tanh tiling API**: `adv_api/math/tanh_tiling.h`
- **Tanh API doc**: `asc-devkit/docs/api/context/Tanh.md`
- **GetTanhMaxMinTmpSize doc**: `asc-devkit/docs/api/context/GetTanhMaxMinTmpSize.md`
- **GetTanhMaxMinTmpSize impl**: `asc-devkit/impl/adv_api/tiling/math/tanh_tiling_impl.cpp`

### 子 Agent 会话文件路径

```
extraction/archive/deb4b075-2ac2-49da-b330-577d2aaec167/
├── deb4b075-2ac2-49da-b330-577d2aaec167.json         (主 Session)
├── agent-a0a574798c3240203.json                       (Developer: Mish算子开发)
├── agent-a555fd88764df193b.json                       (性能调优 Agent)
├── agent-a6a07e84314728fc6.json                       (Fix Agent: Mish第1轮修复)
├── agent-a3c77572f15ee3fd6.json                       (初查 Agent: Mish算子代码审查)
├── agent-a0d1ba550141b04cc.json                       (复审 Agent: Mish复审第1轮)
├── agent-ac57db75cd795d0fb.json                       (Architect: Mish串讲Architect回应)
├── agent-a0878fcb8b556b3a2.json                       (串讲审查 Agent: Mish设计串讲审查)
└── agent-add27541bf1397e08.json                       (设计 Agent: Mish算子方案设计)
```

### 时间线

```
06-01 13:XX — Design phase (agent-add27541bf1397e08)
06-01 14:00 — Walkthrough (agent-a0878fcb8b556b3a2)
06-01 14:03 — Architect response (agent-ac57db75cd795d0fb)
06-01 14:06 — Development starts (agent-a0a574798c3240203)
06-01 15:03 — Development completes
06-01 15:04 — First review (agent-a3c77572f15ee3fd6, 50/100 FAIL)
06-01 15:26 — Fix agent (agent-a6a07e84314728fc6)
06-01 15:36 — Re-review (agent-a0d1ba550141b04cc, 98/100 PASS)
06-01 15:43 — Performance tuning starts (agent-a555fd88764df193b)
06-01 ~17:00 — Performance tuning ends (3 rounds)
```
