# Bug History

## 2026-09-05T12:15:48+08:00 · Collector v0.1.1

- **问题：** Windows Collector 运行时键鼠周期性卡顿，WSL2 后端离线时更明显。
- **根因：** 低级键鼠钩子与定时任务共用消息线程；定时任务同步写盘并执行最长数秒的 WinHTTP 请求，阻塞输入钩子分发。
- **解决：** 消息线程只生成内存快照；独立工作线程负责窗口解析、持久化和上传，并对失败请求执行 5–300 秒指数退避；剪贴板长度改用常量时间的缓冲区尺寸估算。
- **验证：** MinGW 全量编译、后端离线运行烟雾测试及自动化测试通过；离线期间消息线程不再执行文件或网络 I/O。

## 2026-09-05T12:47:40+08:00 · API Authentication

- **问题：** 后端数据接口没有设备认证，公网访问者可以读取、上传或修改活动数据。
- **根因：** API 路由未校验请求身份，Windows 采集器也未发送认证凭据。
- **解决：** 增加共享 API Token；后端保护数据读写接口，采集器通过 `--token` 发送 Bearer Token，浏览器可通过 Token URL 建立 HttpOnly Cookie 会话；健康检查保持公开。
- **版本信息：** Activity Timeline 0.1.1
- **验证：** 后端 API 测试 4 passed，采集器 MinGW 编译成功；未认证请求返回 401，正确 Token 返回 200，健康检查返回 200，运行中的采集器数量为 1。

## 2026-09-05T12:56:38+08:00 · Fixed Timeline Panel

- **问题：** 时间线记录增多后面板随内容无限增长，页面窗口无法保持稳定。
- **根因：** 时间线容器没有固定高度或内部滚动区域。
- **解决：** 固定时间线面板的视口高度，将滚动限制在记录列表内部，并为窄屏设置单独高度约束。
- **版本信息：** Activity Timeline 0.1.1
- **验证：** 运行中的后端返回样式文件 200，固定高度和内部滚动规则均已加载。

## 2026-09-05T15:06:14+08:00 · Idle Detection Threshold

- **问题：** 观看视频时没有移动鼠标，时间线被切成“离开电脑 → 观看视频 → 离开电脑”多个片段。
- **根因：** 采集器每 10 秒采样并非过短；后端原先在连续 60 秒无键鼠输入后直接判定为空闲，视频播放本身不会产生输入事件。
- **解决：** 将空闲判定阈值调整为 300000 毫秒（5 分钟），保留 10 秒采样精度，降低观看视频时的误判和切段。
- **版本信息：** Activity Timeline 0.1.1
- **验证：** 完整测试 8 passed；后端重启加载新规则，健康检查返回 200，采集器持续上传数据。

## 2026-09-05T16:20:00+08:00 · Chrome Distribution View Switch

- **问题：** 在 Google Chrome 网页端点击“按时间”时看不到视图切换响应。
- **根因：** 静态脚本和样式资源没有版本标识，浏览器可能继续使用旧缓存；视图按钮监听也直接绑定在按钮节点上，页面状态更新后缺少统一事件入口。
- **解决：** 为前端静态资源增加版本查询参数，并将视图切换改为在按钮容器上使用事件委托。
- **版本信息：** Activity Timeline 0.1.1
- **验证：** Chrome 页面加载新版本资源后，原生点击将活动视图从 `donut` 切换为 `stack`，分类图隐藏且按时间图显示；`node --check backend/static/app.js` 通过；WSL 回归测试 10 passed。

## 2026-09-05T15:46:11+08:00 · No Device Activity Periods

- **问题：** 时间线只显示设备有记录的片段，运动、睡觉等不使用设备的时间无法被看见。
- **根因：** 时间线接口直接返回数据库活动片段，没有计算当天设备活动覆盖区间之外的时间间隙。
- **解决：** 接口按所有设备的活动区间合并覆盖范围，生成不重叠的“无设备记录”合成片段；今天截断到当前时刻，历史日期覆盖完整自然日，并将其加入汇总、圆环图和按时间视图。
- **版本信息：** Activity Timeline 0.2.0
- **验证：** 完整测试 11 passed；Chrome 实际页面返回“无设备记录”分类并显示 46 个间隙；点击“按时间”后视图正常显示；后端已重启加载最新代码。

