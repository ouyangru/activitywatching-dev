const KNOWLEDGE_BASE_URL = 'https://my-interview-notes.aosikagirl23.workers.dev';

const SECTION_META = {
  home: {
    eyebrow: 'PERSONAL OPERATING SYSTEM',
    title: '总控台',
    subtitle: '时间、知识与秋招状态的统一入口。',
  },
  activity: {
    eyebrow: 'ACTIVITY / OVERVIEW',
    title: '行迹',
    subtitle: '今日时间线、设备活动与分类分布。',
    internalPath: '/',
  },
  daily: {
    eyebrow: 'ACTIVITY / DAILY',
    title: '日报',
    subtitle: '回看一天的节奏、分布与值得调整的时段。',
    internalPath: '/daily',
  },
  compare: {
    eyebrow: 'ACTIVITY / HISTORY',
    title: '多日趋势',
    subtitle: '把几天放在一起比较时间投入与活动节奏。',
    internalPath: '/compare',
  },
  interview: {
    eyebrow: 'INTERVIEW KNOWLEDGE BASE',
    title: '秋招面经',
    subtitle: '原始资料 + 个人增量知识 + 真实面试复盘。',
    externalUrl: KNOWLEDGE_BASE_URL,
  },
  recruiting: {
    eyebrow: 'AUTUMN RECRUITING',
    title: '秋招事务',
    subtitle: 'QQ 邮件识别、笔试测评截止、面试安排与待确认事项。',
    internalPath: '/recruitment',
  },
  projects: {
    eyebrow: 'PROJECTS & TOOLS',
    title: '项目与工具',
    subtitle: '把个人项目、代码仓库与学习入口集中管理。',
    placeholder: 'projects',
  },
  debug: {
    eyebrow: 'SYSTEM / DEBUG',
    title: '调试日志',
    subtitle: '查看 HTTP、采集与 Agent 输入输出，方便复现和定位问题。',
    internalPath: '/devlog',
  },
};

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
  homeView: document.getElementById('homeView'),
  frameView: document.getElementById('frameView'),
  placeholderView: document.getElementById('placeholderView'),
  placeholderContent: document.getElementById('placeholderContent'),
  frame: document.getElementById('workspaceFrame'),
  frameLoading: document.getElementById('frameLoading'),
  workspaceLabel: document.getElementById('workspaceLabel'),
  workspaceTitle: document.getElementById('workspaceTitle'),
  sectionEyebrow: document.getElementById('sectionEyebrow'),
  sectionTitle: document.getElementById('sectionTitle'),
  sectionSubtitle: document.getElementById('sectionSubtitle'),
  openStandalone: document.getElementById('openStandalone'),
  frameStandalone: document.getElementById('frameStandalone'),
  reloadFrame: document.getElementById('reloadFrame'),
};

const routeOverrides = new Map();
let currentSection = 'home';
let standaloneUrl = '';
let frameLoadToken = 0;

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
  els.date.textContent = new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit' }).format(now);
  els.time.textContent = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }).format(now);
  els.weekday.textContent = new Intl.DateTimeFormat('zh-CN', { weekday: 'short' }).format(now);
}

function setStatus(mode, label) {
  els.status.classList.remove('is-online', 'is-offline');
  if (mode) els.status.classList.add(mode);
  els.status.querySelector('b').textContent = label;
}

async function fetchJson(path) {
  const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store' });
  if (response.status === 401) throw new Error('AUTH');
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json();
}

async function reportClientError(error, details = {}) {
  const message = error instanceof Error ? error.message : String(error || 'client error');
  const stack = error instanceof Error ? error.stack || '' : String(details.stack || '');
  try {
    await fetch('/api/v1/debug/client-error', {
      method: 'POST',
      credentials: 'same-origin',
      keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: details.action || 'hub_client_error',
        message,
        stack,
        page: details.page || location.pathname,
        source: details.source || '',
        line: details.line || null,
        column: details.column || null,
      }),
    });
  } catch (_) {
    // Debug reporting must never break the Hub itself.
  }
}

async function loadHubSummary() {
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
    els.focusRate.textContent = summary.total_seconds ? `${Math.round(focusSeconds * 100 / summary.total_seconds)}%` : '0%';
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
      els.currentBehavior.textContent = '登录后即可显示今日数据';
      return;
    }
    setStatus('is-offline', '暂未连接');
    els.currentCategory.textContent = '离线';
    els.currentBehavior.textContent = '总控入口仍可使用，实时数据暂不可用';
    reportClientError(error, { action: 'hub_summary_load' });
  }
}

function setLoading(visible, label = '正在加载工作区…') {
  els.frameLoading.querySelector('p').textContent = label;
  els.frameLoading.classList.toggle('is-hidden', !visible);
}

