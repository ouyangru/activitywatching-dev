const CATEGORY_COLORS = {
  "学习": "#6fe0a3",
  "工作": "#6ba7ff",
  "娱乐": "#f3b562",
  "空闲": "#e07a72",
  "其他": "#b88cff",
  "无设备记录": "#4b5563",
};
const CATEGORIES = Object.keys(CATEGORY_COLORS);
const EDITABLE_CATEGORIES = CATEGORIES.filter((category) => category !== "无设备记录");
let chartInstance = null;
let timeStackChartInstance = null;
let selectedDevice = "";
let timelineOrder = "desc";

function platformLabel(platform) {
  if (platform === "android") return "Android";
  if (platform === "windows") return "Windows";
  return "无设备";
}

function withDevice(path) {
  if (!selectedDevice) return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}device_id=${encodeURIComponent(selectedDevice)}`;
}

function formatClock(iso) {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso));
}

function formatDuration(seconds) {
  if (seconds < 60) return `${seconds} 秒`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours ? `${hours} 小时 ${minutes} 分钟` : `${minutes} 分钟`;
}

function formatTrackedHours(seconds) {
  return `${(seconds / 3600).toFixed(1)} 小时`;
}

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.setTimeout(() => toast.classList.remove("visible"), 2200);
}

function renderTimeline(segments) {
  const target = document.getElementById("timeline");
  if (!segments.length) {
    target.innerHTML = `<div class="empty">今天还没有行为数据。<br>请启动 Windows Collector 或 Android 行迹采集器。</div>`;
    return;
  }

  const orderedSegments = [...segments].sort((left, right) => {
    const direction = timelineOrder === "desc" ? -1 : 1;
    return direction * (new Date(left.start_time) - new Date(right.start_time));
  });

  target.innerHTML = orderedSegments.map((segment) => {
    const color = CATEGORY_COLORS[segment.category] || CATEGORY_COLORS["其他"];
    const options = EDITABLE_CATEGORIES.map((category) =>
      `<option value="${category}" ${category === segment.category ? "selected" : ""}>${category}</option>`
    ).join("");
    const interruption = segment.interruptions.length ? ` · ${segment.interruptions.length} 次短暂打断` : "";
    const manual = segment.manual_override ? " · 已人工修正" : "";
    return `
      <article class="timeline-item" style="--category-color:${color}">
        <time class="timeline-time">${formatClock(segment.start_time_local)}</time>
        <span class="timeline-node" aria-hidden="true"></span>
        <div class="timeline-card">
          <div class="timeline-title">
            <span class="category-badge">${escapeHtml(segment.category)}</span>
            <span class="platform-badge">${platformLabel(segment.platform)}${segment.device_id ? ` · ${escapeHtml(segment.device_id)}` : ""}</span>
            <strong>${escapeHtml(segment.behavior)}</strong>
          </div>
          <p class="timeline-description">${escapeHtml(segment.description)}</p>
          <div class="timeline-details">
            <span>${formatClock(segment.start_time_local)}—${formatClock(segment.end_time_local)}</span>
            <span>${formatDuration(segment.duration_seconds)}${interruption}${manual}</span>
            ${segment.id === null ? "" : `<select class="edit-category" data-segment-id="${segment.id}" aria-label="修改 ${escapeHtml(segment.behavior)} 的分类">${options}</select>`}
          </div>
        </div>
      </article>`;
  }).join("");

  target.querySelectorAll(".edit-category").forEach((select) => {
    select.addEventListener("change", async (event) => {
      const oldValue = event.target.dataset.previous || "";
      try {
        const response = await fetch(`/api/v1/segments/${event.target.dataset.segmentId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ category: event.target.value }),
        });
        if (!response.ok) throw new Error("保存失败");
        showToast("分类已保存，并会用于后续修正");
        await loadDashboard(false);
      } catch (error) {
        if (oldValue) event.target.value = oldValue;
        showToast(error.message || "保存失败");
      }
    });
    select.dataset.previous = select.value;
  });
  target.scrollTop = 0;
}

async function loadDevices() {
  const response = await fetch("/api/v1/devices");
  if (!response.ok) throw new Error("设备列表加载失败");
  const { devices } = await response.json();
  const select = document.getElementById("deviceFilter");
  const current = selectedDevice;
  select.innerHTML = `<option value="">全部设备</option>` + devices.map((device) =>
    `<option value="${escapeHtml(device.device_id)}">${platformLabel(device.platform)} · ${escapeHtml(device.device_id)}</option>`
  ).join("");
  if ([...select.options].some((option) => option.value === current)) select.value = current;
}

