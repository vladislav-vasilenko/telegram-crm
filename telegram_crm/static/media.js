// Attachments load only when a chat is opened: one info request, then previews as they scroll into view.
// Video and audio use preload="none", so nothing streams until Play. Shared by both chat views.
const chatMedia = (() => {
  const fetchable = new Set(['MessageMediaPhoto', 'MessageMediaDocument']);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fileSize = bytes => bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} МБ` : `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  function markup(base, item, downloadIcon) {
    const size = item.width && item.height ? `width="${Number(item.width)}" height="${Number(item.height)}"` : '';
    const file = `${base}/file`, preview = `${base}/preview`;
    if (item.kind === 'image') return `<a href="${file}" target="_blank" rel="noopener"><img src="${item.preview ? preview : file}" loading="lazy" alt="Фото" ${size}></a>`;
    if (item.kind === 'video') return `<video controls preload="none" playsinline src="${file}" ${item.preview ? `poster="${preview}"` : ''} ${size}></video>`;
    if (item.kind === 'audio') return `<audio controls preload="none" src="${file}"></audio>`;
    return `<a class="media-file" href="${file}?download=true" download>${downloadIcon}<span>${escape(item.name)} · ${fileSize(item.size)}</span></a>`;
  }
  async function fill({root, chatId, request, isCurrent, downloadIcon = '↓'}) {
    const slots = [...root.querySelectorAll('.media-slot')];
    if (!slots.length) return;
    const base = `/api/chats/${encodeURIComponent(chatId)}/media`;
    let items;
    try { ({items} = await request(`${base}?ids=${slots.slice(-100).map(s => s.dataset.mediaId).join(',')}`)); } catch { return; }  // labels stay, the chat still reads fine
    if (!isCurrent()) return;
    slots.forEach(slot => {
      const item = items[slot.dataset.mediaId];
      if (!item) return;
      slot.innerHTML = markup(`${base}/${slot.dataset.mediaId}`, item, downloadIcon);
      const label = slot.parentElement.querySelector('.media-label');
      if (label) label.hidden = true;
      const element = slot.querySelector('img,video,audio');
      if (element) element.addEventListener('error', () => { slot.innerHTML = ''; if (label) label.hidden = false; }, {once: true});
    });
  }
  return {fetchable, fill};
})();
