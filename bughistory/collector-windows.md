# Bug History · Windows 采集端

记录 Windows Collector、Windows 本地采集、窗口/键鼠/剪贴板、上传队列与本地状态相关的**已验证解决**问题。

> 模块化前的历史记录见 [`legacy-chronological.md`](legacy-chronological.md)。新条目只追加，不改写旧条目。

## 2026-09-23T18:44:19+08:00 · Token 轮换后 401 被毒丸免疫静默丢数据（28 小时）

- **问题现象：** 生产服务器 9-21 12:16 轮换 API token 后，Windows 采集器自 9-22 14:42:58 起上传/心跳全部返回 HTTP 401，`flush` 将其当作永久拒绝丢弃批次，28 小时约 5400 条事件数据被静默丢弃、不可恢复；网页因 Android 设备 token 已更新仍显示在线，掩盖断流。
- **根因：** v0.2.4 的毒丸免疫把整个 4xx 区间（除 408/429）都视为"重试永不可能成功"，但 401/403 是认证配置错误而非坏数据——token 修好后同一份数据完全可以正常上传，不应丢弃。
- **解决方案：** v0.2.5 在 `flush()` 中为 401/403 单独分支：保留队列并返回失败走退避重试（与网络失败同路径），日志写明 `auth; keeping N event(s) queued and backing off instead of dropping`；仅其余 4xx（422 校验、400 等）维持丢批解堵。同步修正现场：HKCU Run 启动项 token 更新、`.deployment/aliyun-access.env` 本地备份同步、schtasks 独立进程树重启。
- **版本/Commit：** activity_collector 0.2.4 → 0.2.5。
- **验证结果：** v0.2.5（pid=41476）启动后 heartbeat ok、upload ok、remaining=0；服务端 windows-AOSIKA is_online=true 且 last_seen 实时；后续若再现 401，日志将显示保留队列而非 dropping，窗口数据只延迟不丢失。
