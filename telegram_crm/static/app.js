const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths = {
  inbox:'<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3 13h5l2 3h4l2-3h5"/>',
  star:'<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2-5.5-2.9-5.5 2.9 1-6.2L3 9.6l6.2-.9Z"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  heart:'<path d="M20.5 5.5a5 5 0 0 0-7 0L12 7l-1.5-1.5a5 5 0 0 0-7 7L12 21l8.5-8.5a5 5 0 0 0 0-7Z"/>',
  shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="M12 8v5m0 3h.01"/>',
  archive:'<rect x="3" y="3" width="18" height="4" rx="1"/><path d="M5 7v13h14V7M9 11h6"/>',
  moon:'<path d="M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z"/>',
  folder:'<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>',
  sparkles:'<path d="m12 3 2.3 6.7L21 12l-6.7 2.3L12 21l-2.3-6.7L3 12l6.7-2.3ZM20 2v4m-2-2h4"/>',
  settings:'<path d="m9 3-1 3-3 1-2 3 2 2-1 3 3 2 1 3h4l1-3 3-1 2-3-2-2 1-3-3-2-1-3Z"/><circle cx="10.5" cy="11.5" r="3"/>',
  send:'<path d="m21 3-7 18-4-7-7-4Z"/><path d="m10 14 6-6"/>',
  download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  upload:'<path d="M12 16V4m-5 5 5-5 5 5M4 16v5h16v-5"/>',
  refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 6a8 8 0 0 1 13 2M5 16a8 8 0 0 0 13 2"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  pin:'<path d="m16 3 5 5-5 2-2 5-3-2-6 6m2-8 2-5Z"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  megaphone:'<path d="M3 10v4a1 1 0 0 0 1 1h3l7 5V4L7 9H4a1 1 0 0 0-1 1Z"/><path d="M18 9a4 4 0 0 1 0 6"/>',
  short:'<path d="M4 5h16v11H9l-5 4Z"/><path d="M9 10.5h4"/>',
  briefcase:'<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V4h6v3M3 13h18"/>',
  trash:'<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6m4-6v6"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.inbox}</svg>`;
function hydrateIcons(root = document) { $$('[data-icon]', root).forEach(el => el.innerHTML = icon(el.dataset.icon)); }
hydrateIcons();
const meta = {
  all:{label:'Все чаты',icon:'inbox'},
  important:{label:'Важные',icon:'star',short:'Важный'},
  job:{label:'Работа',icon:'briefcase',short:'Работа'},
  stalled:{label:'Знакомства без развития',icon:'heart',short:'Без развития'},
  suspected_scam:{label:'Подозрительные',icon:'shield',short:'Проверить'},
  spam:{label:'Спам',icon:'megaphone',short:'Спам'},
  inactive:{label:'Неактивные',icon:'clock',short:'Неактивный'},
  very_old:{label:'Очень старые',icon:'archive',short:'Очень старый'},
  dead:{label:'Мёртвые',icon:'moon',short:'Мёртвый'},
  short:{label:'Ультракороткие',icon:'short',short:'Короткий'},
  active:{label:'Остальные',icon:'inbox',short:'Без меток'},
};
let state = null, category = 'all', selected = new Set(), detailId = null, detailData = null;
// Infinite scroll: render the first `shown` matching chats, load PAGE_SIZE more near the bottom.
const PAGE_SIZE = 50;
let shown = PAGE_SIZE;
// Deep links: /?triage=<bucket or group>&select=all, /?category=job&kind=channel&sort=oldest&q=текст
const linkParams = new URLSearchParams(location.search);
let triageFilter = linkParams.get('triage') || '', selectOnLoad = linkParams.get('select') === 'all';
let searchHits = {query:'', hits:{}}, searchTimer = null, searchSeq = 0;
let lastBatchId = localStorage.getItem('tg-crm-batch-id') || '', toastTimer;
let exportData = null, lastJobError = '', qrPollTimer = null, lastFullRefresh = 0;
function toast(message, error = false) {
  clearTimeout(toastTimer);
  const node = $('#toast'); node.textContent = message; node.hidden = false;
  node.classList.toggle('error', error);
  toastTimer = setTimeout(() => node.hidden = true, error ? 7000 : 4000);
}
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers:{'Content-Type':'application/json', 'X-CRM-Token':state?.token || '', ...options.headers} });
  let data; try { data = await response.json(); } catch { throw Error('Сервис недоступен. Перезапустите приложение Telegram CRM.'); }
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Не удалось выполнить операцию');
  return data;
}
async function action(fn, button) {
  if (button) button.disabled = true;
  try { await fn(); } catch (error) { toast(error.message, true); }
  finally { if (button) button.disabled = false; }
}
const dateFormat = new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'short',year:'numeric',timeZone:'Europe/Moscow'});
const timeFormat = new Intl.DateTimeFormat('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'});
const date = value => value ? dateFormat.format(new Date(value)) : 'Нет данных';
const moscowDate = value => { const p = new Intl.DateTimeFormat('en-CA',{year:'numeric',month:'2-digit',day:'2-digit',timeZone:'Europe/Moscow'}).formatToParts(new Date(value)); const v = Object.fromEntries(p.map(x=>[x.type,x.value])); return `${v.year}-${v.month}-${v.day}`; };
const initials = title => title.split(/\s+/).slice(0,2).map(x => x[0]).join('').toUpperCase();
const badge = key => `<span class="badge ${key}">${icon(meta[key].icon)}${escapeHTML(meta[key].short || meta[key].label)}</span>`;
const countLabel = number => `${number} ${number % 10 === 1 && number % 100 !== 11 ? 'чат' : number % 10 >= 2 && number % 10 <= 4 && (number % 100 < 12 || number % 100 > 14) ? 'чата' : 'чатов'}`;

