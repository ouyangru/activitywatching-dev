const CATEGORY_COLORS = {
  "学习": "#6fe0a3",
  "工作": "#6ba7ff",
  "娱乐": "#f3b562",
  "空闲": "#e07a72",
  "其他": "#b88cff",
  "无设备记录": "#4b5563",
};
const FALLBACK_COLORS = ["#6fe0a3", "#6ba7ff", "#f3b562", "#e07a72", "#b88cff", "#7fd4d4", "#d98fc0"];
let dailyChart = null;
let currentDimension = "category";
let currentDay = new Date().toISOString().slice(0, 10);
let lastReport = null;

function formatClock(iso) {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso));
}

function formatDuration(seconds) {
  if (!seconds) return "0min";
  if (seconds < 60) return `${seconds}秒`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h${minutes > 0 ? " " + minutes + "min" : ""}`;
  return `${minutes}min`;
}

function formatHours(seconds) {
  return formatDuration(seconds);
}

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

function platformLabel(platform) {
  if (platform === "android") return "Android";
  if (platform === "windows") return "Windows";
  return "无设备";
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.setTimeout(() => toast.classList.remove("visible"), 2200);
}

function shiftDay(day, delta) {
  const date = new Date(`${day}T12:00:00`);
  date.setDate(date.getDate() + delta);
  return date.toISOString().slice(0, 10);
}

function renderHeadline(report) {
  const insights = report.insights || {};
  const focus = insights.focus || {};
  const topApp = (insights.apps || [])[0];
  const topBehavior = (insights.behaviors || [])[0];
  const main = (report.combined_segments || []).filter((item) => item.category !== "无设备记录");
  const overlapTotal = main.reduce((sum, item) => sum + (item.overlap_seconds || 0), 0);
  const parts = [];
  if (topBehavior) parts.push(`最主要是「${topBehavior.behavior}」（${topBehavior.duration_text}）`);
  if (topApp) parts.push(`使用最多的是 ${topApp.process}（${topApp.duration_text}）`);
  if (focus.longest_seconds) parts.push(`最长专注 ${formatDuration(focus.longest_seconds)}`);
  if (overlapTotal >= 60) parts.push(`多设备重叠 ${formatDuration(overlapTotal)} 已按主活动去重`);
  // Agent ② 叙述式日报优先；未生成/不可用时回退到这条确定性摘要
  if (report.narrative && report.narrative.narrative) {
    const suffix = report.narrative.source === "agent-stale" ? "（数据已更新，叙述重新生成中）" : "";
    document.getElementById("dailySummary").textContent = `${report.narrative.narrative}${suffix}`;
    return;
  }
  document.getElementById("dailySummary").textContent = parts.length
    ? `这一天，${parts.join("；")}。`
    : "这一天还没有可用的行为数据。";
}

function renderStats(report) {
  const insights = report.insights || {};
  document.getElementById("trackedTime").textContent = formatHours(report.total_seconds);
  document.getElementById("combinedCount").textContent =
    (report.combined_segments || []).filter((item) => item.category !== "无设备记录").length;
  document.getElementById("longestFocus").textContent = formatDuration((insights.focus || {}).longest_seconds || 0);
  document.getElementById("switchCount").textContent = (insights.switches || {}).behavior_changes || 0;
}

function renderMainTimeline(segments) {
  const target = document.getElementById("mainTimeline");
  const items = segments.filter((segment) => segment.category !== "无设备记录");
  if (!items.length) {
    target.innerHTML = `<div class="empty">该日暂无主活动记录。<br>可切换日期或启动采集器。</div>`;
    return;
  }
  target.innerHTML = [...items].reverse().map((segment) => {
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
            <span class="platform-badge">${platformLabel(segment.main_platform)}</span>
            <strong>${escapeHtml(segment.behavior || segment.category)}</strong>
            <span class="reason-chip">${escapeHtml(segment.reason || "")}</span>
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
}

function renderDistribution() {
  if (!lastReport) return;
  const summary = lastReport.summary || [];
  const items = currentDimension === "category"
    ? summary
    : (lastReport.insights?.purposes || []).map((item) => ({
        category: item.purpose,
        seconds: item.seconds,
        percent: lastReport.total_seconds ? Math.round(item.seconds * 1000 / lastReport.total_seconds) / 10 : 0,
      }));
  const visible = items.filter((item) => item.seconds > 0);
  const colorFor = (name, index) => CATEGORY_COLORS[name] || FALLBACK_COLORS[index % FALLBACK_COLORS.length];

  document.getElementById("dailyLegend").innerHTML = items.map((item, index) => `
    <div class="legend-row">
      <span class="legend-dot" style="background:${colorFor(item.category, index)}"></span>
      <span>${escapeHtml(item.category)}</span><b>${item.percent}%</b>
      <span class="fallback-bar"><i style="width:${item.percent}%;background:${colorFor(item.category, index)}"></i></span>
    </div>`).join("");

  if (!window.echarts) return;
  dailyChart ||= echarts.init(document.getElementById("dailyChart"));
  dailyChart.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "item", formatter: "{b}<br>{c} 秒 · {d}%" },
    series: [{
      type: "pie",
      radius: ["64%", "84%"],
      center: ["50%", "48%"],
      itemStyle: { borderColor: "#10201e", borderWidth: 4, borderRadius: 7 },
      label: { show: false },
      data: visible.length ? visible.map((item, index) => ({
        name: item.category,
        value: item.seconds,
        itemStyle: { color: colorFor(item.category, index) },
      })) : [{ name: "暂无数据", value: 1, itemStyle: { color: "#263430" } }],
    }],
  }, true);
}

function renderRankings(report) {
  const insights = report.insights || {};
  const apps = insights.apps || [];
  document.getElementById("dailyAppRanking").innerHTML = apps.length
    ? apps.map((app) => `
      <li class="ranking-row">
        <span class="ranking-name" title="${escapeHtml(app.process)}">${escapeHtml(app.process)}</span>
        <span class="ranking-bar"><i style="width:${app.share}%"></i></span>
        <b class="ranking-value">${escapeHtml(app.duration_text)}</b>
      </li>`).join("")
    : `<li class="ranking-empty">暂无应用数据</li>`;

  const behaviors = insights.behaviors || [];
  const behaviorTotal = behaviors.reduce((sum, item) => sum + item.seconds, 0);
  document.getElementById("dailyBehaviorRanking").innerHTML = behaviors.length
    ? behaviors.map((behavior) => `
      <li class="ranking-row">
        <span class="ranking-name">${escapeHtml(behavior.behavior)}</span>
        <span class="ranking-bar"><i style="width:${behaviorTotal ? Math.round(behavior.seconds * 100 / behaviorTotal) : 0}%"></i></span>
        <b class="ranking-value">${escapeHtml(behavior.duration_text)}</b>
      </li>`).join("")
    : `<li class="ranking-empty">暂无行为数据</li>`;

  const sources = (insights.switches || {}).top_sources || [];
  document.getElementById("interruptionSources").innerHTML = sources.length
    ? sources.map(([source, count]) => `
      <li class="ranking-row">
        <span class="ranking-name" title="${escapeHtml(source)}">${escapeHtml(source)}</span>
        <span class="ranking-bar"><i style="width:${Math.min(100, count * 20)}%"></i></span>
        <b class="ranking-value">${count} 次</b>
      </li>`).join("")
    : `<li class="ranking-empty">当天没有记录到短暂打断</li>`;
}

async function loadReport() {
  const input = document.getElementById("dayInput");
  input.value = currentDay;
  document.getElementById("dateLabel").textContent =
    new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "long" }).format(new Date(`${currentDay}T12:00:00`));
  try {
    const response = await fetch(`/api/v1/daily/report?day=${currentDay}`);
    if (!response.ok) throw new Error("后端暂时不可用");
    const report = await response.json();
    lastReport = report;
    renderHeadline(report);
    renderStats(report);
    renderMainTimeline(report.combined_segments || []);
    renderDistribution();
    renderRankings(report);
    document.querySelector(".live-pill").classList.remove("offline");
    document.getElementById("connectionLabel").textContent = "服务已连接";
  } catch (error) {
    document.querySelector(".live-pill").classList.add("offline");
    document.getElementById("connectionLabel").textContent = "服务未连接";
    showToast(error.message || "日报加载失败");
  }
}

document.getElementById("prevDay").addEventListener("click", () => { currentDay = shiftDay(currentDay, -1); loadReport(); });
document.getElementById("nextDay").addEventListener("click", () => { currentDay = shiftDay(currentDay, 1); loadReport(); });
document.getElementById("todayButton").addEventListener("click", () => { currentDay = new Date().toISOString().slice(0, 10); loadReport(); });
document.getElementById("dayInput").addEventListener("change", (event) => {
  if (event.target.value) { currentDay = event.target.value; loadReport(); }
});
document.querySelectorAll("[data-daily-dimension]").forEach((button) => {
  button.addEventListener("click", () => {
    currentDimension = button.dataset.dailyDimension;
    document.querySelectorAll("[data-daily-dimension]").forEach((item) => {
      const active = item.dataset.dailyDimension === currentDimension;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-selected", String(active));
    });
    renderDistribution();
  });
});
window.addEventListener("resize", () => dailyChart?.resize());
loadReport();
