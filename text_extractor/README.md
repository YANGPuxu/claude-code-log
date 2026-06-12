# Text Extractor

从 Claude Code JSONL 转录文件中提取和过滤文本内容。

## 功能

- 从主 session 和 subagent 文件中提取文本
- 支持内容类型过滤（text, thinking, tool_use, tool_result）
- 支持消息类型过滤（user, assistant, queue-operation 等）
- 压缩输出，显著减少文件大小

## 使用方法

### 准备 session 文件

确保 session 文件目录结构如下：

```
session/
  {session-id}.jsonl          # 主 session 文件
  {session-id}/                # 同名目录
    subagents/                 # 子 agent 文件夹
      agent-xxx.jsonl
      agent-yyy.jsonl
```

**重要**：主 `.jsonl` 文件必须与同名目录在同一级别。

### 提取单个 session

```bash
python -c "from text_extractor.cli import cli; cli(['extract', '/path/to/session/{session-id}.jsonl', '-o', '/path/to/extraction'])"
```

或者使用命令行：

```bash
python -m text_extractor.cli extract /path/to/session/{session-id}.jsonl -o /path/to/extraction
```

### 提取多个 session

```bash
for f in /path/to/session/{session-id-1},{session-id-2},{session-id-3}.jsonl; do
  python -c "from text_extractor.cli import cli; cli(['extract', '$f', '-o', '/path/to/extraction'])"
done
```

## 输出结构

提取后的文件结构：

```
extraction/
  {session-id}/
    {session-id}.json           # 主 session 提取结果
    agent-xxx.json              # subagent 提取结果
    agent-yyy.json
```

## 输出文件格式

每个提取的 JSON 文件包含：

```json
{
  "session_id": "uuid",
  "agent_type": "agent-type",
  "agent_description": "description",
  "messages": [
    {
      "uuid": "message-uuid",
      "type": "user|assistant",
      "timestamp": "ISO-8601",
      "content_text": "提取的文本内容"
    }
  ],
  "statistics": {
    "original_lines": 1000,
    "original_size_kb": 1000.0,
    "filtered_lines": 100,
    "filtered_size_kb": 50.0,
    "compression_ratio": "95.0%"
  }
}
```

## 从 rushdog.cicd 提取 session 的完整流程

### 1. 找到 session-history 目录

```bash
ls /data/wenjingwen/rushdog.claude/rushdog-cicd/runs/{run-id}/{operator}/{variant}/session-history/
```

### 2. 复制 session 目录

```bash
cp -r /path/to/session-history/{session-id} /path/to/session/
```

### 3. 调整文件结构

确保主 `.jsonl` 文件在 session 目录下，与同名目录平级：

```bash
# 如果文件在子目录中，移动出来
mv /path/to/session/{session-id}/{session-id}.jsonl /path/to/session/
```

### 4. 提取

```bash
python -c "from text_extractor.cli import cli; cli(['extract', '/path/to/session/{session-id}.jsonl', '-o', '/path/to/extraction'])"
```

## 配置

可以通过 YAML 配置文件自定义过滤行为（可选）：

```bash
python -m text_extractor.cli extract /path/to/file.jsonl -c config.yaml -o /path/to/extraction
```

## 命令

- `extract`: 提取单个 JSONL 文件及其 subagents
- `extract-project`: 提取项目目录中的所有 JSONL 文件
