# 08 — 全量真机联调与风险披露

**What to build:** agent 模式在真实 Copilot 上端到端跑通最小任务；所有实测配置默认值回填；用户在 README 与首次运行时能看到条款/风控/数据外发披露。

**Blocked by:** 03 — 真机协议冒烟；06 — 信道可靠性专项；07 — `implement` 命令。

**Status:** ready-for-agent

- [ ] 真机 E2E agent 冒烟（read_file + done）手动触发通过；现有 playwright ask 回归绿
- [ ] 实测值回填配置默认（消息上限/会话预算/轮次上限），删除占位注释
- [ ] README 与首次运行披露：M365 使用条款/账号风控风险、数据外发合规提示（ADR-0006/0007）
- [ ] §13 风险章节与验收清单核对：DESIGN-P0.md §12 九条全勾
