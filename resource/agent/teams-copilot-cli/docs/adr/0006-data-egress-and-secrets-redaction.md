# ADR-0006: 数据外发边界与 secrets 防泄漏

- 状态：已接受（2026-07-18）
- 关联：REVIEW-DESIGN-P0.md H1

## 背景

agent 模式把 repo 源码、命令输出、目录结构批量发送到 M365 Copilot 企业租户，具有 DLP/合规含义；用户在 agent 模式下对"发了什么"失去感知。

## 决策

P0 内实现：

1. **内置 redact**：`read_file`/RESULT 回灌、repo map、@file 注入统一过 redact。模式集小而准（宁漏报不误报）：私钥块头（`-----BEGIN ... PRIVATE KEY-----`）、AWS `AKIA...`、GitHub `ghp_`/`gho_`、通用 `*_KEY=`/`*_TOKEN=`/`*_SECRET=` 赋值行。
2. **命中处理**：打码后继续发送，用 `[REDACTED:<kind>]` 占位；不整块拒发（拒发会让模型对文件内容形成错误认知）。
3. **默认拒读清单**：`.env`、`*.pem`、`id_rsa` 等默认列入 read 拒绝清单，可配置放开。
4. **一次性告示**：`code`/`implement` 首次运行打印"本模式将向 Copilot 发送仓库文件内容与命令输出"。

## 理由

- 过激的 redact 会把正常代码改花，破坏 edit_file 的 ground truth；小模式集把误报率压到可接受。

## 后果

- redact 发生在外发边界（Provider 之前），对工具本地执行结果无影响。
- 需注意：被打码的内容若被模型用于 edit_file 的 old 会匹配失败——拒读清单是第一道防线，redact 只兜底。
