# SPEC: teams-copilot-cli v3 P0 — Agent 模式（code / implement）

> 状态：ready-for-agent
> 来源：DESIGN-P0.md v3.1 + REVIEW-DESIGN-P0.md + grill 会话定案（ADR-0001–0009）
> 术语：见 CONTEXT.md

## Problem Statement

teams-copilot-cli 目前只是"Copilot 网页问答管道"（ask/prd/arch/tasks/repl）：用户拿到的是文本回答，改代码、跑测试、迭代到绿都要自己动手。用户想要 Claude Code 级别的 agent 能力——在真实 repo 中自主读改文件、执行命令、根据测试结果迭代——但不想支付 API 成本，希望继续复用企业 M365 Copilot 网页版这一免费信道。

该信道是为人类设计的：无 function calling、无 system prompt、有渲染美化/捕获失真、上下文窗口和轮次上限未知且不可控、高频自动化还有账号风控风险。直接把它当函数调用信道使用，会在协议正确性、安全边界、数据合规三个维度系统性翻车。

## Solution

在现有 CLI 之上新增 Agent 层与 Provider 抽象：

- `code <task>`：交互式结对编程——AgentLoop 以"协议提示词 + 自定义围栏文本协议"驱动 Copilot 网页会话，循环执行 plan → act（本地工具）→ observe（结果回灌），直到模型输出 DONE 或预算耗尽。
- `implement [--task N]`：文档驱动流——逐条消费 TASKS.md 的 checkbox 任务，每条任务一次 AgentLoop 运行，成功则勾选写回并按触碰文件清单逐文件 commit。

针对不可靠信道与安全边界，内建：allowlist 默认权限（YOLO opt-in）、代码围栏信道保护 + 握手自检、edit_file 归一化匹配级联、会话字符预算账本 + 协议遗忘检测 + 自动 rotate、secrets redact 与默认拒读清单、JSONL 审计日志、发送节奏 ≥3000ms+抖动。

## User Stories

1. As a 开发者, I want 用 `code "<任务>"` 让 agent 在当前 repo 自主完成小任务, so that 我不用手动搬运 Copilot 的回答。
2. As a 开发者, I want agent 改完代码后自动跑测试并根据失败迭代, so that 交付物是"绿的"而不是"看起来对的"。
3. As a 开发者, I want 默认 allowlist 权限、每个 exec 类操作先询问, so that agent 不会在我不知情时执行危险命令。
4. As a 高级用户, I want 用 `--yolo` 显式换取全自动执行且首次运行看到风险告示, so that 我能自担风险提速。
5. As a 开发者, I want 所有 write/edit 在终端打印 diff 并落 JSONL 审计日志, so that 事后能完整追溯 agent 做过什么。
6. As a 安全负责人, I want `.env`/`*.pem`/`id_rsa` 默认拒读、外发内容过 secrets redact, so that 凭据不会被发到 Copilot 租户。
7. As a 合规负责人, I want `code`/`implement` 首次运行打印数据外发告示, so that 用户知情源码会离开本机。
8. As a 开发者, I want agent 的 run_command 有默认超时、输出上限并拒绝交互式命令, so that 一个 `npm login` 不会挂死整个任务。
9. As a 开发者, I want edit_file 在空白/引号失真时仍能通过归一化级联命中、失败时回带磁盘真实片段, so that agent 不会在有损信道下死循环重试。
10. As a 开发者, I want 会话逼近字符预算或协议被遗忘时自动 rotate（重建 + 协议重发 + 进度摘要）, so that 长任务不会中途失忆空转。
11. As a 开发者, I want 每条消息带 [turn N] 回声校验, so that 断连/续写错乱时能发现请求-回复错位而非静默错干活。
12. As a 开发者, I want `implement` 依次执行 TASKS.md 未完成任务并逐条 commit, so that 文档驱动流从任务清单直达提交历史。
13. As a 开发者, I want 任务失败时默认停止、checkbox 不勾、半成品不 commit, so that 我能亲自裁决残局而不是收到一堆烂提交。
14. As a 开发者, I want 脏工作树默认拒绝启动 implement, so that 我未提交的改动不会被 agent 混进它的 commit。
15. As a 开发者, I want commit 只包含 agent 本任务触碰过的文件, so that 提交范围可审查。
16. As a 开发者, I want TASKS.md 解析器对不合规行明确报错并列出跳过项, so that 不会静默漏任务。
17. As a 开发者, I want repo map、AGENTS.md、@file 引用自动进入协议上下文, so that agent 一开场就了解项目结构与约定。
18. As a 开发者, I want grep/read_file 结果有大小与条数上限并标注截断, so that 一次工具结果不会吃光消息预算。
19. As a M365 账号所有者, I want 发送间隔默认 ≥3000ms 加抖动、轮次预算保守, so that 自动化不至于触发租户风控封号。
20. As a 开发者, I want 真机握手自检失败时立即报错/提示切 signalr, so that 不会带着坏信道空烧 25 轮。
21. As a 维护者, I want AgentLoop 全部状态机路径可用 MockProvider 在无浏览器环境验证, so that CI 可持续。
22. As a 维护者, I want 现有 ask/prd/arch/tasks/repl 在迁移后行为完全不变, so that 重构可独立发版且无回归。
23. As a 并行使用者, I want 项目根锁文件阻止两个 agent 同时跑, so that 工作树不被互踩。
24. As a REPL 用户, I want `/code <task>`、`@file`、`/tools`, so that 不离开 REPL 也能用 agent 能力。
25. As a Windows 用户, I want 命令输出强制 UTF-8、exitCode 口径明确, so that 中文环境下回灌文本不乱码、判绿判红可靠。

