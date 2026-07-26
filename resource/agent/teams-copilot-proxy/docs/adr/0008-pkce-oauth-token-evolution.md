# ADR-0008: PKCE OAuth 作为 substrate token 获取的候选演进路径

- 状态：已采纳（opt-in 实现，与 ADR-0001 浏览器抓取方案并存）
- 日期：2026-07-26（2026-07-26 更新为已实现）

## 背景

ADR-0001 决定用浏览器抓取 substrate token（CDP，无 MSAL）。该方案代价：access_token 约 1 小时过期、依赖浏览器保持登录态、无头/CI 环境不友好。

对比研究 HEXUXIU/M365-Copilot2API（Go，87★）时发现其采用标准 Microsoft identity platform OAuth 2.0 + PKCE，通过 `offline_access` 拿 refresh_token 实现长效自动续期，且逆向出了可用的第一方 client 参数。本 ADR 记录该路径的技术细节与权衡，供 ADR-0001 后续演进参考。

## 决策

- **已作为 opt-in 实现**（`oauth_pkce.py` + CLI `login`/`login-device`/`oauth-refresh`），与 ADR-0001 的浏览器抓取方案并存。
- `serve` 的刷新链路优先尝试 OAuth `refresh_token`（缓存存在时），失败再回退到 Chrome WebSocket 抓取；因此可以 `serve --no-launch-chrome` 完全脱离浏览器运行。
- 默认仍不强制启用：未执行 `login` 时行为与之前完全一致（无缓存则 `refresh_token` 分支直接跳过）。

## 技术细节（逆向自 Copilot2API `internal/auth/`）

- **client_id**：`c0ab8ce9-e9a0-42e7-b064-33d422df41f1`（Office web Copilot 第一方 public client，浏览器 PKCE 已验证可用）；备用 FOCI client `d3590ed6-52b3-4102-aeff-aad2292ab01c`（可走 device-code）
- **authority**：`https://login.microsoftonline.com/common`（多租户）
- **scope**：`openid profile offline_access https://substrate.office.com/sydney/M365Chat.Read https://substrate.office.com/sydney/sydney.readwrite`（`offline_access` 换 refresh_token；两个 sydney scope 即 ChatHub 授权范围）
- **redirect_uri**：`https://login.microsoftonline.com/common/oauth2/nativeclient`
- **两条获取路径**：
  1. PKCE Authorization Code：verifier=32B 随机 base64url，challenge=SHA256(verifier) base64url（method=S256），authorize→回调拿 code→`grant_type=authorization_code` 带 code_verifier 换 token
  2. Device Code：POST devicecode 端点拿 user_code+verification_uri，轮询 token 端点（处理 authorization_pending/slow_down），适合无浏览器环境
- **续期**：调用前若 access_token 距过期 <30s，用 `grant_type=refresh_token` 静默续期并回存；从 JWT claims 解出 email/oid/tid/tenant

## 理由

- 一次登录拿 refresh_token 后全自动续期，摆脱"浏览器必须活着"的约束，也支持 device-code 走无头/CI。
- 相比每小时抓 token，运维更稳、更可脚本化。

## 结果

- **未立即实施**：当前浏览器抓取方案已满足单用户 OpenCode 场景，改造成本（完整 OAuth 流 + 安全存储）暂不划算。
- **安全权衡**：refresh_token 泄露等于长期访问权，比 1 小时 access_token 更敏感；若采纳必须加密存储 + 严格文件权限（Copilot2API 用 0600 明文，仅够单机）。
- **合规/稳定性风险**：该 client_id/scope 为逆向所得的第一方 public client，非官方授权用途；微软若收紧该 client 的 PKCE 公共流，路径即失效。采纳前需评估。
- 采纳时应更新 ADR-0001 的状态并交叉引用本 ADR。