## 2026-09-05T15:47:33+08:00 · Idle and No Device Colors

- **问题：** “空闲”和“无设备记录”使用相近的灰绿色，在网页时间线和统计图中不易区分。
- **根因：** 两个状态的颜色明度和色相都接近中性灰绿色。
- **解决：** 将“空闲”改为珊瑚红 `#e07a72`，将“无设备记录”改为冷 slate 灰 `#4b5563`，并更新静态资源版本号。
- **版本信息：** Activity Timeline 0.2.0
- **验证：** Chrome 已加载 `app.js?v=20260905-3` 和 `styles.css?v=20260905-2`；页面实际 DOM 颜色分别为 `#e07a72` 与 `#4b5563`；JavaScript 语法检查和 `git diff --check` 通过。

## 2026-09-05T15:52:00+08:00 · Short Collection Gaps

- **问题：** Windows 采样片段之间几秒的短暂间隔被显示为“无设备记录”，看起来像用户在运动或睡觉。
- **根因：** 接口使用分类后的活动片段而不是原始采集窗口判断设备是否在线，导致片段合并边界被误认为设备离线。
- **解决：** 改用 `feature_windows` 原始窗口计算覆盖范围，仅将连续缺失达到 5 分钟的区间标记为“无设备记录”；短间隙不再生成该状态，原有 `idle_ms` 仍负责识别空闲。
- **版本信息：** Activity Timeline 0.2.0
- **验证：** WSL 完整测试 13 passed；Chrome 实际数据中无设备片段最短 333 秒，短于 5 分钟的片段数量为 0。

## 2026-09-05T16:12:30+08:00 · No Device Platform Label

- **问题：** “无设备记录”合成片段的设备标签显示为 `Windows`，容易误解为电脑仍在采集。
- **根因：** 合成片段的平台值为 `none`，前端平台标签未明确处理该值。
- **解决：** 将 `none` 显示为“无设备”，保留 Windows 和 Android 的原有标签。
- **版本信息：** Activity Timeline 0.3.0
- **验证：** Chrome 已加载 `app.js?v=20260905-4`，无设备记录片段的平台标签均显示“无设备”；完整测试 18 passed。

## 2026-09-05T16:35:19+08:00 · Timeline Controls Visual Polish

- **问题：** 时间线设备筛选器尺寸过大，长选项被截断；平台标签和行为标题的视觉层级不够协调。
- **根因：** 原生 `select` 继承了 16px 字号和较大的默认控件高度，设备说明直接放在选项文本中。
- **解决：** 将筛选器和刷新按钮统一为 34px 高、12px 字号，缩短默认选项为“全部设备”，并收紧时间线徽章的内边距、行高和边框对比；同步更新 CSS 缓存版本。
- **版本信息：** Activity Timeline 0.3.0
- **验证：** Chrome 截图确认控件无截断且与时间线标题协调；筛选器实际尺寸为 150×34px；`node --check backend/static/app.js` 和 `git diff --check` 通过。

## 2026-09-05T16:37:13+08:00 · Timeline Sort Order

- **问题：** 时间线固定按后端顺序显示，用户无法选择优先查看最新记录还是最早记录。
- **根因：** 前端没有保存时间线排序状态，也没有在刷新后重新排序展示数据。
- **解决：** 增加“最新在前 / 最早在前”选择器，默认最新在前；每次刷新、设备筛选和手动切换都会保持并应用当前排序。
- **版本信息：** Activity Timeline 0.3.0
- **验证：** Chrome 默认显示 `16:36、16:35`，切换正序显示 `00:00、01:50`，切回倒序恢复最新片段；`node --check backend/static/app.js` 通过。

## 2026-09-05T16:09:30+08:00 · Mobile Current Status Access

