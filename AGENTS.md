# Repository instructions

- 每当一个缺陷完成修复并经过验证后，必须在 `bughistory.md` 末尾追加一条精简记录，再结束任务。
- 每条记录必须包含：ISO 8601 带时区时间戳、问题现象、根因、解决方案、版本信息和验证结果。
- 只记录已经复现或有代码证据、并且已经解决的问题；不要把未经验证的猜测写入历史。
- 新记录不得改写或删除旧记录，除非用户明确要求更正历史。

## Agent 语义层速览（改动 Agent 相关代码前必读，详见 AGENT.md）

- 架构是**覆盖模型**：规则（analyzer.py）永远先跑出完整底账；Agent 结果只写派生表（classification_evidence / agent_memory / daily_summaries），读取层合并时覆盖语义字段，无结果即回退规则值。**不要**改成"先调 Agent、失败再走规则"的同步阻塞式。
- 隐私红线：发给 LLM 的只有脱敏特征（进程名 + sanitize_title 摘要 + 交互频率），**严禁**把 window_title 原文放进 prompt；Agent 对 activity_segments / feature_windows 只读；时间/时长等硬数据不受 Agent 影响；人工修正（manual_override）永远优先于 Agent 结果。
- 测试铁律（踩过坑）：tests/test_agent.py 的 autouse fixture 清除 ACTIVITYWATCH_AGENT_* 环境变量，勿删；注入假 LLM 必须走 create_app(agent_llm=...) 参数，事后替换 app.state.agent 无效；Pydantic 请求模型必须定义在模块级；断言 enrich 用 evidence_count 等最终效果，不用 new 计数（有后台竞态）。
- 配置：ACTIVITYWATCH_AGENT_BASE_URL / API_KEY / MODEL（OpenAI 兼容）。本地在 backend/.env，生产在服务器 /etc/activity-timeline.env（不随部署上传）。什么都不配 = 功能整体关闭，接口行为与无 Agent 完全一致。

