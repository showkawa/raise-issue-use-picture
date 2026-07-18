# CONTEXT.md — teams-copilot-cli 术语表

> 仅收录领域术语的规范含义，不含实现细节。决策见 `docs/adr/`。

- **Provider**：聊天信道抽象（copilot-web / mock / 未来 API），暴露 `createSession()` 与 `capabilities()`。
- **ChatSession**：Provider 内的一个会话上下文；`send()` 为一"轮"。
- **轮（turn）**：一次 send + 一次完整回复的往返。
- **协议（ToolProtocol）**：让无 function calling 的信道承载结构化工具调用的文本格式约定，含 `<<<TOOL>>>`/`<<<DONE>>>`/`<<<RESULT>>>` 块；默认整体包裹于 ``` 代码围栏内。
- **信道保真（channel fidelity）**：模型输出经 Copilot 渲染、SignalR/DOM 捕获、sanitize 后与原文的一致程度。
- **握手（handshake）**：协议注入后用固定格式块校验往返信道保真的自检轮。
- **回灌（result feedback）**：把工具执行结果以 RESULT 块发回模型的动作；外发边界之一。
- **rotate（会话轮换）**：主动重建 ChatSession 并重发协议 + 进度摘要；触发条件包括不健康、轮次上限、字符预算逼近、协议遗忘。
- **协议遗忘（protocol amnesia）**：会话存活但模型不再遵循协议格式（连续 malformed 且呈自由问答形态）。
- **字符预算账本（char budget ledger）**：会话内累计双向字符数的计数器，用于触发 rotate。
- **PermissionGate**：工具调用的权限裁决器；模式 allowlist（默认）/ yolo / ask。
- **YOLO**：全自动执行模式，显式 opt-in，附首次运行风险告示。
- **destructive**：无法结构化解析或识别为高危的命令风险级别，一律需确认。
- **redact（打码）**：外发前把命中凭据模式的文本替换为 `[REDACTED:<kind>]` 占位。
- **外发边界（egress boundary）**：所有把本地内容发往 Copilot 的路径（协议提示词、RESULT、repo map、@file）。
- **触碰文件清单（touched-files list）**：AgentLoop 记录的本任务写/改过的文件集合，implement 逐文件 git add 的依据。
- **脏工作树（dirty worktree）**：存在未提交改动的工作树；implement 默认拒绝在其上启动。
- **归一化匹配级联（normalized match cascade）**：edit_file 的精确 → 空白归一 → 逐行 trim 三级匹配策略，命中后按磁盘原文替换。
- **真机冒烟（live smoke）**：步骤 2.5 的真实 Copilot 协议保真与预算基线标定。
