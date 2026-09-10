# Agent 语义增强层说明（v0.4.0）

> **阅读策略**：硬规则已浓缩在 `AGENTS.md`（每次会话自动注入，无需读本文）。只有当你实际要改 Agent 相关代码时，再按需查阅本文对应章节，不必通读。

## 一句话架构

**覆盖模型**：规则引擎（`analyzer.py`）永远先跑出完整、确定性的底账；Agent 异步在旁边增强，结果单独存表；读取接口在返回前合并——**有 Agent 结果就覆盖语义字段，没有就用规则值**。用户感知是"Agent 优先，Agent 异常时自动退回固定实现"，但实现上规则值永远已经在库，降级零成本。

```
采集器 (30s/次)
   │ POST /api/v1/events/batch
   ▼
analyzer.rebuild_day()  ──规则底账──▶ activity_segments（永不被 Agent 覆盖）
   │                                     │
   │ 后台异步（不阻塞写入）               │ 读取时
   ▼                                     ▼
AgentService.enrich_day()           apply_evidence() 合并视图
   │ 挑出 category=其他 且未人工修正        ├─ manual_override  >  Agent  >  规则
   │ 按 digest 去重、脱敏、调 LLM           ├─ 只覆盖 behavior/purpose/category/topic/description
   ▼                                     └─ 硬数据（时间/时长/交互计数）永不改
classification_evidence（含置信度、解释、可撤销标记）

DailySummarizer ──▶ daily_summaries（按数据版本缓存，请求永不等待 LLM）
agent_memory    ──▶ 长期记忆（注入两个 Agent 的 prompt）
```

## 核心模块

| 文件 | 职责 |
|---|---|
| `backend/app/agent.py` | **Agent ①**：单片段状态判定。`AgentService` 类：候选挑选、脱敏（`sanitize_title`）、digest 缓存、LLM 调用（OpenAI 兼容）、evidence 落库、高置信自动沉淀记忆、stale 节流清扫、分类时注入应用记忆 + 匹配的 project_fact、读取层覆盖（`apply_evidence`） |
| `backend/app/summarizer.py` | **Agent ②**：日报叙述生成。`DailySummarizer` 类：按 `(day, 数据版本)` 缓存、后台线程重生成、prompt 注入长期记忆 + 近 7 天分类时长趋势 |
| `backend/app/database.py` | 三张派生表的建表与读写方法（见下） |
| `backend/app/debuglog.py` | 开发者视图日志环（见下） |
| `backend/app/main.py` | 接线：`ingest_batch` 投递后台 enrich；`timeline/summary/insights/status/daily` 读取层先过 `apply_evidence`；`combined_segments` 在合并**前**应用覆盖（这样跨设备时间线继承 Agent 语义）；Agent 管理端点；开发者视图端点与 `/devlog` 页面 |
| `backend/app/schemas.py` | `SegmentCorrection`（含 `remember`/`memory_note`）、`AgentEnrichRequest`、`MemoryAddRequest` |
| `tests/test_agent.py` | Agent 全量测试（25 个），含注入 FakeLLM 的 fixture 与环境隔离 fixture |

## 数据表（均只增不覆盖原始数据）

### classification_evidence —— Agent ① 判断缓存

| 字段 | 说明 |
|---|---|
| `digest` | **内容寻址主键**：sha256(平台 + 进程 + 脱敏标题摘要)。同一应用/标题只调一次模型；采集器每 30 秒重建片段不产生重复调用 |
| `behavior/purpose/category/topic/description` | LLM 判断结果 |
| `confidence` / `explanation` | 置信度与一句话解释 |
| `input_json` | 完整输入快照（可追溯当时发了什么） |
| `revoked` | 撤销标记；撤销后读取层立即回退规则值 |
| `hit_count` | 出现次数；≥5 且置信度 ≥0.75 时自动沉淀为 `agent_memory`（每天每条只计一次） |

### agent_memory —— 长期记忆

