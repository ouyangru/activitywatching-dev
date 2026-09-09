const CATEGORY_COLORS = ActivityUI.colors;
const CATEGORIES = Object.keys(CATEGORY_COLORS).filter(x => x !== "生活事务");
const PURPOSE_FALLBACK_COLORS = ["#6fe0a3", "#6ba7ff", "#f3b562", "#e07a72", "#b88cff", "#7fd4d4", "#d98fc0"];
const EDITABLE_CATEGORIES = CATEGORIES.filter((category) => category !== "无设备记录");
const OFFLINE_CATEGORIES = ActivityUI.editable;
const TIMELINE_MERGE_GAP_MS = 0;
const DEVICE_DISPLAY_TIMEOUT_MS = 48 * 60 * 60 * 1000;
const distributionCharts = [];
let dashboardGeneration = 0;
let selectedDevice = "";
let timelineOrder = "desc";
let timelineView = new URLSearchParams(location.search).get("view") === "detail" ? "detail" : "combined";
let selectedDay = "";
let selectedCategory = "";
let latestSegments = [];
let latestScopes = [];
let lastDistributionTopology = "";
let latestInsights = null;
let pendingCombinedCorrection = null;

function platformLabel(platform) {
  if (platform === "android") return "Android";
  if (platform === "windows") return "Windows";
  return "无设备";
}

function withDay(path) {
  const url = new URL(path, location.origin);
  url.searchParams.set('day', selectedDay);
  return url.pathname + url.search;
}
function withDevice(path) {
  const url = new URL(withDay(path), location.origin);
  if (selectedDevice) url.searchParams.set('device_id', selectedDevice);
  return url.pathname + url.search;
}

function formatClock(iso) { return ActivityUI.clock(iso); }