- **问题：** 现有仪表盘没有可直接分享给同一局域网手机的安全网址，也没有只展示当前状态的轻量页面；采集停止后，旧片段还可能被误读为实时状态。
- **根因：** 后端缺少“最后活动及新鲜度”接口，首页虽有响应式样式但面向完整时间线；启动流程也不会生成访问令牌或显示电脑的局域网地址。
- **解决：** 新增受令牌保护且禁止缓存的 `/api/v1/status/current` 接口与 `/mobile` 手机页，用两分钟新鲜度区分实时和最近记录；新增 Windows 启动脚本，自动生成 256 位随机令牌、监听局域网并打印手机访问网址。
- **版本信息：** Activity Timeline 0.3.0
- **验证：** WSL 完整测试 15 passed；JavaScript 与 PowerShell 语法检查通过；接口实测未授权返回 401、令牌网址写入 Cookie 后返回 200；在 390×844 浏览器视口中确认状态、设备、更新时间和手动刷新正常显示。

## 2026-09-05T16:11:48+08:00 · Android App Label

- **问题：** Android 时间线规则命中后仍显示 `com.tencent.mm` 等包名，不显示“微信”等应用名称。
- **根因：** 规则描述始终以 Windows `process` 字段生成 `{app}`，未区分 Android 上报的应用标题。
- **解决：** 描述生成器增加平台参数；Android 优先使用 `window_title`，Windows 行为保持不变。
- **版本信息：** Activity Timeline 0.3.0 / Android Collector 0.2.0
- **验证：** Android 微信事件描述断言通过；WSL 完整测试 18 passed，JavaScript、XML 与 Java 语法检查通过。

## 2026-09-05T16:17:26+08:00 · WSL LAN Script Encoding

- **问题：** Windows PowerShell 5.1 执行局域网防火墙脚本时报中文乱码和 `UnexpectedToken`。
- **根因：** 无 BOM 的 UTF-8 中文字符串被 PowerShell 5.1 按系统本地编码读取，破坏脚本解析。
- **解决：** 将脚本源码改为纯 ASCII，并用参数哈希表替代易受空白影响的反引号续行。
- **版本信息：** Activity Timeline 0.3.0 / LAN Setup Script 0.3.1
- **验证：** 脚本非 ASCII 字节为 0；Windows PowerShell 5.1 与 PowerShell 7 解析通过，普通权限运行正确命中管理员保护。

## 2026-09-05T16:28:23+08:00 · Cloud Authentication Defaults

- **问题：** 原后端未配置令牌时允许读取和写入数据，浏览器凭证通过 URL 传递且 Cookie 没有 Secure 标志，不适合直接用于公网部署。
- **根因：** 认证配置沿用本地演示默认值，没有独立生产模式或表单登录流程。
- **解决：** 生产模式要求至少 32 字符令牌；增加 POST 登录和 Secure/HttpOnly/SameSite Cookie，关闭生产 URL 令牌登录，数据响应禁用缓存，公开健康检查不再暴露记录数量；提供固定 HTTPS 托管及持久磁盘配置。
- **版本信息：** Activity Timeline 0.3.1，feature/mobile-status-dashboard。
- **验证：** 本地 WSL 完整测试 20 passed，覆盖拒绝空令牌、登录认证、重启保留记录及重复补传去重；JavaScript 语法检查通过，部署 YAML 可解析。未创建云资源，公网 HTTPS 与移动网络端到端验收待账号部署后执行。

## 2026-09-05T23:10:12+08:00 · Aliyun Public HTTPS Access

- **问题：** 后端仅有本地服务，手机离开局域网无法连接，也没有可用的公网 HTTPS 入口。
- **根因：** 阿里云实例尚未安装后端、配置网页入口或 IP 证书。
- **解决：** 在 47.82.104.59 部署后端独立系统服务及持久数据库，配置 Nginx、正式 IP 证书、每日两次自动续期与证书重载，启用开机启动。
- **版本信息：** Activity Timeline 0.3.1；Ubuntu 24.04；Certbot 5.8.0。
- **验证：** 公网证书验证通过，健康检查 200，未认证数据 401，登录 204，认证手机页 200；服务 active，模拟续期成功；本地测试 20 passed。云端数据库为空，手机移动网络上传和旧历史迁移尚未执行。

## 2026-09-05T23:15:03+08:00 · Android Gradle Bootstrap Timeout

