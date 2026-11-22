/* Presentation adapter: native selects remain the source for forms and change events. */
(() => {
  const instances = new Map();
  let opened = null, serial = 0;
  const enabled = option => !option.disabled && !option.hidden && !option.closest('optgroup')?.disabled;
  function enhance(select) {
    if (instances.has(select) || select.hasAttribute('data-native-select') || select.multiple || select.size > 1 || typeof HTMLElement.prototype.showPopover !== 'function') return;
    const labelCopy = select.labels?.[0]?.cloneNode(true);
    labelCopy?.querySelectorAll('select, button, input').forEach(node => node.remove());
    const label = select.getAttribute('aria-label') || labelCopy?.textContent.trim() || '选择项目';
    const wrapper = document.createElement('span'); wrapper.className = 'glass-select';
    select.before(wrapper); wrapper.append(select);
    select.classList.add('glass-select-native'); select.tabIndex = -1; select.hidden = true; select.setAttribute('aria-hidden', 'true');
    const trigger = document.createElement('button'); trigger.type = 'button'; trigger.className = 'glass-select-trigger';
    const caption = document.createElement('span'); caption.className = 'glass-select-caption';
    const chevron = document.createElement('span'); chevron.className = 'glass-select-chevron'; chevron.setAttribute('aria-hidden', 'true');
    trigger.append(caption, chevron); wrapper.append(trigger);
    const menu = document.createElement('div'); menu.className = 'glass-select-menu'; menu.id = `glass-select-${++serial}`;
    menu.setAttribute('role', 'listbox'); menu.setAttribute('popover', 'manual'); (select.closest('dialog, [role="dialog"]') || document.body).append(menu);
    trigger.setAttribute('role', 'combobox'); trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-controls', menu.id); trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-label', label); menu.setAttribute('aria-label', label);
    let active = -1, showing = false, revision = 0, animation, typed = '', typedAt = 0;
    const options = () => [...select.options];
    function sync() {
      caption.textContent = select.selectedOptions[0]?.label || '请选择';
      trigger.disabled = select.disabled;
      trigger.setAttribute('aria-required', String(select.required));
      if (select.disabled && showing) close();
      if (showing) render();
    }
    function render() {
      menu.replaceChildren();
      options().forEach((option, index) => {
        if (option.hidden) return;
        const row = document.createElement('div'); row.className = 'glass-select-option'; row.id = `${menu.id}-${index}`;
        row.setAttribute('role', 'option'); row.dataset.index = index;
        row.setAttribute('aria-selected', String(option.selected)); row.setAttribute('aria-disabled', String(!enabled(option)));
        const text = document.createElement('span'); text.textContent = option.label;
        const check = document.createElement('span'); check.className = 'glass-select-check'; check.textContent = '✓'; check.setAttribute('aria-hidden', 'true');
        row.append(text, check); menu.append(row);
      });
      if (!enabled(options()[active] || { disabled: true })) active = options().findIndex(enabled);
      highlight(); position();
    }
    function highlight(scroll = false) {
      menu.querySelectorAll('[data-index]').forEach(row => row.classList.toggle('is-active', Number(row.dataset.index) === active));
      const row = menu.querySelector(`[data-index="${active}"]`);
      if (row) { trigger.setAttribute('aria-activedescendant', row.id); if (scroll) row.scrollIntoView({ block: 'nearest' }); }
      else trigger.removeAttribute('aria-activedescendant');
    }
    function position() {
      if (!showing) return;
      const anchor = select.closest('.select-control') || wrapper;
      const box = anchor.getBoundingClientRect();
      const width = Math.min(box.width, innerWidth - 16);
      const below = innerHeight - box.bottom - 14, above = box.top - 14;
      const up = below < Math.min(220, menu.scrollHeight) && above > below;
      const height = Math.max(44, Math.min(300, up ? above : below));
      Object.assign(menu.style, { width: `${width}px`, maxHeight: `${height}px`, left: `${Math.max(8, Math.min(box.left, innerWidth - width - 8))}px`, top: `${up ? Math.max(8, box.top - Math.min(menu.scrollHeight, height) - 6) : box.bottom + 6}px` });
    }
    function open() {
      if (select.disabled || showing) return;
      opened?.close(); revision++; animation?.cancel(); showing = true; opened = api;
      active = select.selectedIndex; menu.showPopover(); trigger.setAttribute('aria-expanded', 'true');
      render(); highlight(true);
      animation = window.LedgerMotion?.play(menu, [{ opacity: 0, transform: 'translateY(-5px)' }, { opacity: 1, transform: 'translateY(0)' }], 'control');
    }
    function close() {
      if (!showing) return;
      showing = false; if (opened === api) opened = null;
      trigger.setAttribute('aria-expanded', 'false'); trigger.removeAttribute('aria-activedescendant');
      const current = ++revision; animation?.cancel();
      const done = () => { if (current === revision) menu.hidePopover(); };
      animation = window.LedgerMotion?.play(menu, [{ opacity: 1, transform: 'translateY(0)' }, { opacity: 0, transform: 'translateY(-3px)' }], 'control', { done });
      if (!window.LedgerMotion) done();
    }
    function choose(index) {
      if (!showing || !enabled(options()[index] || { disabled: true })) return;
      const changed = select.selectedIndex !== index; select.selectedIndex = index; close(); sync();
      trigger.removeAttribute('aria-invalid');
      if (changed) { select.dispatchEvent(new Event('input', { bubbles: true })); select.dispatchEvent(new Event('change', { bubbles: true })); }
    }
    function keydown(event) {
      const list = options(), indices = list.flatMap((option, index) => enabled(option) ? [index] : []);
      if (event.key === 'Escape' && showing) { event.preventDefault(); event.stopPropagation(); close(); return; }
      if (event.key === 'Tab') { close(); return; }
      if (['Enter', ' '].includes(event.key)) { event.preventDefault(); showing ? choose(active) : open(); return; }
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault(); if (!showing) { open(); return; }
        const step = event.key === 'ArrowUp' ? -1 : 1;
        active = event.key === 'Home' ? indices[0] : event.key === 'End' ? indices.at(-1) : indices[Math.max(0, Math.min(indices.length - 1, indices.indexOf(active) + step))];
        highlight(true); return;
      }
      if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault(); if (!showing) open();
        const now = Date.now(); typed = now - typedAt > 700 ? event.key : typed + event.key; typedAt = now;
        const match = list.findIndex(option => enabled(option) && option.label.toLocaleLowerCase().startsWith(typed.toLocaleLowerCase()));
        if (match >= 0) { active = match; highlight(true); }
      }
    }
    trigger.addEventListener('click', () => showing ? close() : open()); trigger.addEventListener('keydown', keydown);
    trigger.addEventListener('blur', close);
    menu.addEventListener('pointerdown', event => event.preventDefault());
    menu.addEventListener('click', event => { const row = event.target.closest('[data-index]'); if (row) choose(Number(row.dataset.index)); });
    select.addEventListener('change', sync);
    select.addEventListener('focus', () => trigger.focus());
    select.addEventListener('invalid', event => { event.preventDefault(); trigger.setAttribute('aria-invalid', 'true'); trigger.focus(); open(); });
    let resetTimer;
    // The reset event precedes the browser's default value restoration.
    const reset = () => { clearTimeout(resetTimer); resetTimer = setTimeout(() => { trigger.removeAttribute('aria-invalid'); sync(); }, 0); };
    select.form?.addEventListener('reset', reset);
    const api = { select, trigger, menu, sync, close, position, destroy() { revision++; clearTimeout(resetTimer); animation?.cancel(); menu.remove(); select.form?.removeEventListener('reset', reset); if (opened === api) opened = null; } };
    instances.set(select, api); sync();
  }
  function scan(node) { if (node.nodeType !== 1) return; if (node.matches('select')) enhance(node); node.querySelectorAll('select').forEach(enhance); }
  document.addEventListener('DOMContentLoaded', () => {
    scan(document.body);
    new MutationObserver(records => {
      const changed = new Set();
      for (const record of records) {
        const select = record.target.closest?.('select'); if (select) changed.add(select);
        record.addedNodes.forEach(scan);
      }
      for (const select of changed) instances.get(select)?.sync();
      for (const [select, item] of instances) if (!select.isConnected) { item.destroy(); instances.delete(select); }
    }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'selected', 'label', 'value', 'required', 'hidden'] });
    document.addEventListener('pointerdown', event => { if (opened && !opened.trigger.contains(event.target) && !opened.menu.contains(event.target)) opened.close(); });
    document.addEventListener('scroll', event => { if (opened && !opened.menu.contains(event.target)) opened.close(); }, true);
    window.addEventListener('resize', () => opened?.position());
  });
  window.LedgerSelect = Object.freeze({ sync: select => instances.get(select)?.sync() });
})();
