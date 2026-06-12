# Claude Code Log 使用方法一：将上下文进行 HTML 可视化

1. 把 `<session-id>.jsonl` 文件，以及其同名的文件夹（里面是 subagent 的 jsonl 文件），复制到本目录的 `session` 文件夹（这一步无法自动完成，需要你手动给 Claude 提供你的 .claude 文件夹位置，并且最好能够提供 `session-id`）
2. 告知 Claude Code 以上信息，叫他帮你把 HTML 输出到 output 文件夹并赋予合理的名字，然后拷贝下来用浏览器查看