// null: no match. '': matched on the visible fields. Otherwise the snippet of a matching message.
function searchHit(row, query) {
  if ([row.title,row.username,row.last_message,row.annotation.notes].some(x => (x || '').toLocaleLowerCase('ru').includes(query))) return '';
  return searchHits.query === query ? searchHits.hits[row.id] ?? null : null;
}
function searchMessages() {
  clearTimeout(searchTimer);
  const query = $('#search').value.trim().toLocaleLowerCase('ru'), seq = ++searchSeq;
  if (query.length < 2) { searchHits = {query:'', hits:{}}; return; }
  searchTimer = setTimeout(async () => {
    try {
      const result = await api(`/api/search?q=${encodeURIComponent(query)}`);
      if (seq === searchSeq) { searchHits = {query, hits:result.hits}; renderTable(); }
    } catch (error) { toast(error.message, true); }
  }, 250);
}
// Same order as primary_category on the server: most urgent status first.
const STATUS_ORDER = ['suspected_scam','important','job','spam','stalled','dead','very_old','inactive','short','active'];
const statusRank = row => STATUS_ORDER.indexOf(row.primary_category);
const SORTS = {
  name:(a,b) => a.title.localeCompare(b.title,'ru'),
  name_desc:(a,b) => b.title.localeCompare(a.title,'ru'),
  status:(a,b) => statusRank(a) - statusRank(b),
  status_desc:(a,b) => statusRank(b) - statusRank(a),
  // Chats without any messages stay at the end in both date directions.
  oldest:(a,b) => (a.last_message_at || '9999').localeCompare(b.last_message_at || '9999'),
  newest:(a,b) => (b.last_message_at || '').localeCompare(a.last_message_at || ''),
  priority:(a,b) => b.outgoing_30d - a.outgoing_30d || b.recent_messages - a.recent_messages || b.priority_score - a.priority_score,
  priority_asc:(a,b) => a.outgoing_30d - b.outgoing_30d || a.recent_messages - b.recent_messages || a.priority_score - b.priority_score,
};
// Column header -> [sort of the first click, sort of the second click], and the arrow each one shows.
const COLUMN_SORTS = {name:['name','name_desc'],status:['status','status_desc'],date:['newest','oldest'],frequency:['priority','priority_asc']};
const ASCENDING = new Set(['name','status','oldest','priority_asc']);
function renderSortHeaders() {
  const sort = $('#sort').value;
  $$('[data-sort-column]').forEach(th => {
    const active = COLUMN_SORTS[th.dataset.sortColumn].includes(sort);
    if (active) th.setAttribute('aria-sort', ASCENDING.has(sort) ? 'ascending' : 'descending'); else th.removeAttribute('aria-sort');
    $('.sort-arrow', th).textContent = active ? (ASCENDING.has(sort) ? '↑' : '↓') : '↕';
  });
}
function visibleRows() {
  if (!state) return [];
  const query = $('#search').value.trim().toLocaleLowerCase('ru');
  const kind = $('#kind-filter').value;
  let rows = state.chats.filter(row => (triageFilter ? row.triage && (row.triage.bucket === triageFilter || row.triage.group === triageFilter) : category === 'all' || row.categories.includes(category)) && (kind === 'all' || row.kind === kind));
  if (query) rows = rows.filter(row => searchHit(row, query) !== null);
  const compare = SORTS[$('#sort').value];
  if (compare) rows.sort(compare);
  return rows;
}
function triageTitle() {
  if (!triageFilter || !state.triage) return '';
  return state.triage.groups[triageFilter] || state.triage.buckets[triageFilter]?.label || 'Разбор';
}
function renderTriageNavigation() {
  const node = $('#triage-navigation'); node.hidden = !state.triage;
  if (!state.triage) return;
  const total = group => Object.values(state.triage.buckets).filter(b => b.group === group).reduce((sum, b) => sum + b.count, 0);
  const icons = {remove:'trash',review:'search',keep:'check'};
  node.innerHTML = `<p class="nav-heading">РАЗБОР</p>` + Object.entries(state.triage.groups).map(([key,label]) => `<button class="nav-item ${triageFilter === key ? 'active' : ''}" data-triage="${key}" title="${label}"><span>${icon(icons[key])}</span><span>${label}</span><span class="nav-count">${total(key)}</span></button>`).join('');
}
function setTriage(key) { triageFilter = key; category = 'all'; shown = PAGE_SIZE; history.replaceState(null,'',`?triage=${encodeURIComponent(key)}`); renderNavigation(); renderTable(); }
function renderNavigation() {
  renderTriageNavigation();
  $('#navigation').innerHTML = ['all','important','job','stalled','suspected_scam','spam','inactive','very_old','dead','short'].map(key => `<button class="nav-item ${!triageFilter && category === key ? 'active' : ''}" data-category="${key}" aria-label="${meta[key].label}" title="${meta[key].label}" ${category === key ? 'aria-current="page"' : ''}><span>${icon(meta[key].icon)}</span><span>${meta[key].label}</span><span class="nav-count">${key === 'all' ? state.chats.length : state.counts[key]}</span></button>`).join('');
  const captions = {important:'Частота + закрепления + ваши метки',job:'Рекрутеры, вакансии, собеседования',stalled:`>${state.settings.stalled_days} дн. без отмеченного прогресса`,suspected_scam:'Сигналы, которые стоит проверить',very_old:`Последний контакт > ${state.settings.old_months} месяцев`,dead:`> ${state.settings.dead_years} г. без сообщений`,spam:`≥ ${state.settings.spam_streak} входящих подряд без ответа`,short:`< ${state.settings.short_chat_messages} сообщений за всю историю`};
  $('#metrics').innerHTML = ['important','job','stalled','suspected_scam','spam','short','very_old','dead'].map(key => `<button class="metric ${key} ${category === key ? 'selected' : ''}" data-category="${key}"><span class="metric-top"><span class="metric-icon">${icon(meta[key].icon)}</span>${key === 'stalled' ? 'Знакомства зависли' : meta[key].label}</span><span class="metric-value">${state.counts[key]}</span><span class="metric-sub">${escapeHTML(captions[key])}</span><span class="metric-arrow">↗</span></button>`).join('');
}
function resetPage() { shown = PAGE_SIZE; renderTable(); window.scrollTo({top: Math.min(window.scrollY, $('.inbox').offsetTop)}); }
function renderTable() {
  renderSortHeaders();
  const filtered = visibleRows();
  const rows = filtered.slice(0, shown);
  const query = $('#search').value.trim().toLocaleLowerCase('ru');
  $('#list-title').textContent = triageTitle() || meta[category].label;
  $('#category-filter').value = category;
  $('#list-caption').textContent = countLabel(filtered.length);
  $('#chat-rows').innerHTML = rows.map((row,index) => {
    const other = row.categories.filter(c => c !== row.primary_category);
    const triageTag = triageFilter && row.triage ? `<div class="secondary-tags">${escapeHTML(row.triage.label)}</div>` : '';
    const amplitude = Math.min(7, Math.ceil(row.outgoing_30d / 4));
    return `<tr class="chat-row ${selected.has(row.id) ? 'selected' : ''}" data-chat-id="${escapeHTML(row.id)}" tabindex="0" aria-label="Открыть чат ${escapeHTML(row.title)}"><td class="check-cell"><input type="checkbox" data-select="${escapeHTML(row.id)}" aria-label="Выбрать ${escapeHTML(row.title)}" ${selected.has(row.id) ? 'checked' : ''}></td><td><div class="chat-person"><div class="avatar ${row.kind} c${index % 4}">${escapeHTML(initials(row.title))}</div><div><div class="chat-name">${escapeHTML(row.title)}${row.pinned ? `<span class="tiny-icon" title="Закреплён">${icon('pin')}</span>` : ''}${row.unread_count ? `<span class="tiny-icon" title="Непрочитанных: ${row.unread_count}">●</span>` : ''}</div>${(hit => hit ? `<p class="chat-preview search-hit">Найдено: ${escapeHTML(hit)}</p>` : `<p class="chat-preview">${row.last_outgoing ? 'Вы: ' : ''}${escapeHTML(row.last_message || 'Нет текстовых сообщений')}</p>`)(query ? searchHit(row, query) : '')}</div></div></td><td>${badge(row.primary_category)}${other.length ? `<div class="secondary-tags">+ ${other.map(c => escapeHTML(meta[c].short)).join(' · ')}</div>` : ''}${triageTag}</td><td class="date-column"><div class="last-date">${escapeHTML(date(row.last_message_at))}</div><div class="idle-text">${row.idle_days === null ? 'Нет истории' : row.idle_days === 0 ? 'Были на связи сегодня' : `${row.idle_days} дн. без общения`}</div></td><td class="frequency-column"><div class="frequency"><div class="frequency-bars" aria-hidden="true">${[0,1,2,3,4,5,6].map((n) => `<i class="bar-${n} ${n < amplitude ? 'on' : ''}"></i>`).join('')}</div><span title="Ваши сообщения за 30 дней в загруженной истории; всего в чате: ${row.recent_messages}${row.frequency_is_lower_bound ? '+' : ''}. Может быть нижней оценкой">${row.outgoing_30d}${row.frequency_is_lower_bound ? '+' : ''}</span></div></td><td class="row-arrow"><button tabindex="-1" aria-label="Открыть карточку">›</button></td></tr>`;
  }).join('');
  $('#table-count').textContent = `Показано ${rows.length} из ${countLabel(filtered.length)}${filtered.length !== state.chats.length ? ` (всего ${state.chats.length})` : ''}${rows.length < filtered.length ? ' · прокрутите, чтобы загрузить ещё' : ''}`;
  // Header checkbox and "select all" act on every chat matching the category and filters, on all pages.
  const filteredIds = new Set(filtered.map(row => row.id));
  const selectedHere = [...selected].filter(id => filteredIds.has(id)).length, hidden = selected.size - selectedHere;
  $('#batch-bar').hidden = selected.size === 0;
  $('#selection-count').textContent = `Выбрано: ${selected.size}${hidden ? ` · вне текущего фильтра: ${hidden}` : ''}${selected.size > 2000 ? ' · для LLM — до 2000' : ''}`;
  const allSelected = filtered.length > 0 && selectedHere === filtered.length;
  $('#select-all').checked = allSelected;
  $('#select-all').indeterminate = !allSelected && selectedHere > 0;
  $('#select-filtered-label').textContent = allSelected ? `Снять выбор по фильтру (${filtered.length})` : `Выбрать все по фильтру (${filtered.length})`;
  $('#select-filtered').disabled = filtered.length === 0;
  const empty = $('#empty-state'); empty.hidden = filtered.length > 0;
  if (!rows.length) empty.innerHTML = state.chats.length ? `<div class="empty-icon">${icon('search')}</div><h3>Здесь пока тихо</h3><p>Чатов по выбранному фильтру нет. Попробуйте другую категорию или измените поиск.</p>` : `<div class="empty-icon">${icon('inbox')}</div><h3>Начнём с ваших диалогов</h3><p>Подключите Telegram для анализа переписок или посмотрите, как всё работает, на демонстрационных данных.</p><div class="empty-actions"><button class="button primary" data-action="connect">${icon('send')}Подключить Telegram</button><button class="button" data-action="demo">Посмотреть демо</button></div>`;
}
function render() {
  if (!state) return;
  renderNavigation(); renderTable();
  $('#connection-text').textContent = state.telegram.authorized ? state.telegram.account?.title || 'Telegram подключён' : 'Подключить Telegram';
  $('#workspace-caption').textContent = state.demo ? 'Демонстрационный режим' : state.telegram.authorized ? 'Аккаунт подключён' : 'Локальная CRM';
  $('#llm-nav .new-label').textContent = `${state.settings.llm_messages_per_chat} сообщ.`;
  $('#notice').hidden = !state.demo;
  if (state.demo) $('#notice').innerHTML = `<span>${icon('info')}</span><span><strong>Демонстрационный режим.</strong> Все имена и сообщения вымышлены. Подключите аккаунт, чтобы увидеть свои чаты.</span><button data-action="connect">Подключить Telegram ↗</button>`;
  $('#scan-caption').textContent = state.last_scan_at ? `${state.demo ? 'Демо обновлено' : 'Обновлено'} ${timeFormat.format(new Date(state.last_scan_at))}` : 'Ещё не синхронизировано';
  const scan = state.scan, llm = state.llm_job;
  $('#scan-button').disabled = scan.running;
  $('#full-scan-button').disabled = scan.running || !state.telegram.authorized;
  $('#scan-button').innerHTML = `${icon('refresh')}${scan.running ? 'Выполняется…' : 'Обновить чаты'}`;
  const job = $('#job-status'); job.hidden = !(scan.running || llm.running || scan.error || llm.error);
  job.classList.toggle('error', !!(scan.error || llm.error));
  if (!job.hidden) job.innerHTML = `<span>${escapeHTML(scan.error || llm.error || (scan.running ? `${scan.message} · ${scan.done}/${scan.total || '…'}` : `LLM: проанализировано ${llm.done} из ${llm.total}`))}</span>${scan.running ? '<button data-action="cancel-scan">Остановить</button>' : ''}`;
  const error = scan.error || llm.error;
  if (error && error !== lastJobError) { toast(error,true); lastJobError = error; }
}
async function refresh() {
  const incoming = await api('/api/state'); state = incoming; lastFullRefresh = Date.now();
  const ids = new Set(state.chats.map(c => c.id)); selected = new Set([...selected].filter(id => ids.has(id)));
  if (selectOnLoad) { selectOnLoad = false; visibleRows().forEach(row => selected.add(row.id)); }
  render();
}
function setCategory(key) { if (triageFilter) { triageFilter = ''; history.replaceState(null,'',location.pathname); } category = key; shown = PAGE_SIZE; renderNavigation(); renderTable(); }
function modal(title, content) {
  $('#modal-title').textContent = title; $('#modal-body').innerHTML = content;
  if (!$('#modal').open) $('#modal').showModal();
  hydrateIcons($('#modal'));
}
function closeModal() { if(qrPollTimer){clearInterval(qrPollTimer);qrPollTimer=null;api('/api/auth/qr/cancel',{method:'POST'}).catch(()=>{});} $('#modal').close(); }
$('#close-modal').onclick = closeModal;
$('#modal').addEventListener('click', event => { if (event.target === $('#modal')) { const box = $('#modal').getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) closeModal(); } });

