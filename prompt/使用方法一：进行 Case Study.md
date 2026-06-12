# Claude Code Log 使用方法一：进行 Case Study

1. （可选）准备原始算子工程文件的路径
2. 把 `<session-id>.jsonl` 文件，以及其同名的文件夹（里面是 subagent 的 jsonl 文件），复制到本目录的 `session` 文件夹（这一步无法自动完成，需要你手动给 Claude 提供你的 .claude 文件夹位置，并且最好能够提供 `session-id`）
3. 使用本目录 `text-extractor` 文件夹里面的 python 文件，提取压缩后的上下文到 `extraction` 文件夹
4. 修改以下提示词：（把 `<算子名称>` 替换为你想要的算子，并且可以提供原始的算子工程文件）

---

我有一个项目，是外接一个知识库来供 Agent 查阅，以便生成高质量的 Ascend C 算子。
我需要研究下面的问题，进行一个 Case Study：

## 研究目标

我们要研究：
1. **两边分别遇到了什么问题**
   - 特别关注点：without-exp 遇到的问题中，有没有 knowledge 里面已经记载的
2. **两边分别是如何解决的**
   - 特别关注点：with-exp 在遇到 bug 或瓶颈时，是否有重新搜索 knowledge
3. **两边分别取得了什么成果**
4. （可选）**为什么使用了经验，with-exp 反而比 without-exp 要差/持平？**

## 资源位置

### 压缩后的上下文文件
路径：`/data/ypx-dev/workspace/claude-code-log/extraction/`

上下文文件的使用方法参见：`/data/ypx-dev/workspace/claude-code-log/extraction/extraction 文档阅读指引.md`

### Session ID 映射

#### <算子名称> 算子
| 版本 | Session ID | 算子工程路径 |
|------|------------|--------------|
| with-exp | 待填写 | 待填写（可选） |
| without-exp | 待填写 | 待填写（可选） |

## 输出要求

你需要给我提供两份文件，都使用 markdown 形式：

1. **<算子名称>(with-exp) 的 "问题-解法-效果" 统计**
2. **<算子名称>(without-exp) 的 "问题-解法-效果" 统计**，其中重点关注哪些问题是 with-exp 使用经验快速解决了的
3. **<算子名称>有经验和无经验的对比报告**：遇到的问题对比、有哪些区别于对方的做法、为什么更好/更差。

## 工作建议

请你善于使用 subagent 来进行调查，不必事事亲力亲为。当 subagent 出现 API 错误时，请多重试几次，非必要不要亲自调查。

建议的调查方式：
- 先阅读 extraction 中的主 session 了解整体流程
- 根据问题类型深入对应的 subagent 文件（这一步是必要的）
- 对比两个 session 在相同阶段的不同处理方式
- 搜索 knowledge 相关内容来判断是否使用了经验库
- 最后写报告的时候，可以灵活借鉴 SwiGLU(with-exp) 的写法（如果存在的话）：使用时间顺序介绍每一个“问题-解法-效果”三元组
- 报告输出到本目录的 work 文件夹