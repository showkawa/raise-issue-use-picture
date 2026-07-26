# ADR-0003: agentic 能力仅保证 /v1/chat/completions

- 状态：已接受
- 日期：2026-07-21

## 背景

proxy 现有端点：`/v1/chat/completions`（支持 tools）、`/v1/responses`（仅文本半实现）、`/v1/messages`（Anthropic 格式，无 tools）。Responses API 的 item/state/tool 协议远比 chat 复杂（`input`/`output` item、`previous_response_id` 服务端串联、内置 tools），且 M365 通道的硬上限（无真实 token usage、无原生并行 tool call）在 Responses 语义下更难兼容。

主流 agent（OpenCode、Cline、Aider 等）主力使用 chat completions；Codex CLI 可配 `wire_api="chat"` 回退。

## 决策

- **`/v1/chat/completions` 是 agentic（tools/tool_choice/流式/finish_reason/429 分型）的唯一保证面。**
- `/v1/responses` 仅保留**无工具的文本兼容**，不实现其工具/状态协议。
- 文档明确：agentic 客户端请用 chat completions；Codex 请配 `wire_api=chat`。

## 后果

- 依赖 Responses 工具协议的客户端不受支持（需回退 chat）。
- 集中投入把 chat completions 做扎实，覆盖所有 agent 的最大公约数。
