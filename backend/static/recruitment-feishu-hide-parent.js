(() => {
  const tableShell = document.getElementById('feishuTableShell');
  const editFields = document.getElementById('feishuEditFields');
  const mappingEl = document.getElementById('feishuMapping');

  function isParentRecord(text) {
    return String(text || '').replace(/\s+/g, '').includes('父记录');
  }

  function hideParentColumn() {
    const table = tableShell?.querySelector('.feishu-table');
    if (!table) return;
    const headers = [...table.querySelectorAll('thead th')];
    const index = headers.findIndex((header) => isParentRecord(header.dataset.fullName || header.textContent));
    if (index < 0) return;
    headers[index].classList.add('feishu-hidden-field');
    table.querySelectorAll('tbody tr').forEach((row) => {
      if (row.classList.contains('feishu-group-row')) {
        const cell = row.querySelector('td');
        if (cell) cell.colSpan = Math.max(1, headers.length - 1);
        return;
      }
      row.children[index]?.classList.add('feishu-hidden-field');
    });
  }

  function hideParentEditor() {
    editFields?.querySelectorAll('[data-feishu-field]').forEach((input) => {
      if (isParentRecord(input.dataset.feishuField)) input.closest('label')?.classList.add('feishu-hidden-field');
    });
  }

  function hideParentMapping() {
    mappingEl?.querySelectorAll('.feishu-mapping-item').forEach((item) => {
      const label = item.querySelector('span')?.textContent || '';
      const mapped = item.querySelector('strong')?.textContent || '';
      if (isParentRecord(label) || isParentRecord(mapped)) item.classList.add('feishu-hidden-field');
    });
  }

  const refresh = () => {
    hideParentColumn();
    hideParentEditor();
    hideParentMapping();
  };

  if (tableShell) new MutationObserver(refresh).observe(tableShell, { childList: true });
  if (editFields) new MutationObserver(refresh).observe(editFields, { childList: true });
  if (mappingEl) new MutationObserver(refresh).observe(mappingEl, { childList: true });
  refresh();
})();