async function openDetail(chatId) {
  detailData = await api(`/api/chats/${encodeURIComponent(chatId)}`); detailId = chatId;
  const row = detailData, a = row.annotation;
  const options = (values, current) => Object.entries(values).map(([v,label]) => `<option value="${v}" ${String(current ?? 'auto') === v ? 'selected' : ''}>${label}</option>`).join('');
  const ai = row.llm?.result;
  $('#detail').innerHTML = `<div class="detail-top"><span>КАРТОЧКА ПЕРЕПИСКИ</span><button class="icon-button" data-detail-action="close" aria-label="Закрыть карточку">${icon('close')}</button></div><div class="detail-person"><div class="avatar">${escapeHTML(initials(row.title))}</div><div><h2>${escapeHTML(row.title)}</h2><p>${({user:'Личный чат',group:'Группа',channel:'Канал',bot:'Бот'})[row.kind]}${row.archived ? ' · В архиве' : ''}${row.demo ? ' · Демо' : ''}</p></div></div><div class="detail-badges">${row.categories.map(badge).join('')}</div><div class="detail-stat"><div><strong>${row.recent_messages}${row.frequency_is_lower_bound ? '+' : ''}</strong><small>сообщений / 30 дней</small></div><div><strong>${row.idle_days ?? '—'}</strong><small>дней без общения</small></div><div><strong>${row.loaded_messages}</strong><small>сообщений загружено</small></div></div><h3>Почему чат выделен</h3><ul class="reason-list">${(row.reasons.length ? row.reasons : ['Специальных сигналов не обнаружено']).map(reason => `<li>${escapeHTML(reason)}</li>`).join('')}</ul>${ai ? `<h3>Анализ LLM${row.llm_stale ? ' · УСТАРЕЛ' : ''}</h3><div class="llm-result"><strong>${escapeHTML(row.llm.source)} · уверенность ${Math.round(ai.confidence*100)}%</strong><p>${escapeHTML(ai.reason)}</p><div>${escapeHTML(ai.suggested_action)}</div><small>Сообщения: ${ai.evidence_message_ids.map(escapeHTML).join(', ') || 'нет ссылок'}</small></div>` : ''}<h3>Ваши отметки</h3><form id="annotation-form"><div class="detail-form"><label class="field">Важность<select name="important">${options({auto:'Автоматически',true:'Важный',false:'Не важный'},a.important)}</select></label><label class="field">Знакомство<select name="dating">${options({auto:'Определить по контексту',true:'Да, знакомство',false:'Нет'},a.dating)}</select></label><label class="field">Работа и вакансии<select name="job">${options({auto:'Определить по словам',true:'Да, про работу',false:'Нет'},a.job)}</select></label><label class="field">Этап<select name="stage">${options({new:'Без этапа',talking:'Общаемся',meeting_planned:'Планируем встречу',met:'Встретились',kissed:'Поцеловались',sex_protected:'Занялись защищённым сексом',sex_unprotected:'Занялись незащищённым сексом',regular_partner:'Постоянный половой партнёр',relationship:'Отношения',closed:'Завершено'},a.stage)}</select></label><label class="field">Последний прогресс<input name="last_progress_at" type="date" value="${a.last_progress_at ? moscowDate(a.last_progress_at) : ''}"></label><label class="field full">Проверка подозрения<select name="risk_review">${options({auto:'Автоматические сигналы',trusted:'Проверено, доверяю',scam:'Подозрение подтверждено мной'},a.risk_review)}</select></label><label class="field full">Заметка<textarea name="notes" placeholder="Контекст, договорённости, что важно помнить…">${escapeHTML(a.notes)}</textarea></label><label class="field full">Ваш черновик ответа<textarea name="reply_draft" placeholder="Напишите ответ. Его можно скопировать в Telegram.">${escapeHTML(a.reply_draft)}</textarea></label></div><div class="form-actions"><button type="button" class="button" data-detail-action="copy-reply">${icon('copy')}Копировать ответ</button><button class="button primary" type="submit">Сохранить</button></div></form><h3>Последние сообщения</h3><div class="message-history">${row.messages.slice(-30).map(message => `<div class="message ${message.outgoing ? 'outgoing' : ''}" id="message-${message.id}">${chatMedia.fetchable.has(message.media) ? `<div class="media-slot" data-media-id="${message.id}"></div>` : ''}${message.forwarded ? '[Переслано] ' : ''}${message.text ? escapeHTML(message.text) : `<span class="media-label">${escapeHTML(chatMedia.label(message.media))}</span>`}<small>${message.outgoing ? 'Вы' : 'Входящее'} · ${timeFormat.format(new Date(message.date))} · #${message.id}</small></div>`).join('') || '<p class="field-hint">Нет доступной истории</p>'}</div>${chatExport.markup(row.loaded_messages, row.demo)}${row.demo ? '' : `<div class="export-buttons"><button type="button" class="export-button remove-chat" id="remove-chat">${chatExport.removeLabel(row.kind)}</button></div>`}<div class="details-link">${row.demo ? '<span class="field-hint">Демонстрационный чат — переход в Telegram недоступен</span>' : `<a class="button" href="${escapeHTML(row.telegram_url)}">${icon('send')}Открыть в Telegram ↗</a>`}</div>`;
  $('#detail').hidden = false; $('#drawer-scrim').hidden = false;
  $('#annotation-form').onsubmit = event => {
    event.preventDefault();
    action(async () => {
      const form = new FormData(event.target);
      const tri = value => value === 'auto' ? null : value === 'true';
      const annotation = {important:tri(form.get('important')),dating:tri(form.get('dating')),job:tri(form.get('job')),stage:form.get('stage'),risk_review:form.get('risk_review'),notes:form.get('notes'),reply_draft:form.get('reply_draft'),last_progress_at:form.get('last_progress_at') ? `${form.get('last_progress_at')}T00:00:00+03:00` : null};
      await api(`/api/chats/${encodeURIComponent(chatId)}/annotation`,{method:'PUT',body:JSON.stringify(annotation)});
      toast('Отметки и черновик сохранены'); await refresh(); await openDetail(chatId);
    }, $('button[type=submit]',event.target));
  };
  chatExport.bind($('#detail'), chatId, api, toast);
  const removeButton = $('#remove-chat');
  if (removeButton) removeButton.onclick = () => action(async () => cleanupModal([chatId], row.title, true));
  chatMedia.fill({root: $('#detail'), chatId, request: api, isCurrent: () => detailId === chatId, downloadIcon: icon('download')});
  const history = $('.message-history',$('#detail')); history.scrollTop = history.scrollHeight;
}
function closeDetail() { $('#detail').hidden = true; $('#drawer-scrim').hidden = true; detailId = null; }
$('#drawer-scrim').onclick = closeDetail;
$('#detail').addEventListener('click', event => {
  const button = event.target.closest('[data-detail-action]'); if (!button) return;
  if (button.dataset.detailAction === 'close') closeDetail();
  if (button.dataset.detailAction === 'copy-reply') action(async () => {
    const text = $('[name=reply_draft]',$('#detail')).value;
    if (!text.trim()) throw Error('Сначала напишите черновик');
    await navigator.clipboard.writeText(text); toast('Ответ скопирован');
  },button);
});

