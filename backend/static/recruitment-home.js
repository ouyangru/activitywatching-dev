(() => {
  // Mobile browsers can occasionally surface a transient network failure as
  // `TypeError: Failed to fetch`, especially when this dashboard fans out
  // several same-origin GETs at once. A single failed request currently causes
  // the whole overview to hide. Retry only idempotent same-origin GET requests;
  // never retry mutations, so POST/PATCH/DELETE cannot be duplicated.
  if (!window.__ACTIVITY_FETCH_RETRY_INSTALLED__) {
    window.__ACTIVITY_FETCH_RETRY_INSTALLED__ = true;
    const nativeFetch = window.fetch.bind(window);
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    const requestMeta = (input, init = {}) => {
      const rawUrl = typeof input === 'string' ? input : input?.url || '';
      let url;
      try {
        url = new URL(rawUrl, location.href);
      } catch (_) {
        return { retryable: false, label: String(rawUrl || 'unknown request') };
      }
      const method = String(init.method || input?.method || 'GET').toUpperCase();
      return {
        retryable: method === 'GET' && url.origin === location.origin,
        label: `${url.pathname}${url.search}`,
      };
    };

    window.fetch = async (input, init = {}) => {
      const meta = requestMeta(input, init);
      const delays = meta.retryable ? [0, 250, 700] : [0];
      let lastError;

      for (let attempt = 0; attempt < delays.length; attempt += 1) {
        if (delays[attempt]) await sleep(delays[attempt]);
        try {
          return await nativeFetch(input, init);
        } catch (error) {
          lastError = error;
          if (error?.name === 'AbortError' || attempt === delays.length - 1) break;
        }
      }

      if (meta.retryable) {
        const detail = lastError?.message || 'network error';
        const wrapped = new Error(`网络请求失败：${meta.label}（已自动重试 2 次；${detail}）`);
        wrapped.cause = lastError;
        throw wrapped;
      }
      throw lastError;
    };
  }

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
