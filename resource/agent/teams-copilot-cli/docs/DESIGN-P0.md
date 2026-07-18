# teams-copilot-cli v3 技术方案（P0 详细设计）

> 目标：把 teams-copilot-cli 从"Copilot 网页问答管道"升级为可在真实 repo 中自主改代码、跑测试、迭代到绿的软件工程 Agent CLI。对标 Claude Code / OpenCode 的核心能力，同时保留"Copilot 网页版零 API 成本"这一独特优势。
>
> 已确认的方向：Provider 抽象（未来可接 API）；双工作流（交互式结对编程 + PRD→ARCH→TASKS→代码）；默认 YOLO 权限模式。

---

## 1. 总体架构

```
┌────────────────────────── CLI 层 ──────────────────────────┐
│ ask / prd / arch / tasks / repl（现有）                     │
│ code <task>（新：agent 结对编程）                            │
│ implement [--task-id]（新：消费 TASKS.md 的文档驱动流）       │
└──────────────┬─────────────────────────────────────────────┘
               │
┌──────────────▼───────────── Agent 层（新）───────────────────┐
│ AgentLoop：plan → act → observe 循环                         │
│ ToolProtocol：结构化工具调用的编码 / 解析 / 纠错               │
│ ToolRegistry：read_file write_file edit_file run_command     │
│               grep glob git_status/diff/commit               │
│ PermissionGate：yolo | allowlist | ask（默认 yolo）           │
│ WorkspaceContext：项目根、repo map、AGENTS.md、@file 引用     │
└──────────────┬───────────────────────────────────────────────┘
               │ Provider 接口（新抽象）
┌──────────────▼───────────────────────────────────────────────┐
│ CopilotWebProvider  ←  现有 runtime/ 全部代码平移封装          │
│   (session-manager, signalr-stream, browser-api-bridge,      │
│    copilot-page, text-injector, stream-extractor, ...)        │
│ MockProvider（测试用，回放脚本化响应）                          │
│ [未来] GraphApiProvider / OpenAI 兼容 Provider                 │
└───────────────────────────────────────────────────────────────┘
```

## 2. 目录结构（目标形态）

```
src/
  cli/
    index.ts            # 命令注册（新增 code、implement、--yolo/--ask 全局开关）
    ask.ts prd.ts arch.ts tasks.ts repl.ts   # 现有，改为走 Provider
    code.ts             # 新
    implement.ts        # 新
  provider/
    types.ts            # Provider 接口定义
    copilot-web/        # 现有 src/runtime/* 整体迁移至此
      index.ts          # 实现 Provider 接口
      ...(session-manager 等原文件)
    mock.ts             # 测试 Provider
  agent/
    loop.ts             # AgentLoop
    protocol.ts         # 工具调用协议编解码
    system-prompt.ts    # 会话开场协议提示词（模板化）
    tools/
      types.ts          # Tool 接口
      read-file.ts write-file.ts edit-file.ts
      run-command.ts grep.ts glob.ts git.ts
      registry.ts
    permissions.ts      # PermissionGate
  context/
    workspace.ts        # 项目根检测、repo map（.gitignore 感知）
    memory.ts           # AGENTS.md / TEAMS-COPILOT.md 加载
  session/
    store.ts            # P1，本期只留接口占位
  types.ts              # 公共类型（现有，扩充）
```

迁移原则：`src/runtime/` → `src/provider/copilot-web/` 是纯移动 + 一层薄封装，不改内部逻辑，现有单测同步移动，保证 `ask/prd/arch/tasks/repl` 行为完全不变（回归底线）。

## 3. Provider 接口

