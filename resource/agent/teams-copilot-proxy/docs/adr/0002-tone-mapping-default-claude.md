# ADR-0002: tone 按 model 名映射，默认 Claude_Sonnet

- 状态：已接受（fenced-vs-JSON 对照已完成，见 2026-07-22 补充）
- 日期：2026-07-21（2026-07-22 补充对照数据）

## 背景

现有 proxy 在 WS payload 里硬编码 `"tone": "Magic"`，且 `/v1/models` 只暴露一个别名。cramt 维护一张 `MODEL_TONES` 映射表（逐个 tone 对 live API 试出来的，无官方 discovery API）。

## 实测证据（本租户，2026-07-21）

Round 1（tone 可用性 + 身份）：
- `Gpt_5_5_Chat` → "GPT-5 chat model"；`Gpt_5_5_Reasoning` → "GPT-5 reasoning model"；`Gpt_Quick` / `Gpt_Reasoning` 可用；
- **`Claude_Sonnet` → "Claude Sonnet 4.6 from Anthropic"（本租户开启了 Anthropic）**。

Round 2（fenced 工具合规，精简 2 工具 + 简洁任务）：
- **`Claude_Sonnet`: 5/5**（每次干净输出 fenced 工具调用，零 confabulation）；
- `Gpt_5_5_Chat` / `Gpt_Quick` / `Gpt_Reasoning`: **0/N**（confabulate 或纯散文）。

Round 3（真实 15 工具重提示词，当前 tool_call JSON 格式）：Claude 仍能输出合法 ```tool_call（如 bash），但比精简场景有散文漂移；样本受隧道网络抖动影响，不可作为合规率依据。

Round 4（2026-07-22，可信对照，远端本机执行、带握手重试，Claude_Sonnet）：

- **同一紧凑单工具模板、仅换格式**：**FENCED 6/6 TOOL_CALL** vs **JSON 0/6**（5 confab + 1 杂围栏）。fenced 对 JSON 的优势在 Claude 上同样成立，不只是 cramt 在 GPT/agent 上的结论。
- **模板质量是另一决定性变量**：换成松散的 7 工具模板（弱化反 confab 措辞、bullet 工具清单、示例后置）后，**JSON 0/8、FENCED 也 0/8**——坏模板下两种格式一起崩。此前一次 "JSON 在 Claude 上能工作" 的观察是 4 样本噪声中的单次幸运，不成立。
- 复跑早期获胜配方（紧凑模板 + 强反 confab 措辞 + 内联示例）：4/4 干净 fenced 工具调用，可跨会话复现。

## 决策

- **tone 按客户端 `model` 名映射**（移植/精简 cramt 的 `MODEL_TONES`），默认映射到 **`Claude_Sonnet`**。
- 客户端可用 `model` 名**显式覆盖**默认 tone（保留多模型可选的 OpenAI 兼容能力）。
- `/v1/models` 只暴露**实测接受或配置允许**的模型，不声称官方 discovery。

## 理由

- Claude tone 是"免费午餐"：无需 agent 即可高合规。GPT tone 无 agent 时系统性 confab。
- 映射表是唯一可靠的"模型发现"手段（配合启动探测，见 ADR-0004）。

## 后果

- 依赖租户开启 Anthropic；无 Claude 的租户默认落到 GPT tone + T3 降级。
- **已敲定（2026-07-22）：fenced 替换 tool_call JSON 作为默认工具协议格式**（同模板对照 FENCED 6/6 vs JSON 0/6）。
- 工具注入模板必须保持"紧凑 + 强反 confab 措辞（real ACTION / Do NOT claim any result before tool_response）+ 内联示例"的获胜形状；模板退化会让任何格式一起崩（0/8），这是 ADR-0006 守卫层与工具瘦身的直接依据。