function formatDuration(seconds) { return ActivityUI.duration(seconds); }

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
    const isOfflineCorrection = ["空闲", "无设备记录"].includes(segment.category) || Boolean(segment.offline_annotation_id);
    const supportsAgentMemory = !isOfflineCorrection && segment.category === "其他";
    const correctionCategories = isOfflineCorrection ? OFFLINE_CATEGORIES : EDITABLE_CATEGORIES;
    const options = `${segment.category === "无设备记录" ? '<option value="" selected disabled>修改状态…</option>' : ""}` + correctionCategories.map((category) =>
      `<option value="${category}" ${category === segment.category ? "selected" : ""}>${category}</option>`
    ).join("");
    const interruption = segment.interruptions.length ? ` · ${segment.interruptions.length} 次短暂打断` : "";
    const manual = segment.manual_override ? " · 已人工修正" : "";
    const purposeTag = segment.purpose && segment.purpose !== segment.category
      ? `<span class="purpose-badge">目的：${escapeHtml(segment.purpose)}</span>` : "";
    return `
      <article class="timeline-item" data-category="${escapeHtml(segment.category)}" data-purpose="${escapeHtml(segment.purpose || segment.category)}" data-start="${escapeHtml(segment.start_time)}" style="--category-color:${color}">
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
            ${isOfflineCorrection ? `<button type="button" class="ghost-button" data-interval-start="${escapeHtml(segment.start_time)}" data-interval-end="${escapeHtml(segment.end_time)}" data-interval-category="${escapeHtml(segment.category)}">修正时段</button>` : segment.id === null ? "" : `<select class="edit-category"
              data-segment-id="${segment.id ?? ""}" data-source-category="${escapeHtml(segment.category)}"
              data-start-time="${escapeHtml(segment.start_time)}" data-end-time="${escapeHtml(segment.end_time)}"
              aria-label="修改 ${escapeHtml(segment.behavior)} 的分类">${options}</select>`}
            ${supportsAgentMemory ? `<label class="remember-correction" title="普通活动按应用记忆；空闲活动按相似时段记忆">
              <input type="checkbox" data-remember-for="${segment.id ?? `${escapeHtml(segment.start_time)}|${escapeHtml(segment.end_time)}`}"> 让 Agent 参考
            </label>` : ""}
          </div>
        </div>
      </article>`;
  }).join("");

  target.querySelectorAll(".edit-category").forEach((select) => {
    select.addEventListener("change", async (event) => {
      const oldValue = event.target.dataset.previous || "";
      try {
        const key = event.target.dataset.segmentId || `${event.target.dataset.startTime}|${event.target.dataset.endTime}`;
        const remember = [...target.querySelectorAll("[data-remember-for]")]
          .find((input) => input.dataset.rememberFor === key)?.checked || false;
        const isOffline = ["空闲", "无设备记录"].includes(event.target.dataset.sourceCategory);
        const response = isOffline
          ? await fetch("/api/v1/offline-activities", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                start_time: event.target.dataset.startTime,
                end_time: event.target.dataset.endTime,
                category: event.target.value,
                note: "从今日首页纠正",
                remember,
              }),
            })
          : await fetch(`/api/v1/segments/${event.target.dataset.segmentId}`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ category: event.target.value, remember, memory_note: remember ? "从今日首页纠正" : null }),
            });
        if (!response.ok) throw new Error("保存失败");
        showToast(remember ? "状态已保存，Agent 会参考这次纠正" : "状态已保存");
        await loadDashboard(false);
      } catch (error) {
        if (oldValue) event.target.value = oldValue;
        showToast(error.message || "保存失败");
      }
    });
    select.dataset.previous = select.value;
  });
  bindIntervalButtons(target);
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
      <article class="timeline-item" data-category="${escapeHtml(segment.category)}" data-purpose="${escapeHtml(segment.purpose || segment.category)}" data-start="${escapeHtml(segment.start_time)}" style="--category-color:${color}">
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
            ${["空闲", "无设备记录"].includes(segment.category) || segment.offline_annotation_id ? `<button type="button" class="ghost-button" data-interval-start="${escapeHtml(segment.start_time)}" data-interval-end="${escapeHtml(segment.end_time)}" data-interval-category="${escapeHtml(segment.category)}">修正时段</button>` : ''}
          </div>
        </div>
      </article>`;
  }).join("");
  bindIntervalButtons(target);
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

  const behaviors = insights.behaviors || [];
  const behaviorTotal = behaviors.reduce((sum, item) => sum + item.seconds, 0);
  document.getElementById("behaviorRanking").innerHTML = behaviors.length
    ? behaviors.map((behavior) => `
      <li class="ranking-row">
        <span class="ranking-name">${escapeHtml(behavior.behavior)}</span>
        <span class="ranking-bar"><i style="width:${behaviorTotal ? Math.round(behavior.seconds * 100 / behaviorTotal) : 0}%"></i></span>
        <b class="ranking-value">${escapeHtml(behavior.duration_text)}</b>
      </li>`).join("")
    : `<li class="ranking-empty">暂无行为数据</li>`;
}

async function loadDevices() {
  const response = await fetch("/api/v1/devices");
  if (!response.ok) throw new Error("设备列表加载失败");
  const payload = await response.json();
  const devices = payload.devices.filter((device) => selectedDay !== ActivityUI.today() || isDeviceVisible(device));
  const select = document.getElementById("deviceFilter");
  const current = selectedDevice;
  select.innerHTML = `<option value="">全部设备</option>` + devices.map((device) => {
    const status = device.is_online ? "在线 · " : "离线 · ";
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
  ActivityUI.pie(chartId, items, category => selectCategory(category, showPercent));
  const legend = document.getElementById(chartId + '-legend');
  legend.innerHTML = items.filter(x => x.seconds > 0).map(x => `<button type="button" data-category="${escapeHtml(x.category)}"><span><i style="background:${CATEGORY_COLORS[x.category] || CATEGORY_COLORS.其他}"></i>${escapeHtml(x.category)}</span><span>${formatDuration(x.seconds)} · ${x.percent}%</span></button>`).join('');
  legend.querySelectorAll('button').forEach(button => button.onclick = () => selectCategory(button.dataset.category, showPercent));
}

function compactDeviceLabel(scope) {
  if (!scope.device_id) return "综合";
  const kind = scope.platform === "android" ? "Android" : scope.platform === "windows" ? "Windows" : platformLabel(scope.platform);
  const withoutPrefix = String(scope.device_id).replace(/^(windows|android)[-_]/i, "");
  const name = withoutPrefix.length > 10 ? `${withoutPrefix.slice(0, 5)}…${withoutPrefix.slice(-4)}` : withoutPrefix;
  return `${kind} · ${name}`;
}

function mergeTimelineBlocks(segments) {
  const ordered = [...segments].sort((left, right) => new Date(left.start_time) - new Date(right.start_time));
  const merged = [];
  ordered.forEach((segment) => {
    const startMs = new Date(segment.start_time).getTime();
    const endMs = new Date(segment.end_time).getTime();
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return;
    const detail = {
      behavior: segment.behavior || segment.category,
      description: segment.description || "",
      start: segment.start_time_local || segment.start_time,
      end: segment.end_time_local || segment.end_time,
    };
    const previous = merged.at(-1);
    if (previous && previous.category === segment.category && startMs - previous.endMs <= TIMELINE_MERGE_GAP_MS) {
      previous.endMs = Math.max(previous.endMs, endMs);
      previous.end = segment.end_time_local || segment.end_time;
      previous.seconds += segment.duration_seconds ?? Math.round((endMs - startMs) / 1000);
      previous.details.push(detail);
      return;
    }
    merged.push({
      category: segment.category,
      startMs,
      endMs,
      start: segment.start_time_local || segment.start_time,
      end: segment.end_time_local || segment.end_time,
      seconds: segment.duration_seconds ?? Math.round((endMs - startMs) / 1000),
      details: [detail],
    });
  });
  return merged;
}

function openCombinedCorrection(block, scopes) {
  const dialog = document.getElementById("combinedCorrectionDialog");
  if (["空闲", "无设备记录"].includes(block.category)) { ActivityUI.correctInterval({start_time:block.start,end_time:block.end,category:block.category}, () => loadDashboard(false)); return; }
  const categories = EDITABLE_CATEGORIES;
  document.getElementById("combinedCorrectionCategory").innerHTML = categories.map((category) =>
    `<option value="${category}" ${category === block.category ? "selected" : ""}>${category}</option>`
  ).join("");
  document.getElementById("combinedCorrectionSummary").textContent =
    `${formatClock(block.start)}—${formatClock(block.end)} · 当前为“${block.category}” · ${block.details.length} 个分项`;
  document.getElementById("combinedCorrectionRemember").checked = false;
  const sourceSegments = scopes.slice(1).flatMap((scope) => scope.timeline.segments).filter((segment) => {
    if (segment.id == null || segment.category !== block.category) return false;
    const start = new Date(segment.start_time).getTime();
    const end = new Date(segment.end_time).getTime();
    return start < block.endMs && end > block.startMs;
  });
  pendingCombinedCorrection = { block, sourceSegments };
  dialog.showModal();
}

async function saveCombinedCorrection() {
  if (!pendingCombinedCorrection) return;
  const { block, sourceSegments } = pendingCombinedCorrection;
  const category = document.getElementById("combinedCorrectionCategory").value;
  const remember = document.getElementById("combinedCorrectionRemember").checked;
  if (category === block.category) {
    document.getElementById("combinedCorrectionDialog").close();
    return;
  }
  if (block.category === "空闲") {
    const response = await fetch("/api/v1/offline-activities", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start_time: new Date(block.startMs).toISOString(),
        end_time: new Date(block.endMs).toISOString(),
        category,
        note: "从综合时间轴纠正",
        remember,
      }),
    });
    if (!response.ok) throw new Error("空闲状态保存失败");
  } else {
    if (!sourceSegments.length) throw new Error("没有找到可修改的原始片段");
    const responses = await Promise.all(sourceSegments.map((segment) => fetch(`/api/v1/segments/${segment.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category, remember, memory_note: remember ? "从综合时间轴纠正" : null }),
    })));
    if (responses.some((response) => !response.ok)) throw new Error("部分状态保存失败");
  }
  pendingCombinedCorrection = null;
  document.getElementById("combinedCorrectionDialog").close();
  showToast(remember ? "综合状态已修改，Agent 会参考这次纠正" : "综合状态已修改");
  await loadDashboard(false);
}

