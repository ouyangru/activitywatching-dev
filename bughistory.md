# Bug History

`bughistory.md` 从 2026-09-12 起改为**模块化索引**。已解决 bug 不再继续堆在一个超长时间线里，而是按主要根因所属模块追加。

> 历史记录没有删除：旧的完整时间线原样保存在 [`bughistory/legacy-chronological.md`](bughistory/legacy-chronological.md)。

## 模块

| 模块 | 记录文件 | 范围 |
| --- | --- | --- |
| Windows 采集端 | [`bughistory/collector-windows.md`](bughistory/collector-windows.md) | Windows Collector、键鼠/窗口/剪贴板采集、上传队列、Windows 本地状态 |
| Android 采集端 | [`bughistory/collector-android.md`](bughistory/collector-android.md) | Android Collector、前台服务、权限、应用识别、上传 |
| 后端与数据 | [`bughistory/backend-data.md`](bughistory/backend-data.md) | FastAPI、SQLite、聚合、分析规则、API、数据一致性 |
| 前端与交互 | [`bughistory/frontend-ui.md`](bughistory/frontend-ui.md) | 总览、日报、对比、Hub、静态资源、缓存、浏览器交互 |
| Agent / AI | [`bughistory/agent-ai.md`](bughistory/agent-ai.md) | Agent 分类、记忆、LLM 调用、提示词、冷却、日报总结 |
| 秋招与外部集成 | [`bughistory/recruitment-integrations.md`](bughistory/recruitment-integrations.md) | QQ 邮件、招聘事项、Google Calendar、飞书及其他外部集成 |
| 发布与线上运行 | [`bughistory/deployment-release.md`](bughistory/deployment-release.md) | Aliyun、Nginx、systemd、一键发布、生产版本同步、HTTPS |
| 开发工具与脚本 | [`bughistory/tooling.md`](bughistory/tooling.md) | WSL、PowerShell、ADB、开发脚本、本地环境与辅助工具 |

跨模块 bug 只写到**主要根因**所在文件，并在条目中用 `关联模块` 指向其他模块，避免一条问题重复记录多次。

## 记录规则

只记录**已经复现或有代码证据，并且已经修复且完成验证**的问题。统一格式：

```md
### 2026-09-12T01:30:00+08:00 · 标题
- **问题现象：** ...
- **根因：** ...
- **解决方案：** ...
- **版本 / Commit：** ...
- **关联模块：** ...（可选）
- **验证结果：** ...
- **线上验证：** N/A / 已部署 `<sha>`，并通过浏览器或接口确认新行为
```

对于影响网页、生产 API、systemd 服务或部署配置的修复，**GitHub commit 存在不等于 bug 已解决**。进入 bug history 前必须至少确认：

1. 修复代码已经提交；
2. 生产环境实际运行的 commit / 构建版本与预期一致；
3. 浏览器页面或线上接口实际表现已经验证，不允许只凭本地测试或 `git log` 判定上线成功。

若尚未完成上述闭环，记录到 [`bugs/open/`](bugs/open/)；解决后再移动/整理到对应模块的 bug history。

## 历史归档

- [`bughistory/legacy-chronological.md`](bughistory/legacy-chronological.md)：模块化之前的全部历史，保持原文，不再追加。
