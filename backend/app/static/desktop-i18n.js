/* Interface language for the desktop WebView. The page source stays Chinese; in English the host
   injects window.__expenseDesktopI18n (backend/app/static/i18n-en.json, shared with the native
   pages) before this script. Exact keys are swapped, keys with {0} {1} are patterns, and composite
   labels are split on " · ", "；", "、" and new lines. Ledger data is never a key, so merchant and
   item names, notes and categories stay as recorded. */
(() => {
  const language = window.__expenseDesktopLanguage === 'en' ? 'en' : 'zh';
  const source = window.__expenseDesktopI18n;
  const CJK = /[㐀-鿿]/;
  const SLOT = /\{(\d+)\}/g;
  const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const exact = new Map();
  const patterns = [];
  const cache = new Map();

  function compile(zh, en) {
    const order = [];
    let expression = '^', last = 0;
    for (const slot of zh.matchAll(SLOT)) {
      expression += escape(zh.slice(last, slot.index)) + '(.+?)';
      order.push(Number(slot[1]));
      last = slot.index + slot[0].length;
    }
    expression += escape(zh.slice(last)) + '$';
    return { pattern: new RegExp(expression, 's'), english: en, order, weight: zh.replace(SLOT, '').length };
  }
  if (language === 'en' && source && typeof source === 'object') {
    for (const [zh, en] of Object.entries(source)) {
      if (!en || zh.startsWith('//')) continue;
      if (/\{\d+\}/.test(zh)) patterns.push(compile(zh, en));
      else {
        exact.set(zh, en);
        if (zh.endsWith('：')) patterns.push(compile(`${zh}{0}`, `${en.trimEnd()} {0}`));
      }
    }
    patterns.sort((a, b) => b.weight - a.weight);
  }
  const active = language === 'en' && exact.size > 0;
  const separators = [['\n', '\n'], [' · ', ' · '], ['；', '; '], ['、', ', ']];

  function translate(text, depth = 0) {
    if (depth > 4 || !text || !CJK.test(text)) return text;
    const trimmed = text.trim();
    if (!trimmed) return text;
    const lead = text.slice(0, text.indexOf(trimmed));
    const trail = text.slice(lead.length + trimmed.length);
    const direct = exact.get(trimmed);
    if (direct !== undefined) return lead + direct + trail;
    for (const { pattern, english, order } of patterns) {
      const match = pattern.exec(trimmed);
      if (!match) continue;
      const values = [];
      order.forEach((slot, index) => { values[slot] = translate(match[index + 1], depth + 1); });
      return lead + english.replace(SLOT, (whole, slot) => values[Number(slot)] ?? whole) + trail;
    }
    for (const [split, join] of separators) {
      if (!trimmed.includes(split)) continue;
      const parts = trimmed.split(split);
      const translated = parts.map(part => translate(part, depth + 1));
      if (translated.some((part, index) => part !== parts[index])) return lead + translated.join(join) + trail;
    }
    return text;
  }
  function t(text) {
    if (!active || typeof text !== 'string' || !CJK.test(text)) return text;
    if (cache.has(text)) return cache.get(text);
    const result = translate(text);
    if (cache.size > 4000) cache.clear();
    cache.set(text, result);
    return result;
  }

  window.LedgerI18n = Object.freeze({ language, t });
  if (!active) return;

  const ATTRIBUTES = ['title', 'aria-label', 'placeholder', 'alt', 'aria-description', 'aria-roledescription'];
  const SKIP = 'script, style, textarea, [translate="no"], [data-i18n-skip], [contenteditable="true"]';
  const skipped = node => {
    const element = node.nodeType === 1 ? node : node.parentElement;
    return !element || Boolean(element.closest?.(SKIP));
  };
  function text(node) {
    const value = node.data;
    if (!CJK.test(value) || skipped(node)) return;
    const translated = t(value);
    if (translated !== value) node.data = translated;
  }
  function attributes(element) {
    if (skipped(element)) return;
    for (const name of ATTRIBUTES) {
      const value = element.getAttribute?.(name);
      if (!value || !CJK.test(value)) continue;
      const translated = t(value);
      if (translated !== value) element.setAttribute(name, translated);
    }
    if (element.tagName === 'INPUT' && /^(button|submit|reset)$/i.test(element.type) && CJK.test(element.value)) {
      element.value = t(element.value);
    }
  }
  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) { text(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
    if (root.nodeType === 1) attributes(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.nodeType === 3) text(node); else attributes(node);
    }
  }
  new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'characterData') text(record.target);
      else if (record.type === 'attributes') attributes(record.target);
      else record.addedNodes.forEach(walk);
    }
  }).observe(document, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRIBUTES });

  const markLanguage = () => { if (document.documentElement) document.documentElement.lang = 'en'; };
  markLanguage();
  document.addEventListener('DOMContentLoaded', () => { markLanguage(); walk(document); }, { once: true });
  if (document.readyState !== 'loading') walk(document);

  // Native dialogs raised by page scripts.
  for (const name of ['alert', 'confirm', 'prompt']) {
    const original = window[name];
    if (typeof original === 'function') window[name] = (message, ...rest) => original.call(window, t(String(message ?? '')), ...rest);
  }
})();
