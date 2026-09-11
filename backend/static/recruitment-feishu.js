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
  const editHelp = document.getElementById('feishuEditHelp');
  const editFields = document.getElementById('feishuEditFields');
  const editRecordId = document.getElementById('feishuEditRecordId');
  const editSubmit = document.getElementById('feishuEditSubmit');
  const toast = document.getElementById('toast');

  const pipelineKeys = [
    'company', 'url', 'position', 'recruitment_type', 'location', 'status', 'priority',
    'application_date', 'assessment_date', 'written_date',
    'first_interview_date', 'second_interview_date', 'third_interview_date', 'note',
  ];
  const dateKeys = [
    'application_date', 'assessment_date', 'written_date',
    'first_interview_date', 'second_interview_date', 'third_interview_date',
  ];
  const pillKeys = new Set(['recruitment_type', 'status', 'priority']);

  let config = null;
  let schema = [];
  let records = [];
  let mapping = {};
  let editableNames = [];
  let proposals = [];
  let recordOptions = [];
  let editingRecord = null;
  let editingProposal = null;
  let editMode = 'record';

  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('show');
    window.setTimeout(() => toast.classList.remove('show'), 3000);
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
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(date);
  }

  function displayField(field, value) {
    if (Number(field?.type) === 5) return formatDateMillis(value) || '—';
    const text = fieldText(value);
    return text || '—';
  }

  function fieldForKey(key) {
    const name = mapping[key];
    return name ? schema.find((field) => field.field_name === name) : null;
  }

  function orderedFields() {
    const preferred = pipelineKeys.map((key) => mapping[key]).filter(Boolean);
    const preferredSet = new Set(preferred);
    return [
      ...preferred.map((name) => schema.find((field) => field.field_name === name)).filter(Boolean),
      ...schema.filter((field) => !preferredSet.has(field.field_name)),
    ];
  }

  function renderMapping() {
    const labels = {
      company: '公司', url: '网申链接', position: '岗位', recruitment_type: '类型', location: '工作地点',
      status: '投递状态', priority: '优先级', application_date: '投递日期', assessment_date: '测评日期',
      written_date: '笔试日期', first_interview_date: '一面日期', second_interview_date: '二面日期',
      third_interview_date: '三面日期', note: '备注',
    };
    mappingEl.innerHTML = pipelineKeys.map((key) => {
      const value = mapping[key];
      return `
        <div class="feishu-mapping-item${value ? '' : ' is-missing'}">
          <span>${escapeHtml(labels[key] || key)}</span>
          <strong>${escapeHtml(value || '未匹配')}</strong>
        </div>`;
    }).join('');
  }

  function tableCell(field, value, logicalKey) {
    const text = displayField(field, value);
    if (pillKeys.has(logicalKey) && text !== '—') {
      return `<td title="${escapeHtml(text)}"><span class="feishu-cell-pill">${escapeHtml(text)}</span></td>`;
    }
    if (logicalKey === 'url' && text !== '—') {
      const raw = fieldText(value);
      const href = /^https?:\/\//i.test(raw) ? raw : '';
      if (href) {
        return `<td title="${escapeHtml(raw)}"><a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">打开链接</a></td>`;
      }
    }
    return `<td title="${escapeHtml(text)}">${escapeHtml(text)}</td>`;
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
    const keyByName = new Map(pipelineKeys.filter((key) => mapping[key]).map((key) => [mapping[key], key]));
    const statusField = mapping.status;
    const groups = new Map();
    records.forEach((record) => {
      const status = statusField ? fieldText(record.fields?.[statusField]) : '';
      const group = status || '未标记状态';
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(record);
    });

    const head = fields.map((field) => `<th>${escapeHtml(field.field_name)}</th>`).join('');
    const body = [...groups.entries()].map(([group, groupRecords]) => {
      const groupRow = `<tr class="feishu-group-row"><td colspan="${fields.length + 1}">${escapeHtml(group)}<small>${groupRecords.length} 条</small></td></tr>`;
      const rows = groupRecords.map((record) => {
        const cells = fields.map((field) => tableCell(
          field,
          record.fields?.[field.field_name],
          keyByName.get(field.field_name),
        )).join('');
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

  function proposalValue(proposal, key) {
    const name = mapping[key];
    if (!name) return '';
    return proposal.proposed_fields?.[name];
  }

  function proposalDateText(proposal) {
    for (const key of dateKeys) {
      const value = proposalValue(proposal, key);
      if (value !== null && value !== undefined && value !== '') {
        const field = fieldForKey(key);
        return `${mapping[key]} · ${displayField(field, value)}`;
      }
    }
    return '—';
  }

  function matchInfo(proposal) {
    if (proposal.write_mode === 'create') return { text: '新增公司', cls: 'is-ready' };
    if (proposal.write_mode === 'update' && proposal.match_status === 'matched') return { text: '更新已有记录', cls: 'is-ready' };
    if (proposal.match_status === 'ambiguous') return { text: '匹配冲突', cls: 'is-warning' };
    if (proposal.match_status === 'target_missing') return { text: '目标已失效', cls: 'is-warning' };
    if (proposal.match_status === 'unmatched') return { text: '未匹配', cls: 'is-warning' };
    return { text: '待校验', cls: '' };
  }

  function renderProposals() {
    const pending = proposals.filter((item) => item.status === 'pending');
    const applied = proposals.filter((item) => item.status === 'applied');
    pendingCountEl.textContent = String(pending.length);
    appliedCountEl.textContent = String(applied.length);
    if (!pending.length) {
      proposalList.innerHTML = '<div class="empty-recruitment">暂无待审核建议。邮件识别出新的笔试、测评或面试进度后会先出现在这里。</div>';
      return;
    }

    const rows = pending.map((proposal) => {
      const match = matchInfo(proposal);
      const company = proposal.company || fieldText(proposalValue(proposal, 'company')) || '—';
      const position = fieldText(proposalValue(proposal, 'position')) || '—';
      const type = fieldText(proposalValue(proposal, 'recruitment_type')) || '—';
      const location = fieldText(proposalValue(proposal, 'location')) || '—';
      const status = fieldText(proposalValue(proposal, 'status')) || proposal.stage || '—';
      const actions = `
        <div class="feishu-review-actions-inline">
          <button class="ghost-button" type="button" data-edit-proposal="${proposal.id}">修改字段</button>
          <button class="ghost-button" type="button" data-reject-proposal="${proposal.id}">驳回</button>
          <button class="ghost-button correction-save" type="button" data-approve-proposal="${proposal.id}" ${proposal.can_approve ? '' : 'disabled'}>批准写入</button>
        </div>`;
      return `
        <tr data-proposal-id="${proposal.id}" title="${escapeHtml(proposal.review_reason || '')}">
          <td>${escapeHtml(company)}</td>
          <td>${escapeHtml(position)}</td>
          <td>${escapeHtml(type)}</td>
          <td>${escapeHtml(location)}</td>
          <td><span class="feishu-cell-pill">${escapeHtml(status)}</span></td>
          <td>${escapeHtml(proposalDateText(proposal))}</td>
          <td><span class="feishu-match-note ${match.cls}">${escapeHtml(match.text)}</span></td>
          <td class="feishu-review-action-cell">${actions}</td>
        </tr>`;
    }).join('');

    proposalList.innerHTML = `
      <table class="feishu-review-table">
        <thead>
          <tr><th>公司</th><th>岗位</th><th>类型</th><th>地点</th><th>状态</th><th>阶段日期</th><th>写入方式</th><th>操作</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  function fieldInputValue(field, value) {
    const type = Number(field.type);
    if (type === 5) {
      if (value === null || value === undefined || value === '') return '';
      const numeric = Number(value);
      const date = Number.isFinite(numeric) && numeric > 10000000000 ? new Date(numeric) : new Date(String(value));
      if (Number.isNaN(date.getTime())) return '';
      const pad = (part) => String(part).padStart(2, '0');
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }
    if (type === 4) return Array.isArray(value) ? value.map(fieldText).join(', ') : fieldText(value);
    if (type === 7) return Boolean(value);
    if (type === 15 && value && typeof value === 'object') return value.link || value.text || '';
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
    if (value && typeof value === 'object' && ('link' in value || 'text' in value)) {
      return JSON.stringify(value.link || value.text || '');
    }
    return JSON.stringify(value);
  }

  function editablePipelineFields() {
    const preferred = pipelineKeys
      .map((key) => fieldForKey(key))
      .filter((field) => field && editableNames.includes(field.field_name));
    const seen = new Set();
    return preferred.filter((field) => {
      if (seen.has(field.field_name)) return false;
      seen.add(field.field_name);
      return true;
    });
  }

  function inputMarkup(field, raw, includeOriginal = false) {
    const initial = fieldInputValue(field, raw);
    const original = includeOriginal ? ` data-original='${escapeHtml(comparable(initial))}'` : '';
    const fieldName = escapeHtml(field.field_name);
    const type = Number(field.type);
    const fullRow = field.field_name === mapping.note ? ' feishu-full-row' : '';
    if (type === 7) {
      return `<label class="feishu-checkbox-row${fullRow}"><input type="checkbox" data-feishu-field="${fieldName}" data-feishu-type="${field.type}"${original} ${initial ? 'checked' : ''}> ${fieldName}</label>`;
    }
    const hint = type === 4 ? '<small>多选值用英文逗号分隔</small>' : '';
    if (field.field_name === mapping.note) {
      return `<label class="${fullRow.trim()}">${fieldName}${hint}<textarea class="recruitment-input" data-feishu-field="${fieldName}" data-feishu-type="${field.type}"${original}>${escapeHtml(initial)}</textarea></label>`;
    }
    const inputType = type === 5 ? 'datetime-local' : type === 2 ? 'number' : type === 15 ? 'url' : 'text';
    return `<label class="${fullRow.trim()}">${fieldName}${hint}<input class="recruitment-input" type="${inputType}" data-feishu-field="${fieldName}" data-feishu-type="${field.type}"${original} value="${escapeHtml(initial)}"></label>`;
  }

  function openRecordEdit(record) {
    editMode = 'record';
    editingRecord = record;
    editingProposal = null;
    editRecordId.value = record.record_id;
    const company = mapping.company ? fieldText(record.fields?.[mapping.company]) : '';
    editTitle.textContent = company ? `修改 · ${company}` : '修改招聘进度';
    editHelp.textContent = '保存后只会生成一条待审核建议，不会立即写入飞书。';
    editSubmit.textContent = '生成审核建议';
    editFields.innerHTML = editablePipelineFields().map((field) => inputMarkup(field, record.fields?.[field.field_name], true)).join('');
    dialog.showModal();
  }

  function targetOptions(selected) {
    const options = [
      `<option value="" ${!selected ? 'selected' : ''}>自动匹配（未匹配则新建）</option>`,
      `<option value="__new__" ${selected === '__new__' ? 'selected' : ''}>明确创建新记录</option>`,
    ];
    recordOptions.forEach((option) => {
      const label = [option.company || '未命名公司', option.position, option.status].filter(Boolean).join(' · ');
      options.push(`<option value="${escapeHtml(option.record_id)}" ${selected === option.record_id ? 'selected' : ''}>${escapeHtml(label)}</option>`);
    });
    return options.join('');
  }

  function openProposalEdit(proposal) {
    editMode = 'proposal';
    editingProposal = proposal;
    editingRecord = null;
    editRecordId.value = proposal.record_id || '';
    editTitle.textContent = `审核 · ${proposal.company || proposal.source_title || '招聘进度'}`;
    editHelp.textContent = '这里只修改待审核内容；保存不会写入飞书，仍需点击“批准写入”才会调用飞书 API。';
    editSubmit.textContent = '保存审核内容';

    const target = proposal.record_id || '';
    const companyValue = proposal.company || fieldText(proposalValue(proposal, 'company'));
    const headerFields = `
      <label>投递公司<input id="feishuProposalCompany" class="recruitment-input" type="text" value="${escapeHtml(companyValue)}"></label>
      <label>写入目标<select id="feishuProposalTarget" class="recruitment-input">${targetOptions(target)}</select></label>`;
    const fields = editablePipelineFields()
      .filter((field) => field.field_name !== mapping.company)
      .map((field) => inputMarkup(field, proposal.proposed_fields?.[field.field_name], false))
      .join('');
    editFields.innerHTML = headerFields + fields;
    dialog.showModal();
  }

  async function saveRecordProposal() {
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

  async function savePendingProposal() {
    if (!editingProposal) return;
    const fields = {};
    editFields.querySelectorAll('[data-feishu-field]').forEach((input) => {
      const field = schema.find((candidate) => candidate.field_name === input.dataset.feishuField);
      if (!field) return;
      fields[field.field_name] = readInputValue(field, input);
    });
    const company = document.getElementById('feishuProposalCompany').value.trim();
    const recordId = document.getElementById('feishuProposalTarget').value || null;
    await api(`/api/v1/recruitment/feishu/proposals/${editingProposal.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ company, record_id: recordId, fields }),
    });
    showToast('审核内容已保存，尚未写入飞书');
  }

  async function submitEdit(event) {
    event.preventDefault();
    editSubmit.disabled = true;
    try {
      if (editMode === 'proposal') await savePendingProposal();
      else await saveRecordProposal();
      dialog.close();
      editingRecord = null;
      editingProposal = null;
      await loadProposals();
    } catch (error) {
      showToast(`保存失败：${error.message}`);
    } finally {
      editSubmit.disabled = false;
    }
  }

  async function loadConfig() {
    config = await api('/api/v1/recruitment/feishu/config-status');
    if (config.source_url) {
      sourceLink.href = config.source_url;
      sourceLink.hidden = false;
    }
    if (!config.configured) {
      stateEl.textContent = '飞书尚未配置：需要 App ID、App Secret、Wiki/App Token 与 Table ID。当前只会保留本地审核建议，不会写入飞书。';
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
    recordOptions = payload.record_options || [];
    if (payload.mapping && Object.keys(payload.mapping).length) mapping = payload.mapping;
    renderMapping();
    renderProposals();
    if (payload.sync_error) stateEl.textContent = `飞书读取失败：${payload.sync_error}`;
  }

  async function backfill(showResult = false) {
    const result = await api('/api/v1/recruitment/feishu/backfill', { method: 'POST' });
    if (showResult) {
      const parts = [];
      if (result.created) parts.push(`新增 ${result.created} 条`);
      if (result.enriched) parts.push(`补齐 ${result.enriched} 条旧建议`);
      showToast(parts.length ? parts.join('，') : '没有新的邮件建议');
    }
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
      if (config?.configured) stateEl.textContent = '已连接飞书多维表格 · 所有修改均需审核后写入';
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
    if (record) openRecordEdit(record);
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
      const action = proposal.write_mode === 'create' ? '新建一条飞书招聘记录' : '更新匹配到的飞书招聘记录';
      if (!window.confirm(`确认${action}？\n\n批准后会真实修改飞书多维表格。`)) return;
      approve.disabled = true;
      try {
        const result = await api(`/api/v1/recruitment/feishu/proposals/${id}/approve`, { method: 'POST' });
        showToast(result.write_mode === 'create' ? '已在飞书新增招聘记录' : '已更新飞书招聘记录');
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
  form.addEventListener('submit', submitEdit);
  document.getElementById('feishuEditCancel').addEventListener('click', () => dialog.close());

  refreshAll();
})();