function renderTimeComparison(scopes, chartId) {
  if (!window.echarts) {
    document.getElementById(chartId).innerHTML = `<div class="empty">图表库离线，暂时无法显示时间轴。</div>`;
    return;
  }
  const chart = document.getElementById(chartId);
  const timeStackChartInstance = echarts.getInstanceByDom(chart) || echarts.init(chart);
  if (!distributionCharts.includes(timeStackChartInstance)) distributionCharts.push(timeStackChartInstance);
  const rows = scopes.map(compactDeviceLabel);
  const segmentParts = scopes.flatMap((scope, row) => mergeTimelineBlocks(scope.timeline.segments).flatMap((block) => {
      // Use server-local clock values so browser timezone does not shift the day.
      const startText = block.start;
      const endText = block.end;
      const hour = (value) => Number(value.slice(11, 13)) + Number(value.slice(14, 16)) / 60 + Number(value.slice(17, 19)) / 3600;
      const start = hour(startText);
      const end = endText.slice(0, 10) > startText.slice(0, 10) ? 24 : hour(endText);
      if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return [];
      return [{ category: block.category, row, start, end,
        block: { ...block, rowLabel: compactDeviceLabel(scope), editable: row === 0 && ["其他", "空闲", "无设备记录"].includes(block.category) } }];
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
        shape: { x: start[0], y: start[1] - rowHeight * .16, width: Math.max(end[0] - start[0], 2), height: rowHeight * .32, r: 2 },
        style: api.style({
          stroke: api.value(3)?.editable ? "rgba(239,247,242,.72)" : "transparent",
          lineWidth: api.value(3)?.editable ? 1 : 0,
        }),
        cursor: api.value(3)?.editable ? "pointer" : "default",
      };
    },
  }));
  timeStackChartInstance.setOption({
    animation: !matchMedia("(prefers-reduced-motion: reduce)").matches,
    animationDurationUpdate: 450,
    grid: { left: 76, right: 6, top: 4, bottom: 24 },
    tooltip: {
      trigger: "item",
      formatter: (params) => {
        const block = params.data[3];
        const details = block.details.slice(0, 8).map((detail) =>
          `${formatClock(detail.start)}—${formatClock(detail.end)}　${escapeHtml(detail.behavior)}${detail.description ? ` · ${escapeHtml(detail.description)}` : ""}`
        ).join("<br>");
        const more = block.details.length > 8 ? `<br>另有 ${block.details.length - 8} 项` : "";
        return `<strong>${escapeHtml(block.rowLabel)} · ${escapeHtml(block.category)}</strong><br>${formatClock(block.start)}—${formatClock(block.end)} · 累计 ${formatDuration(block.seconds)}${block.details.length > 1 ? `<br><br>具体分项<br>${details}${more}` : `<br>${details}`}${block.editable ? "<br><br>点击修改这个综合状态" : ""}`;
      },
    },
    xAxis: {
      type: "value",
      min: 0,
      max: 24,
      interval: 4,
      axisLabel: { color: "#9eb0c0", fontSize: 12, formatter: (value) => `${String(value).padStart(2, "0")}:00` },
      axisLine: { lineStyle: { color: "rgba(202,230,218,.12)" } },
      splitLine: { lineStyle: { color: "rgba(202,230,218,.07)" } },
    },
    yAxis: { type: "category", inverse: true, data: rows, axisLabel: { color: "#c9d5d0", fontSize: 12, width: 64, overflow: "truncate" }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false } },
    series: seriesData,
  }, true);
  timeStackChartInstance.off("click");
  timeStackChartInstance.on("click", (params) => {
    const block = params.data?.[3];
    if (block?.editable) openCombinedCorrection(block, scopes);
  });
}

