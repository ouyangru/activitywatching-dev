/* 开发者视图：调试日志流（类别筛选 / 搜索 / 自动刷新 / 展开全文）。 */
window.DevLog = (() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
  const clock = value => { try { return new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value)); } catch { return ''; } };
  const POLL_MS = 3000;

  const KIND_LABELS = {http:'API 请求', ingest:'采集上传', agent_inject:'记忆注入', agent_input:'Agent 输入', agent_output:'Agent 输出'};

  function summaryLine(entry) {
    switch (entry.kind) {
      case 'http': return `${entry.method || ''} ${entry.path || ''} · ${entry.status ?? ''}${entry.elapsed_ms != null ? ` · ${entry.elapsed_ms}ms` : ''}`;
      case 'ingest': {
        const devices = (entry.devices || []).map(d => Array.isArray(d) ? d[0] : d).join(', ');
        const days = (entry.days || []).join(', ');
        return `${devices || '无设备'} · 接受 ${entry.accepted ?? 0} / 重复 ${entry.duplicates ?? 0}${days ? ` · ${days}` : ''}`;
      }
      case 'agent_inject': {
        const parts = (entry.items || []).map(item => `${item.process} ${item.app_facts}条记忆${(item.project_facts || []).length ? ` + project_fact(${item.project_facts.join(',')})` : ''}`);
        return `${entry.day || ''} · ${parts.join('；')}`;
      }
      case 'agent_input': return `${entry.llm_kind || ''} · ${entry.model || ''} · 请求 ${entry.request_id || ''}`;
      case 'agent_output': return `${entry.llm_kind || ''} · ${entry.status || ''}${entry.elapsed_ms != null ? ` · ${entry.elapsed_ms}ms` : ''}${entry.error ? ` · ${entry.error}` : ''}`;
      default: return '';
    }
  }

  function bodyHtml(entry) {
    if (entry.kind === 'agent_input') {
      return `<h4>SYSTEM</h4><pre>${escape(entry.system)}</pre><h4>USER</h4><pre>${escape(entry.user)}</pre>`;
    }
    if (entry.kind === 'agent_output') {
      const head = `<h4>OUTPUT · ${escape(entry.status)}${entry.elapsed_ms != null ? ` · ${escape(entry.elapsed_ms)}ms` : ''}${entry.error ? ` · ${escape(entry.error)}` : ''}</h4>`;
      return `${head}<pre>${escape(entry.output)}</pre>`;
    }
    if (entry.kind === 'agent_inject') {
      const rows = (entry.items || []).map(item =>
        `<h4>${escape(item.process)}</h4><pre>应用记忆 ${escape(item.app_facts)} 条\nproject_fact：${escape((item.project_facts || []).join('、') || '无')}</pre>`).join('');
      return rows || '';
    }
    if (entry.kind === 'ingest') {
      const devices = (entry.devices || []).map(d => Array.isArray(d) ? `${d[0]}（${d[1]}）` : String(d)).join('\n');
      return `<pre>设备：${escape(devices || '无')}\n接受 ${escape(entry.accepted ?? 0)} 条，重复 ${escape(entry.duplicates ?? 0)} 条\n重建片段 ${escape(entry.segments_rebuilt ?? 0)} 个，涉及 ${escape((entry.days || []).join('、') || '无')}</pre>`;
    }
    return '';
  }

  function renderEntry(entry, open) {
    const badge = KIND_LABELS[entry.kind] ? entry.kind : '';
    return `<article class="devlog-entry${open ? ' is-open' : ''}" data-entry-id="${escape(entry.id)}">`
      + `<div class="devlog-entry-head"><span class="devlog-entry-id">#${escape(entry.id)}</span><span class="devlog-time">${escape(clock(entry.ts))}</span>`
      + `<span class="devlog-badge ${escape(badge)}">${escape(KIND_LABELS[entry.kind] || entry.kind)}</span>`
      + `<span class="devlog-summary">${escape(summaryLine(entry))}</span></div>`
      + `<div class="devlog-body">${bodyHtml(entry)}</div></article>`;
  }

  function matchesSearch(entry, query) {
    if (!query) return true;
    try { return JSON.stringify(entry).toLowerCase().includes(query.toLowerCase()); } catch { return true; }
  }

  async function main() {
    const $ = id => document.getElementById(id);
    const stream = $('devStream'), state = $('devState'), status = $('devStatus'), notice = $('devNotice');
    let kind = '', query = '', entries = [], open = new Set(), latestId = 0, enabled = null, timer = null;

    const setStatus = text => { if (text) status.innerHTML = text; else status.textContent = ''; };

    async function loadAgentStatus() {
      try {
        const agent = await (window.ActivityUI ? ActivityUI.json('/api/v1/agent/status') : fetch('/api/v1/agent/status').then(r => r.json()));
        setStatus(`Agent：<b>${agent.enabled ? `启用（${agent.model}）` : '未启用'}</b> · 判断缓存 <b>${agent.evidence_count}</b> 条 · 记忆 <b>${agent.memory_count}</b> 条${agent.cooldown_seconds > 0 ? ` · 熔断冷却 ${agent.cooldown_seconds}s` : ''}`);
      } catch { setStatus('Agent 状态不可用'); }
    }

    async function fetchEntries() {
      if (enabled === false) return;
      const url = `/api/v1/debug/logs?after_id=${latestId}&limit=100` + (kind ? `&kind=${encodeURIComponent(kind)}` : '');
      const data = await (window.ActivityUI ? ActivityUI.json(url) : fetch(url).then(r => r.json()));
      if (!data.enabled) {
        enabled = false;
        stopPolling();
        renderDisabled();
        return;
      }
      enabled = true;
      if (latestId === 0) entries = [];
      const incoming = (data.entries || []).filter(e => !entries.some(x => x.id === e.id));
      if (incoming.length) {
        entries = [...incoming, ...entries].slice(0, 200);
        latestId = Math.max(latestId, data.latest_id || 0);
      } else if (data.latest_id) {
        latestId = Math.max(latestId, data.latest_id);
      }
      render();
    }

    function visibleEntries() { return entries.filter(e => matchesSearch(e, query)); }

    function stateLine() {
      state.textContent = `共 ${entries.length} 条${query ? `，匹配 ${visibleEntries().length} 条` : ''}（id 至 ${latestId}）`;
    }

    function render() {
      const list = visibleEntries();
      if (!list.length) { stream.innerHTML = '<p class="devlog-empty">暂无日志。触发一次采集上传或 Agent 增强后刷新。</p>'; }
      else {
        stream.innerHTML = list.map(e => renderEntry(e, open.has(e.id))).join('');
        stream.querySelectorAll('.devlog-entry-head').forEach(head => {
          head.onclick = () => {
            const id = Number(head.parentElement.dataset.entryId);
            open.has(id) ? open.delete(id) : open.add(id);
            head.parentElement.classList.toggle('is-open');
          };
        });
      }
      stateLine();
    }

    function renderDisabled() {
      stream.innerHTML = '<p class="devlog-empty">开发者视图未启用。</p>';
      notice.textContent = '生产环境默认关闭：在服务器环境配置中设置 ACTIVITYWATCH_DEBUG_VIEW=1 并重启后开启。开发环境默认开启。';
      state.textContent = '';
    }

    async function refresh() { try { await fetchEntries(); } catch (error) { state.textContent = `加载失败：${error.message}`; } }

    function startPolling() { stopPolling(); timer = setInterval(() => { if (!document.hidden && $('devAuto').checked) refresh(); }, POLL_MS); }
    function stopPolling() { if (timer) { clearInterval(timer); timer = null; } }

    $('devKindBar').querySelectorAll('button').forEach(button => {
      button.onclick = () => {
        $('devKindBar').querySelectorAll('button').forEach(b => b.classList.toggle('is-active', b === button));
        kind = button.dataset.kind; entries = []; open = new Set(); latestId = 0; render(); refresh();
      };
    });
    $('devSearch').oninput = () => { query = $('devSearch').value.trim(); render(); };
    $('devRefresh').onclick = refresh;
    $('devClear').onclick = async () => {
      try {
        await (window.ActivityUI ? ActivityUI.json('/api/v1/debug/logs', {method: 'DELETE'}) : fetch('/api/v1/debug/logs', {method: 'DELETE'}));
        entries = []; open = new Set(); latestId = 0; render();
      } catch (error) { state.textContent = `清空失败：${error.message}`; }
    };
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });

    await loadAgentStatus();
    await refresh();
    startPolling();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', main);
  else main();

  return {renderEntry, summaryLine, matchesSearch, bodyHtml};
})();
