# Teams Copilot CLI v2 — 浏览器自动化层 Playwright 迁移

## TL;DR

> **Quick Summary**: 将 teams-copilot-cli 的浏览器自动化层从 `@jackwener/opencli` + 手写 IFrameClient 迁移到 Playwright，同时新增 REPL 交互模式，实现跨平台 Chromium 浏览器支持，全程 TDD。
>
> **Deliverables**:
> - 可用的 npm 包 `teams-copilot-cli`，支持 `npx teams-copilot` 全局使用
> - REPL 交互式对话 + ask/prd/arch/tasks 四个命令
> - 跨平台 Chromium 浏览器自动发现
> - 流式输出（默认）+ `--no-stream` 完整输出
> - Vitest 单元测试 + Playwright Test E2E 测试
> - 清晰的 README 和安装文档
>
> **Estimated Effort**: Large (20 tasks, 5+1 waves)
> **Parallel Execution**: YES — 5 execution waves, max 6 parallel tasks
> **Critical Path**: T1 → T5 → T6 → T12 → T16 → T17 → F1-F4

---

## Context

### Original Request
用户需要在现有 teams-copilot-cli（v1）基础上重建，替换不稳定的 `@jackwener/opencli` 依赖，用 Playwright 实现更稳定、跨平台的 Teams Copilot 自动化。

### Interview Summary
**Key Discussions**:
- v1 对 opencli 使用极浅（仅 CDPBridge），实际核心交互全部绕过
- 手写 IFrameClient（130行 CDP WebSocket）是最大的稳定性隐患
- 目标发布为开源 npm 包，需要最小依赖和跨平台支持
- REPL 交互模式 + 流式输出是新核心特性
- 测试策略：TDD（测试驱动开发）
- 不需要会话持久化

**Research Findings**:
- Teams/Outlook 浏览器自动化是稀疏领域，无成熟开源竞品
- Playwright 是替代 opencli 的最优方案：微软维护、14万+ Star、原生 CDP 连接
- v1 的 ClipboardEvent 注入在 Lexical 编辑器中已验证可行，Playwright `insertText` 可能不兼容
- v1 的跨域 iframe 独立 CDP 连接存在于有原因——需在早期验证 Playwright Frame API 是否能替代

### Metis Review
**Identified Gaps** (addressed):
- **Text Injection Risk**: Playwright `keyboard.insertText()` 本质是 `InputEvent`，v1 已测试过该方向不可靠。采纳方案：保留 ClipboardEvent 为主路径，`insertText` 作为实验性备用
- **Cross-Origin Iframe 不确定性**: `page.frame()` 通过主页面 CDP session 访问跨域 iframe 可能受限。采纳方案：早期验证任务确认可行性，若失败则回退到 IFrameClient 模式（保留独立 CDP WebSocket）
- **REPL 多轮 DOM 累积**: 连续对话时 DOM 中会累积多个 "Copilot said:" 段落。采纳方案：track 对话轮次，每次只提取最新响应
- **浏览器发现复杂度**: 跨平台三大 OS 的浏览器路径各异。采纳方案：分层回退策略（环境变量 → 配置文件 → 自动扫描 → 手动指定）
- **TDD 可行性**: 浏览器依赖的模块无法纯 TDD。采纳方案：纯逻辑部分 TDD（sanitize, config, browser-finder），浏览器部分用 E2E smoke test 覆盖
- **ToS 风险**: 自动化 Teams Copilot 可能违反 Microsoft 服务条款。采纳方案：README 添加免责声明，仅用于教育和研究目的

---

## Work Objectives

### Core Objective
用 Playwright 完全替代 `@jackwener/opencli` + 手写 IFrameClient，实现跨平台、稳定、可维护的 Teams Copilot CLI v2。

### Concrete Deliverables
- `src/runtime/browser-adapter.ts` — Playwright 浏览器适配层
- `src/runtime/teams-page.ts` — Teams 页面交互封装
- `src/runtime/text-injector.ts` — 文本注入策略模块
- `src/runtime/stream-extractor.ts` — 流式输出提取模块
- `src/runtime/browser-finder.ts` — 跨平台 Chromium 发现
- `src/runtime/session-manager.ts` — 重构后的会话管理
- `src/cli/repl.ts` — REPL 交互循环
- `src/cli/ask.ts`, `prd.ts`, `arch.ts`, `tasks.ts` — 重构后的命令
- `test/` — Vitest 单元测试 + Playwright Test E2E
- `README.md` — 安装、使用、免责声明

### Definition of Done
- [ ] `npx teams-copilot ask "hello"` 返回 Copilot 响应（流式 + 非流式）
- [ ] `npx teams-copilot prd myproject` 生成 output/PRD.md
- [ ] `npx teams-copilot arch myproject` 依赖 PRD 生成 ARCH.md
- [ ] `npx teams-copilot tasks myproject` 依赖 PRD+ARCH 生成 TASKS.md
- [ ] `npx teams-copilot repl` 进入交互式对话
- [ ] `npm test` 所有测试通过
- [ ] 在 Windows/Mac/Linux 上均能自动发现 Chromium 浏览器

### Must Have
- Playwright 替代 opencli（移除 `@jackwener/opencli` 依赖）
- 跨平台 Chromium 浏览器支持
- REPL 交互式对话
- 流式输出（默认）+ `--no-stream` flag
- TDD 测试覆盖（纯逻辑部分 100%，浏览器部分 smoke test）
- README 含 ToS 免责声明

### Must NOT Have (Guardrails)
- **不引入任何新的非 Playwright 浏览器自动化依赖**（Puppeteer 等）
- **不实现会话持久化**（用户明确不需要）
- **不修改 prompts/ 模板内容**（保持 v1 的 Prompt 模板不变）
- **不删除 debug-*.mjs 文件**（保留作调试参考，但标记为 legacy）
- **不过度抽象**（简单、直接，不引入 DI 容器、插件系统等）
- **不添加 config.yaml 新字段**（除非是实现需要）
- **不修改 Teams Copilot iframe 内的 DOM 选择器假设**（v1 已验证的选择器保留）
- **不执行 ToS 合规审查**（免责声明即可，项目本质是教育/研究用途）

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: NO（需要新建）
- **Automated tests**: TDD（纯逻辑模块先写测试；浏览器依赖模块先实现后补 smoke test）
- **Framework**: Vitest（单元测试）+ Playwright Test（E2E）

### QA Policy
Every task MUST include agent-executed QA scenarios. Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Frontend/CLI**: Use Bash (node REPL / npx) — 运行命令，验证输出
- **API/Backend**: Use Bash (curl) — 验证端口和 CDP 端点
- **Library/Module**: Use Bash (node/bun) — 导入模块，调用函数，比较输出

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately - 基础设施):
├── T1: package.json + 构建配置 [quick]
├── T2: Type 类型定义 [quick]
├── T3: Config 配置系统 [quick]
├── T4: Browser Finder 浏览器发现 [quick]
└── T5: Browser Adapter 浏览器适配（验证跨域 iframe） [deep]

Wave 2 (After Wave 1 - 核心运行时):
├── T6: Teams Page 页面抽象 [unspecified-high]
├── T7: Text Injector 文本注入 [unspecified-high]
├── T8: Stream Extractor 流式提取 [unspecified-high]
├── T9: Session Manager 会话管理 [quick]
├── T10: Markdown Sanitizer 结果清洗 [quick]
└── T11: Copilot Runtime 主编排 [deep]

