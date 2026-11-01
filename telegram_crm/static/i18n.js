// Interface language. Templates keep their Russian source text; in English this layer translates what
// reaches the page, including messages from the server. Chat content is never translated: see SKIP.
const I18N = (() => {
  const STORE_KEY = 'tg-crm-language';
  const CYRILLIC = /[А-Яа-яЁё]/;
  const SKIP = '.chat-name,.chat-preview,.message-history,.msg,.chat-title,.found-title,.found-message,.head-text strong,.detail-person h2,.llm-result p,.llm-result div,textarea,input,script,style,.romance-text';
  const ATTRIBUTES = ['placeholder', 'title', 'aria-label', 'alt'];
  let saved = null;
  try { saved = localStorage.getItem(STORE_KEY); } catch { /* blocked storage: the server setting decides */ }
  const lang = saved === 'en' ? 'en' : 'ru';
  const data = window.I18N_EN;
  const escapeRegExp = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const keys = Object.keys(data.fragments).sort((a, b) => b.length - a.length);
  const fragmentPattern = new RegExp(`(?<![А-Яа-яЁё])(?:${keys.map(escapeRegExp).join('|')})(?![А-Яа-яЁё])`, 'g');
  const quoted = keys.filter(key => key.includes('«'));
  const patterns = data.patterns.map(([source, target]) => [new RegExp(source, 'gu'), target]);

  function phrases(text) {
    let result = text.replace(fragmentPattern, match => data.fragments[match]);
    for (const [pattern, target] of patterns) result = result.replace(pattern, target);
    return result;
  }
  function translate(text) {
    if (lang !== 'en' || !CYRILLIC.test(text)) return text;
    const core = text.trim();
    if (data.exact[core] !== undefined) return text.replace(core, data.exact[core]);
    // A «quoted» part is usually a chat title, so it stays as it is.
    // Fixed sentences that quote a button are replaced first, so the split below does not break them.
    const whole = quoted.reduce((result, key) => result.split(key).join(data.fragments[key]), text);
    return whole.split(/(«[^»]*»)/).map(part => (part.startsWith('«') ? part : phrases(part))).join('');
  }
  function visit(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      if (node.parentElement?.closest(SKIP)) return;
      const next = translate(node.nodeValue);
      if (next !== node.nodeValue) node.nodeValue = next;
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE && node.nodeType !== Node.DOCUMENT_NODE) return;
    if (node.nodeType === Node.ELEMENT_NODE) {
      for (const name of ATTRIBUTES) {
        const value = node.getAttribute(name);
        if (!value || !CYRILLIC.test(value)) continue;
        const next = translate(value);
        if (next !== value) node.setAttribute(name, next);  // writing an unchanged value would observe itself forever
      }
      if (node.matches(SKIP) && !node.matches('input,textarea')) return;
    }
    node.childNodes.forEach(visit);
  }
  if (lang === 'en') {
    document.documentElement.lang = 'en';
    visit(document.documentElement);  // the <title> is parsed before this script runs
    new MutationObserver(records => {
      for (const record of records) {
        if (record.type === 'attributes') visit(record.target);
        else if (record.type === 'characterData') visit(record.target);
        else record.addedNodes.forEach(visit);
      }
    }).observe(document, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRIBUTES});
  }
  function remember(next) { try { localStorage.setItem(STORE_KEY, next); } catch { /* the server setting still applies */ } }
  // The server setting is the source of truth; local storage only avoids a flash of the wrong language.
  function sync(settings) {
    if (settings?.language && settings.language !== lang) { remember(settings.language); location.reload(); }
  }
  async function toggle(request, settings) {
    const next = lang === 'en' ? 'ru' : 'en';
    await request('/api/settings', {method: 'PUT', body: JSON.stringify({...settings, language: next})});
    remember(next);
    location.reload();
  }
  return {lang, locale: lang === 'en' ? 'en-GB' : 'ru-RU', t: translate, sync, toggle, toggleLabel: lang === 'en' ? 'Русский' : 'English'};
})();
