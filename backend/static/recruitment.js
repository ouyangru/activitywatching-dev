(() => {
  const listEl = document.getElementById('recruitmentList');
  const logEl = document.getElementById('mailLogList');
  const stateEl = document.getElementById('recruitmentState');
  const scanButton = document.getElementById('scanMailButton');
  const refreshButton = document.getElementById('refreshRecruitmentButton');
  const dialog = document.getElementById('editRecruitmentDialog');
  const form = document.getElementById('editRecruitmentForm');
  const toast = document.getElementById('toast');
  const summaryToday = document.getElementById('summaryToday');
  const summaryThreeDays = document.getElementById('summaryThreeDays');
  const summaryUncertain = document.getElementById('summaryUncertain');
  const summaryNext = document.getElementById('summaryNext');
  let filter = 'active';
  let allItems = [];
  let mailConfig = null;
  let editingStatus = 'pending';

  const typeLabel = { written_test: '笔试', assessment: '测评', interview: '面试', other: '其他' };
  const modeLabel = { fixed_time: '固定时间', deadline: '截止事项', uncertain: '待确认' };
  const statusLabel = { pending: '待处理', uncertain: '待确认', done: '已完成', cancelled: '已取消', expired: '已过期' };

  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('show');
    window.setTimeout(() => toast.classList.remove('show'), 2200);
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function formatDate(raw) {
    if (!raw) return '时间待确认';
    if (!raw.includes('T')) {
      const [, m, d] = raw.split('-');
      return `${Number(m)}月${Number(d)}日`;
    }
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return raw;
    return new Intl.DateTimeFormat('zh-CN', {
      month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(date);
  }

  function dayDistance(raw) {
    if (!raw) return null;
    const target = raw.includes('T') ? new Date(raw) : new Date(`${raw}T23:59:59`);
    if (Number.isNaN(target.getTime())) return null;
    return (target.getTime() - Date.now()) / 86400000;
  }

  function filteredItems() {
    if (filter === 'all') return allItems;
    if (filter === 'active') return allItems.filter((item) => item.status === 'pending');
    return allItems.filter((item) => item.status === filter);
  }

  function actionButtons(item, uncertain) {
    const terminal = item.status === 'done' || item.status === 'cancelled';
    const actionLink = item.action_url
      ? `<a class="ghost-button" href="${escapeHtml(item.action_url)}" target="_blank" rel="noopener noreferrer">打开链接</a>`
      : '';
    const editButton = `<button class="ghost-button" type="button" data-action="edit" data-id="${item.id}">${uncertain ? '确认时间' : '修改'}</button>`;
    if (terminal) {
      return `${actionLink}${editButton}<button class="ghost-button" type="button" data-action="restore" data-id="${item.id}">恢复待办</button>`;
    }
    return `${actionLink}${editButton}<button class="ghost-button" type="button" data-action="complete" data-id="${item.id}">✓ 已完成</button><button class="ghost-button recruitment-cancel-button" type="button" data-action="cancel" data-id="${item.id}">取消事项</button>`;
  }

  function renderItems() {
    const items = filteredItems();
    if (!items.length) {
      if (mailConfig && !mailConfig.mail_configured) {
        listEl.innerHTML = '<div class="empty-recruitment">QQ 邮箱尚未完成配置，因此还没有可同步的秋招事项。请先在服务器环境中配置 QQ_EMAIL 与 QQ_EMAIL_AUTH_CODE。</div>';
      } else if (!allItems.length) {
        listEl.innerHTML = '<div class="empty-recruitment">邮箱已连接，但当前还没有识别到秋招事项。可以点击“立即检查 QQ 邮箱”主动扫描最近邮件。</div>';
      } else {
        listEl.innerHTML = '<div class="empty-recruitment">当前筛选条件下没有事项。</div>';
      }
      return;
    }
    listEl.innerHTML = items.map((item) => {
      const rawTime = item.deadline_at || item.start_at;
      const distance = dayDistance(rawTime);
      const urgent = item.status === 'pending' && distance !== null && distance >= 0 && distance <= 1;
      const uncertain = item.status === 'uncertain';
      const classes = [
        'recruitment-card',
        urgent ? 'is-urgent' : '',
        uncertain ? 'is-uncertain' : '',
        item.status === 'done' ? 'is-done' : '',
        item.status === 'cancelled' ? 'is-cancelled' : '',
      ].filter(Boolean).join(' ');
      const timePrefix = item.mode === 'fixed_time' ? '开始' : item.mode === 'deadline' ? '截止' : '待确认';
      return `
        <article class="${classes}">
          <div class="recruitment-card-head">
            <div class="recruitment-card-title">
              <h3>${escapeHtml(item.company || '未知公司')} · ${escapeHtml(typeLabel[item.item_type] || '秋招事项')}</h3>
              <p>${escapeHtml(item.title || item.source_subject || '')}</p>
              <div class="recruitment-badges">
                <span class="recruitment-badge emphasis status-${escapeHtml(item.status)}">${escapeHtml(statusLabel[item.status] || item.status)}</span>
                <span class="recruitment-badge">${escapeHtml(modeLabel[item.mode] || item.mode)}</span>
              </div>
            </div>
            <div class="recruitment-time"><small>${timePrefix}</small><strong>${escapeHtml(formatDate(rawTime))}</strong></div>
          </div>
          ${item.extraction_note ? `<p class="recruitment-card-note">${escapeHtml(item.extraction_note)}</p>` : ''}
          <div class="recruitment-card-actions">${actionButtons(item, uncertain)}</div>
        </article>`;
    }).join('');
  }

  function formatLogTime(raw) {
    if (!raw) return '';
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return raw;
    return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
  }

  function renderLogs(logs) {
    if (!logs.length) {
      if (mailConfig && !mailConfig.mail_configured) {
        logEl.innerHTML = '<div class="empty-recruitment">邮箱未配置，暂时不会产生邮件处理记录。</div>';
      } else {
        logEl.innerHTML = '<div class="empty-recruitment">还没有邮件处理记录。</div>';
      }
      return;
    }
    logEl.innerHTML = logs.map((log) => `
      <article class="mail-log-item">
        <strong>${escapeHtml(log.subject || '无标题邮件')}</strong>
        <p>${escapeHtml(log.detail || log.action)}</p>
        <div class="mail-log-meta"><span>${escapeHtml(log.action)}</span><span>${escapeHtml(formatLogTime(log.created_at))}</span></div>
      </article>`).join('');
  }

  function fillSummary(data) {
    summaryToday.textContent = String(data.today ?? 0);
    summaryThreeDays.textContent = String(data.three_days ?? 0);
    summaryUncertain.textContent = String(data.uncertain ?? 0);
    summaryNext.textContent = data.next_item
      ? `${data.next_item.company || '未知公司'} · ${formatDate(data.next_item.deadline_at || data.next_item.start_at)}`
      : '暂无';
  }

  function configMessage(config, count) {
    if (!config.mail_configured) {
      const missing = [];
      if (!config.email_configured) missing.push('QQ_EMAIL');
      if (!config.auth_code_configured) missing.push('QQ_EMAIL_AUTH_CODE');
      return `QQ 邮箱未配置：缺少 ${missing.join(' / ')}。当前页面只能显示已有数据库事项，不会自动拉取新邮件。`;
    }
    const intervalMinutes = Math.max(1, Math.round(Number(config.scan_interval_seconds || 600) / 60));
    const account = config.account_hint ? ` ${config.account_hint}` : '';
    const scanState = config.auto_scan_enabled ? `自动扫描每 ${intervalMinutes} 分钟一次` : '自动扫描已关闭';
    return `QQ 邮箱已连接${account}，${scanState}；当前共 ${count} 条秋招事项。`;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...options });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const payload = await response.json();
        detail = payload.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    return response.json();
  }

  async function updateStatus(item, status) {
    await api(`/api/v1/recruitment/items/${item.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
  }

  async function loadAll() {
    stateEl.textContent = '正在加载秋招事项…';
    try {
      const [itemsPayload, summaryPayload, logsPayload, configPayload] = await Promise.all([
        api('/api/v1/recruitment/items'),
        api('/api/v1/recruitment/summary'),
        api('/api/v1/recruitment/mail-log?limit=80'),
        api('/api/v1/recruitment/config-status'),
      ]);
      allItems = itemsPayload.items || [];
      mailConfig = configPayload;
      fillSummary(summaryPayload);
      renderItems();
      renderLogs(logsPayload.logs || []);
      stateEl.textContent = configMessage(configPayload, allItems.length);
    } catch (error) {
      stateEl.textContent = `加载失败：${error.message}`;
    }
  }

  async function scanMail() {
    scanButton.disabled = true;
    scanButton.textContent = '正在检查…';
    stateEl.textContent = '正在连接 QQ 邮箱并检查最近邮件…';
    try {
      if (mailConfig && !mailConfig.mail_configured) {
        throw new Error('QQ 邮箱尚未配置，请先在服务器环境变量中设置 QQ_EMAIL 与 QQ_EMAIL_AUTH_CODE');
      }
      const result = await api('/api/v1/recruitment/scan', { method: 'POST' });
      showToast(`新增 ${result.imported} 条，待确认 ${result.uncertain} 条`);
      await loadAll();
    } catch (error) {
      stateEl.textContent = `检查邮箱失败：${error.message}`;
    } finally {
      scanButton.disabled = false;
      scanButton.textContent = '立即检查 QQ 邮箱';
    }
  }

  function openEdit(item) {
    editingStatus = item.status || 'pending';
    document.getElementById('editRecruitmentId').value = item.id;
    document.getElementById('editCompany').value = item.company || '';
    document.getElementById('editTitle').value = item.title || '';
    document.getElementById('editType').value = item.item_type || 'other';
    document.getElementById('editMode').value = item.mode || 'uncertain';
    document.getElementById('editDeadline').value = item.deadline_at || '';
    document.getElementById('editStart').value = item.start_at || '';
    document.getElementById('editUrl').value = item.action_url || '';
    dialog.showModal();
  }

  document.querySelectorAll('[data-recruitment-filter]').forEach((button) => {
    button.addEventListener('click', () => {
      filter = button.dataset.recruitmentFilter;
      document.querySelectorAll('[data-recruitment-filter]').forEach((candidate) => candidate.classList.toggle('is-active', candidate === button));
      renderItems();
    });
  });

  listEl.addEventListener('click', async (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const id = Number(button.dataset.id);
    const item = allItems.find((candidate) => candidate.id === id);
    if (!item) return;

    if (button.dataset.action === 'edit') {
      openEdit(item);
      return;
    }

    button.disabled = true;
    try {
      if (button.dataset.action === 'complete') {
        await updateStatus(item, 'done');
        showToast('已标记完成');
      } else if (button.dataset.action === 'cancel') {
        const name = item.company || item.title || '该事项';
        if (!window.confirm(`确认取消「${name}」？取消后不会再计入截止提醒，可随时从“已取消”中恢复。`)) {
          button.disabled = false;
          return;
        }
        await updateStatus(item, 'cancelled');
        showToast('事项已取消');
      } else if (button.dataset.action === 'restore') {
        await updateStatus(item, item.mode === 'uncertain' ? 'uncertain' : 'pending');
        showToast('已恢复为待办');
      } else {
        button.disabled = false;
        return;
      }
      await loadAll();
    } catch (error) {
      button.disabled = false;
      showToast(`操作失败：${error.message}`);
    }
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const id = Number(document.getElementById('editRecruitmentId').value);
    const mode = document.getElementById('editMode').value;
    const terminalStatus = editingStatus === 'done' || editingStatus === 'cancelled' ? editingStatus : null;
    const payload = {
      company: document.getElementById('editCompany').value.trim(),
      title: document.getElementById('editTitle').value.trim(),
      item_type: document.getElementById('editType').value,
      mode,
      status: terminalStatus || (mode === 'uncertain' ? 'uncertain' : 'pending'),
      deadline_at: document.getElementById('editDeadline').value.trim() || null,
      start_at: document.getElementById('editStart').value.trim() || null,
      action_url: document.getElementById('editUrl').value.trim() || null,
    };
    try {
      await api(`/api/v1/recruitment/items/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      dialog.close();
      showToast('事项已更新');
      await loadAll();
    } catch (error) {
      showToast(`保存失败：${error.message}`);
    }
  });

  document.getElementById('cancelRecruitmentEdit').addEventListener('click', () => dialog.close());
  scanButton.addEventListener('click', scanMail);
  refreshButton.addEventListener('click', loadAll);
  loadAll();
})();
