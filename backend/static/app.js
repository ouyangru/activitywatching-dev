const CATEGORY_COLORS = {
  "学习": "#6fe0a3",
  "工作": "#6ba7ff",
  "娱乐": "#f3b562",
  "空闲": "#8a929d",
  "其他": "#b88cff",
};
const CATEGORIES = Object.keys(CATEGORY_COLORS);
let chartInstance = null;

function formatClock(iso) {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso));
}

function formatDuration(seconds) {
  if (seconds < 60) return `${seconds} 秒`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours ? `${hours} 小时 ${minutes} 分钟` : `${minutes} 分钟`;
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
    target.innerHTML = `<div class="empty">今天还没有行为数据。<br>启动 Windows Collector，或运行 <code>python scripts/seed_demo.py</code> 查看演示。</div>`;
    return;
  }

  target.innerHTML = segments.map((segment) => {
    const color = CATEGORY_COLORS[segment.category] || CATEGORY_COLORS["其他"];
    const options = CATEGORIES.map((category) =>
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
            <strong>${escapeHtml(segment.behavior)}</strong>
          </div>
          <p class="timeline-description">${escapeHtml(segment.description)}</p>
          <div class="timeline-details">
            <span>${formatClock(segment.start_time_local)}—${formatClock(segment.end_time_local)}</span>
            <span>${formatDuration(segment.duration_seconds)}${interruption}${manual}</span>
            <select class="edit-category" data-segment-id="${segment.id}" aria-label="修改 ${escapeHtml(segment.behavior)} 的分类">${options}</select>
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
      style: { text: visible.length ? `${Math.round(items.reduce((sum, item) => sum + item.seconds, 0) / 60)} min` : "—", fill: "#eff7f2", font: "600 20px Segoe UI", textAlign: "center" },
    }],
  });
}

async function loadDashboard(showSuccess = false) {
  const button = document.getElementById("refreshButton");
  button.disabled = true;
  button.textContent = "刷新中…";
  try {
    const [timelineResponse, summaryResponse] = await Promise.all([
      fetch("/api/v1/timeline/today"),
      fetch("/api/v1/summary/today"),
    ]);
    if (!timelineResponse.ok || !summaryResponse.ok) throw new Error("后端暂时不可用");
    const timeline = await timelineResponse.json();
    const summary = await summaryResponse.json();
    const segments = timeline.segments;

    renderTimeline(segments);
    renderChart(summary.categories);
    document.getElementById("segmentCount").textContent = segments.length;
    document.getElementById("trackedTime").textContent = formatDuration(summary.total_seconds);
    const focus = summary.categories.filter((item) => item.category === "学习" || item.category === "工作").reduce((sum, item) => sum + item.seconds, 0);
    document.getElementById("focusRate").textContent = summary.total_seconds ? `${Math.round(focus * 100 / summary.total_seconds)}%` : "0%";

    const current = segments.at(-1);
    if (current) {
      const isFresh = Date.now() - new Date(current.end_time).getTime() < 120_000;
      document.getElementById("currentTitle").textContent = `${isFresh ? "正在" : "最近"}${current.category}：${current.description}`;
      document.getElementById("currentMeta").textContent = `${current.behavior} · ${isFresh ? "从" : "记录于"} ${formatClock(current.start_time_local)} · ${current.process}`;
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
window.addEventListener("resize", () => chartInstance?.resize());
loadDashboard();
window.setInterval(() => loadDashboard(false), 30_000);
