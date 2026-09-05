# 未解决 Bug 记录：dev-android.ps1 在 PowerShell 5.1 下 `adb devices` 返回空

> 状态：**未解决** | 记录时间：2026-09-06 03:20 +08:00 | 记录目的：新对话恢复上下文用
> 按仓库规则（AGENTS.md），`bughistory.md` 只收已验证解决的问题，此问题尚未解决，故单独存放于此。

## 现象

`scripts/dev-android.ps1`（一键构建+安装+启动脚本）在 Windows PowerShell 5.1 下运行时，
`Select-Device` 阶段必然抛出 "No device connected"，即使：

- 同一时刻在 **bash 里直接跑** `D:\Android\Sdk\platform-tools\adb.exe devices` 能看到设备
  （vivo V2232A，USB + 无线两条 transport）
- adb server 进程确认就是 `D:\Android\Sdk\platform-tools\adb.exe`（v41，正确版本）
- 脚本内的 `Resolve-Adb` 解析到的路径也确认无误（诊断落盘验证过）

## 环境

- Windows，PowerShell 5.1（powershell.exe，非 pwsh 7）
- adb：`D:\Android\Sdk\platform-tools\adb.exe` v41；PATH 里还有一个 2020 年全志工具附带的旧版 adb（已排除干扰，脚本显式优先 SDK 路径）
- 手机：vivo V2232A（OriginOS），USB 调试 + 无线调试同时在线
- Gradle 构建本身正常（构建成功过、APK 已装上手机）

## 已排除的假设（都做过实验验证，别再重走）

| # | 假设 | 结论 |
|---|------|------|
| 1 | 正则 `\sdevice$` 匹配不到（行尾有 `transport_id:N`） | 已修（改为 `^\S+\s+device\b`），不是根因 |
| 2 | 多 adb 版本冲突（PATH 里旧版全志 adb） | 已修（显式优先 SDK 路径），不是根因 |
| 3 | adb server 版本不匹配（v39 vs v41） | 出现过一次，手动让新版重启 server 后消失，不是持续根因 |
| 4 | 设备瞬时掉线 | 排除——失败可稳定复现，同时刻 bash 能看到设备 |
| 5 | Gradle 构建杀了 adb server | 排除——`_dbg5.ps1` 复现"先构建再查设备"，设备列表正常 |
| 6 | `$ErrorActionPreference="Stop"` 影响 | 排除——`_dbg3.ps1` 验证 |
| 7 | `2>&1` 重定向 + PS5.1 stderr 升级 | 排除——`_dbg6/7.ps1` 验证 |
| 8 | 环境变量（ADB_TRACE 等）指向别的端口 | 排除——导出检查过，无相关变量 |
| 9 | UTF-8 无 BOM 中文注释被 PS5.1 按 ANSI/GBK 解码导致"吞行" | **简单吞行不成立**（Python GBK 解码后行数一致、关键行完好），但见下文——**这是当前最大嫌疑** |

## 关键实验结果（证据链）

1. **纯 ASCII 的最小复现脚本（`_dbg2/3/5/6/7.ps1`）全部成功**——同样的
   `Resolve-Adb` 候选列表、同样的 `& $adb devices 2>&1`、同样在 PS5.1 子进程里跑，
   都能正确解析出设备。
2. **`_dbg9.ps1`（dev-android.ps1 的逐字节副本 + 追加式诊断）失败**：adb 路径解析正确，
   但 `& $Adb devices 2>&1` 只返回**一个空元素**（`RAW COUNT: 1`，内容为空）。
   —— 也就是说命令执行了、没报错、但输出是空的。
3. **不跑 Gradle 的 `reinstall` 分支同样失败** —— 与构建完全无关，纯脚本自身问题。
4. 两个脚本唯一的已知差异：**dev-android.ps1 是 UTF-8 无 BOM 且含中文注释/中文字符串；
   全部成功的 _dbg*.ps1 是纯 ASCII**（PowerShell 工具写入时编码不同）。
5. 最后一项未完成的实验：`_dbg11.ps1` 用 PS5.1 视角查看文件解码后的实际行内容（被用户中断）。

## 当前结论与最大嫌疑

**问题 100% 锁定在"脚本文件本身"（内容/编码），与设备、adb、Gradle、调用方式无关。**

最大嫌疑：PS 5.1 对无 BOM 的 UTF-8 文件按 ANSI/GBK 解码，文件中的中文（UTF-8 多字节）
经 GBK 错误解码后，某处产生了对语法解析有实质影响的字符（比如解码出反引号、引号、
管道符导致字符串/表达式边界错位），使得 `& $Adb devices 2>&1` 这条语句在解析层面
就和源码看上去的不一样。"吞行"只是最简单的表现形式，实际机制更隐蔽。

## 下次恢复时的建议动作（按性价比排序）

1. **首选修复（大概率直接解决）**：把 `dev-android.ps1` 重新保存为
   **UTF-8 with BOM**（或干脆去掉所有中文，改纯 ASCII），然后重跑
   `powershell -ExecutionPolicy Bypass -File scripts\dev-android.ps1 -Action reinstall`。
   一行 Python 即可转码加 BOM。
2. 若仍失败：用 `_dbg11.ps1` 的思路，在 PS5.1 子进程里 dump 解码后的脚本全文，
   与 UTF-8 原文 diff，定位被解码破坏的具体位置。
3. 顺手清理 `scripts/` 下的调试残留：`_dbg*.ps1`、`_dbg*.txt`、`_devrun*.log`、
   `dev-android-diag.txt`、`_dbg9.ps1`（对照实验副本）。

## 脚本功能背景（脚本本身的设计，修好后可直接用）

`scripts/dev-android.ps1`，用法：
- `dev-android.ps1`（默认）：构建 debug APK → 选设备 → `adb install -r` → 启动 App
- `-Action reinstall`：跳过构建，直接装已产出的 APK 并启动
- `-Action log`：拉取 `adb logcat -d -s ActivityTimeline:*` 查看 App 日志
- `-Device <serial>`：多设备时指定序列号

对应操作文档在 `android/docs/OPERATIONS.md`（v0.3.0 前台服务采集版本的完整运维说明）。

## 关联产物（本次会话已完成、不受此 bug 影响）

- Android v0.3.0：前台服务 `CollectorService`、电池优化引导、系统包过滤、
  rules.yaml 分类扩充 —— 已构建成功并安装到手机
- APK 路径：`android/app/build/outputs/apk/debug/app-debug.apk`
- 后端测试 22 passed（test_merger / insights 失败属于仓库并行开发，非本改动引入）
