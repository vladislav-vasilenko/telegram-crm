// Attachments load only when a chat is opened: one info request, then previews as they scroll into view.
// Video and audio use preload="none", so nothing streams until Play. Shared by both chat views.
const chatMedia = (() => {
  const fetchable = new Set(['MessageMediaPhoto', 'MessageMediaDocument']);
  const LABELS = {MessageMediaPhoto: 'фото', MessageMediaDocument: 'вложение', MessageMediaStory: 'история', MessageMediaWebPage: 'ссылка',
    MessageMediaContact: 'контакт', MessageMediaGeo: 'геопозиция', MessageMediaGeoLive: 'геопозиция', MessageMediaVenue: 'место', MessageMediaPoll: 'опрос',
    MessageMediaDice: 'кубик', MessageMediaGame: 'игра', MessageMediaInvoice: 'счёт', MessageMediaUnsupported: 'неподдерживаемое вложение', MessageMediaGiveaway: 'розыгрыш'};
  const label = name => I18N.t(`[${LABELS[name] || 'вложение'}]`);
  const RETRY_DELAYS = [2000, 5000, 10000];
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fileSize = bytes => bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} ${I18N.lang === 'en' ? 'MB' : 'МБ'}` : `${Math.max(1, Math.round(bytes / 1024))} ${I18N.lang === 'en' ? 'KB' : 'КБ'}`;
  function markup(base, item, downloadIcon) {
    const size = item.width && item.height ? `width="${Number(item.width)}" height="${Number(item.height)}"` : '';
    const file = `${base}/file`, preview = `${base}/preview`;
    if (item.kind === 'image') return `<a href="${file}" target="_blank" rel="noopener"><img src="${item.preview ? preview : file}" loading="lazy" alt="${I18N.t('Фото')}" ${size}></a>`;
    if (item.kind === 'video') return `<video controls preload="none" playsinline src="${file}" ${item.preview ? `poster="${preview}"` : ''} ${size}></video>`;
    if (item.kind === 'audio') return `<audio controls preload="none" src="${file}"></audio>`;
    return `<a class="media-file" href="${file}?download=true" download>${downloadIcon}<span>${escape(item.name)} · ${fileSize(item.size)}</span></a>`;
  }
  // The server can be starting or Telegram busy, so a failure is retried and then offered as a button.
  async function fill(options) {
    const {root, chatId, request, isCurrent, downloadIcon = '↓'} = options;
    const slots = [...root.querySelectorAll('.media-slot:empty')].slice(-100);
    if (!slots.length) return;
    const base = `/api/chats/${encodeURIComponent(chatId)}/media`;
    const failed = slot => {
      slot.innerHTML = `<button type="button" class="media-retry">${I18N.t('Вложение не загрузилось. Повторить')}</button>`;
      slot.firstChild.addEventListener('click', () => { slot.innerHTML = ''; fill(options); });
    };
    let items = null;
    for (let attempt = 0; attempt <= RETRY_DELAYS.length; attempt += 1) {
      try {
        const answer = await request(`${base}?ids=${slots.map(s => s.dataset.mediaId).join(',')}`);
        if (!answer.retry) { items = answer.items; break; }
      } catch { /* tried again below */ }
      if (!isCurrent()) return;
      if (attempt < RETRY_DELAYS.length) await wait(RETRY_DELAYS[attempt]);
    }
    if (!isCurrent()) return;
    if (!items) { slots.forEach(failed); return; }
    slots.forEach(slot => {
      const item = items[slot.dataset.mediaId];
      if (!item) return;  // gone from Telegram or self-destructing: the label explains it
      slot.innerHTML = markup(`${base}/${slot.dataset.mediaId}`, item, downloadIcon);
      const text = slot.parentElement.querySelector('.media-label');
      if (text) text.hidden = true;
      const element = slot.querySelector('img,video,audio');
      if (element) element.addEventListener('error', () => { failed(slot); if (text) text.hidden = false; }, {once: true});
    });
  }
  return {fetchable, label, fill};
})();
