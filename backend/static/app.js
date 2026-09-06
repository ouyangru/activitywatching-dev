const CATEGORY_COLORS = {
  "学习": "#6fe0a3",
  "工作": "#6ba7ff",
  "娱乐": "#f3b562",
  "空闲": "#e07a72",
  "其他": "#b88cff",
  "无设备记录": "#4b5563",
  "睡眠": "#8991dd", "运动": "#42cbb2", "出游": "#e6b760",
  "用餐": "#df9972", "通勤": "#78adc8", "休息": "#b6a2c9", "家务": "#b0c979",
};
const CATEGORIES = Object.keys(CATEGORY_COLORS);
const PURPOSE_FALLBACK_COLORS = ["#6fe0a3", "#6ba7ff", "#f3b562", "#e07a72", "#b88cff", "#7fd4d4", "#d98fc0"];
const EDITABLE_CATEGORIES = CATEGORIES.filter((category) => category !== "无设备记录");
const DEVICE_DISPLAY_TIMEOUT_MS = 48 * 60 * 60 * 1000;
const distributionCharts = [];
let dashboardGeneration = 0;
let selectedDevice = "";
let timelineOrder = "desc";
let timelineView = "detail";
let latestInsights = null;

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
  if (seconds < 60) return `${seconds}秒`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h${minutes > 0 ? " " + minutes + "min" : ""}`;
  return `${minutes}min`;
}

function formatTrackedHours(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h${minutes > 0 ? " " + minutes + "min" : ""}`;
  return `${minutes}min`;
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

  if (timelineView === "combined") {
    renderCombinedTimeline(segments);
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
    const purposeTag = segment.purpose && segment.purpose !== segment.category
      ? `<span class="purpose-badge">目的：${escapeHtml(segment.purpose)}</span>` : "";
    return `
      <article class="timeline-item" style="--category-color:${color}">
        <time class="timeline-time">${formatClock(segment.start_time_local)}</time>
        <span class="timeline-node" aria-hidden="true"></span>
        <div class="timeline-card">
          <div class="timeline-title">
            <span class="category-badge">${escapeHtml(segment.category)}</span>
            ${purposeTag}
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

function renderCombinedTimeline(segments) {
  const target = document.getElementById("timeline");
  const orderedSegments = [...segments].sort((left, right) => {
    const direction = timelineOrder === "desc" ? -1 : 1;
    return direction * (new Date(left.start_time) - new Date(right.start_time));
  });

  target.innerHTML = orderedSegments.map((segment) => {
    const color = CATEGORY_COLORS[segment.category] || CATEGORY_COLORS["其他"];
    const secondary = (segment.secondary || []).map((item) =>
      `<span class="secondary-badge">${platformLabel(item.platform)} · ${escapeHtml(item.behavior)}</span>`
    ).join("");
    const overlap = segment.overlap_seconds > 0
      ? `<span class="overlap-badge" title="该时段多设备重叠，仅主活动计入时长">重叠 ${formatDuration(segment.overlap_seconds)}</span>`
      : "";
    return `
      <article class="timeline-item" style="--category-color:${color}">
        <time class="timeline-time">${formatClock(segment.start_time_local)}</time>
        <span class="timeline-node" aria-hidden="true"></span>
        <div class="timeline-card">
          <div class="timeline-title">
            <span class="category-badge">${escapeHtml(segment.category)}</span>
            <span class="platform-badge">${platformLabel(segment.main_platform)}${segment.main_device_id ? ` · ${escapeHtml(segment.main_device_id)}` : ""}</span>
            <strong>${escapeHtml(segment.behavior || segment.category)}</strong>
            <span class="reason-chip" title="主活动判定依据">${escapeHtml(segment.reason || "")}</span>
          </div>
          <p class="timeline-description">${escapeHtml(segment.description || "")}</p>
          ${secondary || overlap ? `<div class="secondary-row">${secondary}${overlap}</div>` : ""}
          <div class="timeline-details">
            <span>${formatClock(segment.start_time_local)}—${formatClock(segment.end_time_local)}</span>
            <span>${formatDuration(segment.duration_seconds)}</span>
          </div>
        </div>
      </article>`;
  }).join("");
  target.scrollTop = 0;
}

function renderInsights(insights) {
  if (!insights) return;
  const focus = insights.focus || {};
  const switches = insights.switches || {};
  document.getElementById("focusGrid").innerHTML = `
    <div class="focus-cell"><span>最长专注</span><strong>${formatDuration(focus.longest_seconds || 0)}</strong></div>
    <div class="focus-cell"><span>专注时段</span><strong>${focus.sessions || 0} 次</strong></div>
    <div class="focus-cell"><span>行为切换</span><strong>${switches.behavior_changes || 0} 次</strong></div>
    <div class="focus-cell"><span>短暂打断</span><strong>${switches.interruptions || 0} 次</strong></div>`;

  const apps = insights.apps || [];
  document.getElementById("appRanking").innerHTML = apps.length
    ? apps.map((app) => `
      <li class="ranking-row">
        <span class="ranking-name" title="${escapeHtml(app.process)}">${escapeHtml(app.process)}</span>
        <span class="ranking-bar"><i style="width:${app.share}%"></i></span>
        <b class="ranking-value">${escapeHtml(app.duration_text)}</b>
      </li>`).join("")
    : `<li class="ranking-empty">暂无应用数据</li>`;
}

async function loadDevices() {
  const response = await fetch("/api/v1/devices");
  if (!response.ok) throw new Error("设备列表加载失败");
  const payload = await response.json();
  const devices = payload.devices.filter((device) => isDeviceVisible(device));
  const select = document.getElementById("deviceFilter");
  const current = selectedDevice;
  select.innerHTML = `<option value="">全部设备</option>` + devices.map((device) => {
    const status = device.is_online
      ? `<span class="device-status is-online" title="采集器在线"></span>`
      : `<span class="device-status is-offline" title="离线或未心跳"></span>`;
    return `<option value="${escapeHtml(device.device_id)}">${status}${platformLabel(device.platform)} · ${escapeHtml(device.device_id)}</option>`;
  }).join("");
  if ([...select.options].some((option) => option.value === current)) {
    select.value = current;
  } else {
    selectedDevice = "";
    select.value = "";
  }
  renderDeviceStatusRow(devices);
  renderDeviceManager(devices);
  return devices;
}

function isDeviceVisible(device, now = Date.now()) {
  if (device.is_online) return true;
  const lastSeen = new Date(device.last_seen || "").getTime();
  return Number.isFinite(lastSeen) && now - lastSeen <= DEVICE_DISPLAY_TIMEOUT_MS;
}

function renderDeviceManager(devices) {
  const container = document.getElementById("deviceManager");
  container.innerHTML = devices.length ? `
    <span class="device-manager-label">设备</span>
    ${devices.map((device) => `<span class="device-manager-item">
      ${escapeHtml(platformLabel(device.platform))} · ${escapeHtml(device.device_id)}
      <button type="button" data-remove-device="${escapeHtml(device.device_id)}" aria-label="删除设备 ${escapeHtml(device.device_id)}">删除</button>
    </span>`).join("")}
    <span class="device-manager-help">删除后仅移出列表，历史数据保留；设备重新上传会自动恢复。</span>` : "";
}

async function removeDevice(deviceId) {
  if (!window.confirm(`确定从设备列表删除“${deviceId}”吗？\n历史时间数据会保留，设备重新上传后会自动恢复。`)) return;
  const response = await fetch(`/api/v1/devices/${encodeURIComponent(deviceId)}`, { method: "DELETE" });
  if (!response.ok) throw new Error("删除设备失败");
  if (selectedDevice === deviceId) selectedDevice = "";
  showToast("设备已从列表删除");
  await loadDashboard(false);
}

const DEVICE_ICONS = {
  windows: `<svg viewBox="0 0 24 24"><path d="M3 12V6.5l8-1.1V12H3zm0 .5h8v6.6l-8-1.1V12.5zM12 5.3l9-1.3v8h-9V5.3zm0 7.2h9v8l-9-1.3v-6.7z"/></svg>`,
  android: `<svg viewBox="0 0 24 24"><path d="M6 18c0 .55.45 1 1 1h1v3.5c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5V19h2v3.5c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5V19h1c.55 0 1-.45 1-1V8H6v10zM3.5 8C2.67 8 2 8.67 2 9.5v7c0 .83.67 1.5 1.5 1.5S5 17.33 5 16.5v-7C5 8.67 4.33 8 3.5 8zm17 0c-.83 0-1.5.67-1.5 1.5v7c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5v-7c0-.83-.67-1.5-1.5-1.5zm-4.97-5.84l1.3-1.3c.2-.2.2-.51 0-.71-.2-.2-.51-.2-.71 0l-1.48 1.48A5.84 5.84 0 0012 1c-.96 0-1.86.23-2.66.63L7.85.15c-.2-.2-.51-.2-.71 0-.2.2-.2.51 0 .71l1.31 1.31A5.983 5.983 0 006 7h12c0-2.12-1.1-3.98-2.74-5.03-.09-.06-.18-.12-.27-.18zM10 5H9V4h1v1zm5 0h-1V4h1v1z"/></svg>`,
};

// 首页 hero 的设备在线徽标（源自服务器端 Codex 实现，改为复用 /api/v1/devices 心跳数据）
function renderDeviceStatusRow(devices) {
  const container = document.getElementById("deviceStatusRow");
  if (!container) return;
  container.innerHTML = devices.map((device) => {
    const icon = DEVICE_ICONS[device.platform] || DEVICE_ICONS.windows;
    const cls = device.is_online ? "device-pill live" : "device-pill offline";
    let label;
    if (device.is_online) {
      label = "在线";
    } else if (device.last_seen) {
      const secondsAgo = Math.max(0, Math.round((Date.now() - new Date(device.last_seen).getTime()) / 1000));
      label = secondsAgo < 60 ? "刚刚活跃" : `${formatDuration(secondsAgo)}前`;
    } else {
      label = "无数据";
    }
    return `<span class="${cls}" title="${escapeHtml(device.device_id)}">${icon}<span class="dot"></span>${label}</span>`;
  }).join("");
}

function renderChart(items, chartId, showPercent = false) {
  const visible = items.filter((item) => item.seconds > 0);
  const colorFor = (name) => CATEGORY_COLORS[name] || PURPOSE_FALLBACK_COLORS[visible.findIndex((item) => item.category === name) % PURPOSE_FALLBACK_COLORS.length] || "#b88cff";

  if (!window.echarts) {
    document.getElementById(chartId).innerHTML = `<div class="empty">图表库离线，暂时无法显示扇形图。</div>`;
    return;
  }
  const chartInstance = echarts.init(document.getElementById(chartId));
  distributionCharts.push(chartInstance);
  chartInstance.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "item", formatter: (params) => `${escapeHtml(params.name)}<br>${visible.length ? formatDuration(params.value) : "暂无数据"}${showPercent && visible.length ? ` · ${params.percent}%` : ""}` },
    series: [{
      type: "pie",
      radius: ["64%", "84%"],
      center: ["50%", "48%"],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: "#10201e", borderWidth: 4, borderRadius: 7 },
      label: {
        show: true,
        color: "#c9d5d0",
        fontSize: 11,
        lineHeight: 15,
        formatter: showPercent ? "{b}\n{d}%" : "{b}",
      },
      labelLine: { length: 8, length2: 5, lineStyle: { color: "#52635d" } },
      emphasis: { scaleSize: 5 },
      data: visible.length ? visible.map((item) => ({
        name: showPercent && item.category === "其他" ? "未判定" : item.category,
        value: item.seconds,
        itemStyle: { color: colorFor(item.category) },
      })) : [{ name: "暂无数据", value: 1, itemStyle: { color: "#263430" } }],
    }],
    graphic: [{
      type: "text", left: "center", top: "42%",
      style: { text: visible.length ? "今日" : "等待数据", fill: "#91a49e", font: "12px Segoe UI" },
    }, {
      type: "text", left: "center", top: "51%",
      style: { text: visible.length ? formatTrackedHours(items.reduce((sum, item) => sum + item.seconds, 0)) : "—", fill: "#eff7f2", font: "600 20px Segoe UI", textAlign: "center" },
    }],
  }, true);
}

function renderTimeComparison(scopes, chartId) {
  if (!window.echarts) {
    document.getElementById(chartId).innerHTML = `<div class="empty">图表库离线，暂时无法显示时间轴。</div>`;
    return;
  }
  const chart = document.getElementById(chartId);
  const timeStackChartInstance = echarts.init(chart);
  distributionCharts.push(timeStackChartInstance);
  const rows = scopes.map((scope) => scope.label);
  const segmentParts = scopes.flatMap((scope, row) => scope.timeline.segments.flatMap((segment) => {
      // Use server-local clock values so browser timezone does not shift the day.
      const startText = segment.start_time_local || segment.start_time;
      const endText = segment.end_time_local || segment.end_time;
      const hour = (value) => Number(value.slice(11, 13)) + Number(value.slice(14, 16)) / 60 + Number(value.slice(17, 19)) / 3600;
      const start = hour(startText);
      const end = endText.slice(0, 10) > startText.slice(0, 10) ? 24 : hour(endText);
      if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return [];
      return [{ category: segment.category, row, start, end,
        block: { category: segment.category, rowLabel: scope.label,
          start: startText, end: endText, seconds: segment.duration_seconds ?? (end - start) * 3600,
          behaviors: [segment.behavior || segment.category], count: 1 } }];
    }));
  const categories = [...new Set(segmentParts.map((part) => part.category))];
  const seriesData = categories.map((category) => ({
    name: category,
    type: "custom",
    coordinateSystem: "cartesian2d",
    itemStyle: { color: CATEGORY_COLORS[category] || "#b88cff" },
    data: segmentParts
      .filter((part) => part.category === category)
      .map((part) => [part.row, part.start, part.end, part.block]),
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
    grid: { left: 175, right: 18, top: 12, bottom: 34 },
    tooltip: {
      trigger: "item",
      formatter: (params) => {
        const block = params.data[3];
        const behaviors = block.behaviors.length > 4
          ? `${block.behaviors.slice(0, 4).join("、")} 等 ${block.behaviors.length} 项`
          : block.behaviors.join("、");
        const records = block.count > 1 ? ` · ${block.count} 段记录` : "";
        return `${escapeHtml(block.rowLabel)} · ${escapeHtml(block.category)}<br>${escapeHtml(behaviors)}<br>${formatClock(block.start)}—${formatClock(block.end)} · ${formatDuration(block.seconds)}${records}`;
      },
    },
    xAxis: {
      type: "value",
      min: 0,
      max: 24,
      interval: 4,
      axisLabel: { color: "#71847d", fontSize: 10, formatter: (value) => `${String(value).padStart(2, "0")}:00` },
      axisLine: { lineStyle: { color: "rgba(202,230,218,.12)" } },
      splitLine: { lineStyle: { color: "rgba(202,230,218,.07)" } },
    },
    yAxis: { type: "category", inverse: true, data: rows, axisLabel: { color: "#c9d5d0", fontSize: 11, width: 158, overflow: "truncate" }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false } },
    series: seriesData,
  }, true);
}

async function fetchDistribution(devices) {
  return Promise.all([{ device_id: "", label: "所有设备综合" }, ...devices.map((device) => ({ ...device, label: `${platformLabel(device.platform)} · ${device.device_id}` }))].map(async (device) => {
    const query = device.device_id ? `device_id=${encodeURIComponent(device.device_id)}` : "";
    const urls = [`/api/v1/summary/today?${query}`, `/api/v1/summary/today?dimension=purpose&${query}`,
      device.device_id ? `/api/v1/timeline/today?${query}` : "/api/v1/timeline/combined"];
    const responses = await Promise.all(urls.map((url) => fetch(url)));
    if (responses.some((response) => !response.ok)) throw new Error("时间分布加载失败");
    const [category, purpose, timeline] = await Promise.all(responses.map((response) => response.json()));
    return { label: device.label, category, purpose, timeline };
  }));
}

function renderDistribution(scopes) {
  distributionCharts.splice(0).forEach((chart) => chart.dispose());
  const scopeCards = (kind, chartClass, ariaLabel) => scopes.map((scope, index) => `
    <article class="distribution-scope-card">
      <h4>${escapeHtml(scope.label)}</h4>
      <div id="${kind}-${index}" class="${chartClass}" role="img" aria-label="${escapeHtml(scope.label)}${ariaLabel}"></div>
    </article>`).join("");
  const scopeStyle = `--scope-count:${scopes.length}`;
  const activeCategories = [...new Set(scopes.flatMap((scope) => scope.category.categories
    .filter((item) => item.seconds > 0)
    .map((item) => item.category)))];
  document.getElementById("distributionTags").innerHTML = activeCategories.map((category) => `
    <span><i style="background:${CATEGORY_COLORS[category] || "#b88cff"}"></i>${escapeHtml(category)}</span>`).join("");
  document.getElementById("distributionScopes").innerHTML = `
    <section class="distribution-band" aria-labelledby="categoryDistributionTitle">
      <div class="distribution-band-heading"><h3 id="categoryDistributionTitle">分类构成</h3><p>看今天具体做了哪些活动</p></div>
      <div class="distribution-scope-grid" style="${scopeStyle}">${scopeCards("category", "distribution-pie", "分类扇形图")}</div>
    </section>
    <section class="distribution-band" aria-labelledby="timelineDistributionTitle">
      <div class="distribution-band-heading"><h3 id="timelineDistributionTitle">全天时间轴</h3><p>所有视图均为一条 00:00—24:00 时间轴</p></div>
      <div id="time-comparison" class="distribution-time-comparison" role="img" aria-label="综合与各设备全天时间轴"></div>
    </section>
    <section class="distribution-band" aria-labelledby="purposeDistributionTitle">
      <div class="distribution-band-heading"><h3 id="purposeDistributionTitle">目的占比</h3><p>看时间投入方向；睡眠、运动、用餐等归入生活事务</p></div>
      <div class="distribution-scope-grid" style="${scopeStyle}">${scopeCards("purpose", "distribution-pie", "目的占比扇形图")}</div>
    </section>`;
  scopes.forEach((scope, index) => {
    renderChart(scope.category.categories, `category-${index}`);
    renderChart(scope.purpose.categories, `purpose-${index}`, true);
  });
  document.getElementById("time-comparison").style.height = `${Math.max(190, scopes.length * 58 + 70)}px`;
  renderTimeComparison(scopes, "time-comparison");
}

