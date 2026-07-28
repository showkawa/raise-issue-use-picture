# Spec: teams-copilot-proxy —— 把 M365 Copilot 暴露为通用 OpenAI 兼容网关（绿地）

- 状态（triage）: **ready-for-agent**
- 来源: `to-spec`（本地 markdown issue tracker 回退；远端未装 gh、未跑 setup-matt-pocock-skills）

> 由 `to-spec` 从当前对话与 `docs/adr/` + `docs/CONTEXT.md` 综合而成的合并 PRD。术语遵循 `docs/CONTEXT.md`。范围对齐 ADR-0001~0005，并纳入其直接依赖的守卫层（ADR-0006）。

## Problem Statement

用户想在 OpenCode（以及 Cline / Aider / Codex 等）里直接使用公司 M365 Copilot 的模型能力，但 M365 Copilot 没有 OpenAI 兼容接口:它走的是内部 substrate WebSocket 通道（SignalR JSON），用一个从浏览器会话抓来的 substrate token 认证，且用"tone"而非"model"来选模型/风格。M365 通道还有一系列硬约束——无官方"列出可用模型"API、无真实 token usage、无原生并行工具调用、快照式吐字（非干净 token 增量）、以及安全过滤器会返回 `Disengaged` 空响应。

因此用户面临:

1. **接不进现有 agent 生态**:主流 agent 只会说 OpenAI/Anthropic 的 HTTP 协议(chat completions / responses / messages)，无法直接对话 substrate WebSocket。
2. **认证与部署门槛**:要么走浏览器 token(轻量但 scope 受限)，要么全面 MSAL(能力强但要落盘密码/TOTP、承担登录风控)，需要一个明确、可部署、低安全面的取舍。
3. **模型选择不确定**:不同租户可用的 tone 不同(是否开通 Anthropic/Claude 决定工具可靠性)，而客户端只会传 OpenAI 的 `model` 名，proxy 必须把它稳定映射到一个可用 tone，并且不能假设每个租户都一样。
4. **工具调用不可靠**:无 Copilot Studio agent 时，模型(尤其 GPT tone)会 confabulation(编造"无法访问文件")或 hallucinated completion(谎称已完成却没发工具调用)，纯散文，或把长文档误当工具调用。工具型 agent 每一轮都带 tools，这条路径的可靠性是成败关键。
5. **长轮次体验差**:带 tools 时若整轮缓冲，客户端可能 read-timeout 断开;即便不超时也没有流式观感。

用户高度在意**稳定性与诚实**:宁可如实上报失败，也不能伪造成功的工具调用;任何"体验优化"不得牺牲工具调用正确性。

## Solution

构建 **teams-copilot-proxy**:一个把 M365 Copilot substrate 通道包装成 **OpenAI 兼容 HTTP 网关**的服务，让任意 OpenAI 兼容 agent 无改造接入。核心设计取舍(逐条对应 ADR):

1. **认证:只用浏览器 substrate token，不引入 MSAL(ADR-0001)。** 不落盘密码/TOTP、不依赖 PowerPlatform/BAP scope;token 通过调试态 Chrome(CDP)自动刷新(约 1 小时寿命)。据此只支持 **T1(有 Claude)** 与 **T3(无 Claude、尽力而为)** 两个能力档，**明确不实现 T2 / Copilot Studio agent**;但把"能力档→策略"留成开放枚举，便于将来补 T2 不返工。

2. **模型:`model` 名 → tone 映射，默认 `Claude_Sonnet`(ADR-0002)。** 维护一张精简的 model→tone 映射表(M365 无官方 discovery，映射靠实测)。客户端可用 `model` 名显式覆盖默认 tone，保留 OpenAI 多模型语义。`/v1/models` 只暴露实测接受或配置允许的模型。默认工具协议格式采用 **fenced tool-call**(同紧凑模板下 fenced 6/6 vs JSON 0/6);工具注入模板必须保持"紧凑 + 强反 confab 措辞 + 内联示例"的获胜形状。

3. **能力分层:启动探测 + 缓存(ADR-0004)。** 启动时逐个试候选 tone、对首个可用 Claude tone 发一次 fenced 探针确认能工具调用，据此定档 T1 或 T3(带 TTL 缓存，如 24h);T3 在日志明确告警"未检测到 Claude，工具调用不可靠"。把不确定性挡在真实流量之前，避免首个真实请求被拿去试错。

