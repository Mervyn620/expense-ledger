/* Desktop-only starfield. One low-resolution canvas, no per-frame DOM layout. */
(() => {
  const root = document.documentElement;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const contrast = matchMedia('(forced-colors: active)');
  const canvas = document.createElement('canvas');
  canvas.id = 'expense-desktop-stars';
  canvas.setAttribute('aria-hidden', 'true');
  const context = canvas.getContext('2d', { alpha: true });
  let mode = window.__expenseDesktopVisualMode === 'particle' ? 'particle' : 'static';
  let width = 0, height = 0, frame = 0, last = 0;
  let pointer = null, hostFrame = null, hostClock = 0, hostGradient = null;
  const halton = (index, base) => {
    let value = 0, fraction = 1 / base;
    while (index > 0) { value += fraction * (index % base); index = Math.floor(index / base); fraction /= base; }
    return value;
  };
  // Star tones per palette, the same values as MainWindow.StarPalette: main, accent, alt.
  const palettes = {
    cloud: ['255,255,255', '155,218,255', '188,170,255'],
    ink: ['242,236,220', '230,195,132', '127,180,202'],
    jade: ['232,244,236', '131,192,146', '214,153,182'],
    indigo: ['228,232,255', '122,162,247', '187,154,247']
  };
  const tones = () => palettes[root.dataset.theme] || palettes.cloud;
  const revealAt = 6.25;
  const stars = Array.from({ length: 160 }, (_, i) => ({
    x: .035 + .93 * halton(i + 1, 2),
    y: .035 + .93 * halton(i + 1, 3),
    actualX: NaN, actualY: NaN,
    radius: i % 19 === 0 ? 2.1 : i % 7 === 0 ? 1.4 : .85,
    tone: i % 9 === 0 ? 2 : i % 4 === 0 ? 1 : 0,
    halo: i % 19 === 0,
    index: i,
    phase: i * 2.17
  }));
  // StarfieldTimeline.Drift: slow ellipses, nearer stars further, zero at the end of the launch.
  function drift(index, seconds) {
    const t = Math.max(0, seconds - revealAt);
    const depth = index % 19 === 0 ? 1 : index % 7 === 0 ? .62 : .36;
    const amplitude = 10 * depth;
    const speed = (.07 + index % 11 * .005) * (.7 + .3 * depth);
    const phaseX = index * 1.618, phaseY = index * 2.414;
    return [amplitude * (Math.sin(t * speed + phaseX) - Math.sin(phaseX)),
      amplitude * .7 * (Math.cos(t * speed * .83 + phaseY) - Math.cos(phaseY))];
  }
  // StarfieldTimeline.Meteor: an occasional shooting star on the same hashed schedule.
  const hash = (value, salt) => { const v = Math.sin(value * 12.9898 + salt * 78.233) * 43758.5453; return v - Math.floor(v); };
  function meteor(w, h, seconds) {
    const t = seconds - revealAt - 2;
    if (t < 0 || w < 1 || h < 1) return null;
    const period = 11, duration = 1.15, slot = Math.floor(t / period);
    if (hash(slot, 0) < .3) return null;
    const start = slot * period + 1 + hash(slot, 1) * (period - duration - 2);
    const p = (t - start) / duration;
    if (p < 0 || p > 1) return null;
    const fromRight = hash(slot, 2) < .5, angle = (22 + hash(slot, 3) * 14) * Math.PI / 180;
    const dirX = (fromRight ? -1 : 1) * Math.cos(angle), dirY = Math.sin(angle);
    const x = w * (fromRight ? .45 + .5 * hash(slot, 4) : .05 + .5 * hash(slot, 4)), y = h * (.04 + .3 * hash(slot, 5));
    const moved = (Math.min(w, h) * .42 + 160) * (1 - (1 - p) * (1 - p));
    return { headX: x + dirX * moved, headY: y + dirY * moved, dirX, dirY,
      length: 60 + 120 * Math.sin(Math.PI * Math.min(1, p * 1.4)), opacity: Math.pow(Math.sin(Math.PI * p), .7) };
  }
  let meteorActive = false;
  const visible = () => mode === 'particle' && !document.hidden && !contrast.matches;
  // Like the native starfield, star mode follows the app's own 减少动态效果 switch (data-motion).
  // The Windows "animation effects" setting (prefers-reduced-motion) must not freeze only the Web
  // pages while the native pages keep moving; outside the desktop host it still applies.
  const moving = () => visible() && root.dataset.motion !== 'reduced' && (window.__expenseDesktopHost === true || !reduced.matches);
  const needsFrames = () => moving();
  function resize() {
    width = innerWidth; height = innerHeight;
    // The interface size zooms the page (html style zoom). Cancel it on the canvas so star and
    // pointer coordinates stay in viewport pixels, the same space as the native DIP frame.
    const zoom = parseFloat(root.style?.zoom) || 1;
    if (canvas.style) canvas.style.zoom = zoom === 1 ? '' : String(1 / zoom);
    // Star dots need only one backing pixel per CSS pixel on integrated graphics.
    canvas.width = Math.max(1, Math.round(width));
    canvas.height = Math.max(1, Math.round(height));
    if (visible()) paint(performance.now(), false);
  }
  function paint(now, animate) {
    if (!context || !width || !height) return;
    context.clearRect(0, 0, width, height);
    if (hostFrame && hostGradient) {
      context.fillStyle = hostGradient;
      context.fillRect(0, 0, width, height);
    }
    const seconds = hostFrame ? hostFrame.seconds + (now - hostClock) / 1000 : now / 1000;
    const colors = tones();
    for (const star of stars) {
      const [driftX, driftY] = animate ? drift(star.index, seconds) : [0, 0];
      const baseX = star.x * (hostFrame?.width ?? width) - (hostFrame?.x ?? 0) + (hostFrame ? star.radius : 0) + driftX;
      const baseY = star.y * (hostFrame?.height ?? height) - (hostFrame?.y ?? 0) + (hostFrame ? star.radius : 0) + driftY;
      let targetX = baseX, targetY = baseY;
      if (animate && pointer) {
        let dx = baseX - pointer.x, dy = baseY - pointer.y;
        let distance = Math.hypot(dx, dy);
        const clearance = 120;
        if (distance < clearance) {
          if (distance < .01) { dx = 1; dy = 0; distance = 1; }
          const push = clearance - distance;
          targetX += dx / distance * push;
          targetY += dy / distance * push;
        }
      }
      if (!Number.isFinite(star.actualX) || !animate) {
        star.actualX = targetX; star.actualY = targetY;
      } else {
        const speed = pointer ? .43 : .24;
        star.actualX += (targetX - star.actualX) * speed;
        star.actualY += (targetY - star.actualY) * speed;
      }
      const alpha = animate ? .47 + .19 * Math.sin(seconds * 1.12 + star.phase) : .55;
      star.alpha = alpha;
      context.beginPath();
      context.fillStyle = `rgba(${colors[star.tone]},${alpha.toFixed(3)})`;
      context.arc(star.actualX, star.actualY, star.radius, 0, Math.PI * 2);
      context.fill();
    }
    // Soft glow on the nearest stars, drawn after the dots like the native halo ellipses.
    for (const star of stars) {
      if (!star.halo) continue;
      context.beginPath();
      context.fillStyle = `rgba(${colors[star.tone]},${(star.alpha * .16).toFixed(3)})`;
      context.arc(star.actualX, star.actualY, 6.5, 0, Math.PI * 2);
      context.fill();
    }
    paintMeteor(animate ? meteor(hostFrame?.width ?? width, hostFrame?.height ?? height, seconds) : null, colors);
    window.expenseDesktopCosmosWallpaper?.paint(context, width, height, now, animate);
  }
  function paintMeteor(m, colors) {
    meteorActive = Boolean(m);
    if (!m || typeof context.moveTo !== 'function') return;
    const headX = m.headX - (hostFrame?.x ?? 0), headY = m.headY - (hostFrame?.y ?? 0);
    const tailX = headX - m.dirX * m.length, tailY = headY - m.dirY * m.length;
    const trail = context.createLinearGradient(tailX, tailY, headX, headY);
    trail.addColorStop(0, `rgba(${colors[1]},0)`);
    trail.addColorStop(1, `rgba(${colors[0]},${(m.opacity * .85).toFixed(3)})`);
    context.save();
    context.strokeStyle = trail; context.lineWidth = 1.8; context.lineCap = 'round';
    context.beginPath(); context.moveTo(tailX, tailY); context.lineTo(headX, headY); context.stroke();
    context.fillStyle = `rgba(${colors[0]},${m.opacity.toFixed(3)})`;
    context.beginPath(); context.arc(headX, headY, 1.7, 0, Math.PI * 2); context.fill();
    context.restore();
  }
  function tick(now) {
    if (!needsFrames()) { frame = 0; sync(); return; }
    frame = requestAnimationFrame(tick);
    // Flying targets get 30 fps; the ambient wallpaper can idle at 15-20 fps.
    // Same pacing as the native timer: 30 fps while stars are dodging a moving pointer.
    const dodging = pointer && now - pointer.at < 800;
    const interval = window.expenseDesktopCosmosWallpaper?.wantsFrame() || meteorActive || dodging ? 33 : 66;
    if (now - last < interval) return;
    last = now;
    paint(now, true);
  }
  function sync() {
    if (visible() && !frame) paint(performance.now(), false);
    if (root.dataset.desktopVisualMode !== mode) root.dataset.desktopVisualMode = mode;
    if (!visible()) {
      if (frame) cancelAnimationFrame(frame);
      frame = 0; last = 0;
      context?.clearRect(0, 0, width, height);
      return;
    }
    if (!needsFrames()) {
      if (frame) cancelAnimationFrame(frame);
      frame = 0; last = 0;
      paint(performance.now(), false);
    } else if (!frame) frame = requestAnimationFrame(tick);
  }
  window.expenseDesktopEffects = {
    setHostFrame(value) {
      if (!value || !['width','height','x','y','seconds'].every(key => Number.isFinite(value[key])) || value.width <= 0 || value.height <= 0 || !Array.isArray(value.colors) || value.colors.length !== 3 || !value.colors.every(color => /^#[0-9a-f]{6}$/i.test(color))) return false;
      hostFrame = { ...value }; hostClock = performance.now();
      hostGradient = context.createLinearGradient(-value.x, -value.y, value.width - value.x, value.height - value.y);
      [0, .52, 1].forEach((stop, index) => hostGradient.addColorStop(stop, value.colors[index]));
      // Reset target coordinates after resizing or moving the native navigation pane.
      for (const star of stars) { star.actualX = NaN; star.actualY = NaN; }
      if (visible()) paint(performance.now(), moving());
      return true;
    },
    setMode(value) { mode = value === 'particle' ? 'particle' : 'static'; sync(); },
    repaint() { sync(); }
  };
  const install = () => {
    document.body.append(canvas);
    const dialog = document.getElementById('appearanceDialog');
    if (dialog && !dialog.querySelector('.particle-appearance-note')) {
      const note = document.createElement('p');
      note.className = 'particle-appearance-note';
      note.textContent = '星空模式保留动态星光，主题色会同步应用到网页和原生页面。切回静态模式可继续使用原来的壁纸。';
      dialog.querySelector('.appearance-heading')?.after(note);
    }
    resize(); sync();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
  else install();
  addEventListener('resize', resize, { passive: true });
  addEventListener('pointermove', event => {
    if (event.pointerType === 'touch') return;
    pointer = { x: event.clientX, y: event.clientY, at: performance.now() };
    if (!frame && moving()) frame = requestAnimationFrame(tick);
  }, { passive: true });
  // Leaving the WebView (for the native navigation pane) lets the stars settle back.
  const release = () => { pointer = null; };
  root.addEventListener?.('pointerleave', release);
  addEventListener('pointerout', event => { if (!event.relatedTarget) release(); }, { passive: true });
  addEventListener('blur', release);
  document.addEventListener('visibilitychange', sync);
  reduced.addEventListener('change', sync);
  contrast.addEventListener('change', sync);
  // A palette change retints the stars at once, even when the canvas is idle.
  // An interface-size change rewrites html style zoom and needs the canvas re-fitted.
  new MutationObserver(records => {
    if (records.some(record => record.attributeName === 'style')) resize();
    sync(); if (visible() && frame) paint(performance.now(), moving());
  }).observe(root, { attributes: true, attributeFilter: ['data-motion', 'data-theme', 'style'] });
  const bounceSelector = 'button, a.button, .glass-button, .operation-action, .analysis-action, .run-button';
  const bounce = target => {
    if (!moving()) return;
    const control = target.closest?.(bounceSelector);
    if (!control || control.disabled || control.getAttribute('aria-disabled') === 'true') return;
    control.classList.remove('particle-bounce');
    void control.offsetWidth;
    control.classList.add('particle-bounce');
    control.addEventListener('animationend', () => control.classList.remove('particle-bounce'), { once: true });
  };
  document.addEventListener('pointerdown', event => bounce(event.target), { passive: true });
  document.addEventListener('click', event => { if (event.detail === 0) bounce(event.target); });
})();
