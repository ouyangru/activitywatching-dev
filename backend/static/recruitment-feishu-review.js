(() => {
  const form = document.getElementById('feishuEditForm');
  const dialog = document.getElementById('feishuEditDialog');
  const toast = document.getElementById('toast');
  const refreshButton = document.getElementById('feishuRefreshButton');
  if (!form || !dialog) return;

  let activeProposalId = null;

  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('show');
    window.setTimeout(() => toast.classList.remove('show'), 3000);
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

  document.addEventListener('click', (event) => {
    const proposalButton = event.target.closest('[data-edit-proposal]');
    if (proposalButton) {
      activeProposalId = Number(proposalButton.dataset.editProposal);
      return;
    }
    if (event.target.closest('[data-edit-record]')) activeProposalId = null;
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
})();
