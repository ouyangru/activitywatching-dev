(() => {
  if (!document.querySelector('link[href^="/static/recruitment.css"]')) {
    const style = document.createElement('link');
    style.rel = 'stylesheet';
    style.href = '/static/recruitment.css?v=20260911-1';
    document.head.appendChild(style);
  }

  const todayEl = document.getElementById('recruitmentTodayCount');
  const threeEl = document.getElementById('recruitmentThreeDayCount');
  const uncertainEl = document.getElementById('recruitmentUncertainCount');
  const nextEl = document.getElementById('recruitmentNextItem');
  if (!todayEl || !threeEl || !uncertainEl || !nextEl) return;

  const formatNext = (item) => {
    if (!item) return '暂无';
    const raw = item.deadline_at || item.start_at;
    if (!raw) return item.company || '待处理';
    const date = raw.includes('T') ? new Date(raw) : new Date(`${raw}T00:00:00`);
    const label = Number.isNaN(date.getTime())
      ? raw
      : `${date.getMonth() + 1}/${date.getDate()}${raw.includes('T') ? ` ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}` : ''}`;
    return `${item.company || '未知公司'} · ${label}`;
  };

  fetch('/api/v1/recruitment/summary', { credentials: 'same-origin' })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((data) => {
      todayEl.textContent = String(data.today ?? 0);
      threeEl.textContent = String(data.three_days ?? 0);
      uncertainEl.textContent = String(data.uncertain ?? 0);
      nextEl.textContent = formatNext(data.next_item);
    })
    .catch(() => {
      todayEl.textContent = '—';
      threeEl.textContent = '—';
      uncertainEl.textContent = '—';
      nextEl.textContent = '未连接';
    });
})();
