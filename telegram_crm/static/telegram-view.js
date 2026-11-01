// Telegram-like reading mode. It uses the same API as the panel; removal goes through /api/cleanup,
// so important chats stay protected and every chat is backed up first.
const $ = (selector, root = document) => root.querySelector(selector);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const PAGE = 100;
const COLORS = {
  crimson: {label: 'Крайне рекомендуется удалить', tag: 'Удалить!', rank: 0},
  red: {label: 'Под удаление', tag: 'Удалить', rank: 1},
  yellow: {label: 'Кандидаты на проверку', tag: 'Проверить', rank: 2},
  green: {label: 'Оставляем', tag: 'Оставить', rank: 3},
};
const KINDS = {all: 'Все', user: 'Личные', group: 'Группы', channel: 'Каналы', bot: 'Боты'};
const KIND_LABEL = {user: 'Личный чат', group: 'Группа', channel: 'Канал', bot: 'Бот'};
const colorOf = row => row.triage?.color || 'none';
const rankOf = row => COLORS[colorOf(row)]?.rank ?? 2;
const SORTS = {
  telegram: {label: 'Как в Telegram', compare: (a, b) => orderOf(b) - orderOf(a)},
  date: {label: 'Дата', compare: (a, b) => (a.last_message_at || '').localeCompare(b.last_message_at || '')},
  mine: {label: 'Мои сообщ.', compare: (a, b) => a.outgoing_30d - b.outgoing_30d || a.recent_messages - b.recent_messages},
  name: {label: 'Имя', compare: (a, b) => b.title.localeCompare(a.title, 'ru')},
  unread: {label: 'Непрочит.', compare: (a, b) => a.unread_count - b.unread_count},
  risk: {label: 'Риск', compare: (a, b) => rankOf(b) - rankOf(a)},
};
const removable = row => ['crimson', 'red'].includes(colorOf(row));
const SMART = [
  {key: 'crimson', label: 'Все бордовые', match: row => colorOf(row) === 'crimson'},
  {key: 'red', label: 'Все красные', match: row => colorOf(row) === 'red'},
  {key: 'empty', label: 'Пустые и удалённые', match: row => ['remove_empty', 'remove_deleted'].includes(row.triage?.bucket)},
  {key: 'feeds', label: 'Мёртвые каналы и группы', match: row => row.triage?.bucket === 'remove_feeds_dead'},
  {key: 'bots', label: 'Боты под удаление', match: row => row.triage?.bucket === 'remove_bots'},
  {key: 'old', label: 'Под удаление, тишина 2+ года', match: row => removable(row) && row.idle_days !== null && row.idle_days >= 730},
];

const params = new URLSearchParams(location.search);
const shownColors = new Set(params.get('color') ? params.get('color').split(',') : [...Object.keys(COLORS), 'none']);
if (params.get('color')) shownColors.add('none');
let state = null, kind = KINDS[params.get('kind')] ? params.get('kind') : 'all';
// Telegram's own list: the order of dialogs, archive and the user's folders. Empty until the first answer.
let layout = {dialogs: [], folders: []}, dialogRank = new Map(), folder = params.get('folder') || 'all';
let sort = {key: SORTS[params.get('sort')] ? params.get('sort') : 'telegram', descending: params.get('dir') !== 'asc'};
let colorMode = params.get('colors') !== 'off', onlySelected = false, shown = PAGE, currentId = null;
let hits = {query: '', hits: {}}, searchTimer = null, searchSeq = 0, toastTimer = null, statusTimer = null;
let selectOnLoad = params.get('select') === 'all', layoutLoading = false;
const selected = new Set();
let cleanupIds = [], cleanupSingle = false;
// Whole-history search: results replace the list until the query changes or the search is closed.
let deep = null, deepLoading = false, currentMatch = null;
// Romance review: results replace the list until another folder or search is chosen.
let romanceMode = params.get('view') === 'romance', romanceFilter = 'all', romanceWatch = null;
// The open chat's messages, so older and newer pages can be added around them.
let reading = {messages: [], target: null, canOlder: false, canNewer: false};

