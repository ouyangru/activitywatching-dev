# 行迹 Android 采集模块操作手册

面向维护者的完整操作文档：构建、安装、首次配置、日常调试、故障排查、规则调优。

---

## 1. 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| JDK | 17+ | 构建用，本机 JDK 25 可用 |
| Android SDK | compileSdk 35 | 本机路径 `D:\Android\Sdk`，`local.properties` 已配置 |
| Android 设备 | Android 10+（minSdk 29） | 需开启「开发者选项 → USB 调试」 |
| 后端 | 任意运行中的实例 | 局域网地址或公网地址均可；debug 版允许 HTTP 明文 |

## 2. 一键脚本（推荐）

所有日常操作通过 `scripts/dev-android.ps1` 完成，在仓库根目录执行：

```powershell
# 构建 + 安装 + 启动（日常开发，改完代码跑这条）
.\scripts\dev-android.ps1

# 看手机端实时日志（Ctrl+C 停止）
.\scripts\dev-android.ps1 -Action log

# 只构建不安装
.\scripts\dev-android.ps1 -Action build

# 跳过构建，重装现有 APK + 启动
.\scripts\dev-android.ps1 -Action reinstall

# 查看服务进程状态 + 最近 20 条日志
.\scripts\dev-android.ps1 -Action status

# 多设备时指定序列号
.\scripts\dev-android.ps1 -Device 10AD4H0CQK001X7
```

注意：Gradle 构建需要写 `D:\Android\Gradle` 锁文件和访问 Windows 凭据（DPAPI），在沙箱受限环境里需以非沙箱方式运行；普通 PowerShell 窗口直接执行没有问题。

## 3. 手动构建与安装（脚本不可用时）

```powershell
cd android
.\gradlew.bat assembleDebug        # 产物: app\build\outputs\apk\debug\app-debug.apk
adb install -r app\build\outputs\apk\debug\app-debug.apk
adb shell am start -n com.ouyangru.activitytimeline.debug/com.ouyangru.activitytimeline.MainActivity
```

要点：

- **debug 包 applicationId 带 `.debug` 后缀**，可与正式版共存。启动 Activity 的组件名是 `com.ouyangru.activitytimeline.debug/com.ouyangru.activitytimeline.MainActivity`（注意前半是包名、后半是不带后缀的类名）。
- `install -r` 覆盖安装，保留本地 SQLite 队列和游标；卸载重装（`adb uninstall com.ouyangru.activitytimeline.debug`）会清空队列并重置游标，导致下次采集从当前时刻开始（历史数据已在服务端，不丢失）。
- release 构建：`.\gradlew.bat assembleRelease`，默认禁止明文 HTTP，只接受 HTTPS 后端地址。

## 4. 首次配置清单（装完必做）

按顺序在手机上完成，缺一步后台就不可靠：

1. **使用情况访问权限**：App 内点「打开系统权限页面」→ 找到行迹 → 允许。没有它采集直接抛 SecurityException。
2. **后端连接**：填局域网地址（如 `http://192.168.x.x:8000`）、API Token、设备名，勾选「启用后台采集与自动上传」，点「保存并启动」。
3. **通知权限**：Android 13+ 首次启动会请求，允许（前台服务通知需要）。
4. **电池优化白名单**：App 内点「申请忽略电池优化」→ 允许。
5. **厂商后台限制**（vivo/OriginOS 为例）：
   - 设置 → 电池 → 后台耗电管理 → 行迹 → **允许后台高耗电**
   - 最近任务卡片下拉 → **锁定**
   - OPPO/一加对应路径：设置 → 电池 → 应用耗电管理 → 允许完全后台行为。
6. 验证：通知栏出现「行迹」常驻通知即服务已运行。

## 5. 架构速览（改代码前必读）

```
MainActivity          设置界面：地址/Token/设备名/启停/测试连接
   │
CollectorService      specialUse 前台服务，60s 循环（v0.3.0 核心）
   │  ├─ UsageCollector   读 UsageStatsManager.queryEvents，游标增量，过滤系统包
   │  ├─ QueueDatabase    SQLite 队列（sequence 去重，上限 2 万条）
   │  ├─ ApiClient        批量上传 /api/v1/events/batch + 心跳 /api/v1/heartbeat
   │  └─ 通知栏状态更新
   │
SyncWorker/Scheduler  WorkManager 15 分钟兜底（服务被杀后补数据）
```

关键机制：

