# Teams Copilot CLI 操作手册

## 1. 安装

```bash
cd resource/agent/teams-copilot-cli
npm install
npm run build
```

要求 Node.js 20+，并安装 Microsoft Edge、Google Chrome 或 Chromium。

## 2. 登录

CLI 会通过 CDP 启动或连接浏览器，并复用 `browser.userDataDir` 对应的登录态。首次使用时，请在该浏览器 Profile 中登录 Teams 并完成 MFA。

CLI 不存储 Microsoft 密码、Cookie 或 MFA 信息。

## 3. 配置

默认配置可直接运行。需要覆盖时，复制 `config.example.yaml`，然后通过 `--config` 指定：

```bash
teams-copilot --config C:\path\to\config.yaml ask "你好"
```

也可用 `TEAMS_COPILOT_BROWSER` 指定浏览器可执行文件。未指定时会扫描常见 Edge、Chrome 和 Chromium 安装路径。

旧版 `edge.executablePath`、`edge.debuggingPort`、`copilot.inputSelector` 配置仍兼容。

## 4. 命令

```bash
teams-copilot ask "用 TypeScript 写一个防抖函数"
teams-copilot prd demo-chat-app
teams-copilot arch demo-chat-app
teams-copilot tasks demo-chat-app
teams-copilot repl
```

- `ask`：直接问答。
- `prd`：生成 `output/PRD.md`。
- `arch`：读取 `output/PRD.md` 并生成 `output/ARCH.md`。
- `tasks`：读取 PRD 和 ARCH，生成 `output/TASKS.md`。
- `repl`：复用同一个 Copilot 会话进行多轮问答。

使用 `--no-stream` 可关闭逐步输出。

REPL 内置命令：

```text
/help
/clear
/stream on
/stream off
/exit
```

## 5. 验证

```bash
npm run typecheck
npm test
npm run build
npm pack --dry-run
```

## 6. 故障排查

| 错误 | 处理 |
|---|---|
| `No Chromium browser found` | 设置 `TEAMS_COPILOT_BROWSER` 或 `browser.path` |
| `AUTH_EXPIRED` | 在配置的浏览器 Profile 中重新登录 Teams |
| `Copilot iframe not found` | 确认 Copilot 已启用；必要时更新 selectors |
| `Failed to inject prompt` | 更新 `inputArea` selector，确认输入框可编辑 |
| Response truncated | 增加 `timeouts.streaming`，检查网络和 Teams 状态 |

Teams 页面结构可能随 Microsoft 更新而变化。只在有权限的账号和工作区中使用，并人工审阅生成内容。
