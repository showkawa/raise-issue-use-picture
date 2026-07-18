# 06 — 信道可靠性专项

**What to build:** 长任务在 copilot-web 信道上稳定运转：超长消息自动分片、续写拼接不重不漏、发送节奏防风控、RESULT 回灌随剩余预算收缩。

**Blocked by:** 05 — AgentLoop + `code` 命令。

**Status:** ready-for-agent

- [ ] 超 maxMessageChars 自动分片（首片声明"共 n 片，收齐前只回 OK"）
- [ ] 续写拼接改重叠检测（前文尾部 200 字符在续文头部找最长重叠）+ 单测
- [ ] minSendIntervalMs 默认 3000ms + 随机抖动；错误指数退避重试 2 次（ADR-0007）
- [ ] 轮次预算（maxTurnsPerConversation）逼近时 rotate，默认值兼作账号保护
- [ ] RESULT 截断激进化：测试输出留失败摘要 + 尾部 N 行；回灌预算按剩余会话预算动态收缩（ADR-0004）
