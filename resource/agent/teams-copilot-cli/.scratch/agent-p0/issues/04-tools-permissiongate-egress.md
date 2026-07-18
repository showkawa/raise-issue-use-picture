# 04 — 工具七件套 + PermissionGate + 数据边界

**What to build:** 七个本地工具可被安全调用：路径限定项目根、edit_file 具备归一化匹配级联、run_command 有完整防护；权限默认 allowlist，YOLO opt-in；所有外发内容过 redact，敏感文件默认拒读。

**Blocked by:** 01 — Provider 抽象与 runtime 迁移。

**Status:** ready-for-agent

- [ ] read_file/write_file/edit_file/run_command/grep/glob/git 七工具 + schema 校验，路径强制项目根内
- [ ] edit_file：精确 → 空白归一 → 逐行 trim 级联，命中按磁盘原文替换；归一后不唯一报错；全失败回带磁盘带行号片段（ADR-0003）
- [ ] run_command：PSParser token 级分类（不可解析按 destructive）、默认 timeout 120s、输出 64KB 上限、拒绝交互式命令、强制 UTF-8、$LASTEXITCODE 口径（ADR-0008）
- [ ] PermissionGate：allowlist 默认 / yolo opt-in（首次风险告示）/ ask；`--yolo`/`--ask` CLI 开关（ADR-0001）
- [ ] redact 模式集（私钥块/AKIA/ghp_/gho_/*_KEY= 等）打码 `[REDACTED:<kind>]` 后继续；`.env`/`*.pem`/`id_rsa` 默认拒读可配置（ADR-0006）
- [ ] grep 跳过 >1MB 文件、条数上限并标注截断
- [ ] 每个工具 + gate + redact 的单测（临时目录 fixture，沿用现有 vitest 模式）
