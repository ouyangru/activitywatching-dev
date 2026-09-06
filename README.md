# 行迹（Activity Timeline Demo）

一个隐私优先的跨设备行为时间线 Demo。Windows 原生采集器每 10 秒生成一个**特征窗口**（一小段时间内的汇总数据）；Android 采集器读取系统的应用切换与锁屏记录。WSL2 中的 FastAPI 服务把两端数据合并为“编程、阅读技术资料、观看视频、沟通、空闲”等人能理解的行为片段，并通过浏览器展示和修正。

> 隐私边界：采集器只记录按键次数，不记录按键内容；剪贴板只记录类型、长度档位和发生时间，不记录原文或图片内容。

## 目录

```text
backend/                 FastAPI、SQLite、规则分析器和静态网页
collector/               Windows C++/Win32 采集器
android/                 Android 应用使用情况采集器
tests/                   后端和分析规则测试
scripts/seed_demo.py     生成一组可视化演示数据
```

Agent 语义增强层（LLM 状态判定、日报总结、长期记忆）的架构、配置与开发指南见 [AGENT.md](AGENT.md)。

## 五分钟启动

在 WSL2 中进入本目录并执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
uvicorn backend.app.main:app --host 0.0.0.0 --port 8765 --reload
```

如果仓库暂时位于 `/mnt/c` 或 `/mnt/d`，建议直接运行下面的安装器。它会把运行副本复制到 WSL2 的 Linux 文件系统，避免 SQLite 放在 Windows 挂载盘上：

```bash
bash scripts/install-wsl.sh
bash ~/.local/share/activity-timeline/scripts/start-wsl-backend.sh
```

浏览器打开 `http://localhost:8765`。如果还没有 Windows 采集数据，可在另一个终端运行：

```bash
python scripts/seed_demo.py
```

## 手机查看当前状态

外网和移动数据同步：见 [固定 HTTPS 云端部署](DEPLOYMENT_CLOUD.md)。仓库提供 `render.yaml` 部署配置，使用持久磁盘保存记录；实际创建服务需登录 Render 并确认费用。

电脑和手机连接同一个 Wi-Fi 时，可在 Windows PowerShell 中运行：

```powershell
python -m pip install -r backend/requirements.txt
.\scripts\start-mobile.ps1
```

脚本会监听局域网地址、生成一次性的随机访问令牌，并打印形如
`http://192.168.1.20:8765/mobile?token=...` 的网址。把该网址发到自己的手机并打开即可；首次打开后令牌会存入仅供本站使用的 Cookie，之后网址栏不再携带令牌。手机页每 10 秒刷新，超过 2 分钟没有采集数据时会明确显示“最近记录”，避免把旧状态误认为当前状态。

此方式只在同一局域网内提供访问，不会主动把数据发布到公网。如果手机无法连接，请在 Windows 防火墙提示中只允许“专用网络”。

Windows 采集器的编译、运行和隐私说明见 [collector/README.md](collector/README.md)。完整部署说明见 [DEPLOYMENT_DEMO.md](DEPLOYMENT_DEMO.md)。

Android 端的构建、授权和局域网连接说明见 [android/README.md](android/README.md)。

## HTTP 数据格式

上传接口接受 `{"events": [FeatureWindow, ...]}`。服务端用 `device_id + sequence` 去重，因此采集器在网络失败后可以安全重传同一批数据。时间统一使用带时区的 ISO 8601 字符串；数据库内部规范化为 UTC。

```json
{
  "events": [{
    "device_id": "windows-pc",
    "sequence": 1024,
    "start_time": "2026-09-05T00:20:00Z",
    "duration_ms": 10000,
    "context": {
      "process": "Code.exe",
      "window_title": "mini-nccl - Visual Studio Code"
    },
    "interaction": {
      "key_count": 42,
      "mouse_click_count": 5,
      "scroll_count": 1,
      "idle_ms": 0,
      "clipboard_copy_count": 0,
      "clipboard_paste_count": 1,
      "clipboard_events": []
    }
  }]
}
```
# activitywatching-dev