function routeForUrl(url) {
  if (url.origin !== location.origin) return null;
  if (url.pathname === '/') return 'activity';
  if (url.pathname === '/daily') return 'daily';
  if (url.pathname === '/compare') return 'compare';
  if (url.pathname === '/recruitment') return 'recruiting';
  if (url.pathname === '/devlog') return 'debug';
  if (url.pathname === '/hub') return 'home';
  return null;
}

function applyEmbeddedPresentation(doc) {
  let style = doc.getElementById('personal-hub-embed-style');
  if (!style) {
    style = doc.createElement('style');
    style.id = 'personal-hub-embed-style';
    style.textContent = `
      html, body { background: #131a22 !important; }
      .topbar { display: none !important; }
      .shell { width: min(1440px, calc(100% - 28px)) !important; padding-top: 12px !important; padding-bottom: 28px !important; }
      .ambient { display: none !important; }
      @media (max-width: 700px) { .shell { width: calc(100% - 16px) !important; } }
    `;
    doc.head?.appendChild(style);
  }
}

function bindEmbeddedNavigation(doc) {
  if (!doc || doc.documentElement.dataset.hubNavigationBound) return;
  doc.documentElement.dataset.hubNavigationBound = '1';
  doc.addEventListener('click', (event) => {
    const anchor = event.target.closest?.('a[href]');
    if (!anchor || anchor.target === '_blank' || event.defaultPrevented) return;
    try {
      const url = new URL(anchor.href, doc.baseURI);
      const section = routeForUrl(url);
      if (!section) return;
      event.preventDefault();
      if (section !== 'home') routeOverrides.set(section, `${url.pathname}${url.search}`);
      navigate(section);
    } catch (error) {
      reportClientError(error, { action: 'hub_embedded_navigation', page: doc.location?.pathname || '' });
    }
  });
}

function bindEmbeddedErrors(win, doc) {
  if (!win || win.__PERSONAL_HUB_ERROR_BOUND__) return;
  win.__PERSONAL_HUB_ERROR_BOUND__ = true;
  win.addEventListener('error', (event) => {
    reportClientError(event.error || event.message, {
      action: 'window_error',
      page: doc.location?.pathname || '',
      source: event.filename || '',
      line: event.lineno,
      column: event.colno,
      stack: event.error?.stack || '',
    });
  });
  win.addEventListener('unhandledrejection', (event) => {
    reportClientError(event.reason || 'Unhandled promise rejection', {
      action: 'unhandled_rejection',
      page: doc.location?.pathname || '',
      stack: event.reason?.stack || '',
    });
  });
}

function decorateLoadedInternalFrame(token, section) {
  if (token !== frameLoadToken || currentSection !== section) return;
  let win;
  let doc;
  try {
    win = els.frame.contentWindow;
    doc = els.frame.contentDocument;
    if (!win || !doc) return;
    if (win.location.pathname === '/login') {
      location.assign('/login');
      return;
    }
  } catch (error) {
    setLoading(false);
    reportClientError(error, { action: 'hub_frame_access' });
    return;
  }
  win.__PERSONAL_HUB_EMBEDDED__ = true;
  applyEmbeddedPresentation(doc);
  bindEmbeddedNavigation(doc);
  bindEmbeddedErrors(win, doc);
  setLoading(false);
}

function loadInternalPage(path, section, force = false) {
  const token = ++frameLoadToken;
  standaloneUrl = `${location.origin}${path}`;
  setLoading(true, '正在加载工作区…');
  els.frame.removeAttribute('srcdoc');
  els.frame.onload = () => decorateLoadedInternalFrame(token, section);

  try {
    const url = new URL(path, location.origin);
    if (force) url.searchParams.set('_hub_reload', String(Date.now()));
    els.frame.src = `${url.pathname}${url.search}${url.hash}`;
  } catch (error) {
    setLoading(false);
    reportClientError(error, { action: 'hub_internal_url', page: path });
  }
}

function loadExternalPage(url) {
  const token = ++frameLoadToken;
  standaloneUrl = url;
  setLoading(true, '正在加载面试知识库…');
  els.frame.removeAttribute('srcdoc');
  els.frame.onload = () => {
    if (token === frameLoadToken) setLoading(false);
  };
  els.frame.src = url;
  window.setTimeout(() => {
    if (token === frameLoadToken) setLoading(false);
  }, 4500);
}