async function fetchDistribution(devices) {
  return Promise.all([{ device_id: "", label: "所有设备综合" }, ...devices.map((device) => ({ ...device, label: `${platformLabel(device.platform)} · ${device.device_id}` }))].map(async (device) => {
    const query = device.device_id ? `device_id=${encodeURIComponent(device.device_id)}` : "";
    const urls = [`/api/v1/summary/today?${query}`, `/api/v1/summary/today?dimension=purpose&${query}`,
      device.device_id ? `/api/v1/timeline/today?${query}` : "/api/v1/timeline/combined"];
    const responses = await Promise.all(urls.map((url) => fetch(withDay(url))));
    if (responses.some((response) => !response.ok)) throw new Error("时间分布加载失败");
    const [category, purpose, timeline] = await Promise.all(responses.map((response) => response.json()));
    return { ...device, label: device.label, category, purpose, timeline };
  }));
}

function renderDistribution(scopes) {
  const topology = scopes.map(x => x.device_id).join('|');
  const rebuild = topology !== lastDistributionTopology || !document.getElementById('category-0');
  if (rebuild) { ActivityUI.disposeWithin(document.getElementById('distributionScopes')); distributionCharts.splice(0).forEach(chart => chart.dispose()); }
  lastDistributionTopology = topology;
  const scopeCards = (kind, chartClass, ariaLabel) => scopes.map((scope, index) => `
    <article class="distribution-scope-card">
      <h4>${escapeHtml(scope.label)}</h4>
      <div id="${kind}-${index}" class="${chartClass}" role="img" aria-label="${escapeHtml(scope.label)}${ariaLabel}"></div><div class="chart-legend" id="${kind}-${index}-legend"></div>
    </article>`).join("");
  const scopeStyle = `--scope-count:${scopes.length}`;
  const activeCategories = [...new Set(scopes.flatMap((scope) => scope.category.categories
    .filter((item) => item.seconds > 0)
    .map((item) => item.category)))];
  document.getElementById("distributionTags").innerHTML = activeCategories.map((category) => `
    <button type="button" data-category="${escapeHtml(category)}"><i style="background:${CATEGORY_COLORS[category] || "#b88cff"}"></i>${escapeHtml(category)}</button>`).join("");
  if (rebuild) document.getElementById("distributionScopes").innerHTML = `
    <section class="distribution-band" aria-labelledby="categoryDistributionTitle">
      <div class="distribution-band-heading"><h3 id="categoryDistributionTitle">分类构成</h3><p>看当天具体做了哪些活动</p></div>
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
  document.querySelectorAll("#distributionTags button").forEach(button => button.onclick = () => selectCategory(button.dataset.category));
  scopes.forEach((scope, index) => {
    renderChart(scope.category.categories, `category-${index}`);
    renderChart(scope.purpose.categories, `purpose-${index}`, true);
  });
  document.getElementById("time-comparison").style.height = `${Math.max(128, scopes.length * 34 + 42)}px`;
  renderTimeComparison(scopes, "time-comparison");
}

async function loadDashboard(showSuccess = false) {
  const generation = ++dashboardGeneration;
  document.getElementById('pageState').textContent = `正在加载 ${selectedDay}…`;
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
      requests.push(fetch(withDay("/api/v1/timeline/combined")));
    }
    const [responses, distribution] = await Promise.all([Promise.all(requests), fetchDistribution(devices)]);
    if (generation !== dashboardGeneration) return;
    if (responses.some((response) => !response.ok)) throw new Error("后端暂时不可用");
    const [timeline, summary, insights, combined] = await Promise.all(responses.map((response) => response.json()));
    document.querySelectorAll(".stats, .content-grid, .reflection-panel").forEach(el => el.hidden = false);
    ActivityUI.setTimezone(timeline.timezone); ActivityUI.syncDay(selectedDay);
    const segments = timelineView === "combined" ? combined.segments : timeline.segments;
    window.timelineSegments = timeline.segments;
    latestInsights = insights;

    latestSegments = segments; latestScopes = distribution;
    renderTimeline(segments);
    applyCategoryFilter();
    ActivityUI.reflection("overviewReflection", distribution[0]?.timeline.segments || segments, insights, revealSegment);
    renderDistribution(distribution);
    renderInsights(insights);
    document.getElementById("segmentCount").textContent = timeline.segments.length;
    document.getElementById("trackedTime").textContent = ActivityUI.duration(summary.total_seconds);
    document.getElementById("trackedLabel").textContent = selectedDevice ? "该设备覆盖时长" : "当天覆盖时长（含未记录）";
    document.getElementById("unknownTime").textContent = formatDuration(summary.categories.find(x => x.category === "无设备记录")?.seconds || 0);
    const focus = summary.categories.filter((item) => item.category === "学习" || item.category === "工作").reduce((sum, item) => sum + item.seconds, 0);
    document.getElementById("focusRate").textContent = summary.total_seconds ? `${Math.round(focus * 100 / summary.total_seconds)}%` : "0%";

    const current = [...timeline.segments].filter(x=>x.category !== '无设备记录').sort((a,b)=>new Date(a.end_time)-new Date(b.end_time)).at(-1);
    const historical = selectedDay !== ActivityUI.today();
    document.getElementById('currentTitle').textContent = historical ? `${selectedDay} · 活动回顾` : current ? `${Date.now()-new Date(current.end_time).getTime()<120000?'正在':'最近'}${current.category}：${current.description}` : '等待活动记录';
    document.getElementById('currentMeta').textContent = historical ? `当天 ${timeline.segments.filter(x=>x.category !== '无设备记录').length} 段设备活动；可查看图表或修正时段。` : current ? `${platformLabel(current.platform)} · ${current.behavior} · ${formatClock(current.start_time_local)} 开始` : '启动采集器，或选择历史日期查看之前的活动。';
    document.getElementById('pageState').textContent = `已加载 ${selectedDay} 的数据`;
    document.getElementById('pageState').classList.remove('is-error');
    const livePill = document.querySelector(".live-pill");
    livePill.classList.remove("offline");
    document.getElementById("connectionLabel").textContent = "服务已连接";
    if (showSuccess) showToast("数据已刷新");
  } catch (error) {
    if (generation !== dashboardGeneration) return;
    document.querySelector(".live-pill").classList.add("offline");
    document.getElementById("connectionLabel").textContent = "服务未连接";
    document.getElementById("pageState").textContent = `${selectedDay} 加载失败；下方内容已隐藏，请刷新重试。`;
    document.getElementById("pageState").classList.add("is-error");
    document.querySelectorAll(".stats, .content-grid, .reflection-panel").forEach(el => el.hidden = true);
    showToast(error.message || "加载失败");
  } finally {
    if (generation === dashboardGeneration) {
      button.disabled = false;
      button.textContent = "刷新数据";
    }
  }
}

document.getElementById("todayLabel").textContent = new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "long" }).format(new Date());
document.getElementById("retryDashboard").addEventListener("click", () => loadDashboard(true));
document.getElementById("refreshButton").addEventListener("click", () => loadDashboard(true));
document.getElementById("deviceFilter").addEventListener("change", (event) => {
  selectedDevice = event.target.value;
  if(selectedDevice)timelineView = "detail";
  updateViewButtons();
  loadDashboard(false);
});
document.getElementById("timelineOrder").addEventListener("change", (event) => {
  timelineOrder = event.target.value;
  loadDashboard(false);
});
document.querySelectorAll("[data-timeline-view]").forEach((button) => {
  button.addEventListener("click", () => {
    timelineView = button.dataset.timelineView;
    if(timelineView === "combined")selectedDevice = "";
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
document.getElementById("combinedCorrectionForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.getElementById("saveCombinedCorrection");
  button.disabled = true;
  try {
    await saveCombinedCorrection();
  } catch (error) {
    showToast(error.message || "综合状态保存失败");
  } finally {
    button.disabled = false;
  }
});
document.getElementById("cancelCombinedCorrection").addEventListener("click", () => {
  pendingCombinedCorrection = null;
  document.getElementById("combinedCorrectionDialog").close();
});

function updateViewButtons() {
  document.querySelectorAll('[data-timeline-view]').forEach(b=>{const active=b.dataset.timelineView===timelineView;b.classList.toggle('is-active',active);b.setAttribute('aria-selected',String(active));});
}
function bindIntervalButtons(target) {
  target.querySelectorAll('[data-interval-start]').forEach(b=>b.onclick=()=>ActivityUI.correctInterval({start_time:b.dataset.intervalStart,end_time:b.dataset.intervalEnd,category:b.dataset.intervalCategory},()=>loadDashboard(false)));
}
let filterPurpose = false;
function selectCategory(category, purpose = false) {
  selectedCategory = selectedCategory === category && filterPurpose === purpose ? '' : category;
  filterPurpose = purpose; applyCategoryFilter();
  document.getElementById('timelineTitle').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
}
function applyCategoryFilter() {
  const status=document.getElementById('filterStatus');status.hidden=!selectedCategory;
  status.innerHTML=`正在突出显示：${escapeHtml(selectedCategory)} <button class="ghost-button" type="button">清除</button>`;
  status.querySelector('button').onclick=()=>{selectedCategory='';applyCategoryFilter();};
  document.querySelectorAll('#timeline .timeline-item').forEach(el=>el.classList.toggle('is-dimmed',Boolean(selectedCategory)&&el.dataset[filterPurpose?'purpose':'category']!==selectedCategory));
}
async function revealSegment(segment) {
  selectedCategory=''; timelineView='combined';selectedDevice='';updateViewButtons();await loadDashboard(false);
  const el=[...document.querySelectorAll('#timeline .timeline-item')].find(el=>el.dataset.start===segment.start_time);
  if(el){el.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'center'});ActivityUI.flash(el);el.classList.add('is-linked');}
}
ActivityUI.ready.then(()=>{
  selectedDay=ActivityUI.initialDay();
  ActivityUI.bindDate({input:'overviewDay',prev:'overviewPrev',next:'overviewNext',today:'overviewToday',day:selectedDay,onChange:day=>{selectedDay=day;selectedCategory='';document.getElementById('currentTitle').textContent=`${day} · 正在加载`;document.getElementById('currentMeta').textContent='';document.querySelectorAll('.stats, .content-grid, .reflection-panel').forEach(el=>el.hidden=true);loadDashboard(false);}});
  updateViewButtons();loadDashboard();
});
window.setInterval(()=>{if(selectedDay===ActivityUI.today()&&!document.hidden&&!document.querySelector('dialog[open]')&&!document.activeElement?.matches('select,input'))loadDashboard(false);},30000);
