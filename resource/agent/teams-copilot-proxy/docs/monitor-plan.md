# teams-copilot-proxy 阶段二：Monitor 功能实施计划（v2，grilling 后修订）

## 〇、已确认的七条决策（2026-07-26 grilling 会话）

1. **认证**：`/monitor` 全部端点复用现有 proxy Bearer token（与 `/v1/chat/completions` 同一 key）；面板首次输入 token 后存 localStorage。
2. **留存**：`M365_MONITOR_CAPTURE=off|failures|all`，默认 `failures`——仅失败/守卫触发请求保留脱敏 prompt 摘要与模型回复片段（每条截断 2KB）；正常请求只记元数据；保留 30 天自动清理。
3. **会话聚合**：Session = 一次 OpenCode 对话线程；请求带 `x-session-id` 则优先用它，否则用 conversation key（首条 user 消息哈希）。
4. **Attempt 链**：记录每次请求内部的完整 substrate 往返链（原始回复→守卫触发→纠正重试→最终结果），每步含守卫类型、耗时；文本仅在 failures 档保留。
5. **开销边界**：有界异步队列（1000 条）满则丢事件仅打 warning，绝不阻塞主链路；流式只记聚合指标（首 chunk 延迟、chunk 数、平均间隔、[DONE] 完整性），不逐 chunk 落库。
6. **工具闭环**：下一轮请求的 `Tool result (...)` 轻量配对回上一条 tool_call 记录，只提取 error 标记与字节数，不存全文；配不上留空。
7. **面板形态**：纯只读仪表盘（Summary / Requests+详情 attempt 链 / Errors 三视图）；配置只走环境变量，导出用 sqlite3 dump。

术语见仓库 `CONTEXT.md`；技术选型取舍见 `docs/adr/0001-self-hosted-sqlite-monitor.md`。

## 一、开源方案调研结论

| 方案 | Stars | 评估 |
|---|---|---|
| Langfuse (langfuse-python) | 主库 ~12k | 功能最全（trace/span/token/cost），但需自建 Langfuse Server（Docker + Postgres），对单机 Windows 部署太重 |
| OpenLLMetry (traceloop) | 7.3k | 基于 OpenTelemetry 的标准埋点，无自带 UI，需接 Jaeger/Grafana 等后端 |
| MLflow Tracing | 27k | 平台级，依赖重，偏实验管理，不适合常驻轻量 proxy |
| pydantic/logfire | 4.4k | 体验好但核心是云服务 |
| Soju06/codex-lb | 2.4k | 与我们场景最像：OpenCode 兼容 proxy + usage tracking + dashboard，但它是完整 proxy 实现，只能借鉴其 dashboard/统计设计，无法作为库引入 |

**结论**：没有可以直接 pip 引入、零外部依赖、能拿到我们私有维度（tone 路由、守卫触发、substrate disengage、tool_call 修正重试）的现成方案。
**推荐**：自研轻量 Monitor 内核（SQLite + FastAPI 页面，零新增外部服务），埋点接口做成事件总线，后续可选接 OpenTelemetry/Langfuse 导出器（两阶段兼得：本地开箱即用 + 标准生态可扩展）。

## 二、要监控什么（对应 OpenCode 全链路）

每次 `/v1/chat/completions` 请求记录一条 request 事件 + 若干子事件：

1. **请求维度**：时间、model alias、实际 tone、stream 与否、会话 key（:persist / conversation hash）、OpenCode agent（从 system prompt 指纹推断 Build/Plan/subagent）
2. **Token 用量**：prompt/completion/total（估算）、上下文占用率（相对 265k 上限）、单会话累计
3. **工具调用**：每个 tool_call 的 name/参数大小/并行数量；分类统计 builtin（bash/read/write/edit/glob/grep…）、MCP（`mcp__*`）、skill、task(subagent)、todowrite 等
4. **可靠性事件**（定位不稳定的关键）：
   - 守卫触发：confabulation / hallucinated_completion / disengaged / tool_parse_failure，含重试次数与最终结果（纠正成功/失败）
   - substrate 错误：限流（"volume of requests"）、超时、WebSocket 断连
   - JSON mode 违约、上下文超限拒绝
5. **性能**：substrate 首字节延迟、总耗时、重试造成的额外耗时、流式 chunk 间隔
6. **ACP/流式**：SSE 完整性（是否发出 [DONE]）、tool_call chunk 原子性

## 三、架构设计

```
app.py 各环节 ──emit()──> MonitorBus（进程内异步队列）
                              │
                 ┌────────────┼───────────────┐
          SQLiteSink      LogSink(JSONL)   （可选）OTelSink
        monitor.db 滚动    结构化日志       OpenTelemetry 导出
                              │
              GET /monitor/*  查询 API + 内置轻量 HTML 面板
```

- **零侵入原则**：埋点失败绝不影响主链路（try/except + 队列丢弃策略）
- **SQLite** 单文件（WAL 模式），默认保留 30 天，`M365_MONITOR_*` 配置开关/路径/保留期
- **API**（供人和脚本用）：
  - `GET /monitor/summary` 今日/7日 token、请求数、错误率、守卫触发率
  - `GET /monitor/requests?limit=&session=` 最近请求明细（含工具调用链）
  - `GET /monitor/tools` 工具调用排行/失败率
  - `GET /monitor/errors` 守卫与 substrate 错误时间线
  - `GET /monitor/sessions/{key}` 单会话 token 累计与事件流
  - `GET /monitor` 内置单页 HTML 面板（无 npm 构建，纯静态 + fetch）
- **诊断辅助**：每条失败请求保留脱敏后的最后一轮 prompt 摘要与模型原始回复片段（可配置关闭），直接支撑"OpenCode 不稳定时来 proxy 找原因"

## 四、实施步骤（阶段二）

1. `monitor/bus.py`：事件模型（RequestEvent/ToolCallEvent/GuardEvent/SubstrateErrorEvent/UsageEvent）+ 异步总线
2. `monitor/sink_sqlite.py`：建表、写入、保留期清理
3. 在 app.py/substrate_client.py 关键路径埋点（请求入口、tool 解析、守卫重试、disengage、限流、SSE 完成）
4. `monitor/api.py`：查询路由 + HTML 面板
5. 配置项与 README 文档；pytest 单测（事件写入/查询/主链路无感）
6. live 验证：跑一轮 OpenCode 全工具用例，核对面板数据与实际一致
7. （可选，后续）OTelSink：接 OpenLLMetry 语义约定，方便未来上 Grafana/Langfuse

预计新增代码 ~600-800 行，无新增外部服务，依赖仅标准库 + 已有 FastAPI。
