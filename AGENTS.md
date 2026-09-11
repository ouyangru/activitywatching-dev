# Repository instructions

## Bug 记录规则

- `bughistory.md` 现在是模块索引；新的已解决 bug 必须追加到 `bughistory/` 下对应模块文件，不再继续追加到根目录长时间线。
- 模块选择按**主要根因**归类：
  - `bughistory/collector-windows.md`：Windows Collector、Windows 本地采集/上传。
  - `bughistory/collector-android.md`：Android Collector、权限、前台服务、Android 上传。
  - `bughistory/backend-data.md`：FastAPI、SQLite、分析规则、聚合、API 与数据一致性。
  - `bughistory/frontend-ui.md`：总览/日报/对比/Hub、静态资源、缓存、浏览器交互。
  - `bughistory/agent-ai.md`：Agent、LLM、记忆、提示词、日报总结、冷却/重试。
  - `bughistory/recruitment-integrations.md`：QQ 邮件、招聘事项、Google Calendar、飞书与外部集成。
  - `bughistory/deployment-release.md`：Aliyun、Nginx、systemd、一键发布、HTTPS、生产版本同步。
  - `bughistory/tooling.md`：WSL、PowerShell、ADB、开发脚本和本地工具环境。
- 跨模块 bug 只写主要根因模块，可增加“关联模块”，不要复制同一条记录到多个文件。
- 每当一个缺陷完成修复并经过验证后，必须在对应模块文件末尾追加一条精简记录，再结束任务。
- 每条记录必须包含：ISO 8601 带时区时间戳、问题现象、根因、解决方案、版本/Commit 和验证结果；影响线上行为时还必须包含“线上验证”。
- **Commit 已存在不代表线上已修复。** 凡影响网页、生产 API、systemd 服务或部署配置，只有在确认生产实际运行的 commit/构建版本与预期一致，并从浏览器或线上接口验证新行为后，才允许写成“已解决”。
- 只记录已经复现或有代码证据、并且已经解决的问题；未经验证、仍在排查或只完成代码提交的问题必须放在 `bugs/open/`，不能提前写进 bug history。
- `bughistory/legacy-chronological.md` 是模块化之前的只读历史归档，不再追加，也不要改写或删除旧记录，除非用户明确要求更正历史。

## Agent 语义层速览（改动 Agent 相关代码前必读，详见 AGENT.md）

- 架构是**覆盖模型**：规则（analyzer.py）永远先跑出完整底账；Agent 结果只写派生表（classification_evidence / agent_memory / daily_summaries），读取层合并时覆盖语义字段，无结果即回退规则值。**不要**改成"先调 Agent、失败再走规则"的同步阻塞式。
- 隐私红线：发给 LLM 的只有脱敏特征（进程名 + sanitize_title 摘要 + 交互频率），**严禁**把 window_title 原文放进 prompt；Agent 对 activity_segments / feature_windows 只读；时间/时长等硬数据不受 Agent 影响；人工修正（manual_override）永远优先于 Agent 结果。
- 测试铁律（踩过坑）：tests/test_agent.py 的 autouse fixture 清除 ACTIVITYWATCH_AGENT_* 环境变量，勿删；注入假 LLM 必须走 create_app(agent_llm=...) 参数，事后替换 app.state.agent 无效；Pydantic 请求模型必须定义在模块级；断言 enrich 用 evidence_count 等最终效果，不用 new 计数（有后台竞态）。
- 配置：ACTIVITYWATCH_AGENT_BASE_URL / API_KEY / MODEL（OpenAI 兼容）。本地在 backend/.env，生产在服务器 /etc/activity-timeline.env（不随部署上传）。什么都不配 = 功能整体关闭，接口行为与无 Agent 完全一致。
