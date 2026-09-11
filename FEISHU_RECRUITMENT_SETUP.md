# 飞书招聘进度表接入

`/recruitment/progress` 将飞书多维表格作为招聘进度的主数据源。网页直接读取同一张飞书表；网页修改和邮件识别都只会先生成本地审核建议，只有用户点击“批准写入”后才会调用飞书新增/更新记录接口。

## 1. 创建飞书自建应用

在飞书开放平台创建企业自建应用，并为应用开通：

- 多维表格记录与字段的读取权限；
- 多维表格记录的新增、编辑权限；
- 如果使用 Wiki 链接解析 `app_token`，还需要读取知识库节点信息的权限。

同时确保该应用有权访问目标多维表格。具体权限名称以飞书开放平台当前控制台为准。

## 2. 服务器环境变量

生产环境请写入 `/etc/activity-timeline.env`，不要把 `FEISHU_APP_SECRET` 提交到 Git。

```bash
FEISHU_APP_ID=
FEISHU_APP_SECRET=

# 两种方式任选一种：
# A. 直接配置多维表格 app_token
FEISHU_RECRUITMENT_APP_TOKEN=
# B. 表格位于 Wiki 中时，可只配置 Wiki 节点 token，由后端自动解析 app_token
FEISHU_RECRUITMENT_WIKI_TOKEN=

FEISHU_RECRUITMENT_TABLE_ID=
FEISHU_RECRUITMENT_VIEW_ID=
FEISHU_RECRUITMENT_SOURCE_URL=
```

当前招聘总表按以下真实列名匹配：

```bash
FEISHU_RECRUITMENT_COMPANY_FIELD=投递公司
FEISHU_RECRUITMENT_URL_FIELD=网申链接
FEISHU_RECRUITMENT_POSITION_FIELD=岗位
FEISHU_RECRUITMENT_TYPE_FIELD=类型
FEISHU_RECRUITMENT_LOCATION_FIELD=工作地点
FEISHU_RECRUITMENT_STAGE_FIELD=投递状态
FEISHU_RECRUITMENT_PRIORITY_FIELD=优先级
FEISHU_RECRUITMENT_APPLICATION_DATE_FIELD=投递日期
FEISHU_RECRUITMENT_ASSESSMENT_DATE_FIELD=测评日期
FEISHU_RECRUITMENT_WRITTEN_DATE_FIELD=笔试日期
FEISHU_RECRUITMENT_FIRST_INTERVIEW_DATE_FIELD=一面日期
FEISHU_RECRUITMENT_SECOND_INTERVIEW_DATE_FIELD=二面日期
FEISHU_RECRUITMENT_THIRD_INTERVIEW_DATE_FIELD=三面日期
FEISHU_RECRUITMENT_LATEST_FIELD=备注
```

生产环境如果仍保留旧字段名（例如 `公司`、`招聘进度`、`最新动态`），后端会先核对飞书现场 schema；旧字段不存在时会自动回退到上述真实列名，不需要为了这次升级先手工清理旧变量。

## 3. 审核流程

### 邮件 -> 飞书

1. QQ 邮箱继续解析为本地 `recruitment_items`，并尽量识别公司、网申链接、岗位、招聘类型、工作地点和时间。
2. 打开 `/recruitment/progress` 或点击“从邮件生成建议”时，邮件事项被转换为待审核字段；旧版尚未处理且 `fields_json` 为空的建议也会自动补齐。
3. 后端按“投递公司”匹配飞书记录：唯一匹配时准备更新；完全未匹配时准备新增；匹配到多条时暂停批准，要求用户选择目标记录或明确新建。
4. 用户可在审核弹窗中补充字段、修改公司并选择写入目标。保存审核内容本身不会写飞书。
5. 只有点击“批准写入”才会调用飞书 API。飞书返回成功后，本地建议才标记为“已写入”；飞书失败时建议保持待审核并记录错误。

### 网页编辑 -> 飞书

1. 在招聘总表中点击某一行的“修改”。
2. 修改支持的基础字段后点击“生成审核建议”。
3. 修改进入待审核区，此时飞书仍未变化。
4. 检查后点击“批准写入”，才更新原飞书记录。

## 4. 邮件字段映射

当前会把可可靠识别的信息映射到：

- 投递公司、网申链接；
- 岗位、类型（校招/实习/社招）、工作地点；
- 投递状态；
- 测评日期、笔试日期、一面日期、二面日期、三面日期；
- 备注。

“投递/申请成功”邮件可把邮件接收时间作为投递确认时间写入“投递日期”；其他阶段不会用邮件接收时间反推投递日期。优先级默认不自动猜测，留给审核时手工补充。

## 5. 当前支持的阶段识别

- 已投递（投递成功 / 申请成功 / 网申成功等确认邮件）；
- 测评；
- 笔试；
- 一面 / 初面；
- 二面 / 复试；
- 三面；
- 第 4～9 轮面试（状态保留轮次，但表格没有对应日期列时不强行写错日期）；
- HR 面；
- Offer / 录用；
- 流程结束 / 未通过；
- 无法识别具体轮次时保留为“面试”。

阶段识别只是审核建议，不会绕过审核直接写飞书。

## 6. 网页可编辑字段类型

网页开放常见基础类型：文本、数字、单选、多选、日期时间、复选框、手机号和超链接。人员、附件、关联、公式等复杂字段只展示，不提供网页编辑。
