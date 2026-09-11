(() => {
  const listView = document.getElementById('recruitmentListView');
  const calendarView = document.getElementById('recruitmentCalendarView');
  const grid = document.getElementById('recruitmentCalendarGrid');
  const monthLabel = document.getElementById('calendarMonthLabel');
  const dayTitle = document.getElementById('calendarDayTitle');
  const dayList = document.getElementById('calendarDayList');
  const googleState = document.getElementById('googleCalendarState');
  const googleDot = document.getElementById('googleCalendarDot');
  const googleButton = document.getElementById('googleCalendarButton');
  const disconnectButton = document.getElementById('googleCalendarDisconnect');
  const syncAllButton = document.getElementById('calendarSyncAllButton');
  const addButton = document.getElementById('calendarAddButton');
  const toast = document.getElementById('toast');
  const dialog = document.getElementById('calendarEventDialog');
  const form = document.getElementById('calendarEventForm');
  const localMeta = document.getElementById('calendarLocalMeta');
  const modeSelect = document.getElementById('calendarEventMode');
  const fixedFields = document.getElementById('calendarFixedFields');
  const deadlineFields = document.getElementById('calendarDeadlineFields');
  const googleAllDayRow = document.getElementById('calendarGoogleAllDayRow');
  const googleAllDay = document.getElementById('calendarGoogleAllDay');
  const googleDateRow = document.getElementById('calendarGoogleDateRow');
  const localUrlRow = document.getElementById('calendarLocalUrlRow');
  const googleDescriptionRow = document.getElementById('calendarGoogleDescriptionRow');
  const syncRow = document.getElementById('calendarSyncRow');
  const deleteButton = document.getElementById('calendarDeleteButton');
  const dialogTitle = document.getElementById('calendarDialogTitle');

  let currentMonth = new Date();
  currentMonth = new Date(currentMonth.getFullYear(), currentMonth.getMonth(), 1);
  let selectedDay = localDay(new Date());
  let events = [];
  let googleStatus = { configured: false, connected: false };
  let editing = null;

  const typeLabel = { written_test: '笔试', assessment: '测评', interview: '面试', other: '其他' };
  const statusLabel = { pending: '待处理', uncertain: '待确认', done: '已完成', cancelled: '已取消' };

  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('show');
    window.setTimeout(() => toast.classList.remove('show'), 2400);
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

  function pad(value) {
    return String(value).padStart(2, '0');
  }

  function localDay(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  function monthRange() {
    const start = new Date(currentMonth.getFullYear(), currentMonth.getMonth(), 1);
    const end = new Date(currentMonth.getFullYear(), currentMonth.getMonth() + 1, 1);
    return { start: localDay(start), end: localDay(end) };
  }

  function eventTime(event) {
    if (event.all_day || !event.start || !String(event.start).includes('T')) return '全天';
    const date = new Date(event.start);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
  }

  function eventLabel(event) {
    const company = event.company ? `${event.company} · ` : '';
    return `${eventTime(event) === '全天' ? '' : `${eventTime(event)} `}${company}${event.title || '日程'}`;
  }

  function inputDateTime(raw) {
    if (!raw || !String(raw).includes('T')) return '';
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return String(raw).slice(0, 16);
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function deadlineParts(raw) {
    if (!raw) return { date: '', time: '' };
    if (!String(raw).includes('T')) return { date: String(raw).slice(0, 10), time: '' };
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return { date: String(raw).slice(0, 10), time: String(raw).slice(11, 16) };
    return { date: localDay(date), time: `${pad(date.getHours())}:${pad(date.getMinutes())}` };
  }

  function setView(view, updateUrl = true) {
    const calendar = view === 'calendar';
    listView.hidden = calendar;
    calendarView.hidden = !calendar;
    document.querySelectorAll('[data-recruitment-view]').forEach((button) => {
      button.classList.toggle('is-active', button.dataset.recruitmentView === view);
    });
    if (calendar) {
      loadMonth();
      if (updateUrl && !window.__PERSONAL_HUB_EMBEDDED__ && location.pathname !== '/recruitment/calendar') {
        history.replaceState(null, '', '/recruitment/calendar');
      }
    } else if (updateUrl && !window.__PERSONAL_HUB_EMBEDDED__ && location.pathname !== '/recruitment') {
      history.replaceState(null, '', '/recruitment');
    }
  }

  async function loadGoogleStatus() {
    try {
      googleStatus = await api('/api/v1/recruitment/calendar/google/status');
      googleDot.classList.toggle('is-connected', !!googleStatus.connected);
      disconnectButton.hidden = !googleStatus.connected;
      syncAllButton.disabled = !googleStatus.connected;
      if (!googleStatus.configured) {
        googleState.textContent = 'Google Calendar OAuth 尚未在服务器配置。';
        googleButton.textContent = 'Google 未配置';
        googleButton.disabled = true;
      } else if (!googleStatus.connected) {
        googleState.textContent = '尚未连接 Google Calendar，本地日历仍可正常使用。';
        googleButton.textContent = '连接 Google 日历';
        googleButton.disabled = false;
      } else {
        googleState.textContent = `Google Calendar 已连接 · ${googleStatus.calendar_id || 'primary'}`;
        googleButton.textContent = 'Google 已连接';
        googleButton.disabled = true;
      }
    } catch (error) {
      googleState.textContent = `Google 状态读取失败：${error.message}`;
    }
  }

  async function connectGoogle() {
    try {
      const payload = await api('/api/v1/recruitment/calendar/google/connect', { method: 'POST' });
      const popup = window.open(payload.authorization_url, 'activitywatch-google-calendar', 'width=620,height=760');
      if (!popup) showToast('浏览器阻止了 Google 登录窗口，请允许弹窗后重试');
    } catch (error) {
      showToast(`连接失败：${error.message}`);
    }
  }

  async function disconnectGoogle() {
    try {
      await api('/api/v1/recruitment/calendar/google/disconnect', { method: 'POST' });
      showToast('已断开 Google Calendar');
      await loadGoogleStatus();
      await loadMonth();
    } catch (error) {
      showToast(`断开失败：${error.message}`);
    }
  }

  function renderCalendar() {
    monthLabel.textContent = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long' }).format(currentMonth);
    const first = new Date(currentMonth.getFullYear(), currentMonth.getMonth(), 1);
    const firstWeekday = (first.getDay() + 6) % 7;
    const cursor = new Date(first);
    cursor.setDate(cursor.getDate() - firstWeekday);
    const today = localDay(new Date());
    const byDay = new Map();
    events.forEach((event) => {
      if (!byDay.has(event.day)) byDay.set(event.day, []);
      byDay.get(event.day).push(event);
    });

    const cells = [];
    for (let i = 0; i < 42; i += 1) {
      const day = localDay(cursor);
      const outside = cursor.getMonth() !== currentMonth.getMonth();
      const dayEvents = byDay.get(day) || [];
      const chips = dayEvents.slice(0, 3).map((event) => {
        const classes = [
          'calendar-event-chip',
          event.mode === 'deadline' ? 'is-deadline' : '',
          event.source === 'google' ? 'is-google' : '',
          event.source === 'manual' ? 'is-manual' : '',
          event.synced ? 'is-synced' : '',
        ].filter(Boolean).join(' ');
        return `<button type="button" class="${classes}" data-event-key="${escapeHtml(event.key)}" title="${escapeHtml(eventLabel(event))}">${escapeHtml(eventLabel(event))}</button>`;
      }).join('');
      cells.push(`
        <div class="calendar-day${outside ? ' is-outside' : ''}${day === today ? ' is-today' : ''}${day === selectedDay ? ' is-selected' : ''}" data-day="${day}">
          <div class="calendar-day-number"><b>${cursor.getDate()}</b>${dayEvents.length ? `<small>${dayEvents.length}</small>` : ''}</div>
          <div class="calendar-event-stack">${chips}${dayEvents.length > 3 ? `<div class="calendar-more">还有 ${dayEvents.length - 3} 条</div>` : ''}</div>
        </div>`);
      cursor.setDate(cursor.getDate() + 1);
    }
    grid.innerHTML = cells.join('');
    renderDayPanel();
  }

  function renderDayPanel() {
    const date = new Date(`${selectedDay}T00:00:00`);
    dayTitle.textContent = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' }).format(date);
    const dayEvents = events.filter((event) => event.day === selectedDay);
    if (!dayEvents.length) {
      dayList.innerHTML = '<div class="calendar-empty">这一天暂时没有日程。点击“新增日程”可以直接添加。</div>';
      return;
    }
    dayList.innerHTML = dayEvents.map((event) => {
      const source = event.source === 'google' ? 'Google' : event.source === 'manual' ? '手动' : '邮件';
      const status = statusLabel[event.status] || event.status || '已确认';
      const sync = event.source === 'google' ? 'Google 原生日程' : event.synced ? '已同步 Google' : '仅本地';
      const localActions = event.source === 'google' ? '' : `
        <button class="ghost-button" type="button" data-calendar-action="edit" data-event-key="${escapeHtml(event.key)}">修改</button>
        ${event.status === 'done' ? '' : `<button class="ghost-button" type="button" data-calendar-action="complete" data-event-key="${escapeHtml(event.key)}">完成</button>`}
        ${event.status === 'cancelled' ? '' : `<button class="ghost-button calendar-danger" type="button" data-calendar-action="cancel" data-event-key="${escapeHtml(event.key)}">取消</button>`}
        ${googleStatus.connected ? `<button class="ghost-button" type="button" data-calendar-action="sync" data-event-key="${escapeHtml(event.key)}">${event.synced ? '重新同步' : '同步 Google'}</button>` : ''}
        ${event.synced ? `<button class="ghost-button" type="button" data-calendar-action="unlink" data-event-key="${escapeHtml(event.key)}">移出 Google</button>` : ''}`;
      const googleActions = event.source !== 'google' ? '' : `
        <button class="ghost-button" type="button" data-calendar-action="edit" data-event-key="${escapeHtml(event.key)}">修改</button>
        ${event.html_link ? `<a class="ghost-button" href="${escapeHtml(event.html_link)}" target="_blank" rel="noopener noreferrer">在 Google 打开</a>` : ''}
        <button class="ghost-button calendar-danger" type="button" data-calendar-action="delete-google" data-event-key="${escapeHtml(event.key)}">删除</button>`;
      return `
        <article class="calendar-detail-card${event.source === 'google' ? ' is-google' : ''}">
          <h3>${escapeHtml(event.company ? `${event.company} · ${event.title}` : event.title)}</h3>
          <p>${escapeHtml(eventTime(event))}${event.mode === 'deadline' ? ' · 截止' : ''}</p>
          <div class="calendar-detail-meta"><span>${escapeHtml(source)}</span><span>${escapeHtml(status)}</span><span>${escapeHtml(sync)}</span></div>
          <div class="calendar-detail-actions">${localActions}${googleActions}</div>
        </article>`;
    }).join('');
  }

  async function loadMonth() {
    const { start, end } = monthRange();
    try {
      const payload = await api(`/api/v1/recruitment/calendar/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&include_google=true`);
      events = payload.events || [];
      renderCalendar();
      if (payload.google_error) googleState.textContent = `Google Calendar 暂时读取失败：${payload.google_error}`;
    } catch (error) {
      grid.innerHTML = `<div class="calendar-empty" style="grid-column:1/-1">加载日历失败：${escapeHtml(error.message)}</div>`;
    }
  }

  function updateDialogMode() {
    const google = editing?.source === 'google';
    if (google) {
      const allDay = googleAllDay.checked;
      fixedFields.hidden = allDay;
      googleDateRow.hidden = !allDay;
      deadlineFields.hidden = true;
      return;
    }
    fixedFields.hidden = modeSelect.value !== 'fixed_time';
    deadlineFields.hidden = modeSelect.value !== 'deadline';
    googleDateRow.hidden = true;
  }

  function resetDialog() {
    form.reset();
    editing = null;
    dialogTitle.textContent = '新增日程';
    localMeta.hidden = false;
    googleAllDayRow.hidden = true;
    googleDateRow.hidden = true;
    localUrlRow.hidden = false;
    googleDescriptionRow.hidden = true;
    syncRow.hidden = !googleStatus.connected;
    deleteButton.hidden = true;
    document.getElementById('calendarEventSource').value = 'local';
    document.getElementById('calendarEventCompany').value = '';
    document.getElementById('calendarEventType').value = 'other';
    modeSelect.value = 'fixed_time';
    const defaultStart = `${selectedDay}T09:00`;
    document.getElementById('calendarEventStart').value = defaultStart;
    document.getElementById('calendarEventEnd').value = '';
    document.getElementById('calendarDeadlineDate').value = selectedDay;
    document.getElementById('calendarDeadlineTime').value = '';
    document.getElementById('calendarSyncGoogle').checked = googleStatus.connected;
    updateDialogMode();
  }

  function openAddDialog() {
    resetDialog();
    dialog.showModal();
  }

  function openEditDialog(event) {
    editing = event;
    dialogTitle.textContent = event.source === 'google' ? '修改 Google 日程' : '修改日程';
    document.getElementById('calendarEventSource').value = event.source;
    document.getElementById('calendarEventTitle').value = event.title || '';
    document.getElementById('calendarEventCompany').value = event.company || '';
    document.getElementById('calendarEventType').value = event.item_type || 'other';
    modeSelect.value = event.mode || 'fixed_time';
    document.getElementById('calendarEventUrl').value = event.action_url || '';
    document.getElementById('calendarGoogleDescription').value = event.description || '';

    if (event.source === 'google') {
      localMeta.hidden = true;
      localUrlRow.hidden = true;
      googleDescriptionRow.hidden = false;
      syncRow.hidden = true;
      googleAllDayRow.hidden = false;
      googleAllDay.checked = !!event.all_day;
      document.getElementById('calendarGoogleDate').value = event.day || selectedDay;
      document.getElementById('calendarEventStart').value = event.all_day ? '' : inputDateTime(event.start);
      document.getElementById('calendarEventEnd').value = event.all_day ? '' : inputDateTime(event.end);
      deleteButton.hidden = false;
    } else {
      localMeta.hidden = false;
      localUrlRow.hidden = false;
      googleDescriptionRow.hidden = true;
      googleAllDayRow.hidden = true;
      googleDateRow.hidden = true;
      syncRow.hidden = !googleStatus.connected;
      document.getElementById('calendarSyncGoogle').checked = !!event.synced;
      document.getElementById('calendarEventStart').value = event.mode === 'fixed_time' ? inputDateTime(event.start) : '';
      document.getElementById('calendarEventEnd').value = inputDateTime(event.end);
      const deadline = deadlineParts(event.mode === 'deadline' ? event.start : '');
      document.getElementById('calendarDeadlineDate').value = deadline.date || selectedDay;
      document.getElementById('calendarDeadlineTime').value = deadline.time;
      deleteButton.hidden = true;
    }
    updateDialogMode();
    dialog.showModal();
  }

  async function saveDialog(event) {
    event.preventDefault();
    const source = document.getElementById('calendarEventSource').value;
    const title = document.getElementById('calendarEventTitle').value.trim();
    if (!title) return;
    try {
      if (source === 'google' && editing) {
        const allDay = googleAllDay.checked;
        const payload = {
          title,
          all_day: allDay,
          start: allDay ? document.getElementById('calendarGoogleDate').value : document.getElementById('calendarEventStart').value,
          end: allDay ? null : (document.getElementById('calendarEventEnd').value || null),
          description: document.getElementById('calendarGoogleDescription').value.trim() || null,
        };
        await api(`/api/v1/recruitment/calendar/google-events/${encodeURIComponent(editing.google_event_id)}`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        showToast('Google 日程已更新');
      } else {
        const mode = modeSelect.value;
        const deadlineDate = document.getElementById('calendarDeadlineDate').value;
        const deadlineTime = document.getElementById('calendarDeadlineTime').value;
        const payload = {
          title,
          company: document.getElementById('calendarEventCompany').value.trim(),
          item_type: document.getElementById('calendarEventType').value,
          mode,
          start_at: mode === 'fixed_time' ? (document.getElementById('calendarEventStart').value || null) : null,
          end_at: mode === 'fixed_time' ? (document.getElementById('calendarEventEnd').value || null) : null,
          deadline_at: mode === 'deadline' ? (deadlineTime ? `${deadlineDate}T${deadlineTime}` : deadlineDate || null) : null,
          action_url: document.getElementById('calendarEventUrl').value.trim() || null,
        };
        let localId;
        let alreadySynced = false;
        if (editing?.local_id) {
          localId = editing.local_id;
          alreadySynced = !!editing.synced;
          await api(`/api/v1/recruitment/items/${localId}`, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
          });
        } else {
          const created = await api('/api/v1/recruitment/calendar/items', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
          });
          localId = created.item.id;
        }
        if (googleStatus.connected && (alreadySynced || document.getElementById('calendarSyncGoogle').checked)) {
          await api(`/api/v1/recruitment/calendar/items/${localId}/sync`, { method: 'POST' });
        }
        showToast(editing ? '日程已更新' : '日程已创建');
      }
      dialog.close();
      await loadMonth();
      window.dispatchEvent(new CustomEvent('recruitment:changed'));
    } catch (error) {
      showToast(`保存失败：${error.message}`);
    }
  }

  async function patchLocalStatus(event, status) {
    await api(`/api/v1/recruitment/items/${event.local_id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
    });
    if (event.synced) {
      try {
        await api(`/api/v1/recruitment/calendar/items/${event.local_id}/sync`, { method: 'POST' });
      } catch (error) {
        showToast(`本地状态已更新，但 Google 同步失败：${error.message}`);
      }
    }
  }

  async function handleCalendarAction(action, event) {
    try {
      if (action === 'edit') {
        openEditDialog(event);
        return;
      }
      if (action === 'complete') {
        await patchLocalStatus(event, 'done');
        showToast('已标记完成');
      } else if (action === 'cancel') {
        await patchLocalStatus(event, 'cancelled');
        showToast('事项已取消');
      } else if (action === 'sync') {
        await api(`/api/v1/recruitment/calendar/items/${event.local_id}/sync`, { method: 'POST' });
        showToast('已同步到 Google Calendar');
      } else if (action === 'unlink') {
        await api(`/api/v1/recruitment/calendar/items/${event.local_id}/google`, { method: 'DELETE' });
        showToast('已从 Google Calendar 移除，本地事项保留');
      } else if (action === 'delete-google') {
        await api(`/api/v1/recruitment/calendar/google-events/${encodeURIComponent(event.google_event_id)}`, { method: 'DELETE' });
        showToast('Google 日程已删除');
      }
      await loadMonth();
      window.dispatchEvent(new CustomEvent('recruitment:changed'));
    } catch (error) {
      showToast(`操作失败：${error.message}`);
    }
  }

  grid.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-event-key]');
    if (chip) {
      event.stopPropagation();
      const item = events.find((candidate) => candidate.key === chip.dataset.eventKey);
      if (item) openEditDialog(item);
      return;
    }
    const day = event.target.closest('[data-day]');
    if (!day) return;
    selectedDay = day.dataset.day;
    renderCalendar();
  });

  dayList.addEventListener('click', (event) => {
    const button = event.target.closest('[data-calendar-action]');
    if (!button) return;
    const item = events.find((candidate) => candidate.key === button.dataset.eventKey);
    if (item) handleCalendarAction(button.dataset.calendarAction, item);
  });

  document.querySelectorAll('[data-recruitment-view]').forEach((button) => {
    button.addEventListener('click', () => setView(button.dataset.recruitmentView));
  });

  document.getElementById('calendarPrevMonth').addEventListener('click', () => {
    currentMonth = new Date(currentMonth.getFullYear(), currentMonth.getMonth() - 1, 1);
    loadMonth();
  });
  document.getElementById('calendarNextMonth').addEventListener('click', () => {
    currentMonth = new Date(currentMonth.getFullYear(), currentMonth.getMonth() + 1, 1);
    loadMonth();
  });
  document.getElementById('calendarTodayButton').addEventListener('click', () => {
    const now = new Date();
    currentMonth = new Date(now.getFullYear(), now.getMonth(), 1);
    selectedDay = localDay(now);
    loadMonth();
  });

  addButton.addEventListener('click', openAddDialog);
  googleButton.addEventListener('click', connectGoogle);
  disconnectButton.addEventListener('click', disconnectGoogle);
  syncAllButton.addEventListener('click', async () => {
    syncAllButton.disabled = true;
    try {
      const result = await api('/api/v1/recruitment/calendar/sync-all', { method: 'POST' });
      showToast(result.failed?.length ? `已同步 ${result.synced} 条，${result.failed.length} 条失败` : `已同步 ${result.synced} 条待办`);
      await loadMonth();
    } catch (error) {
      showToast(`同步失败：${error.message}`);
    } finally {
      syncAllButton.disabled = !googleStatus.connected;
    }
  });

  modeSelect.addEventListener('change', updateDialogMode);
  googleAllDay.addEventListener('change', updateDialogMode);
  form.addEventListener('submit', saveDialog);
  document.getElementById('calendarDialogCancel').addEventListener('click', () => dialog.close());
  deleteButton.addEventListener('click', async () => {
    if (!editing || editing.source !== 'google') return;
    await handleCalendarAction('delete-google', editing);
    dialog.close();
  });

  window.addEventListener('message', async (event) => {
    if (event.origin !== location.origin || event.data !== 'activitywatch-google-calendar-connected') return;
    await loadGoogleStatus();
    await loadMonth();
    showToast('Google Calendar 已连接');
  });

  window.addEventListener('recruitment:changed', () => {
    if (!calendarView.hidden) loadMonth();
  });

  loadGoogleStatus().then(() => {
    const initialCalendar = !window.__PERSONAL_HUB_EMBEDDED__ && location.pathname === '/recruitment/calendar';
    setView(initialCalendar ? 'calendar' : 'list', false);
  });
})();
