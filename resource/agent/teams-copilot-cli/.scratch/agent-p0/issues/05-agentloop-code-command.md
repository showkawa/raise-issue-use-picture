# 05 — AgentLoop + `code` 命令（Mock 全路径）

**What to build:** `teams-copilot code "<task>"` 端到端可用（MockProvider 下）：协议注入 → 任务 → 工具执行 → 结果回灌循环直至 DONE/预算耗尽；带预算账本、协议遗忘检测、自动 rotate、审计日志、锁文件与首次外发告示。

**Blocked by:** 02 — 工具调用协议；04 — 工具七件套 + PermissionGate。

**Status:** ready-for-agent

- [ ] AgentLoop 状态机：done/malformed(≤2)/tool_calls 分支、迭代耗尽输出已完成清单并非 0 退出
- [ ] 会话字符预算账本逼近阈值主动 rotate；连续 2 次 malformed 且自由问答形态 → 协议遗忘 rotate（ADR-0004）
- [ ] rotate = 重建 session + 重发协议 + 进度摘要；unhealthy 同路径
- [ ] workspace context：项目根、repo map（gitignore 感知、封顶）、AGENTS.md、@file 引用
- [ ] JSONL 审计日志（入参/diff/exitCode）落盘；项目根锁文件；首次运行外发告示（ADR-0006/0008）
- [ ] commentary 流式打终端；Ctrl+C 一次中断进交互、两次退出
- [ ] MockProvider 集成测试覆盖：happy path、malformed×2 中止、遗忘 rotate、预算 rotate、edit_file 级联、防注入转义、TOOL+DONE、denied 回灌、回声错位