async function showQR() {
  if(qrPollTimer) clearInterval(qrPollTimer);
  modal('Вход по QR-коду',`<p class="modal-copy">На телефоне откройте <strong>Telegram → Настройки → Устройства → Подключить устройство</strong>. Наведите камеру на QR и подтвердите вход в свою CRM.</p><div class="qr-box"><img id="telegram-qr" alt="QR-код входа в ваш Telegram" hidden><span id="qr-caption">Подготавливаю QR…</span></div><p class="field-hint">QR обновляется автоматически. Он предназначен только для вашего входа, не делитесь им. Если включён облачный пароль, приложение запросит его после сканирования.</p>`);
  async function poll() {
    const result=await api('/api/auth/qr/status');
    if(result.image){const image=$('#telegram-qr');if(image){image.src=result.image;image.hidden=false;$('#qr-caption').textContent='Сканируйте через раздел «Устройства» в Telegram';}}
    if(result.step==='done'||result.step==='password') {
      clearInterval(qrPollTimer);qrPollTimer=null;await refresh();
      if(result.step==='password')return connect('password');
      closeModal();toast('Telegram подключён. Загружаю чаты…');await api('/api/scan',{method:'POST'});await refresh();
    }else if(result.error){clearInterval(qrPollTimer);qrPollTimer=null;throw Error(result.error);}
  }
  qrPollTimer=setInterval(()=>action(poll),1500);
  await poll();
}