function renderChart(items) {
  const visible = items.filter((item) => item.seconds > 0);
  const legend = document.getElementById("legend");
  legend.innerHTML = items.map((item) => `
    <div class="legend-row">
      <span class="legend-dot" style="background:${CATEGORY_COLORS[item.category]}"></span>
      <span>${item.category}</span><b>${item.percent}%</b>
      <span class="fallback-bar"><i style="width:${item.percent}%;background:${CATEGORY_COLORS[item.category]}"></i></span>
    </div>`).join("");

  if (!window.echarts) {
    document.getElementById("chart").innerHTML = `<div class="empty">图表库离线，右侧比例仍可正常查看。</div>`;
    return;
  }
  chartInstance ||= echarts.init(document.getElementById("chart"));
  chartInstance.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "item", formatter: "{b}<br>{c} 秒 · {d}%" },
    series: [{
      type: "pie",
      radius: ["64%", "84%"],
      center: ["50%", "48%"],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: "#10201e", borderWidth: 4, borderRadius: 7 },
      label: { show: false },
      emphasis: { scaleSize: 5 },
      data: visible.length ? visible.map((item) => ({
        name: item.category,
        value: item.seconds,
        itemStyle: { color: CATEGORY_COLORS[item.category] },
      })) : [{ name: "暂无数据", value: 1, itemStyle: { color: "#263430" } }],
    }],
    graphic: [{
      type: "text", left: "center", top: "42%",
      style: { text: visible.length ? "今日" : "等待数据", fill: "#91a49e", font: "12px Segoe UI" },
    }, {
      type: "text", left: "center", top: "51%",
      style: { text: visible.length ? formatTrackedHours(items.reduce((sum, item) => sum + item.seconds, 0)) : "—", fill: "#eff7f2", font: "600 20px Segoe UI", textAlign: "center" },
    }],
  });
}

function renderTimeStack(segments) {
  if (!window.echarts) return;
  const chart = document.getElementById("timeStackChart");
  if (!timeStackChartInstance) {
    if (getComputedStyle(chart).display === "none") return;
    timeStackChartInstance = echarts.init(chart);
  }
  const rows = ["00:00—08:00", "08:00—16:00", "16:00—24:00"];
  const segmentParts = [];
  segments.forEach((segment) => {
    const startDate = new Date(segment.start_time);
    const endDate = new Date(segment.end_time);
    if (!Number.isFinite(startDate.getTime()) || !Number.isFinite(endDate.getTime())) return;
    let cursor = startDate.getTime();
    const end = endDate.getTime();
    while (cursor < end) {
      const current = new Date(cursor);
      const dayStart = new Date(current.getFullYear(), current.getMonth(), current.getDate()).getTime();
      const elapsedHours = (cursor - dayStart) / 3600000;
      const row = Math.min(2, Math.floor(elapsedHours / 8));
      const rowEnd = dayStart + (row + 1) * 8 * 3600000;
      const partEnd = Math.min(end, rowEnd);
      const rowStart = dayStart + row * 8 * 3600000;
      segmentParts.push({
        category: segment.category,
        row,
        start: (cursor - rowStart) / 3600000,
        end: (partEnd - rowStart) / 3600000,
        segment,
      });
      cursor = partEnd;
    }
  });
  const seriesData = CATEGORIES.map((category) => ({
    name: category,
    type: "custom",
    coordinateSystem: "cartesian2d",
    itemStyle: { color: CATEGORY_COLORS[category] },
    data: segmentParts
      .filter((segment) => segment.category === category)
      .map((part) => [part.row, part.start, part.end, part.segment]),
    renderItem(params, api) {
      const start = api.coord([api.value(1), api.value(0)]);
      const end = api.coord([api.value(2), api.value(0)]);
      const rowHeight = Math.abs(api.size([0, 1])[1]);
      return {
        type: "rect",
        shape: { x: start[0], y: start[1] - rowHeight * .24, width: Math.max(end[0] - start[0], 2), height: rowHeight * .48, r: 3 },
        style: api.style(),
      };
    },
  }));
  timeStackChartInstance.setOption({
    animation: false,
    grid: { left: 72, right: 10, top: 30, bottom: 22 },
    tooltip: {
      trigger: "item",
      formatter: (params) => {
        const segment = params.data[3];
        return `${segment.category}<br>${segment.behavior}<br>${formatClock(segment.start_time)}—${formatClock(segment.end_time)}`;
      },
    },
    legend: { show: true, top: 0, textStyle: { color: "#91a49e", fontSize: 10 }, itemWidth: 10, itemHeight: 8 },
    xAxis: {
      type: "value",
      min: 0,
      max: 8,
      interval: 2,
      axisLabel: { color: "#71847d", fontSize: 10, formatter: (value) => `${String(value).padStart(2, "0")}:00` },
      axisLine: { lineStyle: { color: "rgba(202,230,218,.12)" } },
      splitLine: { lineStyle: { color: "rgba(202,230,218,.07)" } },
    },
    yAxis: { type: "category", inverse: true, data: rows, axisLabel: { color: "#91a49e", fontSize: 10 }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false } },
    series: seriesData,
  }, true);
}