- **问题：** Android 首次构建无法下载 Gradle 9.6.0，10 秒后连接超时。
- **根因：** Gradle 分发地址重定向到 GitHub，Wrapper 未继承 Windows 本地代理，且下载超时仅为 10 秒。
- **解决：** 将 Wrapper 超时提高至 120 秒，并在 D 盘 Gradle 用户目录配置现有 `127.0.0.1:7897` 代理。
- **版本信息：** Android Collector 0.2.0-debug；Gradle Wrapper 9.6.0。
- **验证：** Gradle 与依赖均缓存到 D 盘，`:app:assembleDebug` 成功，生成 7.48 MB APK（SHA-256 `7BAB722396C8D8082410E7D677073A05D5CBD56BA39648E66E1E010475BC2865`）。

## 2026-09-06T02:20:00+08:00 · Cross-Device Merger Development Fixes

- **问题：** 跨设备主活动合并功能开发中出现三类缺陷：洞察接口对 `sqlite3.Row` 调用 `.get()` 抛 AttributeError；合并段 `reason` 字段被共享缓存对象污染导致 SOLO 判定失效；相邻同主活动区间因用行对象身份比较而不合并。
- **根因：** `sqlite3.Row` 不支持 `.get()` 方法；`_Active.reason` 挂在被多区间共享的活动对象上被后写覆盖；合并条件误用对象身份（`is`）而非活动语义键（设备+进程+行为）。
- **解决：** 洞察函数先 `dict(row)` 转普通字典再安全取 `purpose`；`reason` 从 `_Active` 移到 `_Combined` 逐区间持有；合并条件改用 `_activity_key()` 语义比较。同时无设备占位行补齐 `purpose` 键。
- **版本信息：** Activity Timeline 0.4.0（merger/insights/heartbeat/daily 功能集）。
- **验证：** 本地回归 34 passed（含 10 个新测试：固定样例合并、重叠不翻倍、主活动总时长不超当天、结果可重复）；双设备冒烟脚本全链路通过。

## 2026-09-06T02:29:59+08:00 · Collector v0.2.0 Startup Crash

- **问题：** 重新编译的 Windows 采集器在脱离 MinGW 环境启动时立即退出（退出码 0xc0000139，找不到入口点）；以分离进程或开机自启方式启动必然复现。
- **根因：** MinGW 默认动态链接 `libstdc++-6.dll` 与 `libgcc_s_seh-1.dll`，此前仅因启动 shell 的 PATH 包含 `D:/mingw64/bin` 才能运行；Explorer 从 HKCU Run 键开机启动时 PATH 不含该目录，加载到不匹配的运行时 DLL 后解析入口点失败。
- **解决：** CMakeLists 非 MSVC 分支增加 `-static-libgcc -static-libstdc++` 链接选项，exe 仅依赖系统 DLL（ADVAPI32/KERNEL32/msvcrt/SHELL32/USER32/WINHTTP）。
- **版本信息：** activity_collector 0.2.0（MinGW 14.2 / CMake 3.31）
- **验证：** `objdump -p` 确认导入表不含 MinGW 运行时 DLL；以仅含 SystemRoot 的最小环境变量分离启动，进程稳定驻留；心跳与数据上传链路经本地桩服务器端到端验证（heartbeat 请求体符合后端 `HeartbeatRequest` 契约，`events/batch` 收到 503 后离线队列 606 条完整保留并按退避重试）。

## 2026-09-06T02:50:00+08:00 · Agent Tests Broken by Local .env

- **问题：** 本地 `backend/.env` 填入真实 DeepSeek Key 后，`test_agent_disabled_endpoints_unchanged` 等 2 个"未配置 Agent"测试失败（`enabled` 断言为 True）。
- **根因：** `main.py` 模块导入时 `load_dotenv(backend/.env)` 把开发机真实配置注入环境，测试未隔离 `ACTIVITYWATCH_AGENT_*` 变量，"默认关闭"场景随开发机配置漂移。
- **解决：** `tests/test_agent.py` 增加 autouse fixture，测试期间 `monkeypatch.delenv` 清除全部 4 个 Agent 环境变量，保证测试封闭性。
- **版本信息：** Activity Timeline 0.4.0（agent/summarizer/memory 功能集）。
- **验证：** 本地回归 60 passed（填 Key 状态下两遍稳定）。

## 2026-09-06T02:50:00+08:00 · Agent Tests Broken by Local .env