function connect(step = state?.telegram.step === 'qr' ? 'qr' : state?.telegram.step === 'password' ? 'password' : state?.telegram.step === 'code' ? 'code' : 'phone') {
  if(step === 'qr') return showQR();
  if (state.telegram.authorized) { modal('Telegram подключён',`<p class="modal-copy">Аккаунт «${escapeHTML(state.telegram.account?.title || 'Telegram')}» подключён. Нажмите «Обновить чаты» для загрузки переписок.</p><button class="button primary" id="connect-scan">Обновить чаты</button>`); $('#connect-scan').onclick = () => action(async () => { await api('/api/scan',{method:'POST'}); closeModal(); await refresh(); }); return; }
  if (step === 'phone') {
    modal('Подключить Telegram',`${state.telegram.configured ? '<p class="saved-api-hint">API_ID и API_HASH уже сохранены. Для входа без кода нажмите <strong>Войти по QR</strong> — поля заполнять повторно не нужно.</p>' : ''}<p class="modal-copy">Нужны данные личного приложения с <a href="https://my.telegram.org" target="_blank" rel="noreferrer">my.telegram.org → API development tools ↗</a>. Токен @BotFather здесь не используется: у бота нет доступа к вашей переписке.</p><form id="auth-form"><div class="modal-grid"><label class="field">API_ID<input name="api_id" inputmode="numeric" type="number" min="1" required placeholder="Например, 12345678"></label><label class="field">API_HASH<input name="api_hash" type="password" required pattern="[a-fA-F0-9]{32}" autocomplete="off" placeholder="32 символа"></label><label class="field full">Номер вашего Telegram<input name="phone" type="tel" required pattern="\+[1-9][0-9]{7,14}" placeholder="+79991234567" autocomplete="tel"></label></div><p class="field-hint">API_HASH и сессия сохраняются только на этом Mac. Код входа может прийти в Telegram, SMS или другим доступным Telegram способом.</p><div class="form-actions"><button class="button" type="button" id="qr-sign-in">Войти по QR</button><button class="button primary" type="submit">Получить код</button></div></form>`);
  } else if (step === 'code') {
    modal('Код входа',`<p class="modal-copy">${escapeHTML(state.telegram.delivery?.message || 'Куда отправлен код, определяет Telegram. Проверьте служебный чат Telegram на телефоне; SMS для стороннего клиента может быть недоступно.')}</p><p class="field-hint">Код не пришёл? Можно войти по QR через Telegram → Настройки → Устройства → Подключить устройство.</p><form id="auth-form"><label class="field">Код подтверждения / слово / фраза<input name="code" autocomplete="one-time-code" required placeholder="Код из выбранного Telegram способа"></label><div class="form-actions"><button type="button" class="button" id="qr-sign-in">Войти по QR</button>${state.telegram.delivery?.can_resend ? '<button type="button" class="button" id="auth-resend">Повторить доставку</button>' : ''}<button class="button primary" type="submit">Войти</button></div><div class="form-actions"><button type="button" class="text-button" id="auth-back">Другой номер / новые данные API</button></div></form>`);
    $('#auth-back').onclick = () => connect('phone');
  } else {
    modal('Двухэтапная проверка',`<p class="modal-copy">Введите облачный пароль Telegram. Он используется для входа и не сохраняется в CRM.</p><form id="auth-form"><label class="field">Пароль Telegram<input name="password" type="password" autocomplete="current-password" required></label><div class="form-actions"><button class="button primary" type="submit">Подключить аккаунт</button></div></form>`);
  }
  if($('#qr-sign-in')) $('#qr-sign-in').onclick = event => action(async()=>{
    const form = new FormData($('#auth-form'));
    let credentials = null;
    if(step==='phone' && form.get('api_id') && form.get('api_hash')) credentials={api_id:Number(form.get('api_id')),api_hash:form.get('api_hash').trim()};
    if(!credentials && !state.telegram.configured) throw Error('Сначала заполните API_ID и API_HASH; номер телефона для QR не нужен');
    const result=await api('/api/auth/qr',{method:'POST',body:JSON.stringify(credentials)});
    await refresh(); if(result.step==='done'){closeModal();toast('Telegram подключён');}else await showQR();
  },event.currentTarget);
  if($('#auth-resend')) $('#auth-resend').onclick=event=>action(async()=>{
    const seconds=Math.ceil((new Date(state.telegram.delivery.resend_after)-Date.now())/1000);
    if(seconds>0) throw Error(`Telegram разрешит повтор через ${seconds} сек.`);
    await api('/api/auth/resend',{method:'POST'});await refresh();connect('code');
  },event.currentTarget);
  $('#auth-form').onsubmit = event => { event.preventDefault(); action(async () => {
    const form = Object.fromEntries(new FormData(event.target));
    if (step === 'phone') form.api_id = Number(form.api_id);
    const result = await api(`/api/auth/${step === 'phone' ? 'start' : step}`,{method:'POST',body:JSON.stringify(form)});
    event.target.reset(); await refresh();
    if (result.step === 'done') { closeModal(); toast('Telegram подключён. Загружаю чаты…'); await api('/api/scan',{method:'POST'}); await refresh(); }
    else connect(result.step);
  },$('button[type=submit]',event.target)); };
}

