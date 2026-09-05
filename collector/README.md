# Windows Collector

这是一个 Win32 后台采集器。它每 10 秒把当前活动窗口、键鼠**计数**、系统空闲时长和剪贴板**元数据**聚合为一个 `FeatureWindow`，然后批量上传到 `http://localhost:8765`。

## 隐私设计

低级键盘钩子确实会短暂收到虚拟键码，这是 Windows 用来通知“有键被按下”的机制；实现只做两件事：计数，以及识别 `Ctrl+V` 以增加粘贴次数。键码不会写入对象、日志、磁盘或 HTTP 请求。剪贴板监听器只判断格式并计算文本长度档位，读出的临时文本不会离开当前函数。

发送队列先落盘、后上传。HTTP 返回 2xx 后才从 `queue.jsonl` 删除对应批次，因此程序崩溃或 WSL2 暂停不会让尚未确认的数据悄悄消失。服务端通过 `device_id + sequence` 去重，所以“服务已经收到但客户端尚未删队列”造成的重发也是安全的。

从 v0.1.1 开始，键鼠钩子所在的消息线程只交换内存计数并立即返回。窗口解析、磁盘队列和 HTTP 位于独立工作线程；后端离线时按 5、10、20 秒逐步退避，最高等待 5 分钟，网络故障不会再阻塞输入链路。

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

程序在通知区域创建一个托盘图标。右键选择“退出采集器”可以干净地停止钩子并尝试最后一次上传。
