const els = {
  status: document.getElementById('hubStatus'),
  date: document.getElementById('hubDate'),
  time: document.getElementById('hubTime'),
  weekday: document.getElementById('hubWeekday'),
  trackedTime: document.getElementById('trackedTime'),
  focusRate: document.getElementById('focusRate'),
  focusTime: document.getElementById('focusTime'),
  longestFocus: document.getElementById('longestFocus'),
  currentCategory: document.getElementById('currentCategory'),
  currentBehavior: document.getElementById('currentBehavior'),
};

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return '0min';
  const minutes = Math.round(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const remain = minutes % 60;
  if (!hours) return `${minutes}min`;
  return remain ? `${hours}h ${remain}m` : `${hours}h`;
}

function updateClock() {
  const now = new Date();
  els.date.textContent = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(now);
  els.time.textContent = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }).format(now);
  els.weekday.textContent = new Intl.DateTimeFormat('zh-CN', { weekday: 'long' }).format(now);
}

function setStatus(mode, label) {
  els.status.classList.remove('is-online', 'is-offline');
  if (mode) els.status.classList.add(mode);
  els.status.querySelector('b').textContent = label;
}

async function fetchJson(path) {
  const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store' });
  if (response.status === 401) {
    throw new Error('AUTH');
  }
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json();
}

async function loadHub() {
  setStatus('', '正在同步');
  try {
    const [summary, insights, status] = await Promise.all([
      fetchJson('/api/v1/summary/today'),
      fetchJson('/api/v1/insights/today'),
      fetchJson('/api/v1/status/current'),
    ]);

    const focusSeconds = (summary.categories || [])
      .filter((item) => item.category === '学习' || item.category === '工作')
      .reduce((sum, item) => sum + Number(item.seconds || 0), 0);

    els.trackedTime.textContent = formatDuration(Number(summary.total_seconds || 0));
    els.focusRate.textContent = summary.total_seconds
      ? `${Math.round(focusSeconds * 100 / summary.total_seconds)}%`
      : '0%';
    els.focusTime.textContent = `${formatDuration(focusSeconds)} 用于学习 / 工作`;

    const longest = Number(insights?.focus?.longest_seconds || 0);
    els.longestFocus.textContent = longest ? formatDuration(longest) : '—';

    if (status.current) {
      els.currentCategory.textContent = status.current.category || '活动中';
      els.currentBehavior.textContent = status.current.description || status.current.behavior || '最近活动';
    } else {
      els.currentCategory.textContent = '暂无';
      els.currentBehavior.textContent = '等待采集器上报';
    }

    setStatus('is-online', status.is_live ? '设备在线' : '服务已连接');
  } catch (error) {
    if (error.message === 'AUTH') {
      setStatus('is-offline', '需要登录');
      els.currentCategory.textContent = '未登录';
      els.currentBehavior.textContent = '打开行迹页面完成登录后即可显示今日数据';
      return;
    }
    setStatus('is-offline', '暂未连接');
    els.currentCategory.textContent = '离线';
    els.currentBehavior.textContent = '总控入口仍可使用，实时数据暂不可用';
  }
}

updateClock();
setInterval(updateClock, 30_000);
loadHub();
setInterval(() => {
  if (!document.hidden) loadHub();
}, 60_000);
