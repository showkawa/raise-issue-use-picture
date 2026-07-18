# 07 — `implement` 命令（文档驱动流）

**What to build:** `teams-copilot implement` 逐条消费 TASKS.md 未完成任务：每条一次 AgentLoop，成功勾选写回并按触碰文件清单逐文件 commit；失败默认停止且不污染用户工作树。

**Blocked by:** 05 — AgentLoop + `code` 命令。

**Status:** done

- [x] TASKS.md 解析容错：不合规行明确报错、跳过并列出，不静默漏任务
- [x] 失败默认停止：checkbox 不勾、半成品不 commit、不 stash、终端打印 git diff 提示；`--continue-on-failure` 跳过继续（ADR-0005）
- [x] 脏工作树拒绝启动；`--allow-dirty` 放行（ADR-0005）
- [x] 逐文件 `git add <path>`（AgentLoop 触碰清单），禁止 `git add -A`；commit message 含任务编号
- [x] `--task N` 单条执行；prompts/tasks.md 模板改造保证输出可解析
- [x] 临时 git 仓 fixture 测试：失败停止/脏树拒启/--allow-dirty/逐文件 add/checkbox 写回/容错解析
