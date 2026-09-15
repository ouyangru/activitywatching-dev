/* 开发者视图：调试日志流（类型 / 模块 / 异常筛选 / 搜索 / 自动刷新）。 */
window.DevLog = (() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#39;'}[x]));
  const clock = value => { try { return new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value)); } catch { return ''; } };
  const POLL_MS = 3000;

  const KIND_LABELS = {
    http:'API 请求', ingest:'采集上传', agent_inject:'记忆注入',
    agent_input:'Agent 输入', agent_output:'Agent 输出', event:'模块事件'
  };
  const MODULE_LABELS = {
    activity:'活动分析', frontend:'前端', recruitment:'秋招事项', feishu:'招聘进度', calendar:'日历',
    mail:'邮件扫描', bridge:'招聘↔日历', agent:'Agent', ingest:'采集', system:'系统'
  };

  function isSlow(entry) {
    return entry.elapsed_ms != null && Number(entry.elapsed_ms) >= 200;
  }

  function isAbnormal(entry) {
    return entry.level === 'error' || entry.level === 'warn' || Number(entry.status) >= 400 || !!entry.error || !!entry.exception;
  }

  function summaryLine(entry) {
    switch (entry.kind) {
      case 'http': return `${entry.method || ''} ${entry.path || ''} · HTTP ${entry.status ?? ''}${entry.elapsed_ms != null ? ` · ${entry.elapsed_ms}ms` : ''}${entry.error ? ` · ${entry.error}` : ''}`;
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
      case 'event': {
        if (entry.module === 'agent' && entry.action === 'llm_usage') {
          const cost = entry.pricing_configured ? ` · ≈$${Number(entry.estimated_cost_usd || 0).toFixed(6)}` : '';
          const batch = entry.batch_size != null ? ` · batch ${entry.batch_size}` : '';
          const waste = entry.possible_waste ? ` · 疑似浪费：${(entry.waste_reasons || []).join(', ')}` : '';
          return `LLM ${entry.llm_kind || ''} · ≈${entry.estimated_total_tokens ?? 0} tokens${batch}${cost}${waste}`;
        }
        return `${entry.action || '事件'}${entry.status ? ` · ${entry.status}` : ''}${entry.detail ? ` · ${entry.detail}` : ''}${entry.error ? ` · ${entry.error}` : ''}`;
      }
      default: return entry.action || entry.detail || '';
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
    if (entry.kind === 'http' || entry.kind === 'event') {
      const safe = {...entry};
      delete safe.id; delete safe.ts; delete safe.kind; delete safe.module; delete safe.level;
      return `<pre>${escape(JSON.stringify(safe, null, 2))}</pre>`;
    }
    return '';
  }

  function renderEntry(entry, open) {
    const kindBadge = KIND_LABELS[entry.kind] ? entry.kind : '';
    const moduleLabel = MODULE_LABELS[entry.module] || entry.module || '系统';
    const level = entry.level || 'info';
    const flags = `${level === 'error' ? ' is-error' : ''}${level === 'warn' ? ' is-warn' : ''}${isSlow(entry) ? ' is-slow' : ''}`;
    const alert = level === 'error' ? '<span class="devlog-alert is-error">异常</span>' : level === 'warn' ? '<span class="devlog-alert is-warn">警告</span>' : isSlow(entry) ? '<span class="devlog-alert is-slow">慢</span>' : '';
    return `<article class="devlog-entry${open ? ' is-open' : ''}${flags}" data-entry-id="${escape(entry.id)}">`
      + `<div class="devlog-entry-head"><span class="devlog-entry-id">#${escape(entry.id)}</span><span class="devlog-time">${escape(clock(entry.ts))}</span>`
      + `<span class="devlog-module">${escape(moduleLabel)}</span>`
      + `<span class="devlog-badge ${escape(kindBadge)}">${escape(KIND_LABELS[entry.kind] || entry.kind)}</span>`
      + alert
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
    let kind = '', module = '', query = '', onlyAbnormal = false, onlySlow = false;
    let entries = [], open = new Set(), latestId = 0, enabled = null, timer = null;

    const setStatus = text => { if (text) status.innerHTML = text; else status.textContent = ''; };

    async function loadAgentStatus() {
      try {
        const read = url => window.ActivityUI ? ActivityUI.json(url) : fetch(url).then(r => r.json());
        const [agent, usage] = await Promise.all([read('/api/v1/agent/status'), read('/api/v1/agent/usage-summary').catch(() => null)]);
        let usageText = '';
        if (usage?.enabled) {
          const cost = usage.pricing_configured ? ` · 估算费用 <b>$${Number(usage.estimated_cost_usd || 0).toFixed(6)}</b>` : ' · 费用单价未配置';
          usageText = ` · LLM 调用 <b>${usage.calls}</b> 次 · ≈<b>${usage.estimated_total_tokens}</b> tokens · 疑似浪费 <b>${usage.possible_waste_calls}</b> 次${cost}`;
        }
        setStatus(`Agent：<b>${agent.enabled ? `启用（${agent.model}）` : '未启用'}</b> · 判断缓存 <b>${agent.evidence_count}</b> 条 · 记忆 <b>${agent.memory_count}</b> 条${agent.cooldown_seconds > 0 ? ` · 熔断冷却 ${agent.cooldown_seconds}s` : ''}${usageText}`);
      } catch { setStatus('Agent 状态不可用'); }
    }

    async function fetchEntries() {
      if (enabled === false) return;
      const url = `/api/v1/debug/logs?after_id=${latestId}&limit=200`;
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
        entries = [...incoming, ...entries].slice(0, 300);
        latestId = Math.max(latestId, data.latest_id || 0);
      } else if (data.latest_id) {
        latestId = Math.max(latestId, data.latest_id);
      }
      render();
    }

    function visibleEntries() {
      return entries.filter(entry =>
        (!kind || entry.kind === kind)
        && (!module || entry.module === module)
        && (!onlyAbnormal || isAbnormal(entry))
        && (!onlySlow || isSlow(entry))
        && matchesSearch(entry, query)
      );
    }

    function stateLine() {
      const errors = entries.filter(e => e.level === 'error').length;
      const warnings = entries.filter(e => e.level === 'warn').length;
      state.innerHTML = `共 ${entries.length} 条 · <b class="devlog-count-error">异常 ${errors}</b> · <b class="devlog-count-warn">警告 ${warnings}</b>${module ? ` · 模块 ${escape(MODULE_LABELS[module] || module)}` : ''}${kind || query || onlyAbnormal || onlySlow ? ` · 当前显示 ${visibleEntries().length} 条` : ''}（id 至 ${latestId}）`;
    }

    function render() {
      const list = visibleEntries();
      if (!list.length) { stream.innerHTML = '<p class="devlog-empty">当前筛选条件下暂无日志。</p>'; }
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

    async function refresh() { try { await Promise.all([fetchEntries(), loadAgentStatus()]); } catch (error) { state.textContent = `加载失败：${error.message}`; } }

    function startPolling() { stopPolling(); timer = setInterval(() => { if (!document.hidden && $('devAuto').checked) refresh(); }, POLL_MS); }
    function stopPolling() { if (timer) { clearInterval(timer); timer = null; } }

    $('devKindBar').querySelectorAll('button').forEach(button => {
      button.onclick = () => {
        $('devKindBar').querySelectorAll('button').forEach(b => b.classList.toggle('is-active', b === button));
        kind = button.dataset.kind;
        render();
      };
    });
    $('devModuleBar').querySelectorAll('button').forEach(button => {
      button.onclick = () => {
        $('devModuleBar').querySelectorAll('button').forEach(b => b.classList.toggle('is-active', b === button));
        module = button.dataset.module;
        render();
      };
    });
    $('devOnlyAbnormal').onchange = () => { onlyAbnormal = $('devOnlyAbnormal').checked; render(); };
    $('devOnlySlow').onchange = () => { onlySlow = $('devOnlySlow').checked; render(); };
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

  return {renderEntry, summaryLine, matchesSearch, bodyHtml, isAbnormal, isSlow};
})();
