# 01 — Provider 抽象与 runtime 迁移

**What to build:** 现有 ask/prd/arch/tasks/repl 全部经由新的 Provider 抽象工作，行为与迁移前完全一致，可独立发版；同时测试可以用 MockProvider 回放脚本化对话驱动任何依赖 ChatSession 的代码。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] runtime 代码纯移动 + 薄封装为 copilot-web Provider，内部逻辑不改，现有单测同步移动且全绿
- [ ] Provider/ChatSession/ProviderCapabilities 接口落地（send/healthy/close/capabilities），config 增加 provider 键（默认 copilot-web）
- [ ] MockProvider 可按脚本回放多轮回复，供集成测试使用
- [ ] `npm run typecheck && npm test && npm run build` 全绿；现有命令手工冒烟行为不变
