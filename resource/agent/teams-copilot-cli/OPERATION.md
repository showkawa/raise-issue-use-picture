# Microsoft 365 Copilot CLI 操作手册

## 1. 安装

```bash
cd resource/agent/teams-copilot-cli
npm install
npm run build
```

要求 Node.js 20+，并安装 Microsoft Edge、Google Chrome 或 Chromium。

## 2. 登录

CLI 会通过 CDP 启动或连接浏览器，直接访问 `https://m365.cloud.microsoft/chat`，并复用 `browser.userDataDir` 对应的登录态。首次使用时，请在该浏览器 Profile 中登录 Microsoft 365 Copilot 并完成 MFA。

若要复用已经打开的 Chrome 标签页，请先完全退出普通 Chrome，再用 PowerShell 启动 Debug 模式：

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --user-data-dir="$env:USERPROFILE\.m365-copilot\chrome-profile"
```

随后在该 Chrome 中打开 Copilot Chat。CLI 会优先复用已有的 `/chat` 或 `/chat/conversation/...` 标签页。

CLI 不存储 Microsoft 密码、Cookie 或 MFA 信息。

## 3. 配置

默认配置可直接运行。需要覆盖时，复制 `config.example.yaml`，然后通过 `--config` 指定：

```bash
teams-copilot --config C:\path\to\config.yaml ask "你好"
```

推荐使用简短命令 `tcc`；原来的 `teams-copilot` 仍作为兼容别名保留：

```powershell
tcc "你好"
tcc ask "你好"
```

以上两条命令等价。

也可用 `TEAMS_COPILOT_BROWSER` 指定浏览器可执行文件。未指定时优先使用 Chrome，再扫描 Edge 和 Chromium。

旧版 `edge.executablePath`、`edge.debuggingPort`、`copilot.inputSelector` 配置仍兼容。

`copilot.responseMode` 控制回复读取方式：

- `auto`：默认值，优先监听浏览器内 Copilot SignalR/WebSocket 回复，失败时回退 DOM 轮询。
- `signalr`：强制使用 SignalR/WebSocket 回复捕获。
- `dom`：只使用 DOM 轮询。

`copilot.requestMode` 控制 Prompt 提交方式：

- `auto`：默认值；页面加载后的第一次请求通过编辑器发送并捕获浏览器内 SignalR 请求模板，后续请求在页面上下文中直接调用该接口。
- `browser-api`：要求当前页面内已经捕获请求模板。
- `dom`：始终通过编辑器和发送按钮提交。

认证 WebSocket 地址和请求模板仅保留在当前页面内存中，不打印、不持久化，也不会复制到 Node 配置；页面刷新后会自动重新通过一次 DOM 请求建立模板。

## 4. 命令

```bash
tcc "用 TypeScript 写一个防抖函数"
tcc ask "用 TypeScript 写一个防抖函数"
tcc @
tcc ask "解释这段代码" --file .\src\example.ts
tcc ask "解释这段代码" -f .\src\example.ts -o .\answer.md
tcc review .\src\example.ts
tcc prd demo-chat-app
tcc arch demo-chat-app
tcc tasks demo-chat-app
tcc repl
```

- `ask`：直接问答。
- `ask --file` / `ask --stdin`：将本地文件或标准输入包装为 Markdown 代码块后提问。
- `review`：将本地代码文件上传到当前 Copilot Chat，并输出 Markdown 审查报告。
- `prd`：生成 `output/PRD.md`。
- `arch`：读取 `output/PRD.md` 并生成 `output/ARCH.md`。
- `tasks`：读取 PRD 和 ARCH，生成 `output/TASKS.md`。
- `repl`：复用同一个 Copilot 会话进行多轮问答。

使用 `--no-stream` 可关闭逐步输出。

`tcc ask` 会把辅助状态写入 stderr，包括浏览器连接、登录检查、Copilot
页面准备、Prompt 提交、响应捕获和回退路径。长时间等待时每 15 秒输出
一次已等待时间，并显示配置的超时值（默认 120 秒）；Copilot 正文仍只
写入 stdout。

在 CMD 或 Git Bash 中输入包含引号、反引号、`$`、重定向符号等特殊字符
的多行问题时，运行 `tcc @`，粘贴文本或代码，并用单独一行 `@` 结束：

```text
tcc @
请解释下面的 TypeScript：
import { writeFileSync } from 'fs';
const message = `cost: "$5"`;
@
```

CLI 会自行读取 `tcc @` 后的每一行，因此内容不会再作为 Shell 命令参数
解析。只有内容完全等于 `@` 的一行会结束输入。

通过文件内容提问，不执行附件上传：

```powershell
tcc ask "解释这段代码在做什么" --file .\src\cli\tasks.ts
tcc ask "解释这段代码在做什么" -f .\src\cli\tasks.ts
```

通过 PowerShell 标准输入直接提供多行代码。`ask` 会自动读取非空管道输入，因此管道场景可省略 `--stdin`：

```powershell
Get-Content -Raw .\src\cli\tasks.ts | tcc ask "解释这段代码在做什么" --language typescript

@'
const value = 1;
console.log(value);
'@ | tcc ask "解释这段代码" --stdin --language typescript
```

在 Bash 中可使用带引号的 heredoc，源码中的单引号和 `<project-name>` 不会再被 Shell 解析：

```bash
tcc ask "解释这段代码" --language typescript < ./src/cli/tasks.ts

tcc ask "解释这段代码" --stdin --language typescript <<'CODE'
import path from 'node:path';
const usage = '<project-name>';
CODE
```

保存回答：

```powershell
tcc ask "解释这段代码" -f .\src\cli\tasks.ts -o .\answer.md
```

上传代码并将报告同时保存到本地：

```powershell
tcc --no-stream review .\src\example.ts --output .\review.md
tcc --no-stream review .\src\example.ts -o .\review.md
```

Copilot 页面原生支持的代码扩展名会直接上传；`.ts` 等未列入页面上传白名单的文本代码会以临时的 `.txt` 附件名上传，Prompt 会注明原始文件名，本地文件不会被重命名或修改。空文件和二进制文件会被拒绝。代码内容会发送到 Microsoft 365 Copilot，请只上传账号有权共享的文件。

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
| `AUTH_EXPIRED` | 在配置的浏览器 Profile 中重新登录 Microsoft 365 Copilot |
| `Microsoft 365 Copilot chat input not found` | 确认当前标签页为 Copilot Chat；必要时更新 selectors |
| `Failed to inject prompt` | 更新 `inputArea` selector，确认输入框可编辑 |
| `No SignalR assistant response was captured` | 使用默认 `auto` 或临时切到 `copilot.responseMode: "dom"` |
| `Browser API request failed` | 使用默认 `auto` 自动回退；刷新页面后先执行一次请求以重新捕获模板 |
| `Microsoft 365 Copilot file upload failed: TooManyRequests` | 等待 Microsoft Graph 限流恢复后重试；减少连续上传次数 |
| `did not finish attaching` | 检查 Copilot 上传权限、OneDrive 状态、文件类型和网络 |
| 命令长时间没有返回 | 查看 stderr 中最后一条 `[tcc]` 日志；每 15 秒心跳会说明当前等待阶段和已等待时间 |
| Response truncated | 增加 `timeouts.streaming`，检查网络和 Copilot 状态 |

Microsoft 365 Copilot 页面结构可能随 Microsoft 更新而变化。只在有权限的账号和工作区中使用，并人工审阅生成内容。
