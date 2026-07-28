# ADR-0001: 保留浏览器 token 认证，不引入 MSAL，仅支持 T1+T3

- 状态：已接受
- 日期：2026-07-21

## 背景

目标是把 M365 Copilot 暴露为通用 OpenAI-compatible proxy，服务 OpenCode 及其他 AI Agent。可选认证方案：
1. 纯浏览器 substrate token（现有）；
2. 分层（默认浏览器 token，开 agent 才引入 MSAL）；
3. 全面 MSAL（同 cramt）。

Copilot Studio agent 是提升 **GPT tone** 工具合规的最强杠杆，但它需要 PowerPlatform + BAP scope 的 token，而浏览器 substrate token 拿不到这两个 scope，且两类 token 不能相互交换/提权。MSAL 方案还需 email/password/TOTP 明文落盘并承担自动化登录风控。

## 决策

- **保留浏览器 substrate token 认证**，不引入 MSAL、不落盘密码/TOTP、不依赖 PowerPlatform/BAP。
- **仅实现 T1（有 Claude）与 T3（无 Claude、尽力而为）**。
- **不实现 T2 / Copilot Studio agent**（`threadLevelGptId` 路径不做）。

## 理由

- 实测本租户 Claude tone 可用且 fenced 工具合规 5/5，无需 agent 即可达到高可靠性（见 ADR-0002）——agent 的主要受益者是 GPT tone，而它恰与 OpenCode 这类重 harness 相冲。
- 认证保持轻量：只需一次浏览器登录，部署门槛最低，最契合"通用网关"定位。
- 砍掉 agent/MSAL 显著降低复杂度与安全面（无凭证落盘）。

## 后果

- 无 Claude 的租户只能落到 T3，工具调用不可靠（聊天正常）；这类租户需自行接受降级，或未来另立 ADR 重新引入 T2。
- proxy 架构须把 T1/T2/T3 的"策略选择"留成开放枚举，便于未来补 T2 而不返工。