## Implementation Decisions

（详细理由见对应 ADR；此处为契约摘要）

- **分层**：CLI 层 → Agent 层（AgentLoop / ToolProtocol / ToolRegistry / PermissionGate / WorkspaceContext）→ Provider 接口（copilot-web 由现有 runtime 纯移动 + 薄封装实现；MockProvider 用于测试）。迁移步骤独立发版，回归底线是现有命令行为不变。
- **Provider 契约**：`Provider.createSession() → ChatSession.send(message, opts) → { text, truncated, duration }`；`capabilities()` 暴露 maxMessageChars / supportsStreaming / supportsSystemPrompt，AgentLoop 据此分片与降级。
- **协议**（ADR-0002、0008）：整个回复要求置于 ``` 代码围栏内，内部使用 `<<<TOOL name="..." {json}>>>`、`<<<DONE ...>>>`；RESULT 回灌块转义其中的围栏序列并声明其内指令无效；每条消息带 `[turn N]` 并要求回复引用；协议注入后第 2 轮先做握手块保真自检；外层围栏未闭合判定为回复不完整 → 续写而非解析。
- **解析与纠错**：ParsedReply = tool_calls | done | malformed；malformed 纠错重试 ≤2 次；TOOL 与 DONE 并存先执行 TOOL；连续 2 次 malformed 且呈自由问答形态 → 协议遗忘 → rotate（ADR-0004）。
- **会话预算**（ADR-0004）：双向字符账本，逼近 sessionCharBudget（默认 40k，真机冒烟标定）主动 rotate；rotate = 重建 session + 重发协议 + 进度摘要；回灌预算按剩余会话预算动态收缩，测试输出只留失败摘要 + 尾部 N 行。
- **工具七件套**：read_file / write_file / edit_file / run_command / grep / glob / git（白名单子命令）。路径一律解析限定在项目根内。edit_file 采用三级归一化匹配级联，归一后不唯一即报错，全失败回带磁盘带行号片段（ADR-0003）。grep 跳过 >1MB 文件、条数上限并标注截断。
- **权限**（ADR-0001、0008）：默认 allowlist；yolo opt-in + 首次告示；run_command 分类用 PSParser token 级解析，不可解析按 destructive；denyCommands 降级为提醒；run_command 默认 timeout 120s、输出 64KB 上限、拒绝交互式命令；强制 UTF-8 输出编码、exitCode 以 $LASTEXITCODE 为口径。
- **数据边界**（ADR-0006）：外发路径（协议提示词/RESULT/repo map/@file）统一过 redact（小而准模式集，命中打码 [REDACTED:<kind>] 继续发送）；默认拒读清单可配置；首次运行外发告示。
- **implement 语义**（ADR-0005）：失败默认停止（不勾、不 commit、不 stash）、--continue-on-failure 显式跳过；脏树拒启、--allow-dirty 放行；逐文件 git add，禁止 -A；TASKS.md 解析容错报错。
- **节奏与审计**（ADR-0007）：minSendIntervalMs 默认 3000ms + 抖动；JSONL 审计日志（工具入参、diff、exit code）落盘。
- **并发**（ADR-0008）：项目根锁文件，单实例假设。
- **实施顺序**（ADR-0009）：迁移 → 协议 → 真机冒烟（定格式 + 标定预算基线）→ 工具/权限 → AgentLoop/code → 可靠性 → implement → 全量真机联调；合计 9–12 天。

## Testing Decisions

- 好的测试只断言外部行为：给定脚本化的模型回复序列，断言工具执行序列、发回的消息内容、终端产出与退出码——不断言内部状态。
- **主接缝：AgentLoop × MockProvider**（脚本化对话回放，无浏览器）。必须覆盖：happy path（tool→result→done）、malformed×2 后中止、协议遗忘触发 rotate、预算逼近触发 rotate、edit_file 级联各分支、RESULT 防注入转义、TOOL+DONE 并存、握手失败、turn 回声错位、denied 工具调用回灌。
- **次接缝：implement × 临时 git 仓库 fixture**（MockProvider 驱动）：失败停止、脏树拒启、--allow-dirty、逐文件 add、checkbox 写回、TASKS.md 不合规行容错。
- 工具级单测沿用现有 vitest + 临时目录 fixture 模式（repo 已有先例）；redact、PSParser 分类、续写重叠拼接、repo map 各自独立单测。
- 真机 E2E：现有 playwright ask 回归 + 新增最小 agent 冒烟（read_file + done），手动触发；步骤 2.5 的握手冒烟脚本沉淀为可重复运行的脚本。

## Out of Scope

- API Provider（GraphApi / OpenAI 兼容）——仅留接口位。
- 智能上下文 compaction（P1；P0 只做进度摘要 rotate）。
- session store 持久化（P1，接口占位）。
- P0.5 项：手写 schema 校验器表驱动全分支单测、Ctrl+C 竞态定义、repo map 目录优先加权、MockProvider 录制模式。
- 多实例并发支持（P0 仅锁文件 + 单实例声明）。

## Further Notes

- 协议格式选型必须在步骤 2.5 用真机数据定案后才继续，不得后置。
- 40k 会话预算、maxMessageChars、轮次上限均为保守占位，真机冒烟标定后回填 config 默认值。
- M365 条款/账号风控风险已写入 §13 并需在 README 与首次运行披露。
- 验收清单见 DESIGN-P0.md §12。
