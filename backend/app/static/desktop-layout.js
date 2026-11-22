// Only the new native host enables this layout. Older hosts retain their layout.
(() => {
  if (window.__expenseDesktopWallpaperLayout !== true || window.expenseDesktopLayout) return;
  const root = document.documentElement;
  root.dataset.desktopLayout = 'wallpaper';
  let frame = null;
  // Interface size chosen in the host (0.9 / 1 / 1.15). CSS zoom scales every length on the page,
  // so the wallpaper frame below divides it back out to stay aligned with the native wallpaper.
  function currentZoom() {
    const zoom = Number(window.__expenseDesktopZoom);
    return Number.isFinite(zoom) && zoom >= 0.5 && zoom <= 2 ? zoom : 1;
  }
  function applyZoom() {
    const zoom = currentZoom();
    root.style.zoom = zoom === 1 ? '' : String(zoom);
    applyFrame();
  }
  window.expenseDesktopApplyZoom = applyZoom;
  addEventListener('keydown', event => {
    if (!event.ctrlKey || event.altKey || event.metaKey) return;
    const step = ['=', '+'].includes(event.key) ? 1 : event.key === '-' ? -1 : event.key === '0' ? 0 : null;
    if (step === null) return;
    event.preventDefault();
    try { window.chrome?.webview?.postMessage({type: 'ui-scale-step', step}); } catch { /* host unavailable */ }
  });
  function applyFrame() {
    if (!frame) return;
    // Native DIPs and CSS pixels differ when the WebView is zoomed.
    const scale = innerWidth / frame.webWidth / currentZoom();
    for (const [key, value] of Object.entries({width: frame.width, height: frame.height, x: frame.x, y: frame.y}))
      root.style.setProperty(`--host-wallpaper-${key}`, `${value * scale}px`);
    root.dataset.desktopWallpaperFrame = 'aligned';
  }
  function setHostFrame(value) {
    if (!value || !['width', 'height', 'x', 'y', 'webWidth'].every(key => Number.isFinite(value[key]))
      || value.width <= 0 || value.height <= 0 || value.webWidth <= 0
      || value.width > 32768 || value.height > 32768 || Math.abs(value.x) > 32768 || Math.abs(value.y) > 32768) return false;
    frame = {...value}; applyFrame(); return true;
  }
  function refreshCategories(payload) {
    const categories = document.getElementById('desktopCategoryBreakdown');
    if (categories && typeof renderBars === 'function') renderBars('desktopCategoryBreakdown', payload?.categories ?? []);
  }
  function install() {
    const overview = document.querySelector('[data-panel="overview"]');
    if (!overview || document.getElementById('desktopScenicGap')) return;
    const gap = document.createElement('div');
    gap.id = 'desktopScenicGap'; gap.className = 'desktop-scenic-gap'; gap.setAttribute('aria-hidden', 'true');
    overview.querySelector('.metric-grid')?.after(gap);
    const stack = overview.querySelector('.distribution-stack');
    if (!stack) return;
    const sections = [...stack.querySelectorAll('.distribution-surface')];
    const card = document.createElement('section');
    card.className = 'surface desktop-distribution-card';
    card.innerHTML = '<div class="section-heading"><h2 id="desktopDistributionTitle">支出分布</h2></div><div class="desktop-distribution-tabs" role="tablist" aria-labelledby="desktopDistributionTitle"></div>';
    const tabs = card.querySelector('[role="tablist"]');
    const category = document.createElement('section');
    category.innerHTML = '<div class="bar-list breakdown-scroll" id="desktopCategoryBreakdown" tabindex="0" aria-label="分类支出分布，可上下滚动"></div>';
    const panels = [...sections, category];
    const buttons = [];
    function select(index, focus = false) {
      buttons.forEach((button, i) => {
        const selected = i === index;
        button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
        panels[i].hidden = !selected;
      });
      if (focus) buttons[index].focus();
    }
    ['平台', '付款', '分类'].forEach((label, index) => {
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = label; button.id = `desktopDistributionTab${index}`;
      button.setAttribute('role', 'tab'); button.setAttribute('aria-controls', `desktopDistributionPanel${index}`);
      const panel = panels[index]; panel.id = `desktopDistributionPanel${index}`; panel.classList.add('desktop-distribution-panel');
      panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', button.id);
      button.addEventListener('click', () => select(index));
      button.addEventListener('keydown', event => {
        const delta = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
        if (!delta && event.key !== 'Home' && event.key !== 'End') return;
        event.preventDefault(); select(event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + delta + 3) % 3, true);
      });
      buttons.push(button); tabs.append(button); card.append(panel);
    });
    stack.replaceChildren(card); select(0);
    // Ordinary app scripts have run before DOMContentLoaded, including their lexical state.
    if (typeof state !== 'undefined' && state.overview) refreshCategories(state.overview);
  }
  window.expenseDesktopLayout = {setHostFrame, updateOverview: refreshCategories};
  applyZoom();
  if (window.__expenseDesktopLayoutFrame) setHostFrame(window.__expenseDesktopLayoutFrame);
  addEventListener('resize', applyFrame, {passive: true});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, {once: true});
  else install();
})();