```ts
// src/provider/types.ts
export interface ChatTurnOptions {
  onUpdate?: (chunk: string) => void;
  timeoutMs?: number;
}

export interface ChatTurnResult {
  text: string;
  truncated: boolean;
  duration: number;
}

export interface ChatSession {
  /** 在同一会话上下文中发送一轮消息（含自动续写拼接） */
  send(message: string, options?: ChatTurnOptions): Promise<ChatTurnResult>;
  /** 会话是否仍健康（登录态、页面/连接存活） */
  healthy(): Promise<boolean>;
  close(): Promise<void>;
}

export interface Provider {
  readonly id: string;                       // 'copilot-web' | 'mock' | ...
  init(): Promise<void>;
  createSession(): Promise<ChatSession>;
  close(): Promise<void>;
  capabilities(): ProviderCapabilities;
}

export interface ProviderCapabilities {
  maxMessageChars: number;      // 单条消息长度上限（copilot-web 需实测，先配置化）
  supportsStreaming: boolean;
  supportsSystemPrompt: boolean; // copilot-web = false → 协议提示词走首条消息
}
```

要点：
- `CopilotWebProvider.createSession()` 内部即现有 `SessionManager.createSession()`；`send()` 即现有 `ask()`（保留 autoContinue 续写逻辑，默认开启，maxContinuations 提到配置里，agent 模式建议 4）。
- `capabilities()` 让 AgentLoop 在发送前做长度检查并自动分片（超长上下文拆成多条"[part i/n] ...请等待全部接收完再回复 OK"消息）。
- config.yaml 增加 `provider: copilot-web`（默认），为未来 API Provider 留位。

## 4. 工具调用协议（核心难点）

Copilot 网页版无 function calling、无 system prompt 控制，用"首条协议消息 + 严格文本格式"实现：

### 4.1 协议提示词（会话首条消息注入）

`src/agent/system-prompt.ts` 生成，包含：角色设定（资深工程师 agent）、工具清单及 JSON 参数 schema、输出格式规则、当前工作区信息（OS=Windows、shell=PowerShell、项目根、repo map 摘要、AGENTS.md 内容）、迭代规则（一次回复最多 N 个工具调用；改完代码必须跑测试验证；结束时输出 done 块）。

### 4.2 输出格式

采用带围栏的自定义块（对 LLM 最稳，且易与正文 markdown 区分）：

```
<<<TOOL name="read_file"
{"path": "src/app.ts"}
>>>

<<<TOOL name="edit_file"
{"path": "src/app.ts", "old": "const x = 1;", "new": "const x = 2;"}
>>>

<<<DONE
修复完成：xxx。测试已通过。
>>>
```

选 `<<<TOOL ... >>>` 自定义围栏而非纯 XML/JSON 的原因：Copilot 网页渲染会转义/美化 XML 和 markdown 代码块，SignalR 捕获到的原文与 DOM 兜底读取的文本可能不同；自定义围栏在两种通道下都保持原样、正则可靠。

### 4.3 解析与纠错（protocol.ts）

```ts
export type ParsedReply =
  | { kind: 'tool_calls'; calls: ToolCall[]; commentary: string }
  | { kind: 'done'; summary: string }
  | { kind: 'malformed'; raw: string; problems: string[] };

export interface ToolCall { name: string; args: Record<string, unknown>; raw: string }
```

- 正则提取所有围栏块 → 逐块 JSON.parse → 按工具 schema 校验（手写轻量校验器即可，不引依赖）。
- `malformed` 时 AgentLoop 自动回发纠错消息（附上具体问题 + 格式示例），最多重试 2 次，仍失败则中止并把原文打给用户。
- 回复中既有 TOOL 又有 DONE：先执行 TOOL，忽略 DONE（防模型抢跑）。

### 4.4 工具结果回灌格式

```
<<<RESULT name="run_command" ok="true" exit="0"
(stdout/stderr，超过 maxMessageChars 预算时截断中部并标注)
>>>
```

多个结果合并为一条消息发回，控制轮次消耗。

## 5. 工具集（P0 七个）

```ts
export interface Tool<A = unknown> {
  name: string;
  description: string;        // 进协议提示词
  schema: JsonSchemaLite;     // 参数校验
  risk: 'read' | 'write' | 'exec' | 'destructive';
  run(args: A, ctx: ToolContext): Promise<ToolResult>;
}
```

