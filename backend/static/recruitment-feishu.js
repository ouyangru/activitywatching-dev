(() => {
  const stateEl = document.getElementById('feishuState');
  const tableShell = document.getElementById('feishuTableShell');
  const proposalList = document.getElementById('feishuProposalList');
  const mappingEl = document.getElementById('feishuMapping');
  const recordCountEl = document.getElementById('feishuRecordCount');
  const pendingCountEl = document.getElementById('feishuPendingCount');
  const appliedCountEl = document.getElementById('feishuAppliedCount');
  const refreshButton = document.getElementById('feishuRefreshButton');
  const backfillButton = document.getElementById('feishuBackfillButton');
  const sourceLink = document.getElementById('feishuSourceLink');
  const dialog = document.getElementById('feishuEditDialog');
  const form = document.getElementById('feishuEditForm');
  const editTitle = document.getElementById('feishuEditTitle');
  const editFields = document.getElementById('feishuEditFields');
  const editRecordId = document.getElementById('feishuEditRecordId');
  const toast = document.getElementById('toast');

  let config = null;
  let schema = [];
  let records = [];
  let mapping = {};
  let editableNames = [];
  let proposals = [];
  let editingRecord = null;

  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('show');
    window.setTimeout(() => toast.classList.remove('show'), 2600);
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
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
    const text = await response.text();
    return text ? JSON.parse(text) : {};
  }

  function fieldText(value) {
    if (value === null || value === undefined || value === '') return '';
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (Array.isArray(value)) {
      return value.map((item) => {
        if (item && typeof item === 'object') return item.text || item.name || item.link || JSON.stringify(item);
        return String(item);
      }).filter(Boolean).join(', ');
    }
    if (typeof value === 'object') return value.text || value.name || value.link || JSON.stringify(value);
    return String(value);
  }

  function formatDateMillis(value) {
    if (value === null || value === undefined || value === '') return '';
    const numeric = Number(value);
    const date = Number.isFinite(numeric) && numeric > 10000000000 ? new Date(numeric) : new Date(String(value));
    if (Number.isNaN(date.getTime())) return fieldText(value);
    return new Intl.DateTimeFormat('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(date);
  }

  function displayField(field, value) {
    if (Number(field?.type) === 5) return formatDateMillis(value) || '—';
    const text = fieldText(value);
    return text || '—';
  }

  function orderedFields() {
    const preferred = [mapping.company, mapping.stage, mapping.latest, mapping.next].filter(Boolean);
    const preferredSet = new Set(preferred);
    return [
      ...preferred.map((name) => schema.find((field) => field.field_name === name)).filter(Boolean),
      ...schema.filter((field) => !preferredSet.has(field.field_name)),
    ];
  }

  function renderMapping() {
    const entries = [
      ['公司匹配', mapping.company],
      ['当前阶段', mapping.stage],
      ['最新动态', mapping.latest],
      ['下一节点', mapping.next],
    ];
    mappingEl.innerHTML = entries.map(([label, value]) => `
      <div class="feishu-mapping-item${value ? '' : ' is-missing'}">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value || '未匹配')}</strong>
      </div>`).join('');
  }

  function renderRecords() {
    recordCountEl.textContent = String(records.length);
    if (!config?.configured) {
      tableShell.innerHTML = '<div class="empty-recruitment">服务器尚未配置飞书自建应用。配置完成前不会访问或修改飞书表格。</div>';
      return;
    }
    if (!records.length) {
      tableShell.innerHTML = '<div class="empty-recruitment">当前视图没有读取到记录，请检查飞书应用权限、Table ID 与 View ID。</div>';
      return;
    }
    const fields = orderedFields();
    const head = fields.map((field) => `<th>${escapeHtml(field.field_name)}</th>`).join('');
    const body = records.map((record) => {
      const cells = fields.map((field) => `<td title="${escapeHtml(displayField(field, record.fields?.[field.field_name]))}">${escapeHtml(displayField(field, record.fields?.[field.field_name]))}</td>`).join('');
      return `<tr data-record-id="${escapeHtml(record.record_id)}">${cells}<td class="feishu-action-cell"><button class="ghost-button" type="button" data-edit-record="${escapeHtml(record.record_id)}">修改</button></td></tr>`;
    }).join('');
    tableShell.innerHTML = `
      <table class="feishu-table">
        <thead><tr>${head}<th>操作</th></tr></thead>
        <tbody>${body}</tbody>
      </table>`;
  }

  function diffRows(proposal) {
    const current = proposal.current_fields || {};
    const proposed = proposal.proposed_fields || {};
    const names = Object.keys(proposed);
    if (!names.length) return '<div class="feishu-proposal-empty">没有可写入字段。</div>';
    return names.map((name) => {
      const field = schema.find((candidate) => candidate.field_name === name);
      const before = displayField(field, current[name]);
      const after = displayField(field, proposed[name]);
      return `
        <div class="feishu-diff-row">
          <span>${escapeHtml(name)}</span>
          <div><del>${escapeHtml(before)}</del><b>→</b><ins>${escapeHtml(after)}</ins></div>
        </div>`;
    }).join('');
  }

  function proposalStatusLabel(status) {
    return { pending: '待审核', applied: '已写入', rejected: '已驳回' }[status] || status;
  }

  function renderProposals() {
    const pending = proposals.filter((item) => item.status === 'pending');
    const applied = proposals.filter((item) => item.status === 'applied');
    pendingCountEl.textContent = String(pending.length);
    appliedCountEl.textContent = String(applied.length);
    if (!proposals.length) {
      proposalList.innerHTML = '<div class="empty-recruitment">暂无审核建议。邮件识别出新的笔试/测评/面试阶段后，会先出现在这里。</div>';
      return;
    }
    proposalList.innerHTML = proposals.map((proposal) => {
      const sourceLabel = proposal.source === 'mail' ? '邮件识别' : '网页修改';
      const matchLabel = proposal.match_status === 'matched' ? '已匹配飞书记录' : proposal.match_status === 'ambiguous' ? '公司匹配有歧义' : proposal.match_status === 'unmatched' ? '未匹配公司' : '尚未校验';
      const actions = proposal.status === 'pending' ? `
        <button class="ghost-button" type="button" data-reject-proposal="${proposal.id}">驳回</button>
        <button class="ghost-button correction-save" type="button" data-approve-proposal="${proposal.id}" ${proposal.can_approve ? '' : 'disabled'}>批准写入</button>` : '';
      return `
        <article class="feishu-proposal-card ${proposal.status === 'pending' ? 'is-pending' : ''}">
          <div class="feishu-proposal-head">
            <div>
              <span class="recruitment-badge">${escapeHtml(sourceLabel)}</span>
              <span class="recruitment-badge status-${escapeHtml(proposal.status)}">${escapeHtml(proposalStatusLabel(proposal.status))}</span>
              <h3>${escapeHtml(proposal.company || proposal.source_title || '招聘进度修改')}</h3>
              ${proposal.stage ? `<p>${escapeHtml(proposal.stage)}${proposal.next_at ? ` · ${escapeHtml(proposal.next_at)}` : ''}</p>` : ''}
            </div>
            <small>${escapeHtml(matchLabel)}</small>
          </div>
          ${diffRows(proposal)}
          <p class="feishu-review-reason">${escapeHtml(proposal.review_reason || '')}</p>
          <div class="feishu-proposal-actions">${actions}</div>
        </article>`;
    }).join('');
  }

  function fieldInputValue(field, value) {
    const type = Number(field.type);
    if (type === 5) {
      const numeric = Number(value);
      const date = Number.isFinite(numeric) && numeric > 10000000000 ? new Date(numeric) : new Date(String(value || ''));
      if (Number.isNaN(date.getTime())) return '';
      const pad = (part) => String(part).padStart(2, '0');
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }
    if (type === 4) return Array.isArray(value) ? value.map(fieldText).join(', ') : fieldText(value);
    if (type === 7) return Boolean(value);
    return fieldText(value);
  }

  function readInputValue(field, input) {
    const type = Number(field.type);
    if (type === 7) return input.checked;
    if (type === 2) return input.value.trim() === '' ? '' : Number(input.value);
    if (type === 4) return input.value.split(',').map((part) => part.trim()).filter(Boolean);
    if (type === 5) return input.value ? new Date(input.value).getTime() : '';
    return input.value.trim();
  }

  function comparable(value) {
    if (Array.isArray(value)) return JSON.stringify(value.map(String));
    if (value === null || value === undefined) return '';
    return JSON.stringify(value);
  }

  function openEdit(record) {
    editingRecord = record;
    editRecordId.value = record.record_id;
    const company = mapping.company ? fieldText(record.fields?.[mapping.company]) : '';
    editTitle.textContent = company ? `修改 · ${company}` : '修改招聘进度';
    const editable = schema.filter((field) => editableNames.includes(field.field_name));
    editFields.innerHTML = editable.map((field) => {
      const raw = record.fields?.[field.field_name];
      const initial = fieldInputValue(field, raw);
      if (Number(field.type) === 7) {
        return `<label class="feishu-checkbox-row"><input type="checkbox" data-feishu-field="${escapeHtml(field.field_name)}" data-feishu-type="${field.type}" data-original='${escapeHtml(comparable(initial))}' ${initial ? 'checked' : ''}> ${escapeHtml(field.field_name)}</label>`;
      }
      const inputType = Number(field.type) === 5 ? 'datetime-local' : Number(field.type) === 2 ? 'number' : 'text';
      const hint = Number(field.type) === 4 ? '<small>多选值用英文逗号分隔</small>' : '';
      return `<label>${escapeHtml(field.field_name)}${hint}<input class="recruitment-input" type="${inputType}" data-feishu-field="${escapeHtml(field.field_name)}" data-feishu-type="${field.type}" data-original='${escapeHtml(comparable(initial))}' value="${escapeHtml(initial)}"></label>`;
    }).join('');
    dialog.showModal();
  }

  async function createManualProposal(event) {
    event.preventDefault();
    if (!editingRecord) return;
    const changed = {};
    editFields.querySelectorAll('[data-feishu-field]').forEach((input) => {
      const field = schema.find((candidate) => candidate.field_name === input.dataset.feishuField);
      if (!field) return;
      const value = readInputValue(field, input);
      if (comparable(value) !== input.dataset.original) changed[field.field_name] = value;
    });
    if (!Object.keys(changed).length) {
      showToast('没有检测到字段变化');
      return;
    }
    const company = mapping.company ? fieldText(editingRecord.fields?.[mapping.company]) : '';
    try {
      await api('/api/v1/recruitment/feishu/proposals/manual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ record_id: editingRecord.record_id, company, fields: changed }),
      });
      dialog.close();
      editingRecord = null;
      showToast('已生成审核建议，尚未写入飞书');
      await loadProposals();
    } catch (error) {
      showToast(`生成建议失败：${error.message}`);
    }
  }

  async function loadConfig() {
    config = await api('/api/v1/recruitment/feishu/config-status');
    if (config.source_url) {
      sourceLink.href = config.source_url;
      sourceLink.hidden = false;
    }
    if (!config.configured) {
      stateEl.textContent = '飞书尚未配置：需要 FEISHU_APP_ID、FEISHU_APP_SECRET、Wiki/App Token 与 Table ID。当前只会保留本地审核建议，不会写入飞书。';
    }
  }

  async function loadRecords() {
    if (!config?.configured) {
      records = [];
      schema = [];
      mapping = {};
      editableNames = [];
      renderMapping();
      renderRecords();
      return;
    }
    const payload = await api('/api/v1/recruitment/feishu/records');
    schema = payload.fields || [];
    records = payload.records || [];
    mapping = payload.mapping || {};
    editableNames = payload.editable_field_names || [];
    renderMapping();
    renderRecords();
  }

  async function loadProposals() {
    const payload = await api('/api/v1/recruitment/feishu/proposals?limit=150');
    proposals = payload.proposals || [];
    if (payload.mapping && Object.keys(payload.mapping).length) mapping = payload.mapping;
    renderMapping();
    renderProposals();
    if (payload.sync_error) stateEl.textContent = `飞书读取失败：${payload.sync_error}`;
  }

  async function backfill(showResult = false) {
    const result = await api('/api/v1/recruitment/feishu/backfill', { method: 'POST' });
    if (showResult) showToast(result.created ? `新增 ${result.created} 条邮件审核建议` : '没有新的邮件建议');
    return result;
  }

  async function refreshAll() {
    refreshButton.disabled = true;
    stateEl.textContent = '正在读取飞书招聘进度与审核队列…';
    try {
      await loadConfig();
      await backfill(false);
      await Promise.all([loadRecords(), loadProposals()]);
      if (config?.configured) {
        stateEl.textContent = '已连接飞书多维表格 · 修改均需审核后写入';
      }
    } catch (error) {
      stateEl.textContent = `加载失败：${error.message}`;
    } finally {
      refreshButton.disabled = false;
    }
  }

  tableShell.addEventListener('click', (event) => {
    const button = event.target.closest('[data-edit-record]');
    if (!button) return;
    const record = records.find((item) => item.record_id === button.dataset.editRecord);
    if (record) openEdit(record);
  });

  proposalList.addEventListener('click', async (event) => {
    const approve = event.target.closest('[data-approve-proposal]');
    const reject = event.target.closest('[data-reject-proposal]');
    if (!approve && !reject) return;
    const id = Number((approve || reject).dataset.approveProposal || (approve || reject).dataset.rejectProposal);
    if (approve) {
      const proposal = proposals.find((item) => item.id === id);
      if (!proposal?.can_approve) return;
      if (!window.confirm('确认将这条审核建议写入飞书招聘进度表？')) return;
      approve.disabled = true;
      try {
        await api(`/api/v1/recruitment/feishu/proposals/${id}/approve`, { method: 'POST' });
        showToast('已写入飞书');
        await Promise.all([loadRecords(), loadProposals()]);
      } catch (error) {
        showToast(`写入失败：${error.message}`);
        approve.disabled = false;
      }
      return;
    }
    reject.disabled = true;
    try {
      await api(`/api/v1/recruitment/feishu/proposals/${id}/reject`, { method: 'POST' });
      showToast('已驳回，不会修改飞书');
      await loadProposals();
    } catch (error) {
      showToast(`驳回失败：${error.message}`);
      reject.disabled = false;
    }
  });

  refreshButton.addEventListener('click', refreshAll);
  backfillButton.addEventListener('click', async () => {
    backfillButton.disabled = true;
    try {
      await backfill(true);
      await loadProposals();
    } catch (error) {
      showToast(`生成建议失败：${error.message}`);
    } finally {
      backfillButton.disabled = false;
    }
  });
  form.addEventListener('submit', createManualProposal);
  document.getElementById('feishuEditCancel').addEventListener('click', () => dialog.close());

  refreshAll();
})();