| 字段 | 说明 |
|---|---|
| `kind` | `app_fact`（应用事实）/ `project_fact`（项目背景）/ `correction`（纠正记忆） |
| `scope` | 匹配键：进程名或主题词。写入规范化为小写+去路径；检索双向兼容 `.exe` 形态（存 `code.exe` 查 `code`、存 `code` 查 `code.exe` 均命中；Android 包名不按 `.` 切分），**无向量库** |
| `source` | `manual`（用户告知）/ `correction`（修正归纳）/ `auto`（自动沉淀） |
| `status` | `active` / `superseded`（冲突归档，可追溯）/ `stale`（长期未命中自动降级，见下） |
| `hit_count` / `last_seen_at` | 命中统计；被注入 prompt 时 touch，也是 stale 判定依据 |

**记忆生命周期（stale）**：`app_fact` 45 天、`project_fact` 30 天未命中（`last_seen_at`，从未命中则看 `created_at`）自动降级为 `stale`，不再注入 prompt 但保留可查。`correction`（用户亲口纠正）与 `pattern`（用户确认的线下习惯）**豁免**，永不自动降级。降级单向，由 AgentService 每次 enrich 时节流触发（6 小时一次），失败不影响主流程。

**三条写入路径**：
1. 修正时记住：`PATCH /api/v1/segments/{id}` 带 `remember: true`（可选 `memory_note`），系统归纳出该应用的泛化记忆；同一应用再次纠正为不同分类时旧记忆自动 superseded
2. 手动告知：`POST /api/v1/agent/memory`
3. 自动沉淀：Agent ① 高置信判断对同一 digest 累计 ≥5 次

线下活动通过 `POST /api/v1/offline-activities` 按起止时间人工确认，可选 `remember`。
记住后生成 `scope=offline, kind=pattern` 的工作日/周末时段习惯；它只允许 Agent
推测睡眠、运动、出游、用餐、通勤、休息或家务，并在结果中明确标记 `inferred=true`。
没有人工习惯、习惯冲突或判断被撤销时，空白仍保持“无设备记录/空闲”。

### daily_summaries —— 日报缓存

`(day PRIMARY KEY, version, narrative, model, created_at)`。version 是当天语义片段、有效记忆、模型和提示词的内容摘要；这些内容没变就直接命中缓存，任一内容变化时后台线程重生成，**请求本身永不等待 LLM**。

## API 端点

### 读取接口（Agent 结果自动合并，无结果即规则值）

`GET /api/v1/timeline/today`、`/timeline/combined`、`/summary/today`、`/insights/today`、`/daily/report`、`/status/current`——返回的 segment 多一个 `classification` 字段：`{"source": "agent"|"manual", "confidence", "explanation", "topic"}`，无覆盖时为 `null`。`/daily/report` 额外有 `narrative`（Agent ② 叙述，失败为 null）和 `memories`（透明展示当前 active 记忆）。

### Agent 管理端点（均需 Bearer 认证）

| 端点 | 说明 |
|---|---|
| `GET /api/v1/agent/status` | enabled / model / evidence_count，**验证 Agent 是否跑起来的入口** |
| `POST /api/v1/agent/enrich` | body `{"day": "YYYY-MM-DD"}`，同步触发某天增强（调试用；常规由 ingest 自动触发） |
| `POST /api/v1/agent/evidence/{digest}/revoke` | 撤销一条判断，立即回退规则值 |
| `POST /api/v1/agent/summary/{day}` | 手动重生成某天日报 |
| `GET /api/v1/agent/memory` | 列出记忆（默认 active，`?all=1` 含归档） |
| `POST /api/v1/agent/memory` | 手动添加：`{"kind", "scope", "content", "category", "confidence"}` |
| `DELETE /api/v1/agent/memory/{id}` | 单条删除 |

## 开发者视图（调试日志环）

网页 `/devlog`（顶栏「开发」入口）按类别筛选查看五类日志：**API 请求**（method/path/status/耗时）、**采集上传**（设备、接受/重复、重建片段数）、**记忆注入**（哪个进程注入几条记忆 + 哪些 project_fact）、**Agent 输入**（完整 prompt 原文，含 known_facts）、**Agent 输出**（模型原始回复与耗时）。支持搜索、3 秒自动刷新、点击展开全文、一键清空。

