# Demo 部署与运行

## 1. WSL2 后端

源代码与 SQLite 数据库建议都放在 WSL2 的 Linux 文件系统（例如 `~/activity-timeline`），避免跨 `/mnt/c` 的 SQLite 文件锁和 I/O 性能问题。

```bash
cd ~/activity-timeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cp backend/.env.example backend/.env
uvicorn backend.app.main:app --host 0.0.0.0 --port 8765
```

仓库若位于 Windows 盘符下，可以一条命令完成复制和安装：

```bash
bash scripts/install-wsl.sh
```

安装器会忽略 Windows 虚拟环境、编译产物和 Demo 数据库，把运行副本放到 `~/.local/share/activity-timeline`，然后在 Linux 文件系统内创建新的 `.venv`。启动时运行：

若 Ubuntu 没有安装 `python3-venv`，安装器会自动从 Python Packaging Authority 的官方引导地址下载 `virtualenv.pyz` 到应用自己的 `.tools` 目录，不要求 `sudo`，也不会修改系统 Python。

```bash
bash ~/.local/share/activity-timeline/scripts/start-wsl-backend.sh
```

默认数据库位于 `backend/data/activitywatch.db`。可通过环境变量覆盖：

```bash
export ACTIVITYWATCH_DB_PATH="$HOME/.local/share/activity-timeline/activitywatch.db"
export ACTIVITYWATCH_TIMEZONE="Asia/Shanghai"
```

健康检查：

```bash
curl http://localhost:8765/api/v1/health
```

## 2. Windows 采集器

使用 Visual Studio 2022 Developer PowerShell：

```powershell
cmake -S collector -B collector/build -G "Visual Studio 17 2022" -A x64
cmake --build collector/build --config Release
collector\build\Release\activity_collector.exe --server http://localhost:8765
```

也可用当前已安装的 MinGW：

```powershell
cmake -S collector -B collector/build -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release
cmake --build collector/build
collector\build\activity_collector.exe --server http://localhost:8765
```

采集器会把尚未确认上传的数据写到 `%LOCALAPPDATA%\ActivityTimeline\queue.jsonl`。服务恢复后会自动重传。队列默认最多保存 60,480 个窗口，约等于连续离线 7 天；超过上限时会明确写错误日志并丢弃最旧记录，避免无限占满系统盘。

## 3. 开机运行

第一版推荐先使用 Windows“任务计划程序”：登录时启动 `activity_collector.exe`，工作目录设为可执行文件所在目录。WSL2 后端可以通过发行版内的 systemd 用户服务启动。Demo 阶段先连续运行一天并检查：

- 时间轴有没有超过 30 秒的明显空洞；
- 断开后端 2 分钟再恢复时，窗口是否补传；
- 同一批数据重复上传时，`duplicates` 是否增加而数据库记录不重复；
- 页面人工改分类后，刷新和后续上传是否仍保留。
