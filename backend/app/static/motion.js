/* Finite, interruptible motion. Final data is always written before decoration. */
(() => {
  const root = document.documentElement;
  const media = matchMedia('(prefers-reduced-motion: reduce)');
  const fine = matchMedia('(hover: hover) and (pointer: fine)');
  const tokens = typeof getComputedStyle === 'function' ? getComputedStyle(root) : null;
  const timings = Object.freeze(Object.fromEntries(Object.entries({ press: 90, control: 160, panel: 220, enter: 360, data: 480, draw: 640 }).map(([key, fallback]) => [key, parseFloat(tokens?.getPropertyValue(`--motion-${key}`)) || fallback])));
  const ease = tokens?.getPropertyValue('--motion-ease').trim() || 'cubic-bezier(.2,.8,.2,1)';
  const animations = new Map();
  const frames = new Map();
  const reduced = () => media.matches || root.dataset.motion === 'reduced';
  const visible = node => node?.isConnected && node.getClientRects().length > 0;
  const easing = t => 1 - (1 - t) ** 3;
  function play(node, keyframes, kind = 'panel', options = {}) {
    const { done = () => {}, ...extra } = options;
    if (reduced() || !visible(node) || !node.animate) { done(); return null; }
    const animation = node.animate(keyframes, { duration: timings[kind], easing: ease, ...extra });
    let settled = false;
    const finish = () => { if (settled) return; settled = true; animations.delete(animation); done(); };
    animations.set(animation, finish);
    animation.finished.then(finish, finish);
    return animation;
  }
  function stop(key, finish = false) {
    const job = frames.get(key);
    if (!job) return;
    cancelAnimationFrame(job.frame);
    frames.delete(key);
    if (finish) job.update(1);
    job.done();
  }
  function tween(key, kind, update, done = () => {}) {
    stop(key);
    if (reduced() || !visible(key)) { update(1); done(); return; }
    const start = performance.now();
    const job = { update, done, frame: 0 };
    const tick = now => {
      if (!visible(key)) { stop(key, true); return; }
      const t = Math.min(1, (now - start) / timings[kind]);
      update(easing(t));
      if (t < 1) job.frame = requestAnimationFrame(tick);
      else { frames.delete(key); done(); }
    };
    frames.set(key, job);
    update(0);
    job.frame = requestAnimationFrame(tick);
  }
  function settle() {
    if (!reduced()) return;
    for (const [animation, finish] of [...animations]) { animation.cancel(); finish(); }
    for (const key of [...frames.keys()]) stop(key, true);
    clearGlint();
  }
  media.addEventListener('change', settle);
  new MutationObserver(settle).observe(root, { attributes: true, attributeFilter: ['data-motion'] });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) return;
    for (const [animation, finish] of [...animations]) { animation.cancel(); finish(); }
    for (const key of [...frames.keys()]) stop(key, true);
    clearGlint();
  });
  function enter(node, index = 0) {
    return play(node, [{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'translateY(0)' }], 'enter', { delay: Math.min(index, 4) * 45, fill: 'backwards' });
  }
  const metricAnimations = new WeakMap();
  function metrics(container, values, initial = false) {
    container.querySelectorAll('.metric-card').forEach((card, index) => {
      const value = card.querySelector('.metric-value');
      const current = values[index];
      const previous = value.getAttribute('aria-label');
      if (previous === current) return;
      for (const animation of metricAnimations.get(value) || []) animation?.cancel();
      const next = document.createElement('span'); next.className = 'metric-current'; next.textContent = current;
      value.setAttribute('aria-label', current);
      if (initial || reduced() || previous == null) {
        value.replaceChildren(next);
        if (initial) enter(card, index);
        return;
      }
      const old = document.createElement('span'); old.textContent = previous;
      old.className = 'metric-previous'; old.setAttribute('aria-hidden', 'true');
      value.replaceChildren(next, old);
      // Equal-duration opacity curves avoid a dim interval; the card never moves.
      metricAnimations.set(value, [
        play(old, [{ opacity: 1 }, { opacity: 0 }], 'panel', { done: () => old.remove() }),
        play(next, [{ opacity: 0 }, { opacity: 1 }], 'panel'),
      ]);
    });
  }
  function bars(container, previous) {
    const bars = [...container.querySelectorAll('progress')];
    const values = bars.map(bar => ({ bar, target: bar.value, from: previous.get(bar.dataset.barKey) || 0 }));
    // aria-valuenow exposes the final value throughout visual interpolation.
    values.forEach(({ bar, target }) => bar.setAttribute('aria-valuenow', String(target)));
    tween(container, 'data', t => values.forEach(({ bar, target, from }) => { bar.value = from + (target - from) * t; }));
  }
  const readPoints = line => (line?.getAttribute('points') || '').trim().split(/\s+/).filter(Boolean).map(pair => pair.split(',').map(Number));
  function captureTrend(chart) {
    const previous = [...chart.querySelectorAll('[data-series-key]')].map(group => ({
      key: group.dataset.seriesKey, points: readPoints(group.querySelector('polyline')), clone: group.cloneNode(true),
    }));
    stop(chart);
    return { series: previous, months: chart.dataset.motionMonths };
  }
  function trend(chart, previous, months) {
    chart.dataset.motionMonths = months;
    const groups = [...chart.querySelectorAll('[data-series-key]')];
    if (!groups.length) return;
    const sameAxis = previous.months === months;
    const targets = groups.map((group, index) => {
      const line = group.querySelector('polyline');
      const to = readPoints(line);
      const match = previous.series.find(item => item.key === group.dataset.seriesKey);
      const start = sameAxis ? (match || (index === 0 ? previous.series[0] : null))?.points : null;
      return { group, line, to, matched: Boolean(match && sameAxis), from: start?.length === to.length ? start : to };
    });
    const svg = chart.querySelector('svg');
    const ghosts = sameAxis ? previous.series.filter(old => !groups.some(group => group.dataset.seriesKey === old.key)).map(old => {
      const clone = old.clone; clone.removeAttribute('data-series-key'); clone.removeAttribute('data-trend-series');
      clone.classList.add('trend-ghost'); clone.setAttribute('aria-hidden', 'true');
      clone.querySelectorAll('.trend-area').forEach(area => area.remove());
      svg.insertBefore(clone, groups[0]); return clone;
    }) : [];
    const reveal = svg.querySelector('.trend-reveal-rect');
    const fullWidth = Number(reveal?.getAttribute('width') || 0);
    const first = !previous.series.length;
    tween(chart, first ? 'draw' : 'data', t => {
      if (reveal && first) reveal.setAttribute('width', String(fullWidth * t));
      for (const { group, line, to, from, matched } of targets) {
        const points = to.map((p, i) => p.map((v, axis) => from[i][axis] + (v - from[i][axis]) * t));
        line.setAttribute('points', points.map(p => p.join(',')).join(' '));
        group.querySelectorAll('circle').forEach((circle, i) => { circle.setAttribute('cx', points[i][0]); circle.setAttribute('cy', points[i][1]); });
        const area = group.querySelector('.trend-area');
        if (area) area.setAttribute('d', `M ${points[0][0]} ${group.dataset.baseline} L ${points.map(p => p.join(',')).join(' L ')} L ${points.at(-1)[0]} ${group.dataset.baseline} Z`);
        if (!first && !matched) group.setAttribute('opacity', String(t));
      }
      ghosts.forEach(ghost => ghost.setAttribute('opacity', String(1 - t)));
    }, () => { ghosts.forEach(ghost => ghost.remove()); groups.forEach(group => group.removeAttribute('opacity')); });
  }
  let indicator;
  let indicatorAnimation;
  function navigation(animate = true) {
    const nav = document.querySelector('.primary-nav');
    if (!nav) return;
    indicator ||= Object.assign(document.createElement('span'), { className: 'nav-indicator' });
    if (!indicator.isConnected) { indicator.setAttribute('aria-hidden', 'true'); nav.prepend(indicator); nav.classList.add('motion-nav'); }
    const selected = nav.querySelector('.nav-item.active');
    if (!selected) { indicator.hidden = true; return; }
    const wasVisible = !indicator.hidden && indicator.offsetWidth > 0;
    const before = indicator.getBoundingClientRect();
    indicatorAnimation?.cancel();
    indicator.hidden = false;
    const box = selected.getBoundingClientRect(), parent = nav.getBoundingClientRect();
    Object.assign(indicator.style, { left: `${box.left - parent.left}px`, top: `${box.top - parent.top}px`, width: `${box.width}px`, height: `${box.height}px` });
    if (animate && wasVisible) indicatorAnimation = play(indicator, [{ transform: `translate(${before.left - box.left}px,${before.top - box.top}px) scale(${before.width / box.width},${before.height / box.height})` }, { transform: 'translate(0,0) scale(1,1)' }]);
  }
  function view(panel) { if (!panel) return; panel.getAnimations().forEach(a => a.cancel()); enterPanel(panel); navigation(); }
  function enterPanel(panel) { return play(panel, [{ opacity: 0, transform: 'translateY(7px)' }, { opacity: 1, transform: 'translateY(0)' }], 'panel'); }
  let closingDialog = null;
  let dialogRevision = 0;
  function openDialog(dialog) {
    dialogRevision++;
    closingDialog?.cancel(); closingDialog = null;
    if (!dialog.open) dialog.showModal();
    play(dialog, [{ opacity: 0, transform: 'translateY(10px) scale(.975)' }, { opacity: 1, transform: 'translateY(0) scale(1)' }], 'panel');
  }
  function closeDialog(dialog) {
    if (closingDialog) return;
    const revision = ++dialogRevision;
    closingDialog = play(dialog, [{ opacity: 1, transform: 'translateY(0) scale(1)' }, { opacity: 0, transform: 'translateY(5px) scale(.985)' }], 'control', { done: () => { if (revision !== dialogRevision) return; dialog.close(); closingDialog = null; } });
  }
  let glint, glintFrame = 0, pointer;
  const materials = '.metric-card, .trend-surface, .distribution-surface, .analysis-result, .analysis-action, .watch-card, .update-surface';
  function clearGlint() { cancelAnimationFrame(glintFrame); glintFrame = 0; glint?.remove(); }
  function light(event) {
    if (reduced() || !fine.matches || event.pointerType === 'touch') return;
    pointer = event;
    if (glintFrame) return;
    glintFrame = requestAnimationFrame(() => {
      glintFrame = 0;
      const target = pointer.target.closest?.(materials);
      if (!target) { clearGlint(); return; }
      const box = target.getBoundingClientRect();
      if (!glint) {
        glint = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        glint.classList.add('material-glint'); glint.setAttribute('aria-hidden', 'true');
        glint.innerHTML = '<defs><radialGradient id="material-light" gradientUnits="userSpaceOnUse" r="220"><stop stop-color="white" stop-opacity=".16"/><stop offset="1" stop-color="white" stop-opacity="0"/></radialGradient><radialGradient id="material-edge" gradientUnits="userSpaceOnUse" r="180"><stop stop-color="white" stop-opacity=".7"/><stop offset="1" stop-color="white" stop-opacity="0"/></radialGradient></defs><rect class="glint-fill" width="100%" height="100%" fill="url(#material-light)"/><rect class="glint-edge" x=".5" y=".5" fill="none" stroke="url(#material-edge)"/>';
      }
      if (glint.parentElement !== target) target.append(glint);
      glint.querySelectorAll('radialGradient').forEach(g => { g.setAttribute('cx', pointer.clientX - box.left); g.setAttribute('cy', pointer.clientY - box.top); });
      const edge = glint.querySelector('.glint-edge');
      edge.setAttribute('width', Math.max(0, box.width - 1)); edge.setAttribute('height', Math.max(0, box.height - 1)); edge.setAttribute('rx', getComputedStyle(target).borderTopLeftRadius);
    });
  }
  document.addEventListener('pointermove', light, { passive: true });
  document.addEventListener('pointerleave', clearGlint);
  document.addEventListener('scroll', clearGlint, { passive: true, capture: true });
  document.addEventListener('DOMContentLoaded', () => {
    navigation(false);
    const nav = document.querySelector('.primary-nav');
    if (nav) new ResizeObserver(() => navigation(false)).observe(nav);
    document.querySelectorAll('.analysis-main > section, .watch-shell > section').forEach((section, index) => enter(section, index));
    document.addEventListener('pointerdown', event => {
      const button = event.target.closest('button, a.button');
      if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true') return;
      play(button, [{ filter: 'brightness(1)' }, { filter: 'brightness(1.1)' }, { filter: 'brightness(1)' }], 'panel');
    });
  });
  window.LedgerMotion = Object.freeze({ reduced, timings, play, enter, metrics, bars, captureTrend, trend, navigation, view, openDialog, closeDialog, stop });
})();