- **保留方式**：内存环形缓冲最近 200 条，重启即清空，不落盘；文本字段截断 2 万字符
- **开关**：`ACTIVITYWATCH_DEBUG_VIEW`——开发环境默认开，生产默认关，`=1`/`=0` 显式覆盖。关闭时记录点全部 no-op（零开销），`GET /api/v1/debug/logs` 返回 `{"enabled": false}`
- **端点**：`GET /api/v1/debug/logs?kind=&after_id=&limit=`（需 Bearer 认证）、`DELETE /api/v1/debug/logs`；端点自身不进日志环（防轮询刷屏）
- **隐私边界**：记录的 prompt 本就是脱敏特征；HTTP 条目不记 query string（防 token 泄露）
- 记录点：`main.py` 中间件（http）、`ingest_batch`（ingest）、`agent.py` 的 `invoke_llm`（agent_input/agent_output，Agent ①② 共用此入口）与 `_classify_chunk`（agent_inject，同时打一行 `[agent.memory]` INFO 日志，生产不开调试视图也能在 journalctl 看到）

## 配置与部署

### 环境变量（OpenAI 兼容 /chat/completions）

```bash
ACTIVITYWATCH_AGENT_BASE_URL   # 如 https://api.deepseek.com/v1
ACTIVITYWATCH_AGENT_API_KEY    # 三项齐全才启用
ACTIVITYWATCH_AGENT_MODEL      # 如 deepseek-chat / qwen-plus / gpt-4o-mini
ACTIVITYWATCH_AGENT_ENABLED=0  # 可选，强制关闭
ACTIVITYWATCH_AGENT_LOG_PAYLOADS=0  # 可选；开发默认开，生产默认关
ACTIVITYWATCH_DEBUG_VIEW=1    # 可选；开发者视图日志环，开发默认开，生产默认关
```

**什么都不配 = 完全关闭，所有接口行为与无 Agent 版本一致**（有测试专门验证这一点）。

### 两处配置位置

- **本地**：`backend/.env`（被 gitignore，`main.py` 启动时 `load_dotenv` 加载）
- **生产（阿里云）**：`/etc/activity-timeline.env`（root:600）。`.env` **不会随部署上传**——`scripts/deploy-aliyun.sh` 只推代码；服务器上改 Agent 配置需手动编辑该文件后 `systemctl restart activity-timeline`
- 生产访问令牌在 `.deployment/aliyun-access.env`（与本地 backend/.env 的 token 不同）

### 验证 Agent 生效

```bash
curl -H "Authorization: Bearer $TOKEN" https://47.82.104.59/api/v1/agent/status
# {"enabled":true,"model":"deepseek-chat","evidence_count":N}  ← N>0 说明真实数据已被 enrich
```

## 常量（agent.py 顶部，调参入口）

| 常量 | 默认 | 含义 |
|---|---|---|
| `CONFIDENCE_THRESHOLD` | 0.55 | 低于此置信度的判断不覆盖规则值 |
| `MAX_DIGESTS_PER_CALL` | 20 | 每次 LLM 调用最多判多少条 |
| `LLM_TIMEOUT_SECONDS` | 45 | 超时即丢弃本批，回退规则 |
| `LLM_CIRCUIT_BASE_SECONDS` | 60 | 临时失败后的首次暂停时间，连续失败指数增长 |
| `LLM_CIRCUIT_MAX_SECONDS` | 3600 | 失败退避上限；401/402/403 直接暂停一小时 |
| `INVALID_RESULT_RETRY_SECONDS` | 3600 | 模型遗漏条目或输出未通过校验时，该 digest 的重试间隔 |
| `AUTO_PROMOTE_HITS` | 5 | 自动沉淀记忆的命中门槛 |
| `AUTO_PROMOTE_CONFIDENCE` | 0.75 | 自动沉淀的置信度门槛 |
| `TITLE_MAX_CHARS` | 80 | 标题脱敏截断长度 |
| `STALE_AFTER_DAYS` | app_fact 45 / project_fact 30 | 未命中多少天自动降级为 stale（correction/pattern 豁免） |
| `STALE_SWEEP_INTERVAL_SECONDS` | 21600 | stale 清扫节流间隔（挂在 enrich 入口） |
| `PROJECT_FACTS_PER_ITEM` | 3 | 分类时每个片段最多注入的 project_fact 条数（按标题摘要子串匹配） |

