# Teams Copilot CLI — 操作手册

> 基于 @jackwener/opencli 的 Teams Copilot 自动化 CLI。
> 将 Teams Copilot 变成你的 AI Coding + Agent 后端。

---

## 1. 前置条件

| 条件 | 说明 |
|------|------|
| **Node.js >= 20** 或 Bun | 运行时 |
| **Microsoft Edge** | 安装在默认路径 `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` |
| **Microsoft 365 账号** | 有 Teams Copilot 许可（需手动 MFA 登录一次） |
| **磁盘空间** | `C:\teams-copilot-profile` 约 200MB（独立浏览器 Profile） |

---

## 2. 安装

```bash
cd resource/agent/teams-copilot-cli
bun install
```

---

## 3. 配置

编辑 `config.yaml`，确认 Edge 路径和端口正确：

```yaml
edge:
  executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
  userDataDir: "C:\\teams-copilot-profile"
  debuggingPort: 9222

copilot:
  url: "https://teams.microsoft.com"
  inputSelector: "[data-tid='messageInputEditor'], [role='textbox'], [aria-label*='message']"
  timeout: 90000
```

---

## 4. 四条命令 — Coding & Agent 能力展示

### 4.1 `ask` — 自由问答（Coding 能力）

向 Teams Copilot 发送任意问题，获取代码、方案、解释。

```bash
bun run cli/ask.ts "用 TypeScript 写一个防抖函数，带泛型和完整注释"
```

**预期输出**：终端直接打印 Copilot 回复的完整代码。

---

### 4.2 `prd` — 生成 PRD（Agent 能力：需求分析）

输入项目名，自动生成一份结构化的产品需求文档。

```bash
bun run cli/prd.ts my-saas-app
```

**流程**：
1. 读取 `prompts/prd.md` 模板，替换 `{project_name}`
2. 打开 Teams Copilot，注入 Prompt
3. 等待流式输出完成（自动检测截断并续写）
4. 清洗 AI 寒暄语，保存到 `output/PRD.md`

---

### 4.3 `arch` — 生成架构设计（Agent 能力：技术设计）

基于 PRD 自动生成技术架构文档。

```bash
bun run cli/arch.ts my-saas-app
```

**依赖**：必须先运行 `prd` 生成 `output/PRD.md`。

---

### 4.4 `tasks` — 生成任务拆解（Agent 能力：项目管理）

基于 PRD + 架构设计，拆解为可执行开发任务。

```bash
bun run cli/tasks.ts my-saas-app
```

**依赖**：必须先运行 `prd` 和 `arch`。

---

## 5. 首次运行 — 认证流程

```
[OpenCLIAdapter] Starting new Edge instance...
[OpenCLIAdapter] Browser connection established.
[CopilotRuntime] Copilot entry not found, assuming already open.

→ Edge 浏览器弹出 → 手动完成 Microsoft MFA 登录 → 浏览器保持在 Teams 页面 → CLI 自动继续
```

**重要**：
- 登录后不要关闭 Edge 窗口，CLI 会自动注入 Prompt
- 登录态被检测到失效时抛出 `AUTH_EXPIRED`（退出码 77），手动重登后重新运行
- 流式输出超时抛出 `STREAMING_TIMEOUT`（退出码 88）

---

## 6. 运行时行为

| 阶段 | 说明 |
|------|------|
| 初始化 | 检测 9222 端口，有则复用，无则启动新 Edge |
| 会话检测 | 导航到 Teams，检查 URL 是否被重定向到登录页 |
| Prompt 注入 | CSS 选择器定位输入框 → 聚焦 → 原生 setter 注入文本 → Enter 发送 |
| 流式监听 | MutationObserver 监听 Copilot 回复区的 DOM 变化，拼接完整 Markdown |
| 截断检测 | 无句末标点 or 代码块未闭合 → 自动发送 "请继续" |
| 结果清洗 | 去除 "好的，这是您的..." / "是否需要我继续?" 等废话 |

---

## 7. 验证步骤（确认一切就绪）

```bash
# 1. 检查依赖
bun install --frozen-lockfile

# 2. TypeScript 编译检查
bun run typecheck

# 3. 测试 Edge 路径
ls "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

# 4. 确认端口未被占用
netstat -ano | findstr 9222
```

---

## 8. 演示流程

以下演示 Coding + Agent 能力：

```bash
# Step 1: Coding 能力 — 生成代码
bun run cli/ask.ts "用 TypeScript 写一个 EventEmitter 类，支持 once 和 off"

# Step 2-4: Agent 能力 — 完整规划链路
bun run cli/prd.ts demo-chat-app     # 生成 PRD
bun run cli/arch.ts demo-chat-app    # 生成架构（依赖 PRD）
bun run cli/tasks.ts demo-chat-app   # 生成任务拆解（依赖 PRD+ARCH）
```

---

## 9. 故障排查

| 错误 | 原因 | 解决 |
|------|------|------|
| `AUTH_EXPIRED` (77) | 登录态失效 | 在弹出浏览器中重新登录 Teams |
| `STREAMING_TIMEOUT` (88) | Copilot 响应超时 | 检查网络或增加 `config.yaml` 中 `timeout` |
| `CDP port not ready` | Edge 启动失败 | 检查 Edge 路径，关闭所有 Edge 进程后重试 |
| `Element not found` | Teams UI 更新 | 更新 `config.yaml` 中 `inputSelector` |