function placeholderHtml() {
  return `
    <section class="placeholder-panel">
      <p class="eyebrow">PROJECTS & TOOLS</p>
      <h2>项目与工具</h2>
      <p>这里只做入口聚合，不把多个仓库硬塞进同一个代码库。每个系统继续独立维护，总控台负责把它们组织到一起。</p>
      <div class="placeholder-grid">
        <article class="placeholder-card"><span>Activity</span><h3>ActivityWatching</h3><p>跨设备活动采集、行为理解、日报与趋势分析。</p></article>
        <article class="placeholder-card"><span>Knowledge</span><h3>Interview Notes</h3><p>Obsidian / Markdown 为源，持续沉淀秋招面试知识。</p></article>
        <article class="placeholder-card"><span>Next</span><h3>AI Infra / Tools</h3><p>后续可以继续接入学习进度、服务器状态与个人实验项目。</p></article>
      </div>
      <div class="placeholder-links"><a href="https://github.com/ouyangru/activitywatching-dev" target="_blank" rel="noreferrer">Activity 仓库 ↗</a><a href="https://github.com/ouyangru/my-interview-notes" target="_blank" rel="noreferrer">Interview 仓库 ↗</a></div>
    </section>`;
}

function updateNavigation(section) {
  document.querySelectorAll('[data-section]').forEach((link) => {
    link.classList.toggle('is-active', link.dataset.section === section && link.closest('.sidebar-nav'));
  });
}

function updateHeader(section) {
  const meta = SECTION_META[section] || SECTION_META.home;
  els.sectionEyebrow.textContent = meta.eyebrow;
  els.sectionTitle.textContent = meta.title;
  els.sectionSubtitle.textContent = meta.subtitle;
  document.title = `${meta.title} · Personal Hub`;
}

function showView(view) {
  els.homeView.hidden = view !== 'home';
  els.frameView.hidden = view !== 'frame';
  els.placeholderView.hidden = view !== 'placeholder';
}

function renderSection(section, force = false) {
  const meta = SECTION_META[section] || SECTION_META.home;
  currentSection = SECTION_META[section] ? section : 'home';
  updateNavigation(currentSection);
  updateHeader(currentSection);
  standaloneUrl = '';
  els.openStandalone.hidden = true;

  if (currentSection === 'home') {
    showView('home');
    return;
  }

  if (meta.internalPath || meta.externalUrl) {
    showView('frame');
    els.workspaceLabel.textContent = meta.externalUrl ? 'External knowledge workspace' : 'Embedded workspace';
    els.workspaceTitle.textContent = meta.title;
    const source = routeOverrides.get(currentSection) || meta.internalPath || meta.externalUrl;
    standaloneUrl = meta.externalUrl || `${location.origin}${source}`;
    els.openStandalone.hidden = false;
    if (meta.externalUrl) loadExternalPage(meta.externalUrl);
    else loadInternalPage(source, currentSection, force);
    return;
  }

  showView('placeholder');
  els.placeholderContent.innerHTML = placeholderHtml();
}

function navigate(section) {
  const next = SECTION_META[section] ? section : 'home';
  if (location.hash !== `#${next}`) {
    location.hash = next;
  } else {
    renderSection(next);
  }
}

function sectionFromHash() {
  const section = location.hash.replace(/^#/, '').split('?')[0];
  return SECTION_META[section] ? section : 'home';
}

function openStandalone() {
  if (standaloneUrl) window.open(standaloneUrl, '_blank', 'noopener,noreferrer');
}

els.reloadFrame.addEventListener('click', () => {
  const meta = SECTION_META[currentSection];
  if (!meta) return;
  if (meta.externalUrl) loadExternalPage(meta.externalUrl);
  else if (meta.internalPath) {
    const source = routeOverrides.get(currentSection) || meta.internalPath;
    loadInternalPage(source, currentSection, true);
  }
});
els.frameStandalone.addEventListener('click', openStandalone);
els.openStandalone.addEventListener('click', openStandalone);

document.addEventListener('click', (event) => {
  const link = event.target.closest('[data-section]');
  if (!link) return;
  const section = link.dataset.section;
  if (!SECTION_META[section]) return;
  event.preventDefault();
  navigate(section);
});

window.addEventListener('error', (event) => {
  reportClientError(event.error || event.message, {
    action: 'hub_window_error',
    source: event.filename || '',
    line: event.lineno,
    column: event.colno,
    stack: event.error?.stack || '',
  });
});
window.addEventListener('unhandledrejection', (event) => {
  reportClientError(event.reason || 'Unhandled promise rejection', {
    action: 'hub_unhandled_rejection',
    stack: event.reason?.stack || '',
  });
});
window.addEventListener('hashchange', () => renderSection(sectionFromHash()));

updateClock();
setInterval(updateClock, 30_000);
loadHubSummary();
setInterval(() => {
  if (!document.hidden) loadHubSummary();
}, 60_000);

if (!location.hash) history.replaceState(null, '', `${location.pathname}#home`);
renderSection(sectionFromHash());
