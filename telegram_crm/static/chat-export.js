// Two download buttons for a chat, shared by both chat views.
const chatExport = (() => {
  const markup = (loaded, demo) => `<div class="export-buttons">
    <button type="button" class="export-button" data-export="recent" ${loaded ? '' : 'disabled'}>Выгрузить 100 сообщений</button>
    <button type="button" class="export-button" data-export="full" ${demo ? 'disabled title="Недоступно для демо-чата"' : ''}>Выгрузить всю историю</button></div>`;
  function bind(root, chatId, request, notify) {
    const base = `/api/chats/${encodeURIComponent(chatId)}/export`;
    root.querySelectorAll('[data-export]').forEach(button => {
      button.addEventListener('click', async () => {
        if (button.dataset.export === 'recent') { location.href = `${base}?scope=recent`; return; }
        button.disabled = true;
        try {
          // Asked first, so a Telegram error shows as a message and not as a broken download.
          const {total} = await request(`${base}/info`);
          if (!total) { notify('В этом чате нет сообщений'); return; }
          notify(`Скачиваю всю историю: ${total} сообщ. Большие чаты выгружаются дольше, следите за загрузкой в браузере.`);
          location.href = `${base}?scope=full`;
        } catch (error) { notify(error.message, true); }
        finally { button.disabled = false; }
      });
    });
  }
  const removeLabel = kind => ({channel: 'Отписаться', group: 'Выйти из группы'})[kind] || 'Удалить чат';
  return {markup, bind, removeLabel};
})();