function settingsModal() {
  const s = state.settings;
  const number = (name,label,value,min=1,max=3650) => `<label class="field">${label}<input type="number" name="${name}" value="${value}" min="${min}" max="${max}" required></label>`;
  modal('Настройки анализа',`<form id="settings-form"><p class="modal-copy">Порог важности основан на взаимном общении в загруженной истории за 30 дней. Закрепления и ручные отметки дополняют частоту.</p><div class="modal-grid">${number('frequent_messages','Важный: сообщений / 30 дней',s.frequent_messages,4,1000)}${number('history_limit','Загружать сообщений на чат',s.history_limit,20,500)}${number('spam_streak','Спам: входящих подряд без ответа',s.spam_streak,5,500)}${number('short_chat_messages','Ультракороткий: меньше сообщений всего',s.short_chat_messages,2,500)}${number('stalled_days','Знакомство без прогресса, дней',s.stalled_days)}${number('inactive_days','Неактивный чат, дней',s.inactive_days)}${number('old_months','Очень старый, месяцев',s.old_months,1,120)}${number('dead_years','Мёртвый чат, лет',s.dead_years,1,20)}${number('scan_interval_hours','Обновлять каждые N часов (0 = вручную)',s.scan_interval_hours,0,720)}</div><h3 class="modal-section">LLM: OpenAI или Anthropic</h3><p class="field-hint">Модели <code>claude-…</code> идут в Anthropic API, остальные — в OpenAI. Выбранные сообщения передаются провайдеру; API оплачивается отдельно. Копирование пачек работает без ключа.</p><div class="modal-grid"><label class="field">Модель<input name="llm_model" list="llm-models" value="${escapeHTML(s.llm_model)}" required maxlength="120"><datalist id="llm-models"><option value="gpt-6-luna">OpenAI</option><option value="claude-haiku-5-5">Anthropic · Claude Haiku 5.5</option></datalist></label>${number('llm_messages_per_chat','Сообщений из каждого чата',s.llm_messages_per_chat,5,500)}<label class="field">OpenAI API key ${state.llm_keys.openai ? '(сохранён)' : '(не настроен)'}<input type="password" name="openai_key" autocomplete="off" placeholder="оставьте пустым — без изменений"></label><label class="field">Anthropic API key ${state.llm_keys.anthropic ? '(сохранён)' : '(не настроен)'}<input type="password" name="anthropic_key" autocomplete="off" placeholder="оставьте пустым — без изменений"></label><label class="field">Цена входа, $ / 1M токенов<input type="number" name="llm_price_input_per_m" min="0" max="1000" step="0.01" value="${s.llm_price_input_per_m ?? ''}" placeholder="для оценки стоимости"></label><label class="field">Цена выхода, $ / 1M токенов<input type="number" name="llm_price_output_per_m" min="0" max="1000" step="0.01" value="${s.llm_price_output_per_m ?? ''}" placeholder="для оценки стоимости"></label>${number('llm_max_chats','Максимум чатов после обновления',s.llm_max_chats,1,2000)}</div><p class="field-hint">Цены нужны только для оценки перед запуском. Для claude-haiku-5-5 без указанных цен используются $0.10 / $0.50.</p><label class="checkbox-field"><input type="checkbox" name="redact_contacts" ${s.redact_contacts ? 'checked' : ''}>Скрывать имена в заголовках, телефоны, email и @username при экспорте. Сам текст может содержать другие личные сведения.</label><label class="checkbox-field"><input type="checkbox" name="llm_after_scan" ${s.llm_after_scan ? 'checked' : ''}>После обновления автоматически отправлять в OpenAI остальные чаты без свежего анализа, до указанного лимита. Возможны расходы API.</label><p class="field-hint">Обновление по расписанию работает, пока запущен локальный сервер; вкладку браузера можно закрыть. Выключенный Mac не выполняет анализ.</p><div class="form-actions"><button type="button" class="button" id="setup-help">Инструкция по API</button><button class="button primary" type="submit">Сохранить настройки</button></div></form>`);
  $('#setup-help').onclick = setupHelp;
  $('#settings-form').onsubmit = event => { event.preventDefault(); action(async () => {
    const form = new FormData(event.target), settings = {...s};
    for (const key of ['stalled_days','inactive_days','old_months','dead_years','frequent_messages','history_limit','spam_streak','short_chat_messages','scan_interval_hours','llm_max_chats','llm_messages_per_chat']) settings[key] = Number(form.get(key));
    for (const key of ['llm_price_input_per_m','llm_price_output_per_m']) settings[key] = form.get(key) === '' ? null : Number(form.get(key));
    settings.llm_model = form.get('llm_model').trim(); settings.redact_contacts = form.has('redact_contacts'); settings.llm_after_scan = form.has('llm_after_scan');
    for (const provider of ['openai','anthropic']) { const key = form.get(`${provider}_key`).trim(); if (key) await api('/api/llm/key',{method:'PUT',body:JSON.stringify({api_key:key,provider})}); }
    await api('/api/settings',{method:'PUT',body:JSON.stringify(settings)});
    closeModal(); await refresh(); toast('Настройки сохранены');
  },$('button[type=submit]',event.target)); };
}
function setupHelp() {
  modal('API_ID и API_HASH',`<p class="modal-copy"><strong>Для анализа личных чатов:</strong></p><ol class="modal-copy"><li>Откройте <a href="https://my.telegram.org" target="_blank" rel="noreferrer">my.telegram.org ↗</a> и войдите по своему номеру.</li><li>Выберите <strong>API development tools</strong> → Create new application.</li><li>Укажите App title: Telegram CRM, Short name: dialoguecrm, платформу Desktop. URL можно оставить пустым.</li><li>Скопируйте <strong>App api_id</strong> и <strong>App api_hash</strong>.</li><li>В этой панели нажмите <strong>Подключить Telegram</strong> и вставьте их в <strong>API_ID</strong> и <strong>API_HASH</strong>, введите номер, затем код входа и при необходимости облачный пароль.</li></ol><p class="modal-copy">Бот и токен от @BotFather не нужны: бот не видит ваши личные переписки.</p><p class="field-hint">Подробная инструкция находится в файле SETUP.md в папке приложения. Код входа, пароль, API_HASH и файл сессии не публикуйте.</p><div class="form-actions"><button class="button primary" id="help-connect">Подключить Telegram</button></div>`);
  $('#help-connect').onclick = () => connect();
}