async function loadDashboard(showSuccess = false) {
  const generation = ++dashboardGeneration;
  const button = document.getElementById("refreshButton");
  button.disabled = true;
  button.textContent = "刷新中…";
  try {
    const devices = await loadDevices();
    if (generation !== dashboardGeneration) return;
    const requests = [
      fetch(withDevice("/api/v1/timeline/today")),
      fetch(withDevice("/api/v1/summary/today")),
      fetch(withDevice("/api/v1/insights/today")),
    ];
    if (timelineView === "combined") {
      requests.push(fetch("/api/v1/timeline/combined"));
    }
    const [responses, distribution] = await Promise.all([Promise.all(requests), fetchDistribution(devices)]);
    if (generation !== dashboardGeneration) return;
    if (responses.some((response) => !response.ok)) throw new Error("后端暂时不可用");
    const [timeline, summary, insights, combined] = await Promise.all(responses.map((response) => response.json()));
    const segments = timelineView === "combined" ? combined.segments : timeline.segments;
    window.timelineSegments = timeline.segments;
    latestInsights = insights;

    renderTimeline(segments);
    renderDistribution(distribution);
    renderInsights(insights);
    document.getElementById("segmentCount").textContent = timeline.segments.length;
    document.getElementById("trackedTime").textContent = formatTrackedHours(summary.total_seconds);
    document.getElementById("trackedLabel").textContent = selectedDevice ? "该设备覆盖时长" : "设备与无设备时长";
    const focus = summary.categories.filter((item) => item.category === "学习" || item.category === "工作").reduce((sum, item) => sum + item.seconds, 0);
    document.getElementById("focusRate").textContent = summary.total_seconds ? `${Math.round(focus * 100 / summary.total_seconds)}%` : "0%";

    const current = timeline.segments.at(-1);
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
    if (generation !== dashboardGeneration) return;
    document.querySelector(".live-pill").classList.add("offline");
    document.getElementById("connectionLabel").textContent = "服务未连接";
    showToast(error.message || "加载失败");
  } finally {
    if (generation === dashboardGeneration) {
      button.disabled = false;
      button.textContent = "刷新数据";
    }
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
  loadDashboard(false);
});
document.querySelectorAll("[data-timeline-view]").forEach((button) => {
  button.addEventListener("click", () => {
    timelineView = button.dataset.timelineView;
    document.querySelectorAll("[data-timeline-view]").forEach((item) => {
      const active = item.dataset.timelineView === timelineView;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-selected", String(active));
    });
    loadDashboard(false);
  });
});
window.addEventListener("resize", () => distributionCharts.forEach((chart) => chart.resize()));
document.getElementById("deviceManager").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-device]");
  if (!button) return;
  removeDevice(button.dataset.removeDevice).catch((error) => showToast(error.message || "删除设备失败"));
});

loadDashboard();
window.setInterval(() => loadDashboard(false), 30_000);
