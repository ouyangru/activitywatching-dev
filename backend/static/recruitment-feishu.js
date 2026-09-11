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
  const dialogHelp = dialog?.querySelector('.dialog-help');
  const toast = document.getElementById('toast');

  const mappingOrder = [
    ['company', '投递公司'],
    ['application_url', '网申链接'],
    ['position', '岗位'],
    ['recruitment_type', '类型'],
    ['location', '工作地点'],
    ['stage', '投递状态'],
    ['priority', '优先级'],
    ['applied_date', '投递日期'],
    ['assessment_date', '测评日期'],
    ['written_test_date', '笔试日期'],
    ['round1_date', '一面日期'],
    ['round2_date', '二面日期'],
    ['round3_date', '三面日期'],
    ['notes', '备注'],
  ];
  const stageOrder = ['待投递', '已投递', '测评', '笔试', '一面', '二面', '三面', 'HR面', 'Offer', '流程结束', '未分类'];

  let config = null;
  let schema = [];
  let records = [];
  let mapping = {};
  let editableNames = [];
  let proposals = [];
  let editingRecord = null;
  let editingProposal = null;

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
    const preferred = mappingOrder.map(([key]) => mapping[key]).filter(Boolean);
    const preferredSet = new Set(preferred);
    return [
      ...preferred.map((name) => schema.find((field) => field.field_name === name)).filter(Boolean),
      ...schema.filter((field) => !preferredSet.has(field.field_name)),
    ];
  }

  function renderMapping() {
    mappingEl.innerHTML = mappingOrder.map(([key, label]) => {
      const value = mapping[key];
      return `
        <div class="feishu-mapping-item${value ? '' : ' is-missing'}">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value || '未匹配')}</strong>
        </div>`;
    }).join('');
  }

  function recordStage(record) {
    const fieldName = mapping.stage;
    const value = fieldName ? fieldText(record.fields?.[fieldName]).trim() : '';
    return value || '未分类';
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
    const grouped = new Map();
    records.forEach((record) => {
      const stage = recordStage(record);
      if (!grouped.has(stage)) grouped.set(stage, []);
      grouped.get(stage).push(record);
    });
    const groups = [...grouped.entries()].sort((a, b) => {
      const ai = stageOrder.indexOf(a[0]);
      const bi = stageOrder.indexOf(b[0]);
      return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi) || a[0].localeCompare(b[0], 'zh-CN');
    });
    const body = groups.map(([stage, items]) => {
      const groupRow = `<tr class="feishu-group-row"><td colspan="${fields.length + 1}"><span>${escapeHtml(stage)}</span><small>${items.length}</small></td></tr>`;
      const rows = items.map((record) => {
        const cells = fields.map((field) => `<td title="${escapeHtml(displayField(field, record.fields?.[field.field_name]))}">${escapeHtml(displayField(field, record.fields?.[field.field_name]))}</td>`).join('');
        return `<tr data-record-id="${escapeHtml(record.record_id)}">${cells}<td class="feishu-action-cell"><button class="ghost-button" type="button" data-edit-record="${escapeHtml(record.record_id)}">修改</button></td></tr>`;
      }).join('');
      return groupRow + rows;
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

  function proposalMatchLabel(proposal) {
    if (proposal.write_mode === 'create') return '将新建飞书记录';
    if (proposal.write_mode === 'update') return '更新已匹配公司';
    if (proposal.match_status === 'ambiguous') return '公司匹配有歧义';
    if (proposal.match_status === 'unmatched') return '未匹配公司';
    return '尚未校验';
  }

  function renderProposals() {
    const pending = proposals.filter((item) => item.status === 'pending');
    const applied = proposals.filter((item) => item.status === 'applied');
    pendingCountEl.textContent = String(pending.length);
    appliedCountEl.textContent = String(applied.length);
    if (!proposals.length) {
      proposalList.innerHTML = '<div class="empty-recruitment">暂无审核建议。邮件识别出新的笔试、测评或面试阶段后，会先出现在这里。</div>';
      return;
    }
    proposalList.innerHTML = proposals.map((proposal) => {
      const sourceLabel = proposal.source === 'mail' ? '邮件识别' : '网页修改';
      const actions = proposal.status === 'pending' ? `
        ${proposal.source === 'mail' ? `<button class="ghost-button" type="button" data-edit-proposal="${proposal.id}">修改字段</button>` : ''}
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
            <small>${escapeHtml(proposalMatchLabel(proposal))}</small>
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

  function inputMarkup(field, raw, original = true) {
    const initial = fieldInputValue(field, raw);
    const originalAttr = original ? ` data-original='${escapeHtml(comparable(initial))}'` : '';
    if (Number(field.type) === 7) {
      return `<label class="feishu-checkbox-row"><input type="checkbox" data-feishu-field="${escapeHtml(field.field_name)}" data-feishu-type="${field.type}"${originalAttr} ${initial ? 'checked' : ''}> ${escapeHtml(field.field_name)}</label>`;
    }
    const inputType = Number(field.type) === 5 ? 'datetime-local' : Number(field.type) === 2 ? 'number' : Number(field.type) === 15 ? 'url' : 'text';
    const hint = Number(field.type) === 4 ? '<small>多选值用英文逗号分隔</small>' : '';
    return `<label>${escapeHtml(field.field_name)}${hint}<input class="recruitment-input" type="${inputType}" data-feishu-field="${escapeHtml(field.field_name)}" data-feishu-type="${field.type}"${originalAttr} value="${escapeHtml(initial)}"></label>`;
  }

  function openEdit(record) {
    editingProposal = null;
    editingRecord = record;
    editRecordId.value = record.record_id;
    const company = mapping.company ? fieldText(record.fields?.[mapping.company]) : '';
    editTitle.textContent = company ? `修改 · ${company}` : '修改招聘进度';
    if (dialogHelp) dialogHelp.textContent = '保存后只会创建一条待审核建议，不会立即写入飞书。';
    const editable = schema.filter((field) => editableNames.includes(field.field_name));
    editFields.innerHTML = editable.map((field) => inputMarkup(field, record.fields?.[field.field_name], true)).join('');
    dialog.showModal();
  }

  function proposalEditableFields() {
    const names = mappingOrder.map(([key]) => mapping[key]).filter(Boolean);
    return names.map((name) => schema.find((field) => field.field_name === name))
      .filter((field) => field && editableNames.includes(field.field_name));
  }

  function openProposalEdit(proposal) {
    editingRecord = null;
    editingProposal = proposal;
    editRecordId.value = '';
    editTitle.textContent = `审核 · ${proposal.company || proposal.source_title || '招聘进度'}`;
    if (dialogHelp) dialogHelp.textContent = '可修正识别字段并选择写入目标。保存后仍处于待审核状态，只有“批准写入”才会修改飞书。';
    const selectedRecord = proposal.record_id || '';
    const options = [
      `<option value=""${!selectedRecord ? ' selected' : ''}>自动按公司匹配</option>`,
      `<option value="__new__"${selectedRecord === '__new__' ? ' selected' : ''}>新建飞书记录</option>`,
      ...records.map((record) => {
        const company = mapping.company ? fieldText(record.fields?.[mapping.company]) : record.record_id;
        const selected = selectedRecord === record.record_id ? ' selected' : '';
        return `<option value="${escapeHtml(record.record_id)}"${selected}>更新：${escapeHtml(company || record.record_id)}</option>`;
      }),
    ].join('');
    const target = `<label class="feishu-target-field">写入目标<select id="feishuProposalTarget" class="recruitment-input">${options}</select><small>匹配有歧义时请选择具体公司；也可以强制新建。</small></label>`;
    const fields = proposalEditableFields();
    editFields.innerHTML = target + fields.map((field) => inputMarkup(field, proposal.proposed_fields?.[field.field_name], false)).join('');
    dialog.showModal();
  }

  async function createManualProposal() {
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
    await api('/api/v1/recruitment/feishu/proposals/manual', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ record_id: editingRecord.record_id, company, fields: changed }),
    });
    showToast('已生成审核建议，尚未写入飞书');
  }

  async function saveProposalEdit() {
    if (!editingProposal) return;
    const fields = {};
    editFields.querySelectorAll('[data-feishu-field]').forEach((input) => {
      const field = schema.find((candidate) => candidate.field_name === input.dataset.feishuField);
      if (!field) return;
      fields[field.field_name] = readInputValue(field, input);
    });
    const company = mapping.company ? fieldText(fields[mapping.company]) : editingProposal.company || '';
    const target = document.getElementById('feishuProposalTarget')?.value ?? '';
    await api(`/api/v1/recruitment/feishu/proposals/${editingProposal.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ company, record_id: target, fields }),
    });
    showToast('审核字段已更新，尚未写入飞书');
  }

  async function saveEdit(event) {
    event.preventDefault();
    try {
      if (editingProposal) await saveProposalEdit();
      else await createManualProposal();
      dialog.close();
      editingRecord = null;
      editingProposal = null;
      await loadProposals();
    } catch (error) {
      showToast(`保存失败：${error.message}`);
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
      await loadRecords();
      await loadProposals();
      if (config?.configured) stateEl.textContent = '已连接飞书多维表格 · 修改均需审核后写入';
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
    const edit = event.target.closest('[data-edit-proposal]');
    const approve = event.target.closest('[data-approve-proposal]');
    const reject = event.target.closest('[data-reject-proposal]');
    if (edit) {
      const proposal = proposals.find((item) => item.id === Number(edit.dataset.editProposal));
      if (proposal) openProposalEdit(proposal);
      return;
    }
    if (!approve && !reject) return;
    const id = Number((approve || reject).dataset.approveProposal || (approve || reject).dataset.rejectProposal);
    if (approve) {
      const proposal = proposals.find((item) => item.id === id);
      if (!proposal?.can_approve) return;
      const actionText = proposal.write_mode === 'create' ? '在飞书中新建这条招聘记录' : '更新匹配到的飞书招聘记录';
      if (!window.confirm(`确认${actionText}？`)) return;
      approve.disabled = true;
      try {
        const result = await api(`/api/v1/recruitment/feishu/proposals/${id}/approve`, { method: 'POST' });
        showToast(result.write_mode === 'create' ? '已在飞书新建记录' : '已写入飞书');
        await loadRecords();
        await loadProposals();
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
  form.addEventListener('submit', saveEdit);
  document.getElementById('feishuEditCancel').addEventListener('click', () => {
    dialog.close();
    editingRecord = null;
    editingProposal = null;
  });

  refreshAll();
})();
