# Windows Collector

这是一个 Win32 后台采集器。它每 10 秒把当前活动窗口、键鼠**计数**、系统空闲时长和剪贴板**元数据**聚合为一个 `FeatureWindow`，然后批量上传到 `http://localhost:8765`。

## 隐私设计

低级键盘钩子确实会短暂收到虚拟键码，这是 Windows 用来通知“有键被按下”的机制；实现只做两件事：计数，以及识别 `Ctrl+V` 以增加粘贴次数。键码不会写入对象、日志、磁盘或 HTTP 请求。剪贴板监听器只判断格式并计算文本长度档位，读出的临时文本不会离开当前函数。

发送队列先落盘、后上传。HTTP 返回 2xx 后才从 `queue.jsonl` 删除对应批次，因此程序崩溃或 WSL2 暂停不会让尚未确认的数据悄悄消失。服务端通过 `device_id + sequence` 去重，所以“服务已经收到但客户端尚未删队列”造成的重发也是安全的。

从 v0.1.1 开始，键鼠钩子所在的消息线程只交换内存计数并立即返回。窗口解析、磁盘队列和 HTTP 位于独立工作线程；后端离线时按 5、10、20 秒逐步退避，最高等待 5 分钟，网络故障不会再阻塞输入链路。

## 心跳上报（v0.2.0）

从 v0.2.0 开始，工作线程每 60 秒向 `POST /api/v1/heartbeat` 上报一次心跳（携带 `device_id`、`platform=windows` 和 `collector_version`）。后端在 120 秒内收到心跳即把设备标记为在线，`/devices` 页面据此显示在线徽标。心跳失败有独立的退避节奏（60 → 120 → 240 → 480 秒，上限 300 秒），不会影响数据上传的重传节奏；心跳同样只在工作线程发送，消息线程依旧不做任何网络操作。未配置 `--token` 时心跳会留在本地重试，不会崩溃或弹窗。

## 开机自启（v0.2.0）

右键托盘图标，勾选「开机自启」即可。实现方式是在当前用户的注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 下写入 `ActivityTimelineCollector` 一项，无需管理员权限。自启命令会完整保留当前的 `--server` / `--token` 等参数（仅剔除 `--console`），因此开机后连的是同一个后端。取消勾选即删除该注册表项。

也可以用命令行管理，便于部署脚本调用：

```powershell
activity_collector.exe --autostart on    # 启用并退出
activity_collector.exe --autostart off   # 禁用并退出
```

注意：`--token` 会随自启命令一起保存在 HKCU 注册表中（仅当前用户可读）。如果对此敏感，请只在受信任的账户下启用自启。

## 编译

Visual Studio 2022：

```powershell
cmake -S collector -B collector/build -G "Visual Studio 17 2022" -A x64
cmake --build collector/build --config Release
```

MinGW：

```powershell
cmake -S collector -B collector/build -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release
cmake --build collector/build
```

## 运行参数

```powershell
activity_collector.exe `
  --server http://localhost:8765 `
  --token "replace-with-the-same-api-token" `
  --interval 10 `
  --batch-size 12 `
  --max-queue 60480
```

- `--interval` 是特征窗口秒数，默认 10 秒。
- `--batch-size` 是一次最多上传的窗口数，默认 12。
- `--max-queue` 是离线队列上限，默认 60,480 条，约七天。
- `--token` 是后端 `ACTIVITYWATCH_API_TOKEN` 对应的 Bearer Token；启用后必须提供，否则数据会留在本地离线队列。
- `--console` 会显示诊断控制台；诊断中不打印窗口标题或任何剪贴板内容。
- `--autostart on/off` 写入或删除 HKCU Run 注册表项后立即退出，用于脚本化配置开机自启。

程序在通知区域创建一个托盘图标。右键选择“退出采集器”可以干净地停止钩子并尝试最后一次上传。