function setDistributionView(view) {
  const donut = document.getElementById("chart");
  const stack = document.getElementById("timeStackChart");
  const showStack = view === "stack";
  donut.style.display = showStack ? "none" : "block";
  stack.classList.toggle("is-visible", showStack);
  document.querySelectorAll(".view-switch-button").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
  if (showStack) {
    renderTimeStack(window.timelineSegments || []);
    requestAnimationFrame(() => timeStackChartInstance?.resize());
  }
}

async function loadDashboard(showSuccess = false) {
  const button = document.getElementById("refreshButton");
  button.disabled = true;
  button.textContent = "刷新中…";
  try {
    await loadDevices();
    const [timelineResponse, summaryResponse] = await Promise.all([
      fetch(withDevice("/api/v1/timeline/today")),
      fetch(withDevice("/api/v1/summary/today")),
    ]);
    if (!timelineResponse.ok || !summaryResponse.ok) throw new Error("后端暂时不可用");
    const timeline = await timelineResponse.json();
    const summary = await summaryResponse.json();
    const segments = timeline.segments;
    window.timelineSegments = segments;

    renderTimeline(segments);
    renderChart(summary.categories);
    if (document.querySelector(".view-switch-button.is-active")?.dataset.view === "stack") {
      renderTimeStack(segments);
    }
    document.getElementById("segmentCount").textContent = segments.length;
    document.getElementById("trackedTime").textContent = formatTrackedHours(summary.total_seconds);
    document.getElementById("trackedLabel").textContent = selectedDevice ? "该设备覆盖时长" : "设备与无设备时长";
    const focus = summary.categories.filter((item) => item.category === "学习" || item.category === "工作").reduce((sum, item) => sum + item.seconds, 0);
    document.getElementById("focusRate").textContent = summary.total_seconds ? `${Math.round(focus * 100 / summary.total_seconds)}%` : "0%";

    const current = segments.at(-1);
    if (current) {
      const isFresh = Date.now() - new Date(current.end_time).getTime() < 120_000;
      document.getElementById("currentTitle").textContent = `${isFresh ? "正在" : "最近"}${current.category}：${current.description}`;
      document.getElementById("currentMeta").textContent = `${platformLabel(current.platform)} · ${current.behavior} · ${isFresh ? "从" : "记录于"} ${formatClock(current.start_time_local)} · ${current.process}`;
    }
    const livePill = document.querySelector(".live-pill");
    livePill.classList.remove("offline");
    document.getElementById("connectionLabel").textContent = "服务已连接";
    if (showSuccess) showToast("数据已刷新");
  } catch (error) {
    document.querySelector(".live-pill").classList.add("offline");
    document.getElementById("connectionLabel").textContent = "服务未连接";
    showToast(error.message || "加载失败");
  } finally {
    button.disabled = false;
    button.textContent = "刷新数据";
  }
}

document.getElementById("todayLabel").textContent = new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "long" }).format(new Date());
document.getElementById("refreshButton").addEventListener("click", () => loadDashboard(true));
document.getElementById("deviceFilter").addEventListener("change", (event) => {
  selectedDevice = event.target.value;
  loadDashboard(false);
});
document.getElementById("timelineOrder").addEventListener("change", (event) => {
  timelineOrder = event.target.value;
  renderTimeline(window.timelineSegments || []);
});
window.addEventListener("resize", () => chartInstance?.resize());
window.addEventListener("resize", () => timeStackChartInstance?.resize());
document.querySelector(".view-switch")?.addEventListener("click", (event) => {
  const button = event.target.closest(".view-switch-button");
  if (button) setDistributionView(button.dataset.view);
});
loadDashboard();
window.setInterval(() => loadDashboard(false), 30_000);
