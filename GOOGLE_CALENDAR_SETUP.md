# Google Calendar 同步配置

秋招日历本地功能不依赖 Google。只有“读取 Google 日程 / 同步到 Google / 修改或删除 Google 日程”需要完成下面的 OAuth 配置。

## 1. 先准备 HTTPS 域名

Google 的 Web OAuth 回调地址不能使用公网裸 IP。当前如果只通过 `https://47.82.104.59` 访问，需要先给 ActivityWatching 配一个你控制的 HTTPS 域名，例如：

```text
https://activity.example.com
```

回调地址固定为：

```text
https://activity.example.com/api/v1/recruitment/calendar/google/callback
```

本地开发使用 `localhost` 时不受公网 IP 规则限制。

## 2. Google Cloud 配置

1. 在 Google Cloud Console 创建或选择项目。
2. 启用 **Google Calendar API**。
3. 配置 OAuth consent screen。
4. 创建 **OAuth 2.0 Client ID**，类型选择 **Web application**。
5. 在 Authorized redirect URIs 中加入：

```text
https://activity.example.com/api/v1/recruitment/calendar/google/callback
```

应用只请求 `https://www.googleapis.com/auth/calendar.events`，用于读取、创建、修改和删除日历事件。

## 3. 服务器环境变量

编辑 `/etc/activity-timeline.env`：

```bash
GOOGLE_CALENDAR_CLIENT_ID=你的-client-id
GOOGLE_CALENDAR_CLIENT_SECRET=你的-client-secret
GOOGLE_CALENDAR_ID=primary
GOOGLE_CALENDAR_REDIRECT_URI=https://activity.example.com/api/v1/recruitment/calendar/google/callback
```

然后重启：

```bash
sudo systemctl restart activity-timeline
```

不要把 Client Secret 或 OAuth token 提交到 Git。

## 4. 页面连接

打开：

```text
/recruitment
```

进入“日历视图”，点击“连接 Google 日历”。授权完成后：

- 本地秋招事项可以单条同步或批量同步到 Google Calendar；
- 修改已经同步的本地事项会更新对应 Google event；
- 取消已经同步的本地事项会删除对应 Google event；
- Google Calendar 中其他事件会显示在自绘日历里，并可以直接修改或删除；
- 手动新增的日程可以选择“保存后同步到 Google Calendar”。

## 5. 数据边界

SQLite 仍然是秋招事项的主数据源。Google Calendar 是可选的外部日历源与提醒出口。

Google OAuth refresh token 保存在现有 ActivityWatching SQLite 数据库的 `recruitment_google_auth` 表中，因此生产数据库文件应继续保持仅服务账号可读。
