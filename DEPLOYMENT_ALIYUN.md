# 阿里云部署交接

已部署地址：https://47.82.104.59/mobile 。Android 后端地址填写 https://47.82.104.59 ，不带 /mobile。

访问令牌保存在本机 `.deployment/aliyun-access.env` 的 `ACTIVITYWATCH_API_TOKEN` 字段中；该目录已排除 Git。服务器原件是 `/etc/activity-timeline.env`，仅 root 可读。浏览器登录与 Android 上传使用同一令牌。

服务器：香港，Ubuntu 24.04，2 核 1 GB。程序位于 `/opt/activity-timeline`，持久数据库位于 `/var/lib/activity-timeline/activitywatch.db`。云端从空数据库启动，本地历史数据未迁移，现有电脑采集器未切换地址。

后端通过 `activity-timeline.service` 开机启动，以独立系统用户运行，仅监听 127.0.0.1:8765。Nginx 提供 80/443 入口，HTTP 跳转 HTTPS；登录接口有频率限制。正式 IP 证书来自 Let's Encrypt，`activity-cert-renew.timer` 每天检查两次，续期成功后重载 Nginx。已通过模拟续期。

运维命令（通过 SSH 登录后）：

```bash
systemctl status activity-timeline nginx --no-pager
journalctl -u activity-timeline -n 60 --no-pager
systemctl list-timers activity-cert-renew.timer
/opt/activity-certbot/bin/certbot certificates
```

更新程序后执行 `systemctl restart activity-timeline`。不要覆盖数据库或重新生成生产令牌。部署配置保存在 `scripts/configure-aliyun.py`，绑定当前公网 IP；若更换服务器，需要修改地址并重新申请证书。

已验收：公网可信 HTTPS，health=200，未认证状态接口=401，登录=204，认证手机页=200；证书模拟续期成功；本地回归测试 20 passed。手机实际移动网络上传需要在 Android 应用配置后执行，尚未代为操作。没有自动异地备份，长期使用前需配置独立备份。