- **断档自愈**：`queryEvents` 读的是系统历史事件，游标存在本地；服务被杀后重启会补齐断档期间的数据，前提是任务能重新跑起来（前台服务 + 厂商白名单保证这一点）。
- **幂等上传**：`device_id + sequence` 服务端去重，重传不重复。
- **系统包过滤**：SystemUI、桌面 Launcher、输入法的 RESUMED 事件被忽略（视为上一应用的延续）；相机/拨号不过滤，属于真实使用。

## 6. 日常调试

### 6.1 实时日志

```powershell
.\scripts\dev-android.ps1 -Action log
```

日志 tag 为 `ActivityTimeline`，关注四类行：

| 日志行 | 含义 | 处置 |
|---|---|---|
| `service created` | 前台服务启动 | 正常 |
| `service destroyed` | 服务被杀（ROM 干的或用户停止） | 频繁出现 → 检查第 4 节第 4/5 步 |
| `cycle ok: collected=N pending=M upload=...` | 一轮采集+上传（约 60s 一条） | collected 长期为 0 → 权限或游标问题 |
| `usage stats permission missing` | 缺使用情况访问权限 | 去授权 |
| `cycle failed: ...` | 上传/采集异常 | 看具体异常信息，下一轮自动重试 |

### 6.2 快速验证链路通不通

```powershell
# 手机上点「测试连接」按钮，或：
adb shell am start -n com.ouyangru.activitytimeline.debug/com.ouyangru.activitytimeline.MainActivity
# 在 App 里点「立即采集并上传」，然后看日志：
.\scripts\dev-android.ps1 -Action status
```

`cycle ok` 且 `pending=0` 即全链路正常。

### 6.3 查看本地队列积压

服务端断网/地址填错时队列会积压，恢复后自动清空。积压上限 2 万条（约 20+ 天的量），超出后新数据挤掉最旧的。

## 7. 故障排查表

| 现象 | 根因方向 | 排查动作 |
|---|---|---|
| 时间线大片空白 | 服务被 ROM 杀 | `log` 看 `service destroyed` 频率；重做第 4 节第 4/5 步 |
| 采集到但不上传 | 后端地址错 / Token 错 / 网络不通 | App 内「测试连接」；看 `cycle failed` 日志 |
| 上传成功但分类是「其他/使用手机」 | rules.yaml 没覆盖该包名 | 见第 8 节 |
| 出现「桌面」「SystemUI」片段 | 过滤规则漏了该厂商包名 | `isIgnoredPackage` 里补包名，重建重装 |
| 息屏时间被算成某应用 | 亮屏/息屏事件丢失（部分 ROM 行为） | 提供 `log` + 具体时间点，专项分析 |
| 装完不弹通知 | 通知权限被拒 | 设置 → 通知 → 行迹 → 允许 |

## 8. 分类规则调优流程

规则在 `backend/config/rules.yaml`（改的是后端，不用动 App）：

1. 网页时间线上找到误分类片段，记下 `process` 字段（Android 端就是包名，如 `com.xingin.xhs`）。
2. 在 `rules.yaml` 的 `rules:` 下按现有格式加一条：

```yaml
  - match:
      platform: "android"
      process: "^com\\.example\\.app$"   # 正则，^...$ 精确匹配
    behavior: "刷小红书"
    category: "娱乐"                      # 只能是 学习/工作/娱乐/空闲/其他
    description: "使用 {app} 刷小红书"     # {app} 占位符会被替换
```

3. 重启后端（规则启动时加载），点 App「立即采集并上传」或等下一轮，网页刷新看效果。

约束：前端分类枚举固定为「学习/工作/娱乐/空闲/其他」，不能新增分类；购物/支付类归「其他」但描述写明具体应用。

## 9. 版本迭代与数据

- **升级**：改 `android/app/build.gradle` 的 `versionCode`/`versionName` → `dev-android.ps1`（覆盖安装，数据保留）。
- **本地数据清理**：设置 → 应用 → 行迹 → 清除数据（或卸载重装）。服务端数据不动。
- **回滚**：旧 APK 用 `adb install -r -d 旧包.apk` 降级（`-d` 允许 versionCode 回退）。

## 10. 已知限制

- 亮屏静止持机（看视频不碰屏幕）无法与「使用中」区分——UsageStats 只有前后台事件，无触摸采样。
- Android 10+ 无法可靠监听全局剪贴板，属系统限制。
- 若息屏超 5 分钟，事件会按最长 5 分钟切块上报（切块只影响展示粒度，不丢数据）。