## 隐私红线（改动前必读）

1. **发给 LLM 的只有脱敏特征**：进程名、标题摘要（`sanitize_title` 截断 + URL/邮箱替换）、分钟级交互频率。**永远不要**把 `window_title` 原文或键鼠内容放进 prompt。
2. **Agent 只写派生表**，对 `activity_segments` / `feature_windows` 只读。原始数据永远可从零重建一切派生物。
3. **硬数据不受 Agent 影响**：时间、时长、交互计数永远来自采集层；Agent 只解释（语义字段），不改动物理事实。
4. **人工修正永远最优先**：`manual_override` 的片段跳过 Agent 覆盖。

## 测试

```bash
# 测试 venv（已装全部依赖）
cd <repo>
PYTHONIOENCODING=utf-8 "C:\Users\aosika\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m pytest tests/ -q
```

关键 fixture（`tests/test_agent.py`）：
- **autouse 环境隔离**：清除 `ACTIVITYWATCH_AGENT_*` 四个环境变量——否则本地 `backend/.env` 填了真实 Key 会通过 `load_dotenv` 污染测试，"默认关闭"测试随开发机配置漂移（这是踩过的坑，见 bughistory.md）
- **FakeLLM 注入**：`create_app(agent_llm=..., summarizer_llm=...)`。**不能**事后替换 `app.state.agent`（端点闭包捕获 create_app 局部变量，替换无效——另一个踩过的坑）
- 测试取片段要按 `process` 过滤而非 `[0]`（`_with_no_device_periods` 会插入补齐行）
- 断言 enrich 结果用最终效果（evidence_count），不要用 `new` 计数（与后台异步 enrich 有竞态）

## 已知边界 / 待办

- **无标题事件共享 digest**：完全没有 `window_title` 的事件（Android 部分应用）同进程共用一个判断。改进方向：把域名/应用内路径加进 digest 特征
- **记忆检索是归一化精确匹配**：scope 已做进程名归一（小写/去路径/`.exe` 双向兼容），project_fact 按标题摘要子串参与分类；语义检索仍待条目过千再考虑
- Agent ② 日报的"跨周对比"只有近 7 天数据，更长的趋势需要历史聚合表

## 未来迁移到 Agent SDK 的两道接缝（已预留）

1. **LLM 调用走 OpenAI 兼容协议**（`agent.py` 的 `LLMClient = Callable[[str, str], str | None]`）——OpenAI Agents SDK / LangGraph / Claude Agent SDK 都支持该协议作底层，迁移时只换编排层
2. **数据全在 SQLite 表**——`classification_evidence` / `agent_memory` / `user_corrections` 是数据契约，任何 SDK 只是读写它们的另一个客户端。届时把 `database.py` 的查询函数套上 JSON Schema 即可作为工具暴露

**红线延伸**：无论将来用什么 SDK、多少工具，**工具只读，写操作永远走人工修正端点**——证据表、记忆表对 Agent 只能是只读的，防止 Agent 自己改写自己的判断依据。

## 新会话快速上手

新窗口开发 Agent 相关功能时，按顺序读：

1. 本文（架构与红线）
2. `backend/app/agent.py` 模块 docstring（设计原则）
3. `plan.md` Phase 6（原始设计决策：规则优先、LLM 只兜底）
4. `bughistory.md`（踩坑记录：Pydantic 模型必须模块级、闭包注入、环境隔离等）
5. 动手前先跑一遍全量测试确认基线是绿的