function cleanupModal(ids, source, single = false) {
  const rows = state.chats.filter(row => ids.includes(row.id) && !row.demo);
  if (!rows.length) throw Error('Нет чатов для очистки');
  if (!state.telegram.authorized) throw Error('Сначала подключите Telegram');
  const isImportant = row => row.categories.includes('important');
  const leave = rows.filter(row => ['channel','group'].includes(row.kind)), remove = rows.filter(row => ['user','bot'].includes(row.kind));
  const important = rows.filter(isImportant);
  modal('Отписаться / удалить чаты',`<p class="modal-copy">${escapeHTML(source)}: ${countLabel(rows.length)}. Каждый диалог удаляется одним действием Telegram.</p>
  <form id="cleanup-form">
  <label class="checkbox-field"><input type="checkbox" name="unfollow" ${leave.length ? 'checked' : 'disabled'}>Отписаться от каналов и выйти из групп — ${countLabel(leave.filter(r=>!isImportant(r)).length)}</label>
  <label class="checkbox-field"><input type="checkbox" name="delete" ${remove.length ? (single ? 'checked' : '') : 'disabled'}>Удалить личные чаты и ботов <strong>только у себя</strong> — ${countLabel(remove.filter(r=>!isImportant(r)).length)}. У собеседника переписка останется.</label>
  ${important.length ? `<label class="checkbox-field"><input type="checkbox" name="include_important">Включить важные и закреплённые (${important.length}) — по умолчанию они пропускаются</label>` : ''}
  <p class="field-hint">Перед удалением каждого чата его полная текстовая история (до 20 000 сообщений, без медиафайлов) сохраняется в <code>.data/backups/&lt;дата&gt;/</code> как JSON и TXT, плюс снимок всей базы CRM. Если копию сделать не удалось, чат не трогается. Действие в Telegram необратимо.</p>
  <label class="checkbox-field"><input type="checkbox" name="confirm" required>Понимаю: выбранные чаты исчезнут из моего Telegram</label>
  <div class="form-actions"><button type="button" class="button" id="cleanup-cancel">Отмена</button><button class="button danger-solid" type="submit">Сделать копию и очистить</button></div></form>`);
  $('#cleanup-cancel').onclick = closeModal;
  $('#cleanup-form').onsubmit = event => { event.preventDefault(); action(async () => {
    const form = new FormData(event.target);
    const body = {chat_ids: rows.map(r => r.id), unfollow: form.has('unfollow'), delete: form.has('delete'), include_important: form.has('include_important')};
    if (!body.unfollow && !body.delete) throw Error('Выберите хотя бы одно действие');
    const result = await api('/api/cleanup',{method:'POST',body:JSON.stringify(body)});
    closeModal();
    if (single) { selected.delete(ids[0]); closeDetail(); } else selected.clear();
    toast(`Очистка запущена: ${countLabel(result.planned)}${result.skipped.important ? `, важных пропущено: ${result.skipped.important}` : ''}`);
    await refresh();
  }, event.submitter); };
}
function requireSelection() { if (!selected.size) throw Error('Сначала выберите чаты флажками в таблице'); return [...selected]; }
const LLM_MAX_CHATS = 2000;
function requireLLMSelection() { const ids = requireSelection(); if (ids.length > LLM_MAX_CHATS) throw Error(`Для LLM — до ${LLM_MAX_CHATS} чатов за раз, выбрано ${ids.length}`); return ids; }
async function exportBatch() {
  const ids = requireLLMSelection();
  exportData = await api('/api/batches/export',{method:'POST',body:JSON.stringify({chat_ids:ids,redact_contacts:state.settings.redact_contacts})});
  lastBatchId = exportData.batch_id; localStorage.setItem('tg-crm-batch-id',lastBatchId);
  const counts = exportData.message_counts;
  modal('Пачка для анализа LLM',`<p class="modal-copy">${countLabel(exportData.count)}, ${counts.reduce((a,b)=>a+b,0)} сообщений. Из каждого чата взято до ${state.settings.llm_messages_per_chat} последних сообщений, от старых к новым. ${counts.some(n=>n<state.settings.llm_messages_per_chat) ? 'В некоторых чатах сообщений меньше.' : ''}</p><label class="field">Диалоги, промпт и схема ответа<textarea id="export-prompt" class="modal-prompt" readonly></textarea></label><p class="field-hint">Вставьте в выбранную LLM. Затем скопируйте её JSON-ответ и нажмите «Импорт ответа». Идентификатор пачки сохранён в этом браузере на 7 дней.</p><div class="form-actions"><button class="button" id="export-to-import">Импорт ответа</button><button class="button primary" id="copy-export">${icon('copy')}Копировать пачку</button></div>`);
  $('#export-prompt').value = exportData.prompt;
  $('#copy-export').onclick = () => action(async () => { try { await navigator.clipboard.writeText(exportData.prompt); } catch { $('#export-prompt').select(); if (!document.execCommand('copy')) throw Error('Выделите и скопируйте текст вручную'); } toast('Пачка скопирована'); });
  $('#export-to-import').onclick = importModal;
  try { await navigator.clipboard.writeText(exportData.prompt); toast('Диалоги и промпт скопированы'); } catch { /* The explicit copy button retains browser user activation. */ }
}
function importModal() {
  modal('Импорт ответа LLM',`<p class="modal-copy">Вставьте JSON, полученный для скопированной пачки. Результат проверяется по ID чатов и сообщений. Новые сообщения после экспорта потребуют новой пачки.</p><form id="import-form"><label class="field">Идентификатор пачки<input name="batch_id" value="${escapeHTML(lastBatchId)}" required placeholder="ID из экспорта"></label><label class="field">JSON-ответ<textarea class="modal-prompt" name="result" required placeholder='{"results": [...]}'></textarea></label><div class="form-actions"><button class="button primary" type="submit">Проверить и импортировать</button></div></form>`);
  $('#import-form').onsubmit = event => { event.preventDefault(); action(async () => {
    const form = new FormData(event.target), raw = form.get('result').trim().replace(/^```(?:json)?\s*/i,'').replace(/\s*```$/,'');
    let result; try { result = JSON.parse(raw); } catch { throw Error('Не удалось прочитать JSON. Вставьте только объект {"results": [...]}'); }
    const response = await api('/api/batches/import',{method:'POST',body:JSON.stringify({batch_id:form.get('batch_id').trim(),result})});
    closeModal(); await refresh(); toast(`Импортировано результатов: ${response.imported}`);
    if (detailId) await openDetail(detailId);
  },$('button[type=submit]',event.target)); };
}
async function llmModal() {
  if (!selected.size) { modal('Анализ остальных чатов',`<p class="modal-copy">Сначала выберите чаты в таблице. Кнопка «Выбрать остальные для LLM» отмечает чаты, которые ещё не важные и не имеют свежего LLM-анализа (до ${LLM_MAX_CHATS}).</p><p class="modal-copy">Можно скопировать последние сообщения с готовым промптом и импортировать JSON-ответ любой LLM. Или настроить ключ OpenAI / Anthropic и запускать анализ прямо здесь.</p><div class="form-actions"><button class="button" id="llm-import-any">Импорт ответа</button><button class="button primary" id="llm-select-remaining">Выбрать остальные</button></div>`); $('#llm-import-any').onclick=importModal; $('#llm-select-remaining').onclick=()=>{closeModal();selectRemaining();}; return; }
  const ids = requireLLMSelection();
  const fmt = n => Math.round(n).toLocaleString('ru-RU');
  const e = await api('/api/llm/estimate',{method:'POST',body:JSON.stringify({chat_ids:ids,redact_contacts:state.settings.redact_contacts})});
  const provider = e.provider === 'anthropic' ? 'Anthropic' : 'OpenAI', keyReady = state.llm_keys[e.provider];
  const cost = e.cost_usd === null ? 'стоимость: укажите цены модели в настройках' : `≈ $${e.cost_usd.toFixed(2)}`;
  modal('Анализ выбранных чатов',`<p class="modal-copy">Выбрано ${countLabel(e.chats)}: ${fmt(e.messages)} сообщений (до ${e.messages_per_chat} из чата). Модель <strong>${escapeHTML(e.model)}</strong> через ${provider}.</p>
  <div class="folder-row"><span>Запросов</span><span>${e.requests} по ${e.chunk_size} чатов, ${e.parallel} параллельно</span></div>
  <div class="folder-row"><span>Токенов на входе / выходе</span><span>≈ ${fmt(e.input_tokens)} / ≈ ${fmt(e.output_tokens)}</span></div>
  <div class="folder-row"><span>Оценка</span><span>${cost}</span></div>
  <p class="field-hint">Оценка приблизительная (±30%). ${state.settings.redact_contacts ? 'Имена заголовков, телефоны, email и @username скрываются; другие личные сведения могут оставаться в тексте.' : 'Скрытие имён и контактов выключено.'} Неудачная часть не сохраняется, остальные продолжают. Ручные отметки имеют приоритет.${e.copy_paste_ok ? '' : ' Для копирования в чат LLM пачка слишком большая — используйте API.'}</p>
  <div class="form-actions"><button class="button" id="llm-copy-instead" ${e.copy_paste_ok ? '' : 'disabled'}>Скопировать вместо API</button><button class="button primary" id="llm-run">${keyReady ? 'Запустить анализ' : `Добавить ключ ${provider}`}</button></div>`);
  $('#llm-copy-instead').onclick = () => action(exportBatch);
  $('#llm-run').onclick = event => action(async () => {
    if (!keyReady) return settingsModal();
    await api('/api/llm/analyze',{method:'POST',body:JSON.stringify({chat_ids:ids,redact_contacts:state.settings.redact_contacts})});
    closeModal(); toast('Анализ LLM запущен'); await refresh();
  },event.currentTarget);
}
function selectRemaining() {
  const candidates = state.chats.filter(row => !row.categories.includes('important') && (!row.llm || row.llm_stale) && row.loaded_messages);
  if (!candidates.length) return toast('Нет чатов, которым нужен новый анализ');
  selected = new Set(candidates.slice(0,LLM_MAX_CHATS).map(row=>row.id)); category='all'; shown=PAGE_SIZE; $('#search').value=''; $('#kind-filter').value='all'; render();
  toast(`Выбрано ${selected.size}${candidates.length>LLM_MAX_CHATS ? ` из ${candidates.length}; остальные можно обработать следующей пачкой` : ''}`);
}
async function foldersModal() {
  const plan = await api('/api/folders/plan');
  modal('Папки в вашем Telegram',`<p class="modal-copy">Категории можно выделить папками CRM. Будут созданы или обновлены только папки этого приложения. Папки видны во всех клиентах Telegram; чаты остаются в своих переписках.</p>${plan.folders.map(folder=>`<div class="folder-row"><span>${escapeHTML(folder.title)}</span><span>${countLabel(folder.chat_ids.length)}</span></div>`).join('')}<p class="field-hint">Пустые категории пропускаются. Лимиты папок и чатов задаёт Telegram; большие категории не обрезаются. При ошибке часть папок уже может быть обновлена. Демо-чаты в Telegram не передаются.</p><div class="form-actions"><button class="button primary" id="apply-folders" ${state.telegram.authorized ? '' : 'disabled'}>Создать / обновить папки</button></div>`);
  $('#apply-folders').onclick = event => action(async () => {
    const response=await api('/api/folders/apply',{method:'POST'}); toast(`Обновлено папок: ${response.updated.length}`); closeModal();
  },event.currentTarget);
}