4. **API 表面:agentic 能力只保证 `/v1/chat/completions`(ADR-0003)。** chat completions 支持 tools / tool_choice / 流式 / 多 model / finish_reason / 429 分型;`/v1/responses` 仅保留无工具的文本兼容(Codex 配 `wire_api=chat` 回退);`/v1/messages`(Anthropic 格式)提供无 tools 文本兼容。集中把 chat completions 做扎实，覆盖所有 agent 的最大公约数。

5. **工具可靠性:五守卫 + 共享 2 次重试预算 + 诚实上报(ADR-0006)。** 纯客户端守卫(无 agent):confabulation 检测、hallucinated completion 检测、prose-document 防误执行、Disengaged 识别、429/限流分型;所有守卫共享每请求最多 2 次重试;重试仍失败则如实上报(带说明性扩展字段)，绝不伪造成功的工具调用。配套工具集瘦身(注入前压缩工具描述)。

6. **带 tools 的流式:立即 200 + role + keepalive、正文缓冲 + 打字机式分片(ADR-0005 + 本次对话方案A)。** 请求一进来立即返回 200 并发 role chunk，正文解析期间周期发 keepalive 注释帧以抗 read-timeout;正文仍缓冲到工具协议解析完成才吐(保住工具正确性)，但最终纯文本按可配置粒度切成多个 content delta 逐片发出以获得渐进渲染。工具调用轮仍原子性发出 `tool_calls` + `finish_reason=tool_calls`。

## User Stories

1. 作为 OpenCode 用户，我希望把本 proxy 配成一个 OpenAI 兼容 provider(baseURL + 任意 apiKey)就能用 M365 Copilot，这样无需改造 agent。
2. 作为部署者，我希望只靠一次浏览器登录(substrate token)即可运行，不必落盘密码/TOTP，这样部署门槛与安全面最低。
3. 作为部署者，我希望 substrate token 临近过期(约 1 小时)时能经调试态 Chrome 自动刷新，这样长时间使用不中断。
4. 作为部署者，我希望在有 Claude 的租户(T1)上获得高工具合规，这样无需 Copilot Studio agent 也能可靠调用工具。
5. 作为无 Claude 租户的部署者，我希望 proxy 落到 T3 并在日志明确告警"工具调用不可靠"，这样我清楚当前能力边界。
6. 作为客户端，我希望传 OpenAI 的 `model` 名即可被映射到合适的 tone(默认 `Claude_Sonnet`)，这样沿用 OpenAI 习惯即可。
7. 作为客户端，我希望能用 `model` 名显式覆盖默认 tone，这样保留多模型可选能力。
8. 作为客户端，我希望 `/v1/models` 只列出实测可用或配置允许的模型，这样不会拿到用不了的模型名。
9. 作为运维者，我希望 proxy 启动时做一次轻量探测并缓存(带 TTL)定档 T1/T3，这样真实请求不被用来试错、行为可预期。
10. 作为工具型 agent，我希望 `/v1/chat/completions` 完整支持 tools / tool_choice / 流式 / finish_reason / 429 分型，这样我的工具循环能正常运转。
11. 作为工具型 agent，我希望默认以 fenced tool-call 格式驱动模型，这样在 Claude tone 上获得最高合规。
12. 作为工具型 agent，当模型 confabulate("无法访问文件/请粘贴内容")时，我希望守卫换 framing 重试，这样尽量拿到真正的工具调用。
13. 作为工具型 agent，当模型 hallucinated completion(谎称已完成却没发工具调用)时，我希望守卫重试并强调"必须先调用工具"，这样不被假完成误导。
14. 作为工具型 agent，当模型输出的是内嵌代码块的长文档(prose-document)时，我希望它不被误解析为工具调用、原样透传，这样不会误执行。
15. 作为工具型 agent，当触发 M365 安全过滤器(Disengaged 空响应)时，我希望被识别并以新 conversation + 软化 framing 兜底重试，这样偶发拦截不至于直接失败。
16. 作为工具型 agent，当上游限流(429/at-limit)时，我希望收到标准 OpenAI 429 + Retry-After，这样能按规范退避重试。
17. 作为工具型 agent，我希望所有守卫共享每请求最多 2 次重试预算，这样不会因反复重试烧光 M365 会话配额。
18. 作为工具型 agent，当重试仍失败时，我希望 proxy 如实上报错误/原文(带说明性扩展字段)而非伪造成功的工具调用，这样我能正确区分"该等/该改提示/该放弃"。
19. 作为 OpenCode 用户，我希望带 tools 的长轮次请求不会因等待被判 read-timeout，这样长回答也能稳定返回。
20. 作为 OpenCode 用户，我希望请求一发出就尽快收到 200 与首帧(role)、并在正文就绪前有周期性 keepalive，这样连接不被中间层因静默切断。
21. 作为 OpenCode 用户，我希望带 tools 的纯文本回答以打字机式逐步显示，这样体验与其它 provider 一致。
22. 作为追求吞吐的用户，我希望能把片间节奏设为 0(分片但不加延迟)，这样几乎不增加总完成时间。
23. 作为工具型 agent，当这一轮解析出工具调用时，我希望仍收到一次原子性的 `tool_calls` 增量 + `finish_reason=tool_calls`，这样工具循环行为与缓冲实现一致。
24. 作为 Codex 用户，我希望能配 `wire_api=chat` 走 chat completions，这样在不支持 Responses 工具协议的前提下仍可用。
25. 作为用 `/v1/responses` 或 `/v1/messages` 的客户端，我希望获得无 tools 的文本兼容，这样纯聊天场景也能接入。
26. 作为运维者，我希望能配置出站脱敏、最大转写长度、代理、时区、tone 映射等，这样能按环境调优。

