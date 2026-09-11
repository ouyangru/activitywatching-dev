(() => {
  const form = document.getElementById('feishuEditForm');
  const dialog = document.getElementById('feishuEditDialog');
  const editFields = document.getElementById('feishuEditFields');
  const tableShell = document.getElementById('feishuTableShell');
  const mappingEl = document.getElementById('feishuMapping');
  const toast = document.getElementById('toast');
  const refreshButton = document.getElementById('feishuRefreshButton');
  if (!form || !dialog) return;

  const HIDDEN_FIELDS = new Set(['网申链接', '父记录']);
  const SHORT_HEADERS = {
    '投递公司': '公司',
    '工作地点': '地点',
    '投递状态': '状态',
    '投递日期': '投递',
    '测评日期': '测评',
    '笔试日期': '笔试',
    '一面日期': '一面',
    '二面日期': '二面',
    '三面日期': '三面',
  };
  const COLUMN_CLASS = {
    '投递公司': 'col-company',
    '岗位': 'col-position',
    '类型': 'col-type',
    '工作地点': 'col-location',
    '投递状态': 'col-status',
    '优先级': 'col-priority',
    '投递日期': 'col-date',
    '测评日期': 'col-date',
    '笔试日期': 'col-date',
    '一面日期': 'col-date',
    '二面日期': 'col-date',
    '三面日期': 'col-date',
    '备注': 'col-note',
  };

  let activeProposalId = null;
  let fieldSchema = new Map();

  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('show');
    window.setTimeout(() => toast.classList.remove('show'), 3000);
  }

  function comparableValue(value) {
    if (Array.isArray(value)) return JSON.stringify(value.map(String));
    if (value === null || value === undefined) return '';
    return JSON.stringify(value);
  }

  function normalizedControlValue(input) {
    const type = Number(input.dataset.feishuType);
    if (type === 7) return input.checked;
    if (type === 2) return input.value.trim() === '' ? '' : Number(input.value);
    if (type === 4) return input.value.split(',').map((part) => part.trim()).filter(Boolean);
    if (type === 5) return input.value ? new Date(input.value).getTime() : '';
    return input.value.trim();
  }

  function normalizeOriginal(input) {
    if (!input || input.dataset.original === undefined) return;
    input.dataset.original = comparableValue(normalizedControlValue(input));
  }

  function nonEmptyFieldValue(input) {
    const type = Number(input.dataset.feishuType);
    if (type === 7) return input.checked ? true : undefined;
    if (type === 2) return input.value.trim() === '' ? undefined : Number(input.value);
    if (type === 4) {
      const values = input.value.split(',').map((part) => part.trim()).filter(Boolean);
      return values.length ? values : undefined;
    }
    if (type === 5) return input.value ? new Date(input.value).getTime() : undefined;
    const value = input.value.trim();
    return value === '' ? undefined : value;
  }

  async function patchProposal(proposalId, payload) {
    const response = await fetch(`/api/v1/recruitment/feishu/proposals/${proposalId}`, {
      method: 'PATCH',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const data = await response.json();
        detail = data.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
  }

  function fieldOptionNames(field) {
    const property = field?.property || {};
    const options = Array.isArray(property.options)
      ? property.options
      : Array.isArray(property.option)
        ? property.option
        : [];
    return options.map((option) => {
      if (typeof option === 'string') return option.trim();
      return String(option?.name ?? option?.text ?? option?.value ?? '').trim();
    }).filter(Boolean);
  }

  async function loadFieldSchema() {
    try {
      const response = await fetch('/api/v1/recruitment/feishu/records', {
        credentials: 'same-origin',
        cache: 'no-store',
      });
      if (!response.ok) return;
      const payload = await response.json();
      fieldSchema = new Map((payload.fields || []).map((field) => [field.field_name, field]));
      enhanceDialog();
      compactMainTable();
      hideMappingEntries();
    } catch (_) {
      // The main page already surfaces connection errors. UI enhancement is best-effort.
    }
  }

  function copyInputMetadata(from, to) {
    to.className = from.className || 'recruitment-input';
    to.dataset.feishuField = from.dataset.feishuField;
    to.dataset.feishuType = from.dataset.feishuType;
    if (from.dataset.original !== undefined) to.dataset.original = from.dataset.original;
  }

  function enhanceSingleSelect(input, field) {
    if (input.tagName === 'SELECT' || input.dataset.feishuEnhanced === '1') {
      normalizeOriginal(input);
      return input;
    }
    const choices = fieldOptionNames(field);
    if (!choices.length) {
      normalizeOriginal(input);
      return input;
    }

    const current = input.value.trim();
    const select = document.createElement('select');
    copyInputMetadata(input, select);
    select.dataset.feishuEnhanced = '1';
    select.classList.add('recruitment-input');

    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = '— 未选择 —';
    select.appendChild(blank);

    const values = current && !choices.includes(current) ? [current, ...choices] : choices;
    [...new Set(values)].forEach((name) => {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      option.selected = name === current;
      select.appendChild(option);
    });
    input.replaceWith(select);
    normalizeOriginal(select);
    return select;
  }

  function enhanceMultiSelect(input, field) {
    if (input.dataset.feishuEnhanced === '1') {
      normalizeOriginal(input);
      return input;
    }
    const choices = fieldOptionNames(field);
    if (!choices.length) {
      normalizeOriginal(input);
      return input;
    }

    const current = new Set(input.value.split(',').map((part) => part.trim()).filter(Boolean));
    input.dataset.feishuEnhanced = '1';
    input.type = 'hidden';

    const wrap = document.createElement('div');
    wrap.className = 'feishu-multi-options';
    const values = [...new Set([...current, ...choices])];
    values.forEach((name) => {
      const label = document.createElement('label');
      label.className = 'feishu-multi-option';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = current.has(name);
      checkbox.value = name;
      const text = document.createElement('span');
      text.textContent = name;
      label.append(checkbox, text);
      wrap.appendChild(label);
    });

    const syncValue = () => {
      input.value = [...wrap.querySelectorAll('input:checked')].map((checkbox) => checkbox.value).join(',');
      input.dispatchEvent(new Event('change', { bubbles: true }));
    };
    wrap.addEventListener('change', syncValue);
    input.insertAdjacentElement('afterend', wrap);
    normalizeOriginal(input);
    return input;
  }

  function enhanceDialog() {
    if (!editFields) return;
    editFields.querySelectorAll('[data-feishu-field]').forEach((input) => {
      const fieldName = input.dataset.feishuField || '';
      const label = input.closest('label');
      if (HIDDEN_FIELDS.has(fieldName)) {
        label?.classList.add('feishu-hidden-field');
        return;
      }
      const field = fieldSchema.get(fieldName);
      if (!field) {
        normalizeOriginal(input);
        return;
      }
      const type = Number(field.type);
      if (type === 3) {
        enhanceSingleSelect(input, field);
        return;
      }
      if (type === 4) {
        enhanceMultiSelect(input, field);
        return;
      }
      normalizeOriginal(input);
    });
  }

  function compactDate(value) {
    const text = String(value || '').trim();
    const match = text.match(/^(?:\d{4})[\/-](\d{2})[\/-](\d{2})(?:\s+(\d{2}):(\d{2}))?/);
    if (!match) return text;
    const [, month, day, hour, minute] = match;
    if (!hour || `${hour}:${minute}` === '00:00') return `${month}/${day}`;
    return `${month}/${day} ${hour}:${minute}`;
  }

  function compactMainTable() {
    const table = tableShell?.querySelector('.feishu-table');
    if (!table) return;
    const headers = [...table.querySelectorAll('thead th')];
    const names = headers.map((header) => header.dataset.fullName || header.textContent.trim());
    const hiddenIndexes = new Set();

    headers.forEach((header, index) => {
      const fullName = names[index];
      header.dataset.fullName = fullName;
      if (HIDDEN_FIELDS.has(fullName)) {
        hiddenIndexes.add(index);
        header.classList.add('feishu-hidden-field');
        return;
      }
      header.classList.add(COLUMN_CLASS[fullName] || (fullName === '操作' ? 'feishu-action-cell' : 'col-default'));
      const shortName = SHORT_HEADERS[fullName];
      if (shortName && header.textContent.trim() !== shortName) header.textContent = shortName;
    });

    table.querySelectorAll('tbody tr').forEach((row) => {
      if (row.classList.contains('feishu-group-row')) {
        const cell = row.querySelector('td');
        if (cell) cell.colSpan = Math.max(1, headers.length - hiddenIndexes.size);
        return;
      }
      [...row.children].forEach((cell, index) => {
        const fullName = names[index] || '';
        if (hiddenIndexes.has(index)) {
          cell.classList.add('feishu-hidden-field');
          return;
        }
        cell.classList.add(COLUMN_CLASS[fullName] || (fullName === '操作' ? 'feishu-action-cell' : 'col-default'));
        if (COLUMN_CLASS[fullName] === 'col-date') {
          const original = cell.getAttribute('title') || cell.textContent;
          const compact = compactDate(original);
          if (cell.textContent.trim() !== compact) cell.textContent = compact;
          cell.setAttribute('title', original);
        }
      });
    });
  }

  function hideMappingEntries() {
    if (!mappingEl) return;
    mappingEl.querySelectorAll('.feishu-mapping-item').forEach((item) => {
      const label = item.querySelector('span')?.textContent.trim() || '';
      const mapped = item.querySelector('strong')?.textContent.trim() || '';
      if (label === '网申链接' || HIDDEN_FIELDS.has(mapped)) item.classList.add('feishu-hidden-field');
    });
  }

  document.addEventListener('click', (event) => {
    const proposalButton = event.target.closest('[data-edit-proposal]');
    if (proposalButton) {
      activeProposalId = Number(proposalButton.dataset.editProposal);
      queueMicrotask(enhanceDialog);
      return;
    }
    if (event.target.closest('[data-edit-record]')) {
      activeProposalId = null;
      queueMicrotask(enhanceDialog);
    }
  }, true);

  form.addEventListener('submit', async (event) => {
    const companyInput = document.getElementById('feishuProposalCompany');
    const targetSelect = document.getElementById('feishuProposalTarget');
    if (!activeProposalId || !companyInput || !targetSelect) return;

    // pending proposal 的保存由这里接管。只发送非空/实际补充的字段，避免把飞书中
    // 原本存在、但本次邮件没有识别到的字段误清空。真正的 Feishu 写入仍由“批准写入”触发。
    event.preventDefault();
    event.stopImmediatePropagation();

    const submit = document.getElementById('feishuEditSubmit');
    if (submit) submit.disabled = true;
    const fields = {};
    form.querySelectorAll('[data-feishu-field]').forEach((input) => {
      if (HIDDEN_FIELDS.has(input.dataset.feishuField)) return;
      const value = nonEmptyFieldValue(input);
      if (value !== undefined) fields[input.dataset.feishuField] = value;
    });

    try {
      await patchProposal(activeProposalId, {
        company: companyInput.value.trim(),
        record_id: targetSelect.value || null,
        fields,
      });
      dialog.close();
      activeProposalId = null;
      showToast('审核内容已保存，尚未写入飞书');
      refreshButton?.click();
    } catch (error) {
      showToast(`保存失败：${error.message}`);
    } finally {
      if (submit) submit.disabled = false;
    }
  }, true);

  // Observe only direct replacements from the main renderer. The enhancement itself changes
  // descendants, so avoiding subtree observation prevents recursive re-processing.
  const tableObserver = new MutationObserver(() => compactMainTable());
  if (tableShell) tableObserver.observe(tableShell, { childList: true });

  const dialogObserver = new MutationObserver(() => enhanceDialog());
  if (editFields) dialogObserver.observe(editFields, { childList: true });

  const mappingObserver = new MutationObserver(() => hideMappingEntries());
  if (mappingEl) mappingObserver.observe(mappingEl, { childList: true });

  loadFieldSchema();
})();