- **问题：** 本地 `backend/.env` 填入真实 DeepSeek Key 后，`test_agent_disabled_endpoints_unchanged` 等 2 个"未配置 Agent"测试失败（`enabled` 断言为 True）。
- **根因：** `main.py` 模块导入时 `load_dotenv(backend/.env)` 把开发机真实配置注入环境，测试未隔离 `ACTIVITYWATCH_AGENT_*` 变量，"默认关闭"场景随开发机配置漂移。
- **解决：** `tests/test_agent.py` 增加 autouse fixture，测试期间 `monkeypatch.delenv` 清除全部 4 个 Agent 环境变量，保证测试封闭性。
- **版本信息：** Activity Timeline 0.4.0（agent/summarizer/memory 功能集）。
- **验证：** 本地回归 60 passed（填 Key 状态下两遍稳定）。

## 2026-09-06T03:28:00+08:00 · Collector Network Stack Hangs (WPAD + Proxy Tunnel)

- **问题：** 远程后端（47.82.104.59）长时间收不到 Windows 设备数据。两层网络缺陷叠加：① v0.2.0 的 WinHTTP 使用 `WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY` 触发 WPAD 自动发现（DHCP/DNS wpad.* 探测），worker 线程在 `WinHttpDetectAutoProxyConfigUrl` 内无限挂起，且该挂起不被 `WinHttpSetTimeouts` 覆盖（gdb 栈实证）；② v0.2.1 改读 IE 静态代理后，本机 Clash（127.0.0.1:7897）接受 TCP 连接但对后端目标的 CONNECT 隧道永不完成——进程仅存一条对 7897 的 ESTABLISHED 连接且无任何数据流动，心跳/上传全部失败，而同机 curl 直连 1 秒内可达后端。
- **根因：** WPAD 探测不受 WinHTTP 超时约束属平台已知行为；本地代理客户端对特定目标转发失败时，静态代理模式没有直连回退路径，采集器作为后台服务无法自愈。
- **解决：** v0.2.1 弃用 AUTOMATIC_PROXY，仅读用户静态 IE 代理配置（`WinHttpGetIEProxyConfigForCurrentUser`），永不触发 WPAD；v0.2.2 将 `post_json` 拆为直连优先（`NO_PROXY`）+ 失败后回退系统静态代理（`post_json_once`），普通家用/服务器网络零代理开销，企业代理网络自动回退。
- **版本信息：** activity_collector 0.2.1 → 0.2.2（MinGW 14.2 / CMake 3.31）
- **验证：** v0.2.2 干净启动后 `collector.log` 记录 `heartbeat ok`（直连）；344 条离线积压 15 秒内补传完成（remaining 344→0）；服务端 `/api/v1/devices` 显示 `windows-AOSIKA` last_seen 与查询时刻仅差 16 秒、在线、collector_version=0.2.2、window_count 4554→4892。

## 2026-09-06T03:28:00+08:00 · Worker Thread Wakeup Freeze (condition_variable::wait_until)

- **问题：** v0.2.0 起采集器工作线程冻结：消息循环正常（WM_TIMER 触发、`emit_window`/`submit` 均完整执行，gdb 断点实证），但 `pending_` 永不被消费，persist/心跳/上传全部停摆，`queue.jsonl`/`sequence.txt` mtime 冻结；gdb attach（停止并恢复全线程）后线程才恢复响应 notify。另观察到调试器附加期间 persist 断点反复命中但文件仍不更新的未解现象，随重写一并消失。
- **根因：** v0.2.0 把 v0.1.1 的无超时 `condition_.wait(lock, pred)` 改为对 steady_clock 绝对时间点的 `wait_until`；MinGW libstdc++ 的 `__gthr_win32_cond_timedwait` 对该绝对时间换算存在缺陷（gdb 栈显示线程携带 uptime 纪元的 `__abs_time` 无限期阻塞），此状态下通知唤醒不可靠，首次丢唤醒后线程沉睡直至外部扰动。
- **解决：** v0.2.2 弃用 `std::condition_variable`，改用 Win32 auto-reset event（`CreateEventW`/`SetEvent`/`WaitForSingleObject`）：事件信号可锁存，`SetEvent` 落在"检查 pending_ 为空"与"进入等待"之间也不会丢失；等待预算取 min(下次心跳, 下次上传重试, 60s) 有界，即使任何唤醒丢失最长 60 秒自愈。
- **版本信息：** activity_collector 0.2.2（MinGW 14.2 / CMake 3.31）
- **验证：** 无调试器干净启动（ShellExecuteW）后 `collector.log` 显示 worker 每 10 秒 persist+upload、每 60 秒 heartbeat ok，无需任何外部干预持续运行。

