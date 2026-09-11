(() => {
  const pullButton = document.getElementById('calendarPullGoogleButton');
  const googleDot = document.getElementById('googleCalendarDot');
  const googleState = document.getElementById('googleCalendarState');
  const toast = document.getElementById('toast');

  if (!pullButton || !googleDot) return;

  const defaultLabel = pullButton.textContent;

  function isConnected() {
    return googleDot.classList.contains('is-connected');
  }

  function updateEnabledState() {
    pullButton.disabled = !isConnected();
  }

  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('show');
    window.setTimeout(() => toast.classList.remove('show'), 2400);
  }

  pullButton.addEventListener('click', () => {
    if (!isConnected()) {
      showToast('请先连接 Google Calendar');
      return;
    }

    pullButton.disabled = true;
    pullButton.textContent = '正在从 Google 同步…';
    if (googleState) googleState.textContent = '正在从 Google Calendar 重新拉取当前月份日程…';

    // recruitment-calendar.js 会在收到 recruitment:changed 后重新调用
    // /api/v1/recruitment/calendar/events?include_google=true，并把 Google 原生日程
    // 与本地秋招事项重新合并到当前月份视图中。
    window.dispatchEvent(new CustomEvent('recruitment:changed'));

    window.setTimeout(() => {
      pullButton.textContent = defaultLabel;
      updateEnabledState();
      if (isConnected()) {
        if (googleState) googleState.textContent = 'Google Calendar 已连接 · 当前月份已重新拉取';
        showToast('已从 Google Calendar 重新拉取当前月份');
      }
    }, 800);
  });

  const observer = new MutationObserver(updateEnabledState);
  observer.observe(googleDot, { attributes: true, attributeFilter: ['class'] });

  window.addEventListener('message', updateEnabledState);
  updateEnabledState();
})();
