# ADR-0001: 默认权限模式为 allowlist，YOLO 显式 opt-in

- 状态：已接受（2026-07-18）
- 关联：DESIGN-P0.md §6、REVIEW-DESIGN-P0.md B1

## 背景

teams-copilot-cli v3 的 agent 模式通过 Copilot 网页版（不可控信道、无原生 function calling）驱动工具执行。原设计默认 `yolo`，仅靠 denyCommands 子串黑名单兜底。评审指出：子串黑名单可被编码/拼接/落盘转执行等方式无穷绕过；且工具结果（stdout、文件内容）原文回灌给模型，存在提示注入 → 在 YOLO 下构成 RCE 级攻击面。

## 决策

1. 默认 `permissionMode: allowlist`；`yolo` 改为显式 opt-in（CLI `--yolo` 或 config 明示）。
2. 开启 YOLO 的首次运行打印风险告示。
3. denyCommands 保留，但定位降级为"最后一道提醒"，不作为安全边界。

## 理由

- 对标物 Claude Code 默认亦为逐项确认；"默认 YOLO"是降标而非对标。
- copilot-web 信道被注入的概率高于 API 通道，默认值应更保守。
- 想要 YOLO 的用户加 flag 成本极低；反向（默认 YOLO 出事故后收紧）是 breaking change 且信誉损失不可逆。

## 后果

- P0 需实现可用的 allowlist 交互确认流（否则默认体验不可用）。
- 文档/README 的卖点表述需从"开箱全自动"调整为"安全默认 + 可选全自动"。