## 2026-09-06T21:05:00+08:00 · 空闲时段过度聚合与 Agent 派生结果失效

- **问题：** 长时间离开设备全部显示为“空闲/无设备记录”，无法区分睡眠、运动、出游等活动；用户也不能按时间段修正并让后续 Agent 参考。Agent 判断撤销后还可能被再次增强复活，跨设备结果与日报缓存也可能继续展示旧语义。
- **根因：** 空白时段只是请求时生成的临时行且没有可持久修正的身份；Agent 仅处理规则分类“其他”；证据缓存未记录记忆版本，撤销记录被当作缺失证据；日报版本只使用片段数量和最后结束时间。
- **解决：** 新增持久化线下活动区间与睡眠、运动、出游、用餐、通勤、休息、家务分类，在读取层精确切分空闲/无设备时段；可选把工作日/周末及起止时间保存为显式习惯，Agent 只能据此生成带 `inferred=true` 的待确认推测。补充证据记忆版本、永久撤销保护、实时跨设备派生、语义化日报版本、关联请求日志编号与完整记忆展示。
- **版本信息：** Activity Timeline 0.4.1（offline annotations / Agent evidence / daily summary）。
- **验证：** 本地回归 63 passed；新增测试覆盖跨夜人工睡眠、设备活动优先且总时长不重复、时段习惯推测标记、撤销后不复活和习惯覆盖；Python 与三份前端 JavaScript 语法检查通过。

## 2026-09-06T21:06:45+08:00 · 首页时间分布同时展示与全天时间轴

- **问题现象：** 分类、按时间、目的互斥显示；时间轴按八小时拆成三组；目的图切换设备或定时刷新后可能保留旧数据。
- **根因：** 三个维度共用图表和视图状态，目的请求只在切换时触发，时间轴固定使用三个八小时区间。
- **解决方案：** 综合和各设备独立展示分类时长扇形图、目的占比扇形图及 00:00—24:00 单行时间轴；综合使用主活动合并接口；每次刷新统一更新并释放旧图表实例；分类移除百分比，目的保留占比和时长；更新静态资源版本。
- **版本信息：** 基于 e3f63e1，首页静态资源版本 20260906-5。
- **验证结果：** Node 语法检查和 diff 空白检查通过；模拟 DOM/ECharts 验证分类不含百分比、目的占比、标签转义、跨八小时单行、跨午夜、空数据、设备参数隔离及各图同时渲染通过。未进行真实浏览器视觉验收。

## 2026-09-07T00:06:05+08:00 · 时间分布设备布局冗长与过期设备残留

- **问题现象：** 首页按设备纵向重复“分类、目的、时间轴”，无法直接横向比较；扇形图下方的零值项目和具体时长清单占据大量空间；超过 48 小时未活动的演示设备仍持续显示；目的图与分类图的用途区别不清楚。
- **根因：** 渲染结构以设备为外层分组，图例无条件渲染全部分类；设备列表没有前端活动期限；目的视图缺少用途说明且未把兜底值解释为未判定。
- **解决方案：** 在一个大面板内改为分类、全天时间轴、目的三个横向比较区，每一区同时并列综合与所有活跃设备；移除扇形图下的时长图例，分类只显示名称、目的显示名称与比例；离线且最后活动超过 48 小时的设备自动隐藏，在线或重新上报后自动恢复；补充目的统计解释并将“其他”显示为“未判定”。
- **版本信息：** 基于 3f3950a，首页静态资源版本 20260907-1。
- **验证结果：** 本地回归 63 passed；三份前端文件通过语法与空白检查；模拟 DOM/ECharts 验证 48 小时边界、在线设备保留、零值过滤、分类无比例、目的有比例、三个横向区及无底部明细通过。
