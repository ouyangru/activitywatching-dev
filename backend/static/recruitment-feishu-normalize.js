(() => {
  function comparable(value) {
    if (Array.isArray(value)) return JSON.stringify(value.map(String));
    if (value === null || value === undefined) return '';
    return JSON.stringify(value);
  }

  function currentValue(input) {
    const type = Number(input.dataset.feishuType);
    if (type === 7) return input.checked;
    if (type === 2) return input.value.trim() === '' ? '' : Number(input.value);
    if (type === 4) return input.value.split(',').map((part) => part.trim()).filter(Boolean);
    if (type === 5) return input.value ? new Date(input.value).getTime() : '';
    return input.value.trim();
  }

  function normalizeOpenDialog() {
    document.querySelectorAll('#feishuEditFields [data-feishu-field]').forEach((input) => {
      input.dataset.original = comparable(currentValue(input));
    });
  }

  document.addEventListener('click', (event) => {
    if (!event.target.closest('[data-edit-record]')) return;
    queueMicrotask(normalizeOpenDialog);
  });
})();
