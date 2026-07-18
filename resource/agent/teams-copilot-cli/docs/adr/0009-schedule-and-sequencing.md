# ADR-0009: P0 工期修正与实施顺序调整

- 状态：已接受（2026-07-18）
- 关联：DESIGN-P0.md §12；REVIEW-DESIGN-P0.md 第五节

## 背景

原估算 4–5 天偏乐观约 2 倍：protocol 纠错闭环、edit_file 归一化级联、会话 rotate、Windows 编码细节均有"看似完成、真机全翻车"属性；且本轮评审收进的新增项（allowlist 交互流、预算账本、redact、防御工程包、审计日志）扩大了 P0 范围。原顺序把真机联调放在最后，等于把最大风险留到最晚发现。

## 决策

1. P0 按 **9–12 个工作日**规划。
2. 实施顺序调整为：
   - 步骤 1：runtime → provider/copilot-web 迁移 + Provider 接口 + MockProvider（独立发版，回归底线）
   - 步骤 2：protocol.ts + system-prompt.ts + 单测（含代码围栏包裹、ADR-0002）
   - **步骤 2.5（新增）：真机协议冒烟** —— 握手块往返保真校验，定案协议格式，标定 maxMessageChars / 会话预算 / 轮次上限基线
   - 步骤 3–6：tools + PermissionGate（allowlist 默认）→ AgentLoop + code 命令 → 可靠性专项 → implement 命令
   - 步骤 7：全量真机联调
3. P0 验收清单采用 REVIEW-DESIGN-P0.md 第六节 9 条，并要求 ADR-0001 至 ADR-0009 的决策全部落地。

## 后果

- 协议格式相关的返工风险从"步骤 7 才暴露"提前到步骤 2.5。
- 发版节奏不变：步骤 1 完成即可发纯重构版。