Wave 3 (After Wave 2 - CLI 命令):
├── T12: CLI Router + ask 命令 [quick]
├── T13: prd 命令 [quick]
├── T14: arch 命令 [quick]
└── T15: tasks 命令 [quick]

Wave 4 (After Wave 2 - REPL):
└── T16: REPL 模块 [unspecified-high]

Wave 5 (After Wave 3,4 - 测试):
├── T17: 单元测试补齐 [quick]
└── T18: E2E smoke test [unspecified-high]

Wave FINAL:
├── F1: Plan Compliance Audit [oracle]
├── F2: Code Quality Review [unspecified-high]
├── F3: Real Manual QA [unspecified-high]
└── F4: Scope Fidelity Check [deep]
```

**Critical Path**: T1 → T5 → T6 → T11 → T12 → T16 → T17 → F1-F4

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| 1-4 | - | 5-16 | 1 |
| 5 | 1,2,4 | 6,7,8,11 | 1 |
| 6 | 5 | 11 | 2 |
| 7 | 5 | 11 | 2 |
| 8 | 5 | 11,16 | 2 |
| 9 | 3 | 11,16 | 2 |
| 10 | - | 11,16 | 2 |
| 11 | 6,7,8,9,10 | 12-16 | 2 |
| 12 | 11 | 17,18 | 3 |
| 13 | 11 | 17,18 | 3 |
| 14 | 11 | 15,17,18 | 3 |
| 15 | 11,14 | 17,18 | 3 |
| 16 | 11,8 | 18 | 4 |
| 17 | 12-16 | F1-F4 | 5 |
| 18 | 1-16 | F1-F4 | 5 |

### Agent Dispatch Summary

- **Wave 1**: 5 tasks — T1-T4 → `quick`, T5 → `deep`
- **Wave 2**: 6 tasks — T6-T8 → `unspecified-high`, T9-T10 → `quick`, T11 → `deep`
- **Wave 3**: 4 tasks — T12-T15 → `quick`
- **Wave 4**: 1 task — T16 → `unspecified-high`
- **Wave 5**: 2 tasks — T17 → `quick`, T18 → `unspecified-high`
- **FINAL**: 4 tasks — F1 → `oracle`, F2-F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

### Wave 1 — 基础设施（5 个任务，全部并行）

- [x] 1. **package.json + 构建配置 + 项目脚手架**

  **What to do**:
  - 从 v1 package.json 继承基础结构（name, description, bin, scripts）
  - 移除 `@jackwener/opencli` 依赖
  - 添加 `playwright-core` (peer 依赖，提示用户安装), `commander`, `js-yaml`
  - 添加 devDependencies: `vitest`, `@playwright/test`, `typescript`, `@types/node`, `@types/js-yaml`
  - 配置 `tsconfig.json`：target ES2022, module NodeNext, outDir dist, rootDir src
  - 配置 `bin` 字段：`"teams-copilot": "./dist/cli/index.js"`
  - 添加 scripts：build, dev, test, start, lint
  - 创建目录结构：src/cli/, src/runtime/, test/, prompts/
  - 从 v1 复制 prompts/prd.md, prompts/arch.md, prompts/tasks.md 到新项目
  - 从 v1 复制 config.yaml 到新项目
  - 保留 v1 的 debug-*.mjs 文件，移至 debug-legacy/ 目录并添加 LEGACY.md 说明

  **Must NOT do**:
  - 不要添加任何 v1 不需要的新依赖（如 chalk, ora, inquirer 等）
  - 不要修改 prompts/*.md 的内容
  - 不要删除 v1 项目中的任何文件

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T2, T3, T4, T5)
  - **Blocks**: T6-T16 (所有后续任务)
  - **Blocked By**: None

  **References**:
  - `resource/agent/teams-copilot-cli/package.json` — v1 的依赖和 scripts 模板
  - `resource/agent/teams-copilot-cli/prompts/*.md` — 复制到新项目的 Prompt 模板
  - `resource/agent/teams-copilot-cli/config.yaml` — 复制到新项目的配置模板
  - `resource/agent/teams-copilot-cli/bun.lock` — 查看 v1 间接依赖，避免冲突

  **Acceptance Criteria**:
  - [ ] `npm install` 成功（无错误）
  - [ ] `npm run build` 成功（即使 src/ 还为空，至少 tsc 不报 fatal error）
  - [ ] `ls src/cli/ src/runtime/ test/ prompts/ debug-legacy/` 所有目录存在
  - [ ] package.json 中 `@jackwener/opencli` 已移除
  - [ ] package.json 中 `playwright-core` 在 peerDependencies

  **QA Scenarios**:
  ```
  Scenario: 安装成功
    Tool: Bash
    Preconditions: 项目根目录
    Steps:
      1. cd resource/agent/teams-copilot-cli && npm install
      2. npm run build
    Expected Result: 两个命令均无错误退出
    Failure Indicators: npm install 报错、tsc 编译错误
    Evidence: .sisyphus/evidence/task-1-install.txt

  Scenario: 依赖验证
    Tool: Bash
    Preconditions: npm install 已完成
    Steps:
      1. node -e "const pkg = require('./package.json'); console.log(JSON.stringify(Object.keys(pkg.dependencies || {})))"
    Expected Result: 不包含 @jackwener/opencli
    Evidence: .sisyphus/evidence/task-1-deps.txt
  ```

  **Commit**: YES (groups with Wave 1)
  - Message: `feat(scaffold): project setup with playwright, types, config, browser modules`

---

- [x] 2. **Type 类型定义**

  **What to do**:
  - 创建 `src/types.ts`
  - 定义核心接口（基于 v1 的实际使用场景，不做过度抽象）：
    - `BrowserConfig { path?: string; port: number; userDataDir: string }`
    - `CopilotConfig { teamsUrl: string; copilotUrl: string; selectors: {...}; timeouts: {...} }`
    - `AppConfig { browser: BrowserConfig; copilot: CopilotConfig }`（config.yaml 的 TS 类型）
    - `InjectResult { success: boolean; method: 'clipboard' | 'insertText'; error?: string }`
    - `StreamResult { text: string; truncated: boolean; duration: number }`
    - `CommandResult { success: boolean; output: string; error?: string; exitCode: number }`
    - `CopilotSession { ask(prompt: string): Promise<StreamResult>; close(): Promise<void> }`（接口，非实现）
  - 从 `playwright-core` 导入 `Browser`, `Page`, `Frame` 类型（仅作类型引用）

  **Must NOT do**:
  - 不要实现任何逻辑
  - 不要引入 DI 容器或抽象工厂模式
  - 类型定义要精炼，不超过 80 行

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T1, T3, T4, T5)
  - **Blocks**: T5-T16
  - **Blocked By**: None

  **References**:
  - `resource/agent/teams-copilot-cli/runtime/opencli-adapter.ts` — 查看 IFrameClient 和 OpenCLIAdapter 暴露的接口形状
  - `resource/agent/teams-copilot-cli/runtime/copilot-runtime.ts` — 查看 CopilotRuntime 的 ask() 方法签名
  - `resource/agent/teams-copilot-cli/runtime/session-manager.ts` — 查看 SessionManager 的接口

  **Acceptance Criteria**:
  - [ ] `src/types.ts` 存在，所有接口定义完整
  - [ ] `tsc --noEmit` 无类型错误

  **QA Scenarios**:
  ```
  Scenario: 类型编译检查
    Tool: Bash
    Preconditions: T1 完成，npm install 已跑
    Steps:
      1. cd resource/agent/teams-copilot-cli && npx tsc --noEmit
    Expected Result: 无类型错误
    Evidence: .sisyphus/evidence/task-2-types.txt
  ```

  **Commit**: YES (groups with Wave 1)

---

- [x] 3. **Config 配置系统**

  **What to do**:
  - 创建 `src/runtime/config.ts`
  - 实现 `loadConfig(configPath?: string): AppConfig` — 从 config.yaml 加载
  - 实现 `mergeCliFlags(config: AppConfig, flags: Partial<BrowserConfig>): AppConfig` — CLI flag 覆盖 config
  - 优先级规则：CLI flags > config.yaml > 默认值
  - 默认值：port=9222, userDataDir=`~/.teams-copilot/profile`, teamsUrl 用 v1 的 URL
  - 配置验证：检查必填字段（teamsUrl, copilotUrl）存在
  - 友好的错误信息（文件不存在、YAML 解析失败、缺少必填字段）

  **Must NOT do**:
  - 不要引入 AJV、Zod 等 schema 验证库（手写简单验证即可）
  - 不要支持 .env 文件（用户没要求）

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T1, T2, T4, T5)
  - **Blocks**: T9, T11-T16
  - **Blocked By**: None

  **References**:
  - `resource/agent/teams-copilot-cli/config.yaml` — v1 的配置结构和字段
  - `resource/agent/teams-copilot-cli/runtime/session-manager.ts` — v1 的 loadConfig 实现参考

  **Acceptance Criteria**:
  - [ ] `loadConfig()` 正确解析 config.yaml
  - [ ] `mergeCliFlags()` 正确覆盖：CLI flag 优先级最高
  - [ ] 缺失 config.yaml 时使用默认值（不崩溃）
  - [ ] 缺失必填字段时抛友好错误
  - [ ] TDD：先写测试文件 `test/config.test.ts`（RED → GREEN）

  **QA Scenarios**:
  ```
  Scenario: 正常加载 config.yaml
    Tool: Bash
    Preconditions: config.yaml 存在且格式正确
    Steps:
      1. cd resource/agent/teams-copilot-cli
      2. node -e "const {loadConfig} = require('./dist/runtime/config.js'); console.log(JSON.stringify(loadConfig()))"
    Expected Result: 输出包含 browser.port=9222, copilot.teamsUrl 等字段
    Evidence: .sisyphus/evidence/task-3-config-load.txt

  Scenario: 配置文件缺失降级默认值
    Tool: Bash
    Preconditions: config.yaml 重命名为 config.yaml.bak
    Steps:
      1. mv config.yaml config.yaml.bak
      2. node -e "const {loadConfig} = require('./dist/runtime/config.js'); console.log(JSON.stringify(loadConfig()))"
    Expected Result: 不报错，使用默认 port=9222
    Evidence: .sisyphus/evidence/task-3-config-default.txt
  ```

  **Commit**: YES (groups with Wave 1)

---

- [x] 4. **Browser Finder 跨平台浏览器发现**

  **What to do**:
  - 创建 `src/runtime/browser-finder.ts`
  - 实现 `findChromiumBrowser(preferred?: string): string | null`
  - 搜索顺序（按用户优先级）：
    1. 环境变量 `TEAMS_COPILOT_BROWSER`（最高优先级）
    2. config.yaml 中的 `browser.path`
    3. 自动扫描系统路径：
       - Windows: `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`, `C:\Program Files\Google\Chrome\Application\chrome.exe`
       - macOS: `/Applications/Microsoft Edge.app/...`, `/Applications/Google Chrome.app/...`
       - Linux: `/usr/bin/microsoft-edge`, `/usr/bin/google-chrome`, `/usr/bin/chromium-browser`
  - 实现 `getDefaultUserDataDir(browserName: string): string` — 返回 `~/.teams-copilot/profiles/{browser}`
  - 验证找到的路径确实可执行（`fs.accessSync` 检查）
  - 找不到时返回 null + 友好的错误提示（列出已搜索的路径）

  **Must NOT do**:
  - 不要用 glob 或 any 类型
  - 不要搜索非 Chromium 浏览器（Firefox, Safari 等）

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T1, T2, T3, T5)
  - **Blocks**: T5
  - **Blocked By**: None

  **References**:
  - `resource/agent/teams-copilot-cli/runtime/opencli-adapter.ts` — v1 的浏览器启动逻辑（spawn + --remote-debugging-port）
  - `resource/agent/teams-copilot-cli/config.yaml` — v1 的 browser.path 字段

  **Acceptance Criteria**:
  - [ ] Windows 上能找到 Edge 或 Chrome
  - [ ] 找不到浏览器时返回 null（不抛异常）
  - [ ] 环境变量 `TEAMS_COPILOT_BROWSER` 覆盖自动扫描
  - [ ] TDD：先写测试 `test/browser-finder.test.ts`（mock fs.accessSync）

  **QA Scenarios**:
  ```
  Scenario: 自动发现 Edge
    Tool: Bash
    Preconditions: Windows 系统，Edge 已安装
    Steps:
      1. cd resource/agent/teams-copilot-cli
      2. node -e "const {findChromiumBrowser} = require('./dist/runtime/browser-finder.js'); console.log(findChromiumBrowser())"
    Expected Result: 输出 Edge 可执行文件路径（非 null）
    Evidence: .sisyphus/evidence/task-4-browser-found.txt

  Scenario: 环境变量覆盖
    Tool: Bash
    Preconditions: 设置环境变量
    Steps:
      1. export TEAMS_COPILOT_BROWSER="/fake/path/chrome"
      2. node -e "const {findChromiumBrowser} = require('./dist/runtime/browser-finder.js'); console.log(findChromiumBrowser())"
    Expected Result: 输出 "/fake/path/chrome"（环境变量优先）
    Evidence: .sisyphus/evidence/task-4-env-override.txt
  ```

  **Commit**: YES (groups with Wave 1)

---

- [x] 5. **Browser Adapter 浏览器适配（含跨域 iframe 验证）**

  **What to do**:
  - 创建 `src/runtime/browser-adapter.ts`
  - 实现 `launchBrowser(config: BrowserConfig): Promise<{ pid: number; port: number }>`
    - 用 `child_process.spawn()` 启动 Chromium + `--remote-debugging-port={port}` + `--user-data-dir={dir}`
    - 轮询 `http://localhost:{port}/json/version` 检测 CDP 就绪
    - 超时 30s 后抛 `BROWSER_LAUNCH_FAILED`
  - 实现 `connectToBrowser(port: number): Promise<Browser>`
    - `chromium.connectOverCDP('http://localhost:{port}')`
  - **★ 关键验证任务**：实现 `verifyCrossOriginIframe(page: Page): Promise<boolean>`
    - 导航到 Teams URL
    - 尝试 `page.frame({ url: /semanticoverview/ })` 获取 iframe
    - 在 iframe 中执行 `iframe.evaluate(() => document.title)` 验证是否能读跨域内容
    - **如果失败**：返回 false → 回退到独立 CDP WebSocket 方案（复用 v1 IFrameClient 模式）
    - **如果成功**：返回 true → Playwright Frame API 完全替代 IFrameClient

  **Must NOT do**:
  - 不要假设 `page.frame()` 一定能访问跨域 iframe——必须验证
  - 不要在验证失败时崩溃——返回 false 触发回退路径

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T1, T2, T3, T4)
  - **Blocks**: T6, T7, T8, T11
  - **Blocked By**: T1, T2, T4

  **References**:
  - `resource/agent/teams-copilot-cli/runtime/opencli-adapter.ts:startBrowser()` — v1 的浏览器启动逻辑
  - `resource/agent/teams-copilot-cli/runtime/opencli-adapter.ts:IFrameClient` — 跨域 iframe 回退方案
  - `resource/agent/teams-copilot-cli/config.yaml` — CDP 端口和超时配置
  - Playwright docs: `https://playwright.dev/docs/api/class-browsertype#browser-type-connect-over-cdp`

  **Acceptance Criteria**:
  - [ ] `launchBrowser()` 成功启动 Chromium 并返回 pid + port
  - [ ] `connectToBrowser()` 返回 Playwright Browser 实例
  - [ ] `verifyCrossOriginIframe()` 返回验证结果（true 或 false）
  - [ ] 启动超时抛 `BROWSER_LAUNCH_FAILED` 错误
  - [ ] 已有 CDP 端口时复用（不启动新进程）

  **QA Scenarios**:
  ```
  Scenario: 启动浏览器并连接
    Tool: Bash
    Preconditions: Chromium 浏览器已安装
    Steps:
      1. cd resource/agent/teams-copilot-cli
      2. node -e "
        const {launchBrowser, connectToBrowser} = require('./dist/runtime/browser-adapter.js');
        (async () => {
          const {port} = await launchBrowser({port: 9222, userDataDir: '/tmp/test-profile'});
          const browser = await connectToBrowser(port);
          console.log('CONNECTED:', browser.isConnected());
          await browser.close();
        })()
      "
    Expected Result: 输出 "CONNECTED: true"
    Evidence: .sisyphus/evidence/task-5-launch-connect.txt

  Scenario: 跨域 iframe 验证
    Tool: Bash
    Preconditions: 浏览器已启动，已登录 Teams
    Steps:
      1. 运行 verifyCrossOriginIframe() 
      2. 记录返回值（true/false）
    Expected Result: 返回 true 或 false（根据实际情况）
    Failure Indicators: 程序崩溃而非返回 false
    Evidence: .sisyphus/evidence/task-5-iframe-check.txt
  ```

  **Commit**: YES (groups with Wave 1)

---

### Wave 2 — 核心运行时（6 个任务，最大并行）

- [x] 6. **Teams Page 页面抽象**

  **What to do**:
  - 创建 `src/runtime/teams-page.ts`
  - 实现 `TeamsPage` 类，封装 Teams Web 页面的所有交互：
    - `constructor(page: Page, config: CopilotConfig)` — 接收 Playwright Page + 配置
    - `async goto(): Promise<void>` — 导航到 teamsUrl，等待页面加载
    - `async isLoggedIn(): Promise<boolean>` — 检测认证状态（检查 URL 包含 `login` 或特定元素）
    - `async navigateToCopilot(): Promise<void>` — 点击 Copilot 入口，等待 iframe 加载
    - `async getCopilotFrame(): Promise<Frame>` — 获取 Copilot iframe
      - 优先用 `page.frame({ url: /semanticoverview/ })` （T5 验证通过时）
      - 若 T5 验证失败，抛 `IFRAME_ACCESS_FAILED` 错误（让调用方走回退路径）
    - `async waitForReady(): Promise<void>` — 等待 Copilot 输入框就绪
  - CSS 选择器从 config.copilot.selectors 读取（不硬编码）

  **Must NOT do**:
  - 不要把选择器硬编码在代码中——从 config.yaml 读取
  - 不要在 Teams Page 中包含文本注入或流式提取逻辑（那是 T7/T8 的职责）

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T7, T8, T9, T10, T11)
  - **Blocks**: T11, T12, T16
  - **Blocked By**: T5 (需要知道 iframe 访问方式)

  **References**:
  - `resource/agent/teams-copilot-cli/runtime/opencli-adapter.ts:OpenCLIAdapter` — v1 的 navigateToCopilot() 和 getCopilotIframe() 实现
  - `resource/agent/teams-copilot-cli/runtime/copilot-runtime.ts:isLoggedIn()` — v1 的登录检测逻辑
  - `resource/agent/teams-copilot-cli/config.yaml:copilot.selectors` — CSS 选择器配置

  **Acceptance Criteria**:
  - [ ] `goto()` 成功导航到 Teams URL
  - [ ] `isLoggedIn()` 正确检测登录态
  - [ ] `navigateToCopilot()` 打开 Copilot 面板
  - [ ] `getCopilotFrame()` 返回 Copilot iframe 的 Playwright Frame 对象
  - [ ] `waitForReady()` 等待输入框可交互

  **QA Scenarios**:
  ```
  Scenario: 登录态检测（已登录）
    Tool: Bash
    Preconditions: 浏览器已通过 T5 连接，当前已登录 Teams
    Steps:
      1. cd resource/agent/teams-copilot-cli
      2. node -e "测试脚本：连接浏览器，创建 TeamsPage，调用 isLoggedIn()"
    Expected Result: isLoggedIn() 返回 true
    Evidence: .sisyphus/evidence/task-6-login-check.txt

  Scenario: 获取 Copilot iframe
    Tool: Bash
    Preconditions: 已导航到 Copilot 页面
    Steps:
      1. 调用 getCopilotFrame() 
      2. 在 frame 中执行 frame.evaluate(() => document.title)
    Expected Result: 返回 iframe 内文档标题（验证可访问）
    Evidence: .sisyphus/evidence/task-6-iframe-access.txt
  ```

  **Commit**: YES (groups with Wave 2)

---

- [x] 7. **Text Injector 文本注入**

  **What to do**:
  - 创建 `src/runtime/text-injector.ts`
  - 实现 `injectText(frame: Frame, text: string, selector: string): Promise<InjectResult>`
  - **主路径 (ClipboardEvent)**：通过 `frame.evaluate()` 注入 ClipboardEvent 模拟粘贴
    - 聚焦 contentEditable 元素
    - 创建 DataTransfer + ClipboardEvent
    - dispatchEvent 到目标元素
    - v1 已验证此方法对 Lexical 编辑器有效
  - **备用路径 (insertText)**：用 `page.keyboard.insertText()` 注入
    - 仅当 ClipboardEvent 失败时尝试
    - 结果标记 method='insertText'
  - 注入后验证：检查内容是否真的出现在 DOM 中（简单 textContent 检查）
  - 返回 `InjectResult { success, method, error? }`

  **Must NOT do**:
  - 不要用逐字 type（已确认对 Lexical 无效）
  - 不要把注入方法和 Teams 页面逻辑耦合

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T6, T8, T9, T10, T11)
  - **Blocks**: T11
  - **Blocked By**: T5 (需要知道 iframe 访问方式)

  **References**:
  - `resource/agent/teams-copilot-cli/runtime/opencli-adapter.ts:IFrameClient.send()` — v1 的 ClipboardEvent 注入实现（约第 90-130 行）
  - `resource/agent/teams-copilot-cli/debug-lexical-injection.mjs` — v1 的多种注入方式实验
  - `resource/agent/teams-copilot-cli/debug-robust-injection.mjs` — v1 的 Robust 注入测试
  - v1 代码注释：`// ContentEditable Lexical 编辑器不支持逐字 type → 必须用 ClipboardEvent`

  **Acceptance Criteria**:
  - [ ] ClipboardEvent 主路径成功注入文本到 contentEditable
  - [ ] 注入后 DOM 验证：textContent 包含注入的文本
  - [ ] 注入失败时返回 `{ success: false, error: "..." }`（不抛异常）
  - [ ] 方法标记正确（clipboard 或 insertText）

  **QA Scenarios**:
  ```
  Scenario: ClipboardEvent 注入成功
    Tool: Bash
    Preconditions: T6 的 Copilot iframe 可访问
    Steps:
      1. cd resource/agent/teams-copilot-cli
      2. node -e "测试脚本：获取 iframe，调用 injectText(frame, 'test prompt', '[contenteditable]')"
    Expected Result: InjectResult.success = true, method = 'clipboard'
    Evidence: .sisyphus/evidence/task-7-inject-success.txt

  Scenario: 注入验证（内容确实在 DOM 中）
    Tool: Bash
    Preconditions: 刚完成注入
    Steps:
      1. frame.evaluate(() => document.querySelector('[contenteditable]').textContent)
    Expected Result: 包含 "test prompt"
    Evidence: .sisyphus/evidence/task-7-inject-verify.txt
  ```

  **Commit**: YES (groups with Wave 2)

---

- [x] 8. **Stream Extractor 流式提取**

  **What to do**:
  - 创建 `src/runtime/stream-extractor.ts`
  - 实现 `extractStream(frame: Frame, config: CopilotConfig): AsyncGenerator<string, StreamResult, void>`
    - 基于 v1 的 `pollCopilotResponse()` 逻辑
    - 使用 `page.waitForFunction()` 替代 setInterval 轮询（降低 CPU 占用）
    - 增量提取：每次 yield 新增的文本内容
    - 防抖确认：文本长度连续 2 次（间隔 500ms）不变 → 判定流式结束
    - 截断检测：无句末标点（。.!?）或代码块未闭合（\`\`\` 不成对）→ 自动续写
    - 超时控制：config.copilot.timeouts.streaming 超时 → 返回已获取内容 + truncated=true
  - 实现 `extractFull(frame: Frame, config: CopilotConfig): Promise<StreamResult>`
    - 等待流式完成，返回完整结果（non-streaming 模式用）
  - REPL 多轮对话处理：track `roundIndex`，每次只提取最新的 "Copilot said:" 段落后内容

  **Must NOT do**:
  - 不要用 setInterval 轮询（Playwright waitForFunction 更高效）
  - 不要在 extractor 中包含 Markdown sanitization（那是 T10 的职责）

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T6, T7, T9, T10, T11)
  - **Blocks**: T11, T16
  - **Blocked By**: T5

  **References**:
  - `resource/agent/teams-copilot-cli/runtime/opencli-adapter.ts:IFrameClient.pollCopilotResponse()` — v1 的 DOM 轮询逻辑
  - `resource/agent/teams-copilot-cli/runtime/opencli-adapter.ts:IFrameClient.waitForStreamingEnd()` — v1 的防抖确认
  - `resource/agent/teams-copilot-cli/runtime/copilot-runtime.ts:ask()` — 截断检测和续写逻辑
  - Playwright docs: `page.waitForFunction()` — `https://playwright.dev/docs/api/class-page#page-wait-for-function`

  **Acceptance Criteria**:
  - [ ] `extractStream()` 正确 yield 增量文本
  - [ ] 流式结束时返回完整 StreamResult
  - [ ] 截断检测自动触发续写
  - [ ] 超时后返回 truncated=true
  - [ ] 多轮对话时提取最新响应（非历史累积）

  **QA Scenarios**:
  ```
  Scenario: 流式输出
    Tool: Bash
    Preconditions: 已注入 prompt 并触发发送
    Steps:
      1. 启动 extractStream 迭代
      2. 记录每次 yield 的文本片段
    Expected Result: 逐步获取递增的文本，最终返回 StreamResult
    Evidence: .sisyphus/evidence/task-8-stream.txt

  Scenario: 超时处理
    Tool: Bash
    Preconditions: Copilot 正在响应
    Steps:
      1. 设置极短超时（1秒）
      2. 调用 extractStream
    Expected Result: 返回 StreamResult { truncated: true }，包含已获取的部分文本
    Evidence: .sisyphus/evidence/task-8-timeout.txt
  ```

  **Commit**: YES (groups with Wave 2)

---

- [x] 9. **Session Manager 会话管理**

  **What to do**:
  - 创建 `src/runtime/session-manager.ts`（重构 v1 的 session-manager.ts）
  - 实现 `SessionManager` 类：
    - `constructor(config: AppConfig)` — 接收合并后的配置
    - `validateConfig(): void` — 验证必填字段
    - `checkAuth(page: Page): Promise<boolean>` — 调用 TeamsPage.isLoggedIn()
    - `handleError(error: Error): CommandResult` — 错误码映射到用户友好消息
  - 错误码（继承 v1，保持不变）：
    - `1`: INVALID_CONFIG — 配置文件缺失或格式错误
    - `2`: BROWSER_LAUNCH_FAILED — 浏览器启动失败
    - `3`: IFRAME_ACCESS_FAILED — 跨域 iframe 无法访问（★ 新增）
    - `77`: AUTH_EXPIRED — 需要手动重新登录
    - `88`: STREAMING_TIMEOUT — Copilot 响应超时
  - 集成 T3 的 config 系统

  **Must NOT do**:
  - 不要修改 v1 的错误码编号（77, 88 保持不变）
  - 不要在这个模块中操作浏览器——通过 TeamsPage 接口调用

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T6, T7, T8, T10, T11)
  - **Blocks**: T11, T16
  - **Blocked By**: T3

  **References**:
  - `resource/agent/teams-copilot-cli/runtime/session-manager.ts` — v1 的 loadConfig, authCheck, exit codes
  - `resource/agent/teams-copilot-cli/runtime/copilot-runtime.ts:isLoggedIn()` — v1 的认证检测

  **Acceptance Criteria**:
  - [ ] `validateConfig()` 检测配置缺失，抛出 INVALID_CONFIG
  - [ ] `checkAuth()` 委托给 TeamsPage，返回 boolean
  - [ ] `handleError()` 返回正确 exitCode 和用户友好消息
  - [ ] TDD：先写测试 `test/session-manager.test.ts`（mock TeamsPage）

  **QA Scenarios**:
  ```
  Scenario: 配置验证失败
    Tool: Bash
    Preconditions: 空配置对象
    Steps:
      1. node -e "测试：SessionManager with empty config, validateConfig()"
    Expected Result: 抛出错误，exitCode=1
    Evidence: .sisyphus/evidence/task-9-config-error.txt

  Scenario: 错误映射
    Tool: Bash
    Preconditions: SessionManager 实例
    Steps:
      1. 传入 AUTH_EXPIRED 错误
      2. 验证 handleError 返回 exitCode=77
    Expected Result: CommandResult { exitCode: 77, success: false }
    Evidence: .sisyphus/evidence/task-9-error-mapping.txt
  ```

  **Commit**: YES (groups with Wave 2)

---

- [x] 10. **Markdown Sanitizer 结果清洗**

  **What to do**:
  - 创建 `src/runtime/sanitizer.ts`
  - 实现 `sanitizeMarkdown(text: string): string`
  - 从 v1 `copilot-runtime.ts: sanitizeMarkdown()` 移植逻辑：
    - 移除 AI 寒暄前缀（"当然可以！", "好的，我来帮你..." 等）
    - 移除后缀追问（"还有其他问题吗？", "需要我详细解释吗？" 等）
    - 移除续写提示词残留（"请严格从你断开的地方继续输出" 在输出中出现）
    - 移除外层 ```markdown ... ``` 包裹
    - 清理多余的连续空行
  - 所有清洗规则用配置数组（可扩展）
  - 纯函数，无副作用，易于测试

  **Must NOT do**:
  - 不要修改 v1 的清洗逻辑——只做移植
  - 不要引入 AI/ML 依赖做"智能"清洗

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with T6, T7, T8, T9, T11)
  - **Blocks**: T11, T16
  - **Blocked By**: None

  **References**:
  - `resource/agent/teams-copilot-cli/runtime/copilot-runtime.ts:sanitizeMarkdown()` — v1 的清洗逻辑（直接移植）

  **Acceptance Criteria**:
  - [ ] 移除 "当然可以！以下是..." 类型的寒暄前缀
  - [ ] 移除 "还有其他问题吗？" 类型的后缀
  - [ ] 移除 ```markdown ... ``` 外层包裹
  - [ ] 清理连续空行
  - [ ] TDD：先写测试 `test/sanitizer.test.ts`（包含所有清洗场景）

  **QA Scenarios**:
  ```
  Scenario: 移除寒暄前缀
    Tool: Bash
    Preconditions: sanitizer 模块已构建
    Steps:
      1. node -e "const {sanitizeMarkdown} = require('./dist/runtime/sanitizer.js'); console.log(sanitizeMarkdown('当然可以！以下是代码：\n\nconst x = 1;'))"
    Expected Result: 输出 "const x = 1;"（前缀被移除）
    Evidence: .sisyphus/evidence/task-10-prefix.txt

  Scenario: 保留有效内容
    Tool: Bash
    Steps:
      1. node -e "sanitizeMarkdown('```markdown\n# Title\n\nContent\n```')"
    Expected Result: 输出 "# Title\n\nContent"（去掉 markdown 包裹）
    Evidence: .sisyphus/evidence/task-10-unwrap.txt
  ```

  **Commit**: YES (groups with Wave 2)

---

- [x] 11. **Copilot Runtime 主编排**

  **What to do**:
  - 创建 `src/runtime/copilot-runtime.ts`（重构 v1 的 copilot-runtime.ts）
  - 实现 `CopilotRuntime` 类，整合所有 Wave 2 模块：
    - `constructor(config: AppConfig)` — 初始化 SessionManager + BrowserAdapter + TeamsPage
    - `async initialize(): Promise<void>` — 完整启动流程：
      1. SessionManager.validateConfig()
      2. BrowserFinder.findChromiumBrowser()
      3. BrowserAdapter.launchBrowser() + connectToBrowser()
      4. TeamsPage.goto() → isLoggedIn() → navigateToCopilot() → getCopilotFrame()
      5. 创建 TextInjector + StreamExtractor 实例
    - `async ask(prompt: string, opts?: { stream?: boolean }): Promise<StreamResult>`
      - injector.injectText(frame, prompt) → 点击发送
      - 若 opts.stream !== false → extractStream(frame) 逐块输出到 process.stdout
      - 否则 → extractFull(frame) 等待完成
      - sanitizeMarkdown(结果)
      - 截断检测 → 自动续写（沿用 v1 逻辑）
    - `async close(): Promise<void>` — 关闭浏览器
  - 错误处理：任一环节失败 → SessionManager.handleError() → 返回友好错误 + exit code

  **Must NOT do**:
  - 不要在 Runtime 中硬编码业务逻辑——通过注入的子模块完成
  - 不要吞掉错误——所有错误通过 handleError 统一处理

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: ONLY with T9, T10 (after T6-T8)
  - **Parallel Group**: Wave 2
  - **Blocks**: T12-T16 (所有 CLI 和 REPL)
  - **Blocked By**: T6, T7, T8, T9, T10

  **References**:
  - `resource/agent/teams-copilot-cli/runtime/copilot-runtime.ts` — v1 的 initialize() 和 ask() 完整流程
  - `resource/agent/teams-copilot-cli/runtime/opencli-adapter.ts` — IFrameClient.send() 和 waitForStreamingEnd() 逻辑
  - `resource/agent/teams-copilot-cli/config.yaml` — 超时和选择器配置

  **Acceptance Criteria**:
  - [ ] `initialize()` 完整执行启动流程并返回 CopilotSession
  - [ ] `ask("hello")` 流式输出文本到终端，返回 StreamResult
  - [ ] `ask("hello", { stream: false })` 等待完整结果后返回
  - [ ] 截断自动续写正常工作
  - [ ] 认证失败时 exit code = 77
  - [ ] 超时时 exit code = 88

  **QA Scenarios**:
  ```
  Scenario: 完整 ask 流程（流式）
    Tool: Bash
    Preconditions: 浏览器已登录 Teams Copilot
    Steps:
      1. cd resource/agent/teams-copilot-cli
      2. node -e "
        const runtime = new CopilotRuntime(config);
        await runtime.initialize();
        const result = await runtime.ask('say hello');
        console.log(JSON.stringify(result));
        await runtime.close();
      "
    Expected Result: 终端看到逐字输出 "Hello..."，StreamResult { success: true, text: "..." }
    Evidence: .sisyphus/evidence/task-11-ask-stream.txt

  Scenario: 认证失败错误码
    Tool: Bash
    Preconditions: 未登录 Teams
    Steps:
      1. 调用 runtime.initialize()
    Expected Result: 退出码 77，错误信息友好
    Evidence: .sisyphus/evidence/task-11-auth-error.txt
  ```

  **Commit**: YES (groups with Wave 2)

---

### Wave 3 — CLI 命令（4 个任务，全部并行）

- [x] 12. **CLI Router + ask 命令**

  **What to do**:
  - 创建 `src/cli/index.ts`
  - 用 Commander 设置 CLI 入口：
    - 全局 flags: `--browser <path>`, `--port <number>`, `--no-stream`, `--config <path>`
    - 子命令: `ask`, `prd`, `arch`, `tasks`, `repl`
  - 实现 `src/cli/ask.ts`：
    - 接收用户 prompt（剩余参数拼接为字符串）
    - 调用 CopilotRuntime.initialize() + ask(prompt, { stream })
    - `--no-stream` flag → 等待完整结果后一次性输出
    - 默认流式输出到 process.stdout
    - 错误时输出友好消息到 stderr + process.exit(exitCode)
  - 实现 `handleCliError(error: Error): never` 统一错误处理

  **Must NOT do**:
  - 不要实现 REPL 逻辑（那是 T16）
  - 不要在 CLI 层做任何浏览器操作——全部委托给 Runtime

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T13, T14, T15)
  - **Blocks**: T17, T18
  - **Blocked By**: T11

  **References**:
  - `resource/agent/teams-copilot-cli/cli/index.ts` — v1 的命令路由结构
  - `resource/agent/teams-copilot-cli/cli/ask.ts` — v1 的 ask 命令实现

  **Acceptance Criteria**:
  - [ ] `teams-copilot ask "hello"` 流式输出响应
  - [ ] `teams-copilot ask --no-stream "hello"` 一次性输出完整响应
  - [ ] `teams-copilot --browser /path/to/chrome ask "hello"` 使用指定浏览器
  - [ ] 错误时 exit code 非 0 + stderr 有消息

  **QA Scenarios**:
  ```
  Scenario: ask 流式输出
    Tool: Bash
    Preconditions: 项目已 build，浏览器已登录
    Steps:
      1. cd resource/agent/teams-copilot-cli
      2. node dist/cli/index.js ask "say hello in one word"
    Expected Result: 终端逐字输出响应，"Hello" 或 "Hi"
    Evidence: .sisyphus/evidence/task-12-ask-stream.txt

  Scenario: ask --no-stream
    Tool: Bash
    Steps:
      1. node dist/cli/index.js ask --no-stream "what is 1+1"
    Expected Result: 一次性输出包含 "2" 的文本
    Evidence: .sisyphus/evidence/task-12-ask-full.txt

  Scenario: ask 错误处理
    Tool: Bash
    Preconditions: 指定不存在的浏览器路径
    Steps:
      1. node dist/cli/index.js --browser /fake/path ask "hello"
    Expected Result: exit code != 0, stderr 有错误信息
    Evidence: .sisyphus/evidence/task-12-ask-error.txt
  ```

  **Commit**: YES (groups with Wave 3)

---

- [x] 13. **prd 命令**

  **What to do**:
  - 创建 `src/cli/prd.ts`
  - 实现 PRD 生成命令：
    - 读取 `prompts/prd.md` 模板
    - 替换模板中的 `{project_name}` 占位符
    - 调用 CopilotRuntime.ask(prdPrompt)
    - 将结果写入 `output/PRD.md`
    - 同时输出到终端（流式）
  - 如果 prompts/prd.md 不存在，报友好错误

  **Must NOT do**:
  - 不要修改 prompts/prd.md 的内容
  - 不要添加 "save to file" 以外的额外功能

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T12, T14, T15)
  - **Blocks**: T17, T18
  - **Blocked By**: T11

  **References**:
  - `resource/agent/teams-copilot-cli/cli/prd.ts` — v1 的 PRD 命令实现
  - `resource/agent/teams-copilot-cli/prompts/prd.md` — PRD 模板（不要修改）

  **Acceptance Criteria**:
  - [ ] `teams-copilot prd myproject` 生成 output/PRD.md
  - [ ] output/PRD.md 包含有效的 PRD 内容
  - [ ] prompts/prd.md 缺失时报错

  **QA Scenarios**:
  ```
  Scenario: PRD 生成成功
    Tool: Bash
    Preconditions: 浏览器已登录
    Steps:
      1. cd resource/agent/teams-copilot-cli
      2. node dist/cli/index.js prd test-app
      3. cat output/PRD.md
    Expected Result: output/PRD.md 存在且包含产品需求文档内容
    Evidence: .sisyphus/evidence/task-13-prd-output.md
  ```

  **Commit**: YES (groups with Wave 3)

---

- [x] 14. **arch 命令**

  **What to do**:
  - 创建 `src/cli/arch.ts`
  - 实现架构设计命令：
    - 检查 `output/PRD.md` 存在（不存在则报错，提示先运行 prd 命令）
    - 读取 `prompts/arch.md` + `output/PRD.md`
    - 替换模板中的 `{project_name}` 和 `{prd_content}`
    - 调用 CopilotRuntime.ask(archPrompt)
    - 结果写入 `output/ARCH.md` + 流式输出到终端

  **Must NOT do**:
  - 不要在 PRD 缺失时自动运行 prd 命令——只报错提示

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T12, T13, T15)
  - **Blocks**: T15, T17, T18
  - **Blocked By**: T11

  **References**:
  - `resource/agent/teams-copilot-cli/cli/arch.ts` — v1 的 arch 命令实现
  - `resource/agent/teams-copilot-cli/prompts/arch.md` — 架构模板（不要修改）

  **Acceptance Criteria**:
  - [ ] `teams-copilot arch myproject` 读取 PRD → 生成 ARCH.md
  - [ ] output/PRD.md 缺失时友好报错
  - [ ] output/ARCH.md 包含有效的架构设计内容

  **QA Scenarios**:
  ```
  Scenario: ARCH 生成成功
    Tool: Bash
    Preconditions: output/PRD.md 存在
    Steps:
      1. node dist/cli/index.js arch test-app
      2. cat output/ARCH.md
    Expected Result: output/ARCH.md 存在且包含架构内容
    Evidence: .sisyphus/evidence/task-14-arch-output.md

  Scenario: PRD 缺失报错
    Tool: Bash
    Preconditions: output/PRD.md 不存在
    Steps:
      1. rm -f output/PRD.md
      2. node dist/cli/index.js arch test-app
    Expected Result: exit code != 0, 提示"请先运行 prd 命令"
    Evidence: .sisyphus/evidence/task-14-arch-missing-prd.txt
  ```

  **Commit**: YES (groups with Wave 3)

---

- [x] 15. **tasks 命令**

  **What to do**:
  - 创建 `src/cli/tasks.ts`
  - 实现任务拆解命令：
    - 检查 `output/PRD.md` + `output/ARCH.md` 存在
    - 读取 `prompts/tasks.md` + PRD + ARCH
    - 替换 `{project_name}`, `{prd_content}`, `{arch_content}`
    - 调用 CopilotRuntime.ask(tasksPrompt)
    - 结果写入 `output/TASKS.md`

  **Must NOT do**:
  - 不要在 PRD/ARCH 缺失时自动运行——只报错

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T12, T13, T14)
  - **Blocks**: T17, T18
  - **Blocked By**: T11, T14 (需要 ARCH.md)

  **References**:
  - `resource/agent/teams-copilot-cli/cli/tasks.ts` — v1 的 tasks 命令实现
  - `resource/agent/teams-copilot-cli/prompts/tasks.md` — 任务模板（不要修改）

  **Acceptance Criteria**:
  - [ ] `teams-copilot tasks myproject` 生成 output/TASKS.md
  - [ ] PRD 或 ARCH 缺失时友好报错
  - [ ] output/TASKS.md 包含有效的任务拆解

  **QA Scenarios**:
  ```
  Scenario: TASKS 生成成功
    Tool: Bash
    Preconditions: output/PRD.md + output/ARCH.md 存在
    Steps:
      1. node dist/cli/index.js tasks test-app
      2. cat output/TASKS.md
    Expected Result: output/TASKS.md 存在且包含任务拆解
    Evidence: .sisyphus/evidence/task-15-tasks-output.md
  ```

  **Commit**: YES (groups with Wave 3)

---

### Wave 4 — REPL（1 个任务）

- [x] 16. **REPL 模块**

  **What to do**:
  - 创建 `src/cli/repl.ts`
  - 用 Node.js `readline` 实现交互循环：
    - 启动时显示 banner：`Teams Copilot CLI v2 — Type /help for commands, /exit to quit`
    - Prompt 显示 `copilot> `
    - 支持特殊命令：`/exit` (退出), `/help` (帮助), `/stream on|off` (切换流式), `/clear` (清屏)
    - 普通输入 → CopilotRuntime.ask(input)
    - 流式模式：实时输出到终端
    - 非流式模式：显示 "Thinking..." spinner 等待结果
  - REPL 多轮对话：同一 CopilotRuntime 实例内连续 ask()，保持 iframe 上下文
  - 实现 `extractLatestResponse(frame: Frame, roundIndex: number)` — 只提取最新一轮的响应
  - 优雅退出：`/exit` 或 Ctrl+C → CopilotRuntime.close() → process.exit(0)

  **Must NOT do**:
  - 不要使用 inquirer, enquirer, prompts 等第三方 REPL 库（用标准 readline）
  - 不要保存对话历史到文件
  - 不要实现 `/save` 或 `/load` 命令

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO（依赖 T11 和 T8 的多轮支持）
  - **Parallel Group**: Wave 4 (单独)
  - **Blocks**: T18
  - **Blocked By**: T11, T8

  **References**:
  - Node.js readline docs: `https://nodejs.org/api/readline.html`
  - `resource/agent/teams-copilot-cli/runtime/opencli-adapter.ts:pollCopilotResponse()` — v1 的"只提取最新 Copilot said:" 逻辑
  - `resource/agent/teams-copilot-cli/runtime/copilot-runtime.ts:ask()` — v1 的截断续写逻辑（REPL 中仍需）

  **Acceptance Criteria**:
  - [ ] `teams-copilot repl` 进入交互模式，显示 banner
  - [ ] 输入文本 → 流式输出 Copilot 响应
  - [ ] `/stream off` → 切换为非流式模式（显示 "Thinking..."）
  - [ ] `/exit` 或 Ctrl+C → 优雅退出
  - [ ] 多轮对话：第 2 轮能引用第 1 轮的上下文
  - [ ] 截断自动续写在 REPL 中正常触发

  **QA Scenarios**:
  ```
  Scenario: REPL 启动
    Tool: Bash (需要 tmux 或 expect 发送多行输入)
    Preconditions: 浏览器已登录
    Steps:
      1. cd resource/agent/teams-copilot-cli
      2. echo "say hello" | node dist/cli/index.js repl
    Expected Result: 输出包含 "Teams Copilot CLI v2" banner + Copilot 响应
    Evidence: .sisyphus/evidence/task-16-repl-start.txt

  Scenario: REPL 多轮对话
    Tool: Bash
    Steps:
      1. printf "my name is Alice\\nwhat is my name\\n/exit\\n" | node dist/cli/index.js repl
    Expected Result: 第二轮响应包含 "Alice"
    Evidence: .sisyphus/evidence/task-16-repl-multiturn.txt

  Scenario: REPL 切换流式模式
    Tool: Bash
    Steps:
      1. printf "/stream off\\nsay hello\\n/exit\\n" | node dist/cli/index.js repl
    Expected Result: 输出 "Thinking..." 然后完整结果
    Evidence: .sisyphus/evidence/task-16-repl-nostream.txt
  ```

  **Commit**: YES
  - Message: `feat(cli): REPL interactive mode`

---

### Wave 5 — 测试补齐（2 个任务，并行）

- [ ] 17. **单元测试补齐**

  **What to do**:
  - 补全和修复所有单元测试：
    - `test/sanitizer.test.ts` — T10 创建的测试，确认覆盖所有清洗场景
    - `test/config.test.ts` — T3 创建的测试，确认覆盖加载/合并/验证
    - `test/browser-finder.test.ts` — T4 创建的测试，确认 mock 测试通过
    - `test/session-manager.test.ts` — T9 创建的测试，确认 mock 测试通过
  - 确保 `npm test` 运行所有测试并全部通过
  - 修复任何因模块间集成引起的不一致

  **Must NOT do**:
  - 不要创建新的测试框架或测试工具
  - 不要修改已有功能的签名来适配测试

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 5 (with T18)
  - **Blocks**: F1-F4
  - **Blocked By**: T12-T16 (所有实现完成后才能最终跑全量测试)

  **References**:
  - `test/` 目录下各测试文件

  **Acceptance Criteria**:
  - [ ] `npm test` 所有测试通过（0 failures）
  - [ ] 测试覆盖率 > 80%（纯逻辑模块）

  **QA Scenarios**:
  ```
  Scenario: 全量测试通过
    Tool: Bash
    Preconditions: 所有实现任务完成
    Steps:
      1. cd resource/agent/teams-copilot-cli && npm test
    Expected Result: 所有测试 PASS，exit code 0
    Evidence: .sisyphus/evidence/task-17-tests.txt
  ```

  **Commit**: YES (groups with Wave 5)

---

- [ ] 18. **E2E Smoke Test + README 文档**

  **What to do**:
  - 创建 `test/e2e/smoke.test.ts`（Playwright Test）：
    - 启动浏览器 → 检查 CDP 端点可访问
    - 验证 `launchBrowser()` + `connectToBrowser()` 能正常工作
    - 验证 `findChromiumBrowser()` 能找到浏览器
    - 不包含实际 Copilot 交互（依赖登录态，不适合 CI）
  - 创建 `README.md`：
    - 项目简介和核心功能
    - 安装指南：`npm install -g teams-copilot-cli`（或 npx 使用）
    - 前置条件：Chromium 浏览器 + M365 Teams Copilot 许可
    - 使用指南：ask/prd/arch/tasks/repl 命令示例
    - 配置说明：config.yaml 字段说明
    - **ToS 免责声明**：明确标注本工具仅用于教育和研究目的，自动操作 Microsoft Teams 可能违反服务条款
    - 贡献指南和 License

  **Must NOT do**:
  - 不要在 E2E 测试中包含 Copilot 交互（会消耗额度且不稳定）
  - 不要在 README 中承诺 "生产可用" 或 "官方支持"

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 5 (with T17)
  - **Blocks**: F1-F4
  - **Blocked By**: T1-T16

  **References**:
  - Playwright Test docs: `https://playwright.dev/docs/test-configuration`
  - `resource/agent/teams-copilot-cli/OPERATION.md` — v1 的文档参考

  **Acceptance Criteria**:
  - [ ] E2E smoke test 通过
  - [ ] README.md 完整（安装、使用、配置、免责声明）
  - [ ] README 的 ToS 免责声明清晰醒目

  **QA Scenarios**:
  ```
  Scenario: E2E smoke test
    Tool: Bash
    Preconditions: Chromium 浏览器已安装
    Steps:
      1. cd resource/agent/teams-copilot-cli && npx playwright test test/e2e/
    Expected Result: 所有 smoke test 通过
    Evidence: .sisyphus/evidence/task-18-e2e.txt

  Scenario: README 完整性检查
    Tool: Bash
    Steps:
      1. wc -l README.md
      2. grep -i "disclaimer\|免责\|教育" README.md
    Expected Result: README > 50 行，包含免责声明
    Evidence: .sisyphus/evidence/task-18-readme.txt
  ```

  **Commit**: YES (groups with Wave 5)

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Verify all "Must Have" present, all "Must NOT Have" absent. Check evidence files in `.sisyphus/evidence/`.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `tsc --noEmit` + linter + `npm test`. Review for AI slop patterns, dead code, over-abstraction.
  Output: `Build [PASS/FAIL] | Lint [PASS/FAIL] | Tests [N pass/N fail] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Execute EVERY QA scenario from ALL tasks. Test cross-task integration. Test edge cases.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  Verify 1:1 spec-to-implementation mapping. Check for scope creep. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | VERDICT`

---

## Commit Strategy

- **Wave 1**: `feat(scaffold): project setup with playwright, types, config, browser modules`
- **Wave 2**: `feat(runtime): teams page, text injection, stream extraction, sanitizer`
- **Wave 3**: `feat(cli): ask, prd, arch, tasks commands`
- **Wave 4**: `feat(cli): REPL interactive mode`
- **Wave 5**: `test: unit tests and e2e smoke tests`
- **FINAL**: `docs: README with install guide and ToS disclaimer`

---

## Success Criteria

### Verification Commands
```bash
npx teams-copilot ask "say hello in Chinese"    # Expected: 流式输出中文问候
npx teams-copilot ask --no-stream "what is 1+1"  # Expected: 完整输出 "2"
npx teams-copilot prd test-project                # Expected: output/PRD.md 生成
npx teams-copilot arch test-project               # Expected: output/ARCH.md 生成
npx teams-copilot tasks test-project              # Expected: output/TASKS.md 生成
npm test                                          # Expected: 全部通过
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass
- [ ] README 含 ToS 免责声明
- [ ] `@jackwener/opencli` 从 package.json 中移除
