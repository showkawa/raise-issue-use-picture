# Self-hosted SQLite monitor instead of an external observability stack

Status: accepted

The proxy needs observability over everything OpenCode does through it (token usage, tool calls, guard/attempt chains, substrate errors) to diagnose instability. We evaluated Langfuse (requires a server + Postgres), OpenLLMetry/OpenTelemetry (standard instrumentation but no UI; needs Jaeger/Grafana), MLflow tracing (platform-scale) and logfire (cloud). We decided to build a small in-process monitor instead: an async in-memory event bus writing to a single SQLite file (WAL), exposed via read-only `/monitor` endpoints protected by the existing Bearer token.

Why: the deployment target is a single Windows machine behind a cpolar tunnel; adding Docker/Postgres/collector services would cost more operational risk than the feature itself. Our highest-value signals (tone routing, guard triggers, correction attempts, substrate disengagement) are proxy-private and would need custom instrumentation regardless of backend.

## Consequences

- No cross-service tracing ecosystem out of the box; an optional OpenTelemetry export sink can be added later without changing instrumentation points (events are backend-agnostic).
- Monitoring must never destabilize the proxy: bounded queue (drop events when full), aggregated-only streaming metrics, payload capture limited to failed/guard-triggered requests (`failures` capture level) with 30-day retention.
