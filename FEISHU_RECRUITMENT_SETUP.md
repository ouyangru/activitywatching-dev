# 飞书招聘进度表接入

`/recruitment/progress` 将飞书多维表格作为招聘进度的主数据源。网页读取飞书记录；网页修改和邮件识别都只会先生成本地审核建议，只有用户点击“批准写入”后才会调用飞书更新记录接口。

## 1. 创建飞书自建应用

在飞书开放平台创建企业自建应用，并为应用开通：

- 多维表格记录与字段的读取权限；
- 多维表格记录的编辑权限；
- 如果使用 Wiki 链接解析 `app_token`，还需要读取知识库节点信息的权限。

同时确保该应用有权访问目标多维表格。具体权限名称以飞书开放平台当前控制台为准。

## 2. 服务器环境变量

生产环境请写入 `/etc/activity-timeline.env`，不要把 `FEISHU_APP_SECRET` 提交到 Git。

```bash
FEISHU_APP_ID=
FEISHU_APP_SECRET=

# 两种方式任选一种：
# A. 推荐：直接配置多维表格 app_token
FEISHU_RECRUITMENT_APP_TOKEN=
# B. 如果表格位于 Wiki 中，可配置 Wiki 节点 token，由后端解析 app_token
FEISHU_RECRUITMENT_WIKI_TOKEN=

FEISHU_RECRUITMENT_TABLE_ID=
FEISHU_RECRUITMENT_VIEW_ID=
FEISHU_RECRUITMENT_SOURCE_URL=
```

字段名默认按以下值匹配；如果你的表格列名不同，可以覆盖：

```bash
FEISHU_RECRUITMENT_COMPANY_FIELD=公司
FEISHU_RECRUITMENT_STAGE_FIELD=招聘进度
FEISHU_RECRUITMENT_LATEST_FIELD=最新动态
FEISHU_RECRUITMENT_NEXT_FIELD=下一节点
```

如果默认字段不存在，后端只会在常见别名中进行“唯一匹配”；无法唯一确定时不会写入，页面会显示“未匹配”。

## 3. 审核流程

### 邮件 -> 飞书

1. QQ 邮箱继续按现有流程解析为 `recruitment_items`。
2. 打开 `/recruitment/progress` 时，后端为尚未处理的邮件事项生成招聘阶段建议。
3. 后端按公司字段匹配飞书中的唯一记录。
4. 页面展示当前值和拟写入值的 diff。
5. 只有点击“批准写入”才会更新飞书；“驳回”不会修改飞书。

### 网页编辑 -> 飞书

1. 在招聘进度表中点击某一行的“修改”。
2. 修改支持的基础字段后点击“生成审核建议”。
3. 修改进入右侧审核队列，此时飞书仍未变化。
4. 检查 diff 后点击“批准写入”。

## 4. 当前支持的自动阶段识别

- 测评
- 笔试
- 一面 / 初面
- 二面 / 复试
- 三面
- HR 面
- Offer / 录用
- 流程结束 / 未通过
- 无法识别具体轮次时保留为“面试”

阶段识别只是“建议”，不会绕过审核直接写飞书。

## 5. 当前可从网页修改的字段类型

为了避免误写复杂字段，网页第一版只开放常见基础类型：文本、数字、单选、多选、日期时间、复选框。人员、附件、关联、公式等复杂字段只展示，不提供网页编辑。
