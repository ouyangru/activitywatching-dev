const CATEGORY_COLORS = {
  "学习": "#6fe0a3",
  "工作": "#6ba7ff",
  "娱乐": "#f3b562",
  "空闲": "#e07a72",
  "其他": "#b88cff",
  "睡眠": "#8991dd", "运动": "#42cbb2", "出游": "#e6b760",
  "用餐": "#df9972", "通勤": "#78adc8", "休息": "#b6a2c9", "家务": "#b0c979",
};

function formatClock(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function relativeAge(seconds) {
  if (seconds === null) return "尚无记录";
  if (seconds < 15) return "刚刚";
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  return `${Math.floor(seconds / 3600)} 小时前`;
}

function platformLabel(platform) {
  if (platform === "android") return "Android 手机";
  if (platform === "windows") return "Windows 电脑";
  return platform || "未知设备";
}

function setConnection(ok, label) {
  const connection = document.getElementById("connection");
  connection.classList.toggle("offline", !ok);
  connection.querySelector("b").textContent = label;
}

function renderStatus(payload) {
  const segment = payload.current;
  const card = document.getElementById("statusCard");
  card.classList.toggle("stale", !payload.is_live);

  if (!segment) {
    document.getElementById("stateLabel").textContent = "暂无记录";
    document.getElementById("statusTitle").textContent = "还没有收到活动数据";
    document.getElementById("statusDescription").textContent = "启动电脑或手机采集器后，状态会自动出现在这里。";
    return;
  }

  card.style.setProperty("--status-color", CATEGORY_COLORS[segment.category] || CATEGORY_COLORS["其他"]);
  document.getElementById("stateLabel").textContent = payload.is_live ? "实时记录中" : `最近记录 · ${relativeAge(payload.observed_seconds_ago)}`;
  document.getElementById("statusTitle").textContent = `${segment.category} · ${segment.description}`;
  document.getElementById("statusDescription").textContent = payload.is_live
    ? "采集器最近两分钟内仍有数据，当前状态可信。"
    : "采集器最近两分钟没有新数据，因此这里展示的是最后一次已知状态。";
  document.getElementById("device").textContent = `${platformLabel(segment.platform)} · ${segment.device_id}`;
  document.getElementById("behavior").textContent = segment.behavior;
  document.getElementById("startedAt").textContent = formatClock(segment.start_time_local);
  document.getElementById("updatedAt").textContent = relativeAge(payload.observed_seconds_ago);
}

async function loadStatus() {
  const button = document.getElementById("refresh");
  button.disabled = true;
  try {
    const response = await fetch("/api/v1/status/current", { cache: "no-store" });
    if (response.status === 401) {
      window.location.replace("/login");
      return;
    }
    if (!response.ok) throw new Error("状态服务暂时不可用");
    renderStatus(await response.json());
    setConnection(true, "已连接");
  } catch (error) {
    setConnection(false, "连接失败");
    document.getElementById("stateLabel").textContent = "无法更新";
    document.getElementById("statusTitle").textContent = error.message || "状态读取失败";
  } finally {
    button.disabled = false;
  }
}

document.getElementById("refresh").addEventListener("click", loadStatus);
loadStatus();
window.setInterval(loadStatus, 10_000);