## Implementation Decisions

- **总体形态**:一个 OpenAI 兼容 HTTP 服务(现有基于 FastAPI)，前端暴露 `/v1/chat/completions`、`/v1/responses`、`/v1/messages`、`/v1/models`;后端通过一个 **substrate 客户端边界**对接 M365 substrate WebSocket(SignalR JSON，帧分隔 `\x1e`)。所有对 M365 的网络交互集中在这个客户端边界之后。
- **认证与 token(ADR-0001)**:仅浏览器 substrate token;经调试态 Chrome(CDP)自动刷新;不引入 MSAL、不落盘凭证、不依赖 PowerPlatform/BAP。能力档只实现 T1/T3;"能力档→策略"用开放枚举表达，预留 T2。
- **tone 映射(ADR-0002)**:一张 model→tone 映射表，默认 `Claude_Sonnet`;客户端 `model` 名可覆盖;`/v1/models` 只暴露实测接受/配置允许项。默认工具协议为 **fenced tool-call**;工具注入模板固定"紧凑 + 强反 confab 措辞 + 内联示例"形状，注入前做工具描述压缩(瘦身)。
- **启动探测分层(ADR-0004)**:启动时逐个试候选 tone(`Claude_Sonnet` → `Gpt_5_5_Chat` → `Magic` …)记录被接受者;对首个 Claude tone 发一次 fenced 探针;据此定 T1(默认 `Claude_Sonnet`)或 T3(GPT + 守卫 + 日志告警);结果带 TTL(如 24h)缓存。
- **API 契约(ADR-0003)**:agentic(tools/tool_choice/流式/finish_reason/429 分型)只在 chat completions 保证;Responses 仅无工具文本兼容、不实现其 item/state/tool 协议;messages 仅无工具文本兼容。usage 字段在 M365 无真实计量下按占位处理。
- **守卫层(ADR-0006)**:五守卫(confab 检测 / 幻觉完成检测 / prose-document 防误执行 / Disengaged 识别 / 429 分型)共享每请求最多 2 次重试预算;失败诚实上报，带说明性扩展字段(如 `x_m365_guard`);绝不伪造工具调用成功。
- **流式(ADR-0005 + 方案A)**:带 tools + 流式路径:立即 200 → role chunk → 正文就绪前周期(约 15s)keepalive 注释帧 → 正文缓冲解析完成后，把最终纯文本按配置粒度切片为多个 content delta 逐片发出;工具调用轮原子性发 `tool_calls` + finish。不带 tools 的 chat completions 流式走真增量透传(prefix-safe 的快照/增量折叠，防丢头/重复)。**不**对工具调用做乐观提前流式。
- **可配置项(经 Settings，`M365_*` 命名，均有保守默认)**:tone 映射/默认模型别名、脱敏、最大转写长度、代理、时区、工具纠错重试预算(与守卫共享)、带 tools 系统提示抑制、流式分片粒度(≤0 关闭回退整段)、流式片间节奏(默认 0)、keepalive 间隔、探测缓存 TTL。
- **等价性**:流式分片前的最终文本已经过引用清洗与 strip;分片不引入额外文本变换;重组后与非流式路径逐字节一致。