document.addEventListener('click', event => {
  const triageButton = event.target.closest('[data-triage]');
  if (triageButton) setTriage(triageButton.dataset.triage);
  const categoryButton = event.target.closest('[data-category]');
  if (categoryButton) setCategory(categoryButton.dataset.category);
  const button=event.target.closest('[data-action]');
  if (!button) return;
  if (button.dataset.action === 'connect') connect();
  if (button.dataset.action === 'demo') action(async()=>{await api('/api/demo',{method:'POST'});await refresh();},button);
  if (button.dataset.action === 'cancel-scan') action(async()=>{await api('/api/scan/cancel',{method:'POST'});await refresh();},button);
});
$('#chat-rows').addEventListener('click',event=>{
  const checkbox=event.target.closest('[data-select]');
  if (checkbox) {
    if (checkbox.checked) selected.add(checkbox.dataset.select);
    else selected.delete(checkbox.dataset.select);
    renderTable();return;
  }
  const row=event.target.closest('[data-chat-id]'); if(row) action(()=>openDetail(row.dataset.chatId));
});
$('#chat-rows').addEventListener('keydown',event=>{if(event.target.matches('.chat-row')&&['Enter',' '].includes(event.key)){event.preventDefault();action(()=>openDetail(event.target.dataset.chatId));}});
$('#select-all').onchange = event => toggleFiltered(event.target.checked);
$('#cleanup-selected').onclick = () => action(async () => cleanupModal(requireSelection(), 'Выбрано'));
$('#clear-selection').onclick = () => {selected.clear();renderTable();};
$('#select-remaining').onclick=selectRemaining;
$('#search').oninput=()=>{searchMessages();resetPage();};
// Enter searches the whole Telegram history, which this table cannot show.
$('#search').addEventListener('keydown',event=>{if(event.key==='Enter'&&$('#search').value.trim().length>=2)location.href=`/telegram?search=${encodeURIComponent($('#search').value.trim())}`;});
$('#kind-filter').onchange=resetPage;
$('#category-filter').onchange=event=>setCategory(event.target.value);
$('#sort').onchange=resetPage;
$$('[data-sort-column]').forEach(th => { $('.sort-button', th).onclick = () => {
  const [first, second] = COLUMN_SORTS[th.dataset.sortColumn];
  $('#sort').value = $('#sort').value === first ? second : first;
  resetPage();
}; });
function toggleFiltered(checked) { const rows = visibleRows(); if (checked) rows.forEach(row => selected.add(row.id)); else rows.forEach(row => selected.delete(row.id)); renderTable(); }
$('#select-filtered').onclick = () => { const rows = visibleRows(); toggleFiltered(!(rows.length && rows.every(row => selected.has(row.id)))); };
new IntersectionObserver(entries => {
  if (!entries.some(entry => entry.isIntersecting) || !state || shown >= visibleRows().length) return;
  shown += PAGE_SIZE; renderTable();
}, {rootMargin: '400px 0px'}).observe($('#scroll-sentinel'));
$('#scan-button').onclick=event=>action(async()=>{if(!state.telegram.authorized)return connect();await api('/api/scan',{method:'POST'});await refresh();},event.currentTarget);
$('#full-scan-button').onclick=event=>action(async()=>{if(!state.telegram.authorized)return connect();await api('/api/scan',{method:'POST',body:JSON.stringify({full:true})});toast('Полное обновление запущено');await refresh();},event.currentTarget);
$('#connection').onclick=()=>connect();
$('#settings-nav').onclick=settingsModal;
$('#top-settings').onclick=settingsModal;
$('#llm-nav').onclick=()=>action(llmModal);
$('#folders-nav').onclick=()=>action(foldersModal);
$('#copy-batch').onclick=()=>action(exportBatch);
$('#import-batch').onclick=importModal;
$('#analyze-batch').onclick=()=>action(llmModal);
$('#csv-button').onclick=()=>{window.location.href='/api/export.csv';};
document.addEventListener('keydown',event=>{if((event.metaKey||event.ctrlKey)&&event.key==='k'){event.preventDefault();$('#search').focus();}if(event.key==='Escape'&&!$('#modal').open)closeDetail();});
action(refresh);
// While a scan/LLM job runs, poll only its progress: the full state is heavy and every request
// to the local server pauses the Telegram scan. Reload the chat list every 30 s and when the job ends.
setInterval(async()=>{
  if(document.visibilityState!=='visible'||!state)return;
  const busy=state.scan.running||state.llm_job.running, age=Date.now()-lastFullRefresh;
  try{
    if(busy&&age<30000){
      const status=await api('/api/status');
      const finished=(state.scan.running&&!status.scan.running)||(state.llm_job.running&&!status.llm_job.running);
      state.scan=status.scan;state.llm_job=status.llm_job;
      if(finished)await refresh();else render();
    }else if(age>=5000)await refresh();
  }catch{}
},2000);

// Filters from a deep link, applied once the controls exist.
if (linkParams.get('kind')) $('#kind-filter').value = linkParams.get('kind');
if (linkParams.get('sort')) $('#sort').value = linkParams.get('sort');
if (meta[linkParams.get('category')]) category = linkParams.get('category');
if (linkParams.get('q')) { $('#search').value = linkParams.get('q'); searchMessages(); }
