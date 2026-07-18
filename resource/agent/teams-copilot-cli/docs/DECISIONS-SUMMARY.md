# grill 会话决策总览（2026-07-18）

对象：teams-copilot-cli v3 P0 设计（DESIGN-P0.md）× 专家评审（REVIEW-DESIGN-P0.md）。
10 个问题全部达成共识；DESIGN-P0.md 已更新至 v3.1。

| # | 议题 | 定案 | ADR |
|---|---|---|---|
| Q1 | 默认权限模式 | allowlist 默认，YOLO 显式 opt-in + 风险告示 | 0001 |
| Q2 | 协议信道格式 | 默认代码围栏包裹 + 真机冒烟前置到步骤 2.5 | 0002 |
| Q3 | edit_file 匹配 | 归一化级联 + 失败回带磁盘片段（P0） | 0003 |
| Q4 | 上下文预算 | 字符预算账本(40k) + 协议遗忘检测 + 动态收缩回灌 | 0004 |
| Q5 | implement 失败语义 | 默认停止 + 脏树拒启 + 逐文件 add，失败不 stash | 0005 |
| Q6 | 数据外发/secrets | 小而准 redact 模式集 + 打码继续 + 默认拒读清单 + 首次告示 | 0006 |
| Q7 | 账号风控 | 默认 ≥3000ms+抖动 + 披露 + 轮次预算兼作账号保护 | 0007 |
| Q8 | 防御工程包 | PSParser 解析、RESULT 转义、超时/上限/交互检测、锁文件、UTF-8/exitCode、turn 回声 | 0008 |
| Q9 | Medium 分档 | grep 上限、JSONL 审计、TASKS 容错进 P0；其余 P0.5 | —（并入各节） |
| Q10 | 工期与顺序 | 9–12 天；冒烟前置；验收清单 9 条 + ADR 全落地 | 0009 |

## 产出物（均在远端 workspace）

- `resource/agent/teams-copilot-cli/docs/DESIGN-P0.md` — v3.1，融入全部定案
- `resource/agent/teams-copilot-cli/docs/adr/0001`–`0009` — 9 份 ADR
- `resource/agent/teams-copilot-cli/CONTEXT.md` — 术语表

## 遗留到 P0.5 的项

校验器表驱动全分支单测、Ctrl+C 竞态定义、repo map 目录优先加权、MockProvider 录制模式。