| 工具 | 参数 | 说明 |
|---|---|---|
| `read_file` | path, offset?, limit? | 带行号输出；大文件默认截 400 行 |
| `write_file` | path, content | 整文件写；自动 mkdir |
| `edit_file` | path, old, new, all? | 精确字符串替换；old 不唯一/不存在 → 返回错误让模型修正 |
| `run_command` | command, cwd?, timeoutMs? | PowerShell 执行（Windows 优先），捕获 stdout/stderr/exitCode |
| `grep` | pattern, glob?, path? | 内置 JS 实现（逐文件正则），.gitignore 感知 |
| `glob` | pattern | 文件查找 |
| `git` | subcommand ∈ {status, diff, log, add, commit} | 白名单子命令；commit 需 message |

所有路径强制解析到项目根之内（防目录穿越），`run_command` 的 cwd 同理。

## 6. 权限模型（PermissionGate）

默认 `yolo`，但保留硬性红线：

```yaml
# config.yaml 新增
agent:
  permissionMode: yolo        # yolo | allowlist | ask
  maxIterations: 25
  denyCommands:               # 即使 yolo 也需交互确认（TTY 下询问，非 TTY 直接拒绝）
    - "rm -rf"
    - "Remove-Item -Recurse"
    - "git push"
    - "git reset --hard"
    - "npm publish"
```

- `yolo`：read/write/exec 直接执行；命中 denyCommands 或 risk=destructive → 确认。
- `allowlist`：exec 类仅 allowlist 内自动，其余询问。
- `ask`：全部询问。CLI 全局开关 `--ask` / `--allowlist` 可临时降级 yolo。
- 每次 write/edit 前在终端打印统一 diff（纯文本 +/- 即可），YOLO 下仅展示不阻塞——保证可审计。

## 7. AgentLoop

```ts
async function runAgent(task: string, deps: { session, tools, gate, workspace, ui }) {
  await session.send(buildProtocolPrompt(workspace, tools));      // 第 1 轮：协议注入
  let reply = await session.send(buildTaskMessage(task, workspace)); // 第 2 轮：任务
  for (let i = 0; i < cfg.maxIterations; i++) {
    const parsed = parseReply(reply.text);
    if (parsed.kind === 'done') return report(parsed.summary);
    if (parsed.kind === 'malformed') { reply = await session.send(correction(parsed)); continue; }
    const results = [];
    for (const call of parsed.calls) {
      const decision = await gate.check(call);
      results.push(decision.allowed
        ? await executeWithTimeout(call)
        : deniedResult(call, decision.reason));
    }
    reply = await session.send(formatResults(results, provider.capabilities()));
  }
  throw new AgentMaxIterationsError();
}
```

细节：
- 每轮把 commentary（围栏外正文）流式打到终端，用户可实时看到"思考过程"。
- Ctrl+C 一次 = 中断当前轮并进入交互（用户可补充指示）；两次 = 退出。
- 会话中途 `healthy()===false`（登录过期/页面刷新）→ 重建 session，重发协议提示 + 压缩后的进度摘要（"已完成步骤：…；当前文件状态以磁盘为准"），继续。
- 迭代耗尽 → 输出已完成动作清单 + 建议，非 0 退出码。

## 8. 工作区上下文（context/workspace.ts）

- 项目根：从 cwd 向上找 `.git`，找不到用 cwd。
- repo map：目录树（.gitignore 过滤，node_modules/dist 排除），深度/条目数封顶（~200 项），进协议提示词。
- `AGENTS.md`（或 `TEAMS-COPILOT.md`）存在则全文注入。
- `@path/to/file` 引用：`code`/`repl` 输入中出现即自动附带该文件内容（带长度预算）。

## 9. CLI 新命令

```bash
# 结对编程：在当前目录 repo 内自主完成任务
teams-copilot code "把 utils 里的日期处理换成 date-fns 并跑通测试"
teams-copilot code --ask "..."          # 临时降级权限
teams-copilot code --max-iterations 40 "..."

# 文档驱动：消费 output/TASKS.md
teams-copilot implement                  # 依次执行未完成任务
teams-copilot implement --task 3         # 只执行第 3 条
```

