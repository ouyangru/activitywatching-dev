# 行迹（Activity Timeline Demo）

一个隐私优先的 Windows 行为时间线 Demo。Windows 原生采集器每 10 秒生成一个**特征窗口**（一小段时间内的汇总数据），WSL2 中的 FastAPI 服务把这些窗口合并为“编程、阅读技术资料、观看视频、沟通、空闲”等人能理解的行为片段，并通过浏览器展示和修正。

> 隐私边界：采集器只记录按键次数，不记录按键内容；剪贴板只记录类型、长度档位和发生时间，不记录原文或图片内容。

## 目录

```text
backend/                 FastAPI、SQLite、规则分析器和静态网页
collector/               Windows C++/Win32 采集器
tests/                   后端和分析规则测试
scripts/seed_demo.py     生成一组可视化演示数据
```

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

Windows 采集器的编译、运行和隐私说明见 [collector/README.md](collector/README.md)。完整部署说明见 [DEPLOYMENT_DEMO.md](DEPLOYMENT_DEMO.md)。

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
