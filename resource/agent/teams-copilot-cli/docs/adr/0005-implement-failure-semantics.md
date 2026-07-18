# ADR-0005: implement 失败语义与自动 commit 边界

- 状态：已接受（2026-07-18）
- 关联：DESIGN-P0.md §9；REVIEW-DESIGN-P0.md B4

## 背景

§9 只定义了 happy path。任务失败时的 checkbox/commit/继续策略、脏工作树下的自动 commit、`git add` 范围均未定义，其中"把用户未提交改动一起 commit"是必然发生的高愤怒事故。

## 决策

1. **失败默认停止**：某条任务 AgentLoop 迭代耗尽/中止时，checkbox 不勾、半成品不 commit、后续任务不继续，工作树留白由用户裁决；`--continue-on-failure` 显式开启跳过继续。
2. **失败不自动 stash**：半成品留在工作树，终端打印 `git diff` / 恢复提示（stash 对新手更难恢复）。
3. **脏工作树拒绝启动**：任务开始前工作树必须干净，否则拒启；`--allow-dirty` 显式放行。
4. **逐文件 `git add <path>`**：commit 范围限定为 agent 本任务触碰过的文件清单；禁止 `git add -A`。

## 后果

- AgentLoop 需要维护并暴露"本任务触碰文件清单"。
- implement 的集成测试需覆盖：失败停止、脏树拒启、逐文件 add 三条路径。