`implement` 解析 TASKS.md 的任务清单（要求 tasks 提示词模板输出带 checkbox 的固定格式，改 prompts/tasks.md 保证可解析），每条任务 = 一次 AgentLoop 运行 + 完成后自动把 checkbox 勾选写回 TASKS.md + `git commit`（YOLO 下自动，commit message 含任务编号）。

`repl` 增强（低成本顺带）：`/code <task>` 进入 agent 模式、`@file` 引用、`/tools` 列出工具。

## 10. 可靠性专项（copilot-web 特有）

1. **续写拼接去重**：现有逻辑简单换行拼接，改为重叠检测（取前文尾部 200 字符在续文头部找最长重叠后拼接）。
2. **长消息分片发送**：超 `maxMessageChars` 自动分片（见 §3），首片说明"共 n 片，收齐前只回 OK"。
3. **轮次预算**：Copilot 企业版单会话轮次有限（需实测，先配置 `agent.maxTurnsPerConversation`，默认 30）；逼近上限时自动开新会话并注入进度摘要（简易 compaction，P1 再做智能压缩）。
4. **速率控制**：两次 send 间隔最少 `agent.minSendIntervalMs`（默认 1500ms），遇错误指数退避重试 2 次。
5. **协议漂移防护**：SignalR 捕获失败自动回退 DOM（现有 auto 策略不动）；DOM 读取的文本过 sanitizeMarkdown 后再进协议解析。

## 11. 测试策略

- **单测（vitest，现有框架）**：protocol.ts 编解码/纠错、每个 tool（临时目录 fixture）、PermissionGate、workspace repo map、续写重叠拼接。
- **Agent 集成测试**：MockProvider 回放脚本化对话（含 malformed、截断、工具失败场景），验证 AgentLoop 状态机全路径，无需浏览器。
- **E2E（playwright，现有）**：保留现有 ask 流程回归；新增一条真实 Copilot 的最小 agent 冒烟（read_file + done），手动触发。
- 验证命令不变：`npm run typecheck && npm test && npm run build`。

## 12. 实施顺序与工作量估算

| 步骤 | 内容 | 预估 |
|---|---|---|
| 1 | runtime → provider/copilot-web 迁移 + Provider 接口 + MockProvider，全量回归 | 0.5 天 |
| 2 | protocol.ts + system-prompt.ts + 单测 | 0.5 天 |
| 3 | tools 七件套 + PermissionGate + 单测 | 1 天 |
| 4 | AgentLoop + workspace context + `code` 命令 + Mock 集成测试 | 1 天 |
| 5 | 可靠性专项（分片/续写去重/轮次预算/自愈） | 0.5–1 天 |
| 6 | `implement` 命令 + tasks.md 模板改造 | 0.5 天 |
| 7 | 真机联调（实测 Copilot 消息长度/轮次上限，回填配置默认值） | 0.5 天 |

合计约 4–5 个工作日。步骤 1 完成即可发一版（纯重构无行为变化），之后每步可独立验证。

## 13. 风险与开放问题

- **实测数据缺口**：Copilot 企业版单条消息字符上限、单会话轮次上限、日额度——步骤 7 实测前先用保守默认值（8000 字符 / 30 轮）。
- **格式遵循度**：Copilot 对自定义围栏格式的遵循度未知；若纠错率高，备选方案是让其输出放入 ```code fence 中（SignalR 原文通道下同样可解析）。
- **DOM 通道失真**：DOM 兜底读取可能丢失围栏内换行/空格，导致 edit_file 的 old 匹配失败——协议解析优先依赖 SignalR 原文；DOM 模式下 edit_file 失败率高时提示用户切 signalr。
- **YOLO 安全**：denyCommands 只是字符串匹配，不能防绕过；建议在重要 repo 先用 `--allowlist` 跑几次建立信任。
