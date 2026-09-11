(() => {
  const toast = document.getElementById('toast');
  const refreshButton = document.getElementById('feishuRefreshButton');

  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('show');
    window.setTimeout(() => toast.classList.remove('show'), 3000);
  }

  document.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-approve-proposal]');
    if (!button || button.disabled) return;

    // 接管主脚本的“批准写入”点击，跳过原来的 window.confirm 二次确认。
    // 审核队列本身仍是写入前的安全门：只有用户明确点击该按钮才会修改飞书。
    event.preventDefault();
    event.stopImmediatePropagation();

    const proposalId = Number(button.dataset.approveProposal);
    if (!Number.isFinite(proposalId)) return;

    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = '写入中…';

    try {
      const response = await fetch(`/api/v1/recruitment/feishu/proposals/${proposalId}/approve`, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
      });
      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
          const payload = await response.json();
          detail = payload.detail || detail;
        } catch (_) {}
        throw new Error(detail);
      }
      const result = await response.json();
      showToast(result.write_mode === 'create' ? '已在飞书新增招聘记录' : '已更新飞书招聘记录');
      refreshButton?.click();
    } catch (error) {
      showToast(`写入失败：${error.message}`);
      button.disabled = false;
      button.textContent = originalText;
    }
  }, true);
})();
