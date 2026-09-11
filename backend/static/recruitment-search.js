(() => {
  const anchor = document.querySelector('.recruitment-view-tabs');
  const list = document.getElementById('recruitmentList');
  if (!anchor || !list) return;

  const typeLabel = { written_test: '笔试', assessment: '测评', interview: '面试', other: '其他' };
  const modeLabel = { fixed_time: '固定时间', deadline: '截止事项', uncertain: '待确认' };
  const statusLabel = { pending: '待处理', uncertain: '待确认', done: '已完成', cancelled: '已取消', expired: '已过期' };

  const shell = document.createElement('section');
  shell.className = 'recruitment-search-shell';
  shell.innerHTML = `
    <div class="recruitment-search-box">
      <span class="recruitment-search-icon" aria-hidden="true">⌕</span>
      <input id="recruitmentSearchInput" type="search" autocomplete="off" placeholder="搜索公司、标题、笔试 / 面试、状态…" aria-label="搜索秋招事项">
      <span id="recruitmentSearchMeta" class="recruitment-search-meta">全部事项</span>
      <button id="recruitmentSearchClear" class="recruitment-search-clear" type="button" hidden>清空</button>
    </div>
    <div id="recruitmentSearchResults" class="recruitment-search-results" hidden></div>`;
  anchor.insertAdjacentElement('afterend', shell);

  const input = shell.querySelector('#recruitmentSearchInput');
  const clearButton = shell.querySelector('#recruitmentSearchClear');
  const meta = shell.querySelector('#recruitmentSearchMeta');
  const resultsEl = shell.querySelector('#recruitmentSearchResults');

  let items = [];
  let loaded = false;
  let loading = null;

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function normalize(value) {
    return String(value ?? '').toLocaleLowerCase('zh-CN').replace(/\s+/g, ' ').trim();
  }

  function terms() {
    return normalize(input.value).split(' ').filter(Boolean);
  }

  function itemText(item) {
    return normalize([
      item.company,
      item.title,
      item.source_subject,
      item.source_sender,
      item.extraction_note,
      item.action_url,
      typeLabel[item.item_type] || item.item_type,
      modeLabel[item.mode] || item.mode,
      statusLabel[item.status] || item.status,
      item.deadline_at,
      item.start_at,
    ].filter(Boolean).join(' '));
  }

  function matchesItem(item, searchTerms) {
    const haystack = itemText(item);
    return searchTerms.every((term) => haystack.includes(term));
  }

  function formatTime(item) {
    const raw = item.deadline_at || item.start_at;
    if (!raw) return '时间待确认';
    if (!String(raw).includes('T')) return raw;
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return raw;
    return new Intl.DateTimeFormat('zh-CN', {
      month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(date);
  }

  async function loadItems(force = false) {
    if (loaded && !force) return items;
    if (loading && !force) return loading;
    loading = fetch('/api/v1/recruitment/items?limit=500', {
      credentials: 'same-origin', cache: 'no-store',
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        items = payload.items || [];
        loaded = true;
        return items;
      })
      .finally(() => { loading = null; });
    return loading;
  }

  function filterVisibleCards(searchTerms) {
    const cards = [...list.querySelectorAll('.recruitment-card')];
    if (!searchTerms.length) {
      cards.forEach((card) => { card.hidden = false; });
      return;
    }
    cards.forEach((card) => {
      const text = normalize(card.textContent);
      card.hidden = !searchTerms.every((term) => text.includes(term));
    });
  }

  function renderResults(matches, searchTerms) {
    if (!searchTerms.length) {
      resultsEl.hidden = true;
      resultsEl.innerHTML = '';
      meta.textContent = '全部事项';
      return;
    }

    meta.textContent = `匹配 ${matches.length} 条`;
    if (!matches.length) {
      resultsEl.hidden = false;
      resultsEl.innerHTML = '<div class="recruitment-search-empty">没有找到匹配的秋招事项。</div>';
      return;
    }

    const shown = matches.slice(0, 20);
    resultsEl.hidden = false;
    resultsEl.innerHTML = shown.map((item) => `
      <button class="recruitment-search-result" type="button" data-recruitment-search-id="${item.id}">
        <span class="recruitment-search-result-main">
          <strong>${escapeHtml(item.company || '未知公司')}</strong>
          <span>${escapeHtml(item.title || item.source_subject || '秋招事项')}</span>
        </span>
        <span class="recruitment-search-result-side">
          <small>${escapeHtml(typeLabel[item.item_type] || '其他')} · ${escapeHtml(statusLabel[item.status] || item.status)}</small>
          <b>${escapeHtml(formatTime(item))}</b>
        </span>
      </button>`).join('') + (matches.length > shown.length
        ? `<div class="recruitment-search-more">还有 ${matches.length - shown.length} 条结果，继续缩小关键词。</div>`
        : '');
  }

  async function applySearch() {
    const searchTerms = terms();
    clearButton.hidden = !searchTerms.length;
    filterVisibleCards(searchTerms);
    if (!searchTerms.length) {
      renderResults([], []);
      return;
    }

    meta.textContent = '搜索中…';
    try {
      const source = await loadItems();
      const matches = source.filter((item) => matchesItem(item, searchTerms));
      renderResults(matches, searchTerms);
    } catch (error) {
      meta.textContent = '搜索失败';
      resultsEl.hidden = false;
      resultsEl.innerHTML = `<div class="recruitment-search-empty">搜索失败：${escapeHtml(error.message)}</div>`;
    }
  }

  function focusResult(itemId) {
    const listTab = document.querySelector('[data-recruitment-view="list"]');
    const allFilter = document.querySelector('[data-recruitment-filter="all"]');
    listTab?.click();
    allFilter?.click();
    window.setTimeout(() => {
      filterVisibleCards(terms());
      const action = list.querySelector(`button[data-id="${itemId}"]`);
      const card = action?.closest('.recruitment-card');
      if (!card) return;
      card.hidden = false;
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card.classList.add('is-search-target');
      window.setTimeout(() => card.classList.remove('is-search-target'), 1800);
    }, 80);
  }

  input.addEventListener('input', applySearch);
  input.addEventListener('focus', () => {
    if (terms().length) applySearch();
  });

  clearButton.addEventListener('click', () => {
    input.value = '';
    applySearch();
    input.focus();
  });

  resultsEl.addEventListener('click', (event) => {
    const button = event.target.closest('[data-recruitment-search-id]');
    if (!button) return;
    resultsEl.hidden = true;
    focusResult(Number(button.dataset.recruitmentSearchId));
  });

  document.addEventListener('click', (event) => {
    if (!shell.contains(event.target) && terms().length) resultsEl.hidden = true;
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && document.activeElement === input) {
      input.value = '';
      applySearch();
      input.blur();
      return;
    }
    if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const tag = document.activeElement?.tagName;
      if (tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') {
        event.preventDefault();
        input.focus();
      }
    }
  });

  new MutationObserver(() => {
    if (terms().length) filterVisibleCards(terms());
  }).observe(list, { childList: true });

  window.addEventListener('recruitment:changed', async () => {
    loaded = false;
    await loadItems(true).catch(() => {});
    if (terms().length) applySearch();
  });
})();