function toast(message, error = false) {
  clearTimeout(toastTimer);
  const node = $('#toast'); node.textContent = message; node.hidden = false; node.classList.toggle('error', error);
  toastTimer = setTimeout(() => { node.hidden = true; }, error ? 7000 : 4000);
}
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'Content-Type': 'application/json', 'X-CRM-Token': state?.token || '', ...options.headers}});
  let data; try { data = await response.json(); } catch { throw Error('Сервис недоступен. Перезапустите приложение.'); }
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Не удалось выполнить операцию');
  return data;
}
const plural = (n, one, few, many) => { const a = n % 100, b = n % 10; return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many; };
const chatsLabel = n => `${n} ${plural(n, 'чат', 'чата', 'чатов')}`;
const zone = {timeZone: 'Europe/Moscow'};
const dayKey = value => new Intl.DateTimeFormat('en-CA', zone).format(new Date(value));
const timeOnly = value => new Intl.DateTimeFormat(I18N.locale, {...zone, hour: '2-digit', minute: '2-digit'}).format(new Date(value));
const dayLabel = value => new Intl.DateTimeFormat(I18N.locale, {...zone, day: 'numeric', month: 'long', year: 'numeric'}).format(new Date(value));
function listTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (dayKey(value) === dayKey(Date.now())) return timeOnly(value);
  const sameYear = dayKey(value).slice(0, 4) === dayKey(Date.now()).slice(0, 4);
  return new Intl.DateTimeFormat(I18N.locale, {...zone, day: 'numeric', month: 'short', ...(sameYear ? {} : {year: '2-digit'})}).format(date);
}
const initials = title => title.split(/\s+/).filter(Boolean).slice(0, 2).map(word => Array.from(word)[0]).join('').toUpperCase() || '?';
const avatarClass = id => `a${[...String(id)].reduce((sum, c) => sum + c.charCodeAt(0), 0) % 7}`;
const linkify = html => html.replace(/https?:\/\/[^\s<]+/g, url => `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);

const customFolder = () => layout.folders.find(item => item.id === folder && item.chats);
function orderOf(row) {
  const custom = customFolder();
  const index = custom ? custom.rank.get(row.id) : dialogRank.get(row.id);
  // Chats Telegram did not list go last, newest first.
  return index ?? 1e6 + (Date.now() - Date.parse(row.last_message_at || 0)) / 1e12;
}
function inFolder(row, key = folder) {
  if (key === 'archive') return row.archived;
  const custom = layout.folders.find(item => item.id === key && item.chats);
  if (custom) return custom.rank.has(row.id);
  return !layout.dialogs.length || !row.archived;
}
const isPinned = row => { const custom = customFolder(); return custom ? custom.pinnedSet.has(row.id) : row.pinned; };
function folderRows() {
  const query = $('#search').value.trim().toLocaleLowerCase('ru');
  return state.chats.filter(row => inFolder(row) && (kind === 'all' || row.kind === kind) && (!query || searchHit(row, query) !== null));
}
// Fresh list data from Telegram wins over the last scan: order, pins, archive, unread and the preview.
function applyLayout(next) {
  layout = {...next, folders: (next.folders || []).map(item => item.chats ? {...item, rank: new Map(item.chats.map((id, i) => [id, i])), pinnedSet: new Set(item.pinned)} : item)};
  dialogRank = new Map((next.dialogs || []).map((dialog, i) => [dialog.id, i]));
  if (!state) return;
  const fresh = new Map((next.dialogs || []).map(dialog => [dialog.id, dialog]));
  state.chats.forEach(row => {
    const dialog = fresh.get(row.id);
    if (!dialog) return;
    Object.assign(row, {pinned: dialog.pinned, archived: dialog.archived, unread_count: dialog.unread, last_message: dialog.last_text, last_message_at: dialog.last_at, last_outgoing: dialog.last_outgoing});
  });
  if (folder !== 'all' && folder !== 'archive' && !customFolder() && layout.folders.length) folder = 'all';
}
function searchHit(row, query) {
  if ([row.title, row.username, row.last_message, row.annotation.notes].some(x => (x || '').toLocaleLowerCase('ru').includes(query))) return '';
  return hits.query === query ? hits.hits[row.id] ?? null : null;
}
function visibleRows() {
  const rows = folderRows().filter(row => (onlySelected ? selected.has(row.id) : shownColors.has(colorOf(row))));
  const compare = SORTS[sort.key].compare;
  rows.sort((a, b) => (sort.descending ? compare(b, a) : compare(a, b)));
  return rows;
}

function renderRail() {
  const count = key => state.chats.filter(row => inFolder(row, key)).length;
  const folders = (layout.folders.length ? layout.folders : [{id: 'all'}]).map(item => item.chats
    ? {id: item.id, label: `${item.emoticon ? `${item.emoticon} ` : ''}${item.title}`}
    : {id: 'all', label: 'Все чаты'});
  if (state.chats.some(row => row.archived)) folders.push({id: 'archive', label: 'Архив'});
  $('#folders').innerHTML = folders.map(item => `<button class="rail-item ${folder === item.id ? 'active' : ''}" data-folder="${escapeHTML(item.id)}" title="${escapeHTML(item.label)}" ${folder === item.id ? 'aria-current="true"' : ''}><b>${count(item.id)}</b><span>${escapeHTML(item.label)}</span></button>`).join('');
  $('#kinds').innerHTML = Object.entries(KINDS).map(([key, label]) => {
    const total = state.chats.filter(row => inFolder(row) && (key === 'all' || row.kind === key)).length;
    return `<button class="rail-item ${kind === key ? 'active' : ''}" data-kind="${key}" ${kind === key ? 'aria-current="true"' : ''}><b>${total}</b><span>${label}</span></button>`;
  }).join('');
  $('#sorts').innerHTML = Object.entries(SORTS).map(([key, item]) => {
    const active = sort.key === key;
    return `<button class="rail-item ${active ? 'active' : ''}" data-sort="${key}" title="Нажмите ещё раз, чтобы поменять направление"><b>${active ? (sort.descending ? '↓' : '↑') : '↕'}</b><span>${item.label}</span></button>`;
  }).join('');
}
function renderTools() {
  const base = folderRows();
  $('#color-mode').checked = colorMode;
  $('#legend').innerHTML = Object.entries(COLORS).map(([key, item]) => {
    const count = base.filter(row => colorOf(row) === key).length;
    return `<button class="legend-item" data-color="${key}" aria-pressed="${shownColors.has(key)}"><span class="dot c-${key}"></span><span>${item.label}</span><span class="count">${count}</span></button>`;
  }).join('');
  $('#smart').innerHTML = SMART.map(item => {
    const rows = base.filter(item.match);
    const all = rows.length > 0 && rows.every(row => selected.has(row.id));
    return `<button class="smart-item" data-smart="${item.key}" aria-pressed="${all}" ${rows.length ? '' : 'disabled'}><span>${item.label}</span><span class="count">${rows.length}</span></button>`;
  }).join('') + `<button class="smart-item" data-smart="visible"><span>Всё, что сейчас в списке</span><span class="count">${visibleRows().length}</span></button>`;
  $('#selection').hidden = !selected.size;
  $('#selection-count').textContent = `Выбрано: ${chatsLabel(selected.size)}`;
  $('#only-selected').textContent = onlySelected ? 'Показать все' : 'Только выбранные';
  $('#only-selected').setAttribute('aria-pressed', onlySelected);
  if (!selected.size) onlySelected = false;
}
function rowMarkup(row) {
  const color = colorOf(row), query = $('#search').value.trim().toLocaleLowerCase('ru');
  const hit = query ? searchHit(row, query) : '';
  const preview = hit || `${row.last_outgoing ? I18N.t('Вы: ') : ''}${row.last_message || I18N.t(row.loaded_messages ? 'Вложение' : 'Нет сообщений')}`;
  return `<li class="chat c-${color} ${row.id === currentId ? 'current' : ''}" data-id="${escapeHTML(row.id)}" tabindex="0" aria-label="${escapeHTML(row.title)}">
    <input class="chat-check" type="checkbox" data-select="${escapeHTML(row.id)}" aria-label="Выбрать ${escapeHTML(row.title)}" ${selected.has(row.id) ? 'checked' : ''}>
    <div class="avatar ${avatarClass(row.id)}">${escapeHTML(initials(row.title))}</div>
    <div class="chat-main">
      <div class="chat-top"><span class="chat-title">${isPinned(row) ? '📌 ' : ''}${escapeHTML(row.title)}</span><span class="chat-time">${listTime(row.last_message_at)}</span></div>
      <div class="chat-bottom"><span class="chat-preview">${escapeHTML(preview)}</span>${COLORS[color] ? `<span class="tag c-${color}">${COLORS[color].tag}</span>` : ''}${row.unread_count ? `<span class="unread">${row.unread_count > 999 ? '999+' : row.unread_count}</span>` : ''}</div>
    </div></li>`;
}
function deepResultMarkup(result) {
  const row = state.chats.find(item => item.id === result.chat_id);
  if (!row) return '';
  const color = colorOf(row), words = deep.stems.length;
  const complete = words > 1 && result.best === words ? 'все слова в одном сообщении' : words > 1 && result.covered === words ? 'все слова в этом чате' : '';
  const more = result.total - result.matches.length;
  return `<li class="found"><button class="found-head" data-open-chat="${escapeHTML(row.id)}"><span class="avatar ${avatarClass(row.id)}">${escapeHTML(initials(row.title))}</span><span class="found-title">${escapeHTML(row.title)}</span>${COLORS[color] ? `<span class="tag c-${color}">${COLORS[color].tag}</span>` : ''}<span class="found-count">${result.total}</span></button>
    ${complete ? `<div class="found-note">${complete}</div>` : ''}
    ${result.matches.map(match => `<button class="found-message ${currentMatch === `${row.id}:${match.id}` ? 'current' : ''}" data-open-chat="${escapeHTML(row.id)}" data-message="${Number(match.id)}"><time>${dayLabel(match.date)}, ${timeOnly(match.date)} · ${I18N.t(match.outgoing ? 'Вы' : 'Собеседник')}</time>${highlight(escapeHTML(match.snippet))}</button>`).join('')}
    ${more > 0 ? `<div class="found-more">и ещё ${more} в этом чате</div>` : ''}</li>`;
}
function highlight(html) {
  if (!deep?.stems?.length) return html;
  const words = deep.stems.map(item => item.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/е/g, '[её]')).join('|');
  const pattern = new RegExp(`(<[^>]+>|&[a-z#0-9]+;)|(?<![\\p{L}\\p{N}])(?:${words})[\\p{L}\\p{N}]*`, 'giu');
  return html.replace(pattern, (match, skip) => (skip ? match : `<mark>${match}</mark>`));
}
function renderDeep() {
  const list = $('#chats');
  list.classList.toggle('colored', colorMode);
  const count = deep.results.length;
  $('#list-count').textContent = `Вся история Telegram: ${chatsLabel(count)}${deep.partial ? ' · Telegram ограничил запросы, список может быть неполным' : ''}${deep.not_in_crm ? ` · вне списка чатов: ${deep.not_in_crm}` : ''}`;
  list.innerHTML = count ? deep.results.map(deepResultMarkup).join('') : '<li class="empty">В истории Telegram ничего не нашлось. Попробуйте другое слово или форму слова.</li>';
}
function renderDeepControls() {
  const query = $('#search').value.trim();
  $('#deep-search').hidden = query.length < 2;
  const button = $('#deep-search-run');
  button.disabled = deepLoading;
  button.textContent = deepLoading ? 'Ищу в Telegram…' : deep ? 'Показать обычный список' : 'Искать по всей истории';
}
async function runDeepSearch() {
  const query = $('#search').value.trim();
  if (query.length < 2 || deepLoading) return;
  deepLoading = true; renderDeepControls();
  try {
    const answer = await api(`/api/telegram/search?q=${encodeURIComponent(query)}&private_only=${$('#deep-search-private').checked}`);
    if ($('#search').value.trim() === query) { deep = answer; currentMatch = null; }
  } catch (error) { toast(error.message, true); }
  deepLoading = false; renderDeepControls(); renderList();
  if (deep) $('#chats').scrollTop = 0;
}
function closeDeep() { deep = null; currentMatch = null; renderDeepControls(); resetList(); }
const REC_LABEL = {write: 'Написать', wait: 'Подождать', close: 'Закрыть тему', uncertain: 'Неясно'};
const ENDING_LABEL = {ongoing: 'Ещё общаетесь', they_stopped: 'Перестал отвечать собеседник', i_stopped: 'Перестали отвечать вы', mutual_fade: 'Затихло с обеих сторон', declined: 'Собеседник отказал', moved_off_telegram: 'Общение ушло из Telegram', unclear: 'Причина неясна'};
const INTEREST_LABEL = {high: 'Взаимный интерес высокий', medium: 'Взаимный интерес средний', low: 'Взаимный интерес низкий', unclear: 'Взаимный интерес неясен'};
const REC_ORDER = {write: 0, wait: 1, uncertain: 2, close: 3};
const romanceRows = () => state.chats.filter(row => row.kind === 'user' && (row.romance_candidate || row.romance));
const romanceOrder = (a, b) => REC_ORDER[a.romance.result.recommendation] - REC_ORDER[b.romance.result.recommendation] || b.romance.result.promise - a.romance.result.promise;
// One paste stays small enough for an LLM chat window and for a complete JSON reply.
const ROMANCE_COPY_LIMIT = 20;
const needsRomance = () => romanceRows().filter(row => !row.romance || row.romance.stale).sort((a, b) => b.loaded_messages - a.loaded_messages);
function romanceRowMarkup(row) {
  const r = row.romance?.result;
  const meta = r ? [ENDING_LABEL[r.ending], `уверенность ${Math.round(r.confidence * 100)}%`, row.romance.stale ? 'устарел' : ''].filter(Boolean).join(' · ') : (row.idle_days === null ? '' : `${row.idle_days} дн. без общения`);
  return `<li><button class="romance-row ${row.id === currentId ? 'current' : ''}" data-open-chat="${escapeHTML(row.id)}"><div class="romance-top"><span class="avatar ${avatarClass(row.id)}">${escapeHTML(initials(row.title))}</span><span class="chat-title">${escapeHTML(row.title)}</span>${r ? `<span class="rec rec-${r.recommendation}">${REC_LABEL[r.recommendation]}</span><span class="romance-score" title="Перспективность, 0–100">${Number(r.promise)}</span>` : ''}</div><div class="romance-meta">${escapeHTML(meta)}</div>${r ? `<div class="romance-reason romance-text">${escapeHTML(r.reason)}</div>` : ''}</button></li>`;
}
function renderRomance() {
  const list = $('#chats');
  list.classList.remove('colored');
  const rows = romanceRows(), done = rows.filter(row => row.romance).sort(romanceOrder), todo = needsRomance();
  const count = key => (key === 'none' ? rows.filter(row => !row.romance).length : done.filter(row => row.romance.result.recommendation === key).length);
  const filters = [['all', 'Все', rows.length], ...Object.keys(REC_LABEL).map(key => [key, REC_LABEL[key], count(key)]), ['none', 'Без анализа', count('none')]];
  const shownRows = romanceFilter === 'all' ? [...done, ...rows.filter(row => !row.romance)] : romanceFilter === 'none' ? rows.filter(row => !row.romance) : done.filter(row => row.romance.result.recommendation === romanceFilter);
  const closing = done.filter(row => row.romance.result.recommendation === 'close');
  $('#list-count').textContent = `Анализ романтики: ${chatsLabel(rows.length)}`;
  list.innerHTML = `<li class="romance-head"><div>Советы LLM по фрагменту переписки. Ничего не отправляется.</div>
    <div class="romance-actions"><button class="primary" data-romance="sample" ${todo.length ? '' : 'disabled'}>Проверить на ${Math.min(5, todo.length)} чатах</button><button data-romance="rest" ${todo.length ? '' : 'disabled'}>Анализировать все (${todo.length})</button><button data-romance="copy" ${todo.length ? '' : 'disabled'}>Скопировать для LLM (${Math.min(ROMANCE_COPY_LIMIT, todo.length)})</button><button data-romance="import">Импорт ответа</button>${closing.length ? `<button data-romance="select-close">Выбрать «Закрыть тему» (${closing.length})</button>` : ''}</div>
    <div class="romance-filters">${filters.map(([key, label, total]) => `<button data-romance-filter="${key}" class="${romanceFilter === key ? 'active' : ''}">${label} ${total}</button>`).join('')}</div></li>
    ${shownRows.length ? shownRows.map(romanceRowMarkup).join('') : '<li class="empty">Здесь пока пусто</li>'}`;
}
function romanceCard(row) {
  if (!row.romance) return '';
  const r = row.romance.result;
  const note = {declined: 'Вы получили отказ, поэтому совет написать снят.', unanswered: 'Вы уже написали 3 раза подряд без ответа, поэтому совет написать заменён на «Подождать».'}[row.romance.held_back];
  return `<div class="romance-card"><div class="romance-top"><span class="rec rec-${r.recommendation}">${REC_LABEL[r.recommendation]}</span><strong>Перспективность ${Number(r.promise)} / 100</strong></div>
    <p>${ENDING_LABEL[r.ending]}. ${INTEREST_LABEL[r.mutual_interest]}.</p>
    ${note ? `<p class="romance-note">${note}</p>` : ''}${row.romance.stale ? '<p class="romance-note">Есть новые сообщения, анализ устарел.</p>' : ''}
    <p class="romance-text">${escapeHTML(r.reason)}</p>
    ${r.opener ? `<div class="opener romance-text">${escapeHTML(r.opener)}</div><button data-romance-copy>Скопировать идею</button>` : ''}
    ${r.evidence_message_ids.length ? `<p>Основания: ${r.evidence_message_ids.map(id => `<button data-open-chat="${escapeHTML(row.id)}" data-message="${Number(id)}">#${Number(id)}</button>`).join('')}</p>` : ''}
    <p class="hint">Оценка LLM по фрагменту: встречи и звонки вне Telegram она не видит. Уверенность ${Math.round(r.confidence * 100)}%. ${escapeHTML(row.romance.source)}</p></div>`;
}
function romanceRequest(ids) { return {chat_ids: ids, redact_contacts: state.settings.redact_contacts}; }
async function openRomanceRun(ids) {
  const dialog = $('#romance-dialog'), body = $('#romance-body');
  let estimate;
  try { estimate = await api('/api/romance/estimate', {method: 'POST', body: JSON.stringify(romanceRequest(ids))}); }
  catch (error) { return toast(error.message, true); }
  const provider = estimate.provider === 'anthropic' ? 'Anthropic' : 'OpenAI', keyReady = state.llm_keys[estimate.provider];
  body.innerHTML = `<p class="warning">Текст этих сообщений уйдёт выбранному провайдеру LLM. Это личная переписка другого человека. ${state.settings.redact_contacts ? 'Имена в заголовках и контакты скрываются, но остальной текст уходит как есть.' : 'Скрытие имён и контактов выключено.'}</p>
    <table><tr><td>Чатов</td><td>${estimate.chats}</td></tr><tr><td>Сообщений</td><td>${estimate.messages}</td></tr><tr><td>Модель</td><td>${escapeHTML(estimate.model)}</td></tr><tr><td>Токенов на входе / выходе</td><td>${estimate.input_tokens} / ${estimate.output_tokens}</td></tr>${estimate.cost_usd !== null ? `<tr><td>Оценка</td><td>$${estimate.cost_usd}</td></tr>` : ''}</table>
    <p class="hint">Сообщения передаются только после вашего нажатия. Если ключа нет, скопируйте промпт в свою LLM и вставьте её JSON-ответ через «Импорт ответа».</p>
    <div class="dialog-actions"><button class="action" data-romance-action="cancel">Отмена</button>${estimate.copy_paste_ok ? '<button class="action" data-romance-action="copy">Скопировать промпт</button>' : ''}${keyReady ? `<button class="action primary" data-romance-action="run">Отправить в ${provider}</button>` : ''}</div>`;
  body.dataset.ids = JSON.stringify(ids);
  dialog.showModal();
}
function openRomanceImport(batchId = '') {
  $('#romance-body').innerHTML = `<p>Вставьте JSON-ответ вашей LLM. Он проверяется по ID чатов и сообщений.</p><label>Идентификатор пачки<input id="romance-batch" value="${escapeHTML(batchId)}"></label><textarea id="romance-json" placeholder='{"results": [...]}'></textarea><div class="dialog-actions"><button class="action" data-romance-action="cancel">Отмена</button><button class="action primary" data-romance-action="import">Проверить и импортировать</button></div>`;
  if (!$('#romance-dialog').open) $('#romance-dialog').showModal();
}
async function copyRomance(ids) {
  const dialog = $('#romance-dialog'), body = $('#romance-body');
  const exported = await api('/api/romance/export', {method: 'POST', body: JSON.stringify(romanceRequest(ids))});
  body.innerHTML = `<p class="warning">В буфере личная переписка другого человека. Она уйдёт провайдеру, когда вы вставите её в LLM.</p><p>Вставьте в выбранную LLM. Затем скопируйте её JSON-ответ и импортируйте здесь.</p><textarea id="romance-prompt" readonly></textarea><div class="dialog-actions"><button class="action" data-romance-action="cancel">Закрыть</button><button class="action primary" data-romance-action="to-import">Импорт ответа</button></div>`;
  $('#romance-prompt').value = exported.prompt;
  body.dataset.batch = exported.batch_id;
  try { localStorage.setItem('tg-crm-romance-batch', exported.batch_id); } catch { /* the ID stays on screen */ }
  if (!dialog.open) dialog.showModal();
  try { await navigator.clipboard.writeText(exported.prompt); toast(`Скопировано чатов: ${exported.count}`); } catch { $('#romance-prompt').select(); toast('Выделите и скопируйте текст вручную'); }
}
async function romanceAction(kind) {
  const dialog = $('#romance-dialog'), body = $('#romance-body');
  if (kind === 'cancel') return dialog.close();
  try {
    if (kind === 'run') {
      await api('/api/romance/analyze', {method: 'POST', body: JSON.stringify(romanceRequest(JSON.parse(body.dataset.ids)))});
      dialog.close(); toast('Анализ LLM запущен'); watchRomance();
    } else if (kind === 'copy') {
      await copyRomance(JSON.parse(body.dataset.ids));
    } else if (kind === 'to-import') {
      openRomanceImport(body.dataset.batch);
    } else if (kind === 'import') {
      const raw = $('#romance-json').value.trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
      let result; try { result = JSON.parse(raw); } catch { throw Error('Не удалось прочитать JSON. Вставьте только объект {"results": [...]}'); }
      const answer = await api('/api/romance/import', {method: 'POST', body: JSON.stringify({batch_id: $('#romance-batch').value.trim(), result})});
      dialog.close(); toast(`Импортировано результатов: ${answer.imported}`); await load();
    }
  } catch (error) { toast(error.message, true); }
}
function watchRomance() {
  clearTimeout(romanceWatch);
  romanceWatch = setTimeout(async () => {
    let status;
    try { status = await api('/api/status'); } catch { return watchRomance(); }
    const job = status.llm_job, node = $('#status');
    node.hidden = !(job.running || job.error);
    node.classList.toggle('error', Boolean(job.error));
    node.textContent = job.error || `LLM: проанализировано ${job.done} из ${job.total}`;
    if (job.running) return watchRomance();
    await load();
    if (job.error) { node.hidden = false; node.classList.add('error'); node.textContent = job.error; }
  }, 2000);
}
function renderList() {
  renderDeepControls();
  if (deep) return renderDeep();
  if (romanceMode) return renderRomance();
  const rows = visibleRows();
  const list = $('#chats');
  list.classList.toggle('colored', colorMode);
  list.innerHTML = rows.length ? rows.slice(0, shown).map(rowMarkup).join('') : '<li class="empty">Чатов по этим условиям нет</li>';
  $('#list-count').textContent = `${chatsLabel(rows.length)}${rows.length !== state.chats.length ? ` из ${state.chats.length}` : ''}${layoutLoading ? ' · обновляю порядок из Telegram…' : ''}`;
}
function render() { renderRail(); renderTools(); renderList(); }
function resetList() { shown = PAGE; render(); $('#chats').scrollTop = 0; }

function renderHead(row) {
  const color = colorOf(row);
  $('#chat-head').innerHTML = `<button class="head-button back-to-list" data-action="back">←</button><div class="avatar ${avatarClass(row.id)}">${escapeHTML(initials(row.title))}</div>
    <div class="head-text"><strong>${escapeHTML(row.title)}</strong><small>${KIND_LABEL[row.kind]}${row.username ? ` · @${escapeHTML(row.username)}` : ''}</small></div>
    ${COLORS[color] ? `<span class="pill c-${color}">${COLORS[color].tag}</span>` : ''}
    <button class="head-button ${selected.has(row.id) ? 'on' : ''}" data-action="toggle-select" aria-pressed="${selected.has(row.id)}">${selected.has(row.id) ? 'Выбран' : 'Выбрать'}</button>
    ${row.demo ? '' : `<a class="head-button" href="${escapeHTML(row.telegram_url)}">Открыть в Telegram</a><button class="head-button remove" data-action="remove-chat">${chatExport.removeLabel(row.kind)}</button>`}`;
}
function renderAbout(row) {
  $('#about').hidden = false;
  $('#about-body').innerHTML = `<div class="stats"><div><strong>${row.outgoing_30d}${row.frequency_is_lower_bound ? '+' : ''}</strong>ваших за 30 дней</div><div><strong>${row.recent_messages}${row.frequency_is_lower_bound ? '+' : ''}</strong>всего за 30 дней</div><div><strong>${row.idle_days ?? '—'}</strong>дней тишины</div><div><strong>${row.loaded_messages}</strong>загружено</div></div>
    ${romanceCard(row)}<ul>${(row.reasons.length ? row.reasons : ['Специальных сигналов нет']).map(reason => `<li>${escapeHTML(reason)}</li>`).join('')}</ul>${chatExport.markup(row.loaded_messages, row.demo)}`;
  chatExport.bind($('#about-body'), row.id, api, toast);
}
function messageMarkup(message) {
  const media = chatMedia.fetchable.has(message.media) ? `<div class="media-slot" data-media-id="${Number(message.id)}"></div>` : '';
  const body = message.text ? highlight(linkify(escapeHTML(message.text))) : `<span class="media-label">${escapeHTML(chatMedia.label(message.media))}</span>`;
  return `<div class="msg ${message.outgoing ? 'out' : ''} ${message.id === reading.target ? 'target' : ''}" data-message-id="${Number(message.id)}">${message.forwarded ? `<div class="msg-forward">${I18N.t('Переслано')}</div>` : ''}${media}${body}<span class="msg-time">${timeOnly(message.date)}</span></div>`;
}
function renderMessages(note = '') {
  let day = '', html = reading.canOlder ? '<button class="more-history" data-history="older">Загрузить раньше</button>' : note;
  for (const message of reading.messages) {
    const key = dayKey(message.date);
    if (key !== day) { day = key; html += `<div class="day">${dayLabel(message.date)}</div>`; }
    html += messageMarkup(message);
  }
  if (reading.canNewer) html += '<button class="more-history" data-history="newer">Загрузить позже</button>';
  $('#messages').innerHTML = html || '<div class="history-note">Нет загруженных сообщений</div>';
  const id = currentId;
  chatMedia.fill({root: $('#messages'), chatId: id, request: api, isCurrent: () => currentId === id});
}
async function loadMoreHistory(direction, button) {
  const id = currentId, box = $('#messages');
  button.disabled = true; button.textContent = 'Загружаю из Telegram…';
  const edge = direction === 'older' ? `before=${reading.messages[0]?.id ?? 0}` : `after=${reading.messages.at(-1)?.id ?? 0}`;
  try {
    const {messages} = await api(`/api/chats/${encodeURIComponent(id)}/history?${edge}`);
    if (currentId !== id) return;
    const known = new Set(reading.messages.map(message => message.id));
    const fresh = messages.filter(message => !known.has(message.id));
    if (direction === 'older') { reading.messages = [...fresh, ...reading.messages]; reading.canOlder = fresh.length > 0; }
    else { reading.messages = [...reading.messages, ...fresh]; reading.canNewer = fresh.length > 0; }
    const fromBottom = box.scrollHeight - box.scrollTop, top = box.scrollTop;
    renderMessages();
    box.scrollTop = direction === 'older' ? box.scrollHeight - fromBottom : top;
  } catch (error) { button.disabled = false; button.textContent = 'Не удалось. Повторить'; toast(error.message, true); }
}
// Opens a message that may be far older than the loaded ones, with its surroundings from Telegram.
async function openChatAt(id, messageId) {
  currentMatch = `${id}:${messageId}`;
  document.querySelectorAll('.found-message.current').forEach(node => node.classList.remove('current'));
  document.querySelector(`.found-message[data-open-chat="${CSS.escape(id)}"][data-message="${messageId}"]`)?.classList.add('current');
  currentId = id;
  $('#placeholder').hidden = true; $('#chat-view').hidden = false; $('#app').classList.add('reading');
  $('#messages').innerHTML = '<div class="history-note">Загрузка из Telegram…</div>';
  try {
    const [row, {messages}] = await Promise.all([api(`/api/chats/${encodeURIComponent(id)}`), api(`/api/chats/${encodeURIComponent(id)}/history?around=${messageId}`)]);
    if (currentId !== id) return;
    renderHead(row); renderAbout(row);
    reading = {messages, target: messageId, canOlder: messages.length > 0, canNewer: messages.length > 0};
    renderMessages();
    $(`.msg[data-message-id="${messageId}"]`)?.scrollIntoView({block: 'center'});
  } catch (error) { if (currentId === id) $('#messages').innerHTML = `<div class="history-note">${escapeHTML(error.message)}</div>`; }
}
async function openChat(id) {
  currentId = id; currentMatch = null;
  $('#placeholder').hidden = true; $('#chat-view').hidden = false; $('#app').classList.add('reading');
  document.querySelectorAll('.chat.current').forEach(node => node.classList.remove('current'));
  document.querySelector(`.chat[data-id="${CSS.escape(id)}"]`)?.classList.add('current');
  $('#messages').innerHTML = '<div class="history-note">Загрузка…</div>';
  let row;
  try { row = await api(`/api/chats/${encodeURIComponent(id)}`); }
  catch (error) { if (currentId === id) $('#messages').innerHTML = `<div class="history-note">${escapeHTML(error.message)}</div>`; return; }
  if (currentId !== id) return;
  renderHead(row); renderAbout(row);
  reading = {messages: row.messages, target: null, canOlder: row.frequency_is_lower_bound && !row.demo, canNewer: false};
  renderMessages();
  const box = $('#messages');
  box.scrollTop = box.scrollHeight;
}
function toggleSelected(id, on) { if (on) selected.add(id); else selected.delete(id); }
function applySmart(key) {
  const rows = key === 'visible' ? visibleRows() : folderRows().filter(SMART.find(item => item.key === key).match);
  const all = rows.every(row => selected.has(row.id));
  rows.forEach(row => toggleSelected(row.id, !all));
  render();
}
function openCleanup(single = null) {
  cleanupSingle = Boolean(single);
  cleanupIds = single ? [single] : [...selected];
  const rows = state.chats.filter(row => cleanupIds.includes(row.id) && !row.demo);
  if (!rows.length) return toast('Нет чатов для очистки', true);
  if (!state.telegram.authorized) return toast('Сначала подключите Telegram', true);
  const important = rows.filter(row => row.categories.includes('important'));
  const free = rows.filter(row => !row.categories.includes('important'));
  const leave = free.filter(row => ['channel', 'group'].includes(row.kind)).length;
  const remove = free.filter(row => ['user', 'bot'].includes(row.kind)).length;
  const green = rows.filter(row => colorOf(row) === 'green').length, yellow = rows.filter(row => colorOf(row) === 'yellow').length;
  const job = rows.filter(row => row.categories.includes('job')).length;
  const warnings = [green && `зелёных «оставляем»: ${green}`, yellow && `жёлтых «на проверку»: ${yellow}`, job && `о работе: ${job}`].filter(Boolean);
  $('#cleanup-body').innerHTML = `<p>${cleanupSingle ? `Чат: <strong>${escapeHTML(rows[0].title)}</strong>` : `Выбрано: ${chatsLabel(rows.length)}`}.</p>
    ${warnings.length ? `<p class="warning">Среди выбранных есть ${warnings.join(', ')}. Проверьте, что это намеренно.</p>` : ''}
    <label><input type="checkbox" name="unfollow" ${rows.some(row => ['channel', 'group'].includes(row.kind)) ? 'checked' : 'disabled'}><span>Отписаться от каналов и выйти из групп: ${chatsLabel(leave)}</span></label>
    <label><input type="checkbox" name="delete" ${rows.some(row => ['user', 'bot'].includes(row.kind)) ? (cleanupSingle ? 'checked' : '') : 'disabled'}><span>Удалить личные чаты и ботов только у себя: ${chatsLabel(remove)}. У собеседника переписка останется.</span></label>
    ${important.length ? `<label><input type="checkbox" name="include_important"><span>Включить важные и закреплённые (${important.length}). По умолчанию они пропускаются.</span></label>` : ''}
    <p>Перед удалением текст каждого чата сохраняется в <code>.data/backups</code>, но без фото и видео. Если копию сделать не удалось, чат не трогается. Действие в Telegram необратимо.</p>
    <label><input type="checkbox" name="confirm" required><span>Понимаю: выбранные чаты исчезнут из моего Telegram</span></label>`;
  $('#cleanup-dialog').showModal();
}
async function submitCleanup(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  const body = {chat_ids: cleanupIds, unfollow: form.has('unfollow'), delete: form.has('delete'), include_important: form.has('include_important')};
  if (!body.unfollow && !body.delete) return toast('Выберите хотя бы одно действие', true);
  try {
    const result = await api('/api/cleanup', {method: 'POST', body: JSON.stringify(body)});
    $('#cleanup-dialog').close();
    if (cleanupSingle) selected.delete(cleanupIds[0]); else { selected.clear(); onlySelected = false; }
    render();
    toast(`Очистка запущена: ${chatsLabel(result.planned)}${result.skipped.important ? `, важных пропущено: ${result.skipped.important}` : ''}`);
    watchStatus();
  } catch (error) { toast(error.message, true); }
}
function showStatus(scan) {
  const node = $('#status');
  node.hidden = !(scan.running || scan.error);
  node.classList.toggle('error', Boolean(scan.error));
  node.textContent = scan.error || `${scan.message || ''} · ${scan.done}/${scan.total || '…'}`;
}
function watchStatus() {
  clearTimeout(statusTimer);
  statusTimer = setTimeout(async () => {
    let status;
    try { status = await api('/api/status'); } catch { return watchStatus(); }
    showStatus(status.scan);
    if (status.scan.running) return watchStatus();
    await load();
  }, 2000);
}
async function load() {
  state = await api('/api/state');
  I18N.sync(state.settings);
  applyLayout(layout.dialogs.length ? layout : await api('/api/telegram/layout?cached=true'));
  const ids = new Set(state.chats.map(row => row.id));
  [...selected].filter(id => !ids.has(id)).forEach(id => selected.delete(id));
  if (currentId && !ids.has(currentId)) {  // the open chat was just removed
    currentId = null;
    $('#chat-view').hidden = true; $('#placeholder').hidden = false; $('#about').hidden = true; $('#app').classList.remove('reading');
  }
  if (selectOnLoad) { selectOnLoad = false; visibleRows().filter(row => colorOf(row) !== 'green').forEach(row => selected.add(row.id)); }
  render();
  showStatus(state.scan);
  if (state.scan.running) watchStatus();
  refreshLayout();
}
async function refreshLayout() {
  layoutLoading = true; renderList();
  try {
    const next = await api('/api/telegram/layout');
    layoutLoading = false;
    if (next.stale) toast('Telegram сейчас не ответил: порядок чатов из последнего обновления', true);
    const list = $('#chats'), top = list.scrollTop;
    applyLayout(next);
    render();
    list.scrollTop = top;
  } catch (error) { layoutLoading = false; renderList(); toast(error.message, true); }
}
function searchMessages() {
  clearTimeout(searchTimer);
  const query = $('#search').value.trim().toLocaleLowerCase('ru'), seq = ++searchSeq;
  if (query.length < 2) { hits = {query: '', hits: {}}; return; }
  searchTimer = setTimeout(async () => {
    try {
      const result = await api(`/api/search?q=${encodeURIComponent(query)}`);
      if (seq === searchSeq) { hits = {query, hits: result.hits}; shown = PAGE; render(); }
    } catch (error) { toast(error.message, true); }
  }, 250);
}

document.addEventListener('click', event => {
  const target = event.target;
  if (target.closest('#romance-open')) { romanceMode = true; deep = null; return resetList(); }
  const romanceFilterButton = target.closest('[data-romance-filter]');
  if (romanceFilterButton) { romanceFilter = romanceFilterButton.dataset.romanceFilter; return renderList(); }
  const romanceButton = target.closest('[data-romance]');
  if (romanceButton) {
    const kind = romanceButton.dataset.romance, todo = needsRomance().map(row => row.id);
    if (kind === 'sample') return openRomanceRun(todo.slice(0, 5));
    if (kind === 'rest') return openRomanceRun(todo);
    if (kind === 'copy') return copyRomance(todo.slice(0, ROMANCE_COPY_LIMIT)).catch(error => toast(error.message, true));
    if (kind === 'import') { let batch = ''; try { batch = localStorage.getItem('tg-crm-romance-batch') || ''; } catch { /* typed by hand */ } return openRomanceImport(batch); }
    if (kind === 'select-close') { romanceRows().filter(row => row.romance?.result.recommendation === 'close').forEach(row => selected.add(row.id)); return renderTools(); }
  }
  const romanceDialogButton = target.closest('[data-romance-action]');
  if (romanceDialogButton) return romanceAction(romanceDialogButton.dataset.romanceAction);
  const copyOpener = target.closest('[data-romance-copy]');
  if (copyOpener) { navigator.clipboard.writeText($('.romance-card .opener').textContent).then(() => toast('Ответ скопирован'), () => toast('Выделите и скопируйте текст вручную', true)); return; }
  const moreButton = target.closest('[data-history]');
  if (moreButton) return loadMoreHistory(moreButton.dataset.history, moreButton);
  const opener = target.closest('[data-open-chat]');
  if (opener) return opener.dataset.message ? openChatAt(opener.dataset.openChat, Number(opener.dataset.message)) : openChat(opener.dataset.openChat);
  const folderButton = target.closest('[data-folder]');
  if (folderButton) { folder = folderButton.dataset.folder; romanceMode = false; return resetList(); }
  const kindButton = target.closest('[data-kind]');
  if (kindButton) { kind = kindButton.dataset.kind; romanceMode = false; return resetList(); }
  const sortButton = target.closest('[data-sort]');
  if (sortButton) {
    const key = sortButton.dataset.sort;
    sort = sort.key === key ? {key, descending: !sort.descending} : {key, descending: true};
    return resetList();
  }
  const colorButton = target.closest('[data-color]');
  if (colorButton) {
    const key = colorButton.dataset.color;
    if (shownColors.has(key)) shownColors.delete(key); else shownColors.add(key);
    return resetList();
  }
  const smartButton = target.closest('[data-smart]');
  if (smartButton) return applySmart(smartButton.dataset.smart);
  const actionButton = target.closest('[data-action]');
  if (actionButton?.dataset.action === 'back') { $('#app').classList.remove('reading'); return; }
  if (actionButton?.dataset.action === 'remove-chat') return openCleanup(currentId);
  if (actionButton?.dataset.action === 'toggle-select') {
    toggleSelected(currentId, !selected.has(currentId));
    const on = selected.has(currentId);
    actionButton.classList.toggle('on', on); actionButton.setAttribute('aria-pressed', on); actionButton.textContent = on ? 'Выбран' : 'Выбрать';
    const box = document.querySelector(`[data-select="${CSS.escape(currentId)}"]`);
    if (box) box.checked = on;
    return renderTools();
  }
  if (target.matches('.chat-check')) return;
  const chat = target.closest('.chat[data-id]');
  if (chat) openChat(chat.dataset.id);
});
$('#chats').addEventListener('change', event => {
  if (!event.target.matches('.chat-check')) return;
  toggleSelected(event.target.dataset.select, event.target.checked);
  if (event.target.dataset.select === currentId) {
    const button = $('[data-action="toggle-select"]');
    if (button) { button.classList.toggle('on', event.target.checked); button.textContent = event.target.checked ? 'Выбран' : 'Выбрать'; }
  }
  renderTools();
});
$('#chats').addEventListener('keydown', event => {
  if (event.target.matches('.chat') && event.key === 'Enter') { event.preventDefault(); openChat(event.target.dataset.id); }
});
$('#search').addEventListener('input', () => { deep = null; romanceMode = false; currentMatch = null; searchMessages(); resetList(); });
$('#search').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); runDeepSearch(); } });
$('#deep-search-run').addEventListener('click', () => (deep ? closeDeep() : runDeepSearch()));
$('#deep-search-private').addEventListener('change', () => { if (deep) { deep = null; runDeepSearch(); } });
$('#color-mode').addEventListener('change', event => { colorMode = event.target.checked; renderList(); });
$('#only-selected').addEventListener('click', () => { onlySelected = !onlySelected; resetList(); });
$('#clear-selection').addEventListener('click', () => { selected.clear(); onlySelected = false; resetList(); });
$('#cleanup').addEventListener('click', () => openCleanup());
$('#cleanup-form').addEventListener('submit', submitCleanup);
$('#cleanup-cancel').addEventListener('click', () => $('#cleanup-dialog').close());
$('#language-label').textContent = I18N.toggleLabel;
$('#language-toggle').addEventListener('click', () => I18N.toggle(api, state.settings).catch(error => toast(error.message, true)));
$('#tools-close').addEventListener('click', () => $('.tools').classList.toggle('closed'));
new IntersectionObserver(entries => {
  if (!entries[0].isIntersecting || !state) return;
  const total = visibleRows().length;
  if (shown < total) { shown += PAGE; renderList(); }
}, {root: $('#chats').parentElement}).observe($('#sentinel'));
load().then(() => {
  if (!params.get('search')) return;
  $('#search').value = params.get('search');
  searchMessages();
  runDeepSearch();
}).catch(error => toast(error.message, true));