## Testing Decisions

- **好的测试**:只断言外部可观察行为——HTTP 响应体/SSE 帧序列、状态码、finish_reason、错误映射;不耦合内部实现细节(私有函数、循环结构、具体 WS 帧拼装)。
- **首选单一 seam**:在**substrate 客户端边界**注入 fake(替身)——它模拟 M365 的各类产出(干净 fenced 工具调用、confab、幻觉完成、prose-document、Disengaged、429、快照式增量)——然后经 **FastAPI 测试客户端**对四个 HTTP 端点做端到端断言。这是覆盖面最大的最高层缝，`tests/test_app.py` 已有此模式作为 prior art。尽量只用这一个缝。
- **要覆盖的行为**:
  - 模型/tone:`model` 名映射到默认与被覆盖 tone;`/v1/models` 只列允许项。
  - 分层:探测替身返回"有/无 Claude"两种，断言定档 T1/T3 与 T3 的告警。
  - chat completions(非流式/流式 × 有/无 tools):工具调用轮产出 `tool_calls` + `finish_reason=tool_calls`;纯文本轮流式被切成多个 content delta 且重组一致;空正文只有 role + finish;分片关闭回退单块。
  - 流式保活:以可注入/可缩短的 keepalive 间隔断言"正文就绪前有保活帧"，避免实时等待。
  - 守卫:对每类失败态断言对应守卫动作与"共享 2 次重试预算"边界;重试仍失败时断言诚实上报(标准错误 / 429 + Retry-After / 带扩展字段)，且**绝不**出现伪造的成功工具调用。
  - Responses/messages:断言无工具文本兼容的帧结构。
- **prior art**:沿用 `tests/test_app.py` 现有的"构造 app + fake 客户端 + 解析 SSE data 行重组"的风格新增用例。

## Out of Scope

- **T2 / Copilot Studio agent / `threadLevelGptId` / MSAL / 凭证落盘**(ADR-0001):明确不做;仅保留开放枚举以便将来另立 ADR 引入。
- **`/v1/responses` 的工具/状态协议**(ADR-0003):不实现 item/state/内置 tools/`previous_response_id` 串联;仅无工具文本兼容。
- **`/v1/messages`(Anthropic)的 tools**:不实现;仅无工具文本兼容。
- **对 tool_call 分支做真流式 / 乐观提前流式**(ADR-0005):明确不做,正文缓冲是稳定性基石。
- **带 tools 的底层真 token 增量**:M365 快照式吐字 + 工具协议需完整文本,不追求逐 token;只做缓冲后打字机式摊开。
- **真实 token usage 计量 / 原生并行工具调用**:M365 通道不提供,不在范围。
- **官方模型 discovery**:M365 无此 API,只能靠映射表 + 启动探测。
- **网关外的中间层缓冲问题(反代/隧道攒包)**:属部署环境,非 proxy 代码范围。

## Further Notes

- **ADR 索引**:0001 认证与 T1/T3;0002 tone 映射 + fenced 默认;0003 agentic 仅 chat completions;0004 启动探测分层;0005 带 tools 流式;0006 五守卫 + 重试预算 + 诚实上报(本 PRD 纳入,因它与 T1/T3 工具可靠性不可分)。冲突以 `docs/CONTEXT.md` 术语为准。
- **格式对照证据(ADR-0002, 2026-07-22)**:同紧凑单工具模板仅换格式 → FENCED 6/6 vs JSON 0/6;松散模板下两格式一起崩(0/8)。故"fenced 默认"与"模板必须保持获胜形状"是硬约束,直接支撑守卫层与工具瘦身。
- **首字节 vs 总时长(ADR-0005)**:role/keepalive 提前"连接确认/开始生成";打字机分片不改首个正文字节时刻(正文仍需缓冲解析),只把整段摊开;片间节奏>0 增加总时长约 `(字符数/每片字符数)×每片延迟`,默认 0 可忽略。
- **落地现状核对**:当前代码工具调用/文本已就绪,但带 tools 流式仍是"role→缓冲→整段一次发"(既无 keepalive 也无打字机分片),config 无分片项;故 ADR-0005 的 keepalive 与方案A 分片属本 PRD 待实现项,应一并落地。
- **安全阀**:任何疑似流式回归可用"关闭分片 + 节奏归 0"回到接近旧行为。
