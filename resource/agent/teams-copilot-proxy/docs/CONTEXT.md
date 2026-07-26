# CONTEXT — teams-copilot-proxy 术语表 (glossary)

本文件统一本项目中易混淆的术语，作为设计与实现的共同语言。遇到术语冲突以本文件为准。

## 通道与认证

- **substrate token**：从已登录 M365 Copilot 浏览器会话中抓取的 JWT，`aud = https://substrate.office.com/sydney`。只有"聊天"scope，**无法**用于创建 Copilot Studio agent（那需要 PowerPlatform/BAP scope）。寿命约 1 小时，通过调试 Chrome (CDP) 自动刷新。
- **substrate WebSocket**：`wss://substrate.office.com/m365Copilot/Chathub`，M365 Copilot 聊天主通道。本 proxy 走这条。使用 SignalR JSON 协议（帧分隔符 `\x1e`）。

## 模型与 tone

- **tone**：M365 substrate 内部的**模型/风格选择器**（WS payload 里的一个字段）。**不等于** OpenAI 的 `model`。服务端会校验 tone，未知 tone 报 `Failed to invoke 'Chat'`。例：`Magic` / `Gpt_5_5_Chat` / `Claude_Sonnet`。
- **model**：客户端在 `/v1/chat/completions` 请求里传的模型名（OpenAI 语义）。本 proxy 把它**映射**到一个 tone。默认映射到 `Claude_Sonnet`（实测可靠性最高的 tone）。
- **能力分层 (T1/T2/T3)**：按租户实际可用能力对部署分层。
  - **T1**：租户有可用的 Claude tone（实测 fenced 工具合规最高）。默认 tone = `Claude_Sonnet`。**本项目主力路径。**
  - **T2**：无 Claude、但有 PowerPlatform 权限，可用 Copilot Studio agent 提升 GPT tone 合规。**本项目明确不实现**（无 MSAL、不建 agent）。
  - **T3**：无 Claude、无 agent，仅 GPT tone + 守卫，尽力而为（聊天正常，工具调用不可靠）。

## 工具协议

- **tool_call JSON 格式**：现有格式。模型输出一个 ```tool_call 围栏，内含 `{"name": ..., "arguments": {...}}`。
- **fenced tool-call 格式**：拟从 cramt 移植的格式。info-string 即工具名，每个标量参数一行 `key: value`，body 承载主体（如 bash 命令）。
- **shell-routing**：把任意名字的 shell 类工具（bash/run/exec…）路由到模型最愿意输出的 ```bash 围栏形状。

## 失败态（语义不同，勿混用）

- **confabulation**：模型编造"我无法访问文件/找不到文件/请粘贴内容"等，拒绝发工具调用。
- **hallucinated completion（幻觉完成）**：模型声称"已读取/已修改/结果是…"却根本没发工具调用。
- **Disengaged**：M365 安全过滤器触发，返回空 `Disengaged` 消息类型。**与普通空响应不同**，需专门识别。实测在 Claude tone + 合理 framing 下**不是主要瓶颈**（dea 分类器分数极低）。
- **prose-document**：模型输出一篇内嵌代码块的长文档（非工具调用意图），**不应**被误解析为工具调用。

## API 表面

- **`/v1/chat/completions`**：agentic 能力的**唯一保证面**。tools / tool_choice / 流式 / 多 model / finish_reason / 429 分型都在这里。
- **`/v1/responses`**：仅保留**无工具的文本兼容**。agentic 客户端请用 chat completions；Codex 请配 `wire_api=chat`。
