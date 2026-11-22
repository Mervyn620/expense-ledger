/* Desktop update-page game. Flying targets share the starfield canvas; sticker aliens peek in
   from the window edges. No ledger calls. */
(() => {
  const root = document.documentElement;
  const panel = document.querySelector('[data-panel="operations"]');
  const surface = panel?.querySelector('.update-surface');
  const shipScore = document.getElementById('cosmosShipScore');
  const starScore = document.getElementById('cosmosStarScore');
  const alienScore = document.getElementById('cosmosAlienScore');
  const shipCount = document.getElementById('cosmosShipCount');
  const starCount = document.getElementById('cosmosStarCount');
  const alienCount = document.getElementById('cosmosAlienCount');
  const announcement = document.getElementById('cosmosAnnouncement');
  if (!panel || !surface || !shipScore || !starScore || !shipCount || !starCount || !announcement) return;

  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const contrast = matchMedia('(forced-colors: active)');
  const TAU = Math.PI * 2;
  const COMBO_WINDOW = 2200;
  const kinds = ['meteor', 'meteor', 'meteor', 'meteor', 'meteor', 'meteor',
    'star', 'star', 'fighter', 'ufo', 'cruiser', 'rocket'];
  const objects = kinds.map((kind, index) => ({
    kind, index, phase: index * 1.83, age: index * .73,
    seed: (2166136261 ^ Math.imul(index + 1, 16777619)) >>> 0,
    // Staggered first passes so the sky is never empty and never crowded.
    progress: kind === 'meteor' ? (index * .157 + .08) % 1 : kind === 'rocket' ? .14 : (index * .239) % 1,
    pass: null, size: 0, duration: 10, x: -200, y: -200, tilt: 0, spin: 0, trail: [], hiddenUntil: 0
  }));
  const bursts = [];
  const aliens = [];
  let lastPaint = 0, ships = 0, stars = 0, invaders = 0, combo = 0, lastHitAt = -Infinity;
  let gameEnabled = window.__expenseDesktopGameMode !== false;
  let blockedRects = [];
  let cardRect = null;
  let occlusionScheduled = false;
  let pointer = null, aimed = null;

  const enabled = () => gameEnabled && root.dataset.desktopVisualMode === 'particle' &&
    panel.classList.contains('active') && !document.hidden && !contrast.matches;
  // Inside the desktop host only the app's own 减少动态效果 switch stops motion.
  const moving = () => enabled() && root.dataset.motion !== 'reduced' &&
    (window.__expenseDesktopHost === true || !reduced.matches);
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const nextRandom = object => {
    object.seed = (Math.imul(object.seed, 1664525) + 1013904223) >>> 0;
    return object.seed / 4294967296;
  };
  const between = (object, low, high) => low + nextRandom(object) * (high - low);

  // ---------- Flight paths ----------
  // Open columns beside the update card, where a vertical rocket stays visible.
  function gutters(width) {
    const card = cardRect;
    if (!card) return [];
    return [[28, card.left - 28], [card.right + 28, width - 28]].filter(([low, high]) => high - low >= 36);
  }
  // Every pass picks a fresh lane, speed and size, so the sky does not repeat.
  function newPass(object, width, height) {
    const { kind } = object;
    if (kind === 'meteor') {
      // Upper right to lower left at 30-42 degrees below the horizontal.
      const angle = between(object, .52, .73);
      const dx = -Math.cos(angle), dy = Math.sin(angle);
      const fromTop = nextRandom(object) < .68;
      const startX = fromTop ? width * between(object, .3, 1.02) : width + 70;
      const startY = fromTop ? -60 : height * between(object, -.05, .45);
      const length = Math.min((startX + 120) / -dx, (height + 120 - startY) / dy);
      object.size = [3.5, 4.5, 6, 7.5, 9.5][Math.floor(nextRandom(object) * 5)];
      const speed = between(object, 175, 260) * (object.size > 7 ? .8 : 1);
      object.pass = { startX, startY, dx, dy, length, wobble: between(object, 4, 9) };
      object.duration = Math.max(3.5, length / speed);
      object.shape = Array.from({ length: 8 }, () => between(object, .78, 1.14));
    } else if (kind === 'rocket') {
      // Vertical climb with a sideways sway, kept inside an open column when one exists.
      const columns = gutters(width);
      const column = columns.length ? columns[Math.floor(nextRandom(object) * columns.length)] : [width * .1, width * .9];
      const room = (column[1] - column[0]) / 2;
      object.size = between(object, 19, 24);
      object.pass = { lane: (column[0] + column[1]) / 2 + between(object, -.35, .35) * room,
        sway: clamp(room - 12, 6, between(object, 14, 26)), turns: between(object, 1.2, 2.1) };
      object.duration = between(object, 9.5, 13.5);
    } else if (kind === 'ufo') {
      // Side to side with a vertical bob; alternates direction every pass.
      const direction = object.pass ? -object.pass.direction : 1;
      object.size = between(object, 19, 23);
      object.pass = { direction, lane: height * between(object, .16, .84), bob: between(object, 14, 26), dart: between(object, 16, 30) };
      object.duration = between(object, 15, 21);
    } else if (kind === 'fighter' || kind === 'cruiser') {
      const direction = kind === 'fighter' ? 1 : -1;
      object.size = kind === 'fighter' ? between(object, 17, 20) : between(object, 23, 27);
      object.pass = { direction, lane: height * between(object, .14, .86), wobble: between(object, 6, 12) };
      object.duration = kind === 'fighter' ? between(object, 12, 16) : between(object, 18, 24);
    } else {
      object.size = object.index === 6 ? 9 : 11;
      object.pass = {};
    }
  }

  function locate(object, width, height) {
    if (!object.pass) newPass(object, width, height);
    const { kind, progress: t, phase, pass } = object;
    const previousX = object.x, previousY = object.y;
    if (kind === 'meteor') {
      const along = t * pass.length, side = Math.sin(t * 9.3 + phase) * pass.wobble * (1 - t * .4);
      object.x = pass.startX + pass.dx * along - pass.dy * side;
      object.y = pass.startY + pass.dy * along + pass.dx * side;
    } else if (kind === 'rocket') {
      object.x = pass.lane + Math.sin(t * TAU * pass.turns + phase) * pass.sway + Math.sin(t * TAU * 5.3 + phase) * 2.5;
      object.y = height + 80 - t * (height + 170);
    } else if (kind === 'ufo') {
      const span = width + 200;
      object.x = (pass.direction > 0 ? -100 + t * span : width + 100 - t * span) + Math.sin(t * Math.PI * 5 + phase) * pass.dart;
      object.y = clamp(pass.lane + Math.sin(object.age * 1.7 + phase) * pass.bob +
        Math.sin(object.age * .53 + phase) * pass.bob * .6, 56, height - 56);
    } else if (kind === 'star') {
      const baseX = object.index === 6 ? .08 : .93;
      const baseY = object.index === 6 ? .44 : .65;
      object.x = width * baseX + Math.sin(object.age * .37 + phase) * 18;
      object.y = height * baseY + Math.sin(object.age * .49 + phase) * 12;
    } else {
      const span = width + 190;
      object.x = pass.direction > 0 ? -95 + t * span : width + 95 - t * span;
      object.y = pass.lane + Math.sin(t * 10 + phase) * pass.wobble + Math.sin(t * 23 + phase) * 3;
    }
    // Lean into the motion: rockets tilt with their sway, saucers and ships bank as they bob.
    if (Number.isFinite(previousX) && previousX > -199) {
      const dx = object.x - previousX, dy = object.y - previousY;
      const lean = kind === 'rocket' ? Math.atan2(dx, -dy || -1) : kind === 'meteor' || kind === 'star' ? 0 :
        clamp(dy * .09 * (kind === 'ufo' ? 1 : pass.direction), -.3, .3);
      object.tilt += (clamp(lean, -.45, .45) - object.tilt) * .2;
    }
  }

  // ---------- Sprites (canvas) ----------
  function drawMeteor(context, object) {
    const { x, y, size, pass } = object;
    const dx = pass.dx, dy = pass.dy, nx = -dy, ny = dx;
    const tail = size * 15, width = size * 1.15;
    const tx = x - dx * tail, ty = y - dy * tail;
    let gradient = context.createLinearGradient(x, y, tx, ty);
    gradient.addColorStop(0, 'rgba(150,222,255,.46)');
    gradient.addColorStop(1, 'rgba(110,180,255,0)');
    context.beginPath();
    context.moveTo(x + nx * width, y + ny * width); context.lineTo(tx, ty); context.lineTo(x - nx * width, y - ny * width);
    context.closePath(); context.fillStyle = gradient; context.fill();
    gradient = context.createLinearGradient(x, y, x - dx * tail * .62, y - dy * tail * .62);
    gradient.addColorStop(0, 'rgba(255,250,236,.92)');
    gradient.addColorStop(1, 'rgba(255,210,150,0)');
    context.beginPath();
    context.moveTo(x + nx * width * .42, y + ny * width * .42); context.lineTo(x - dx * tail * .62, y - dy * tail * .62);
    context.lineTo(x - nx * width * .42, y - ny * width * .42); context.closePath();
    context.fillStyle = gradient; context.fill();
    gradient = context.createRadialGradient(x, y, 0, x, y, size * 2.6);
    gradient.addColorStop(0, 'rgba(190,236,255,.42)'); gradient.addColorStop(1, 'rgba(150,215,255,0)');
    context.beginPath(); context.arc(x, y, size * 2.6, 0, TAU); context.fillStyle = gradient; context.fill();
    if (size < 5) {
      context.beginPath(); context.arc(x, y, size * .8, 0, TAU); context.fillStyle = '#f0fbff'; context.fill();
      return;
    }
    // A tumbling rock, lit on its leading edge by the heat of entry.
    context.save(); context.translate(x, y); context.rotate(object.spin);
    context.beginPath();
    object.shape.forEach((radius, corner) => {
      const angle = corner / object.shape.length * TAU;
      const px = Math.cos(angle) * radius * size, py = Math.sin(angle) * radius * size;
      if (corner === 0) context.moveTo(px, py); else context.lineTo(px, py);
    });
    context.closePath();
    const lx = Math.cos(-object.spin) * dx - Math.sin(-object.spin) * dy;
    const ly = Math.sin(-object.spin) * dx + Math.cos(-object.spin) * dy;
    gradient = context.createRadialGradient(lx * size * .55, ly * size * .55, size * .1, 0, 0, size * 1.2);
    gradient.addColorStop(0, '#fff4df'); gradient.addColorStop(.38, '#c89b78'); gradient.addColorStop(1, '#5a463f');
    context.fillStyle = gradient; context.fill();
    context.fillStyle = 'rgba(52,38,36,.45)';
    context.beginPath(); context.arc(-size * .3, size * .25, size * .22, 0, TAU); context.fill();
    context.beginPath(); context.arc(size * .32, -size * .28, size * .15, 0, TAU); context.fill();
    context.restore();
  }

  function sparkle(context, radius, waist) {
    context.beginPath();
    for (let point = 0; point < 4; point++) {
      const angle = point * Math.PI / 2 - Math.PI / 2;
      const next = angle + Math.PI / 2;
      if (point === 0) context.moveTo(Math.cos(angle) * radius, Math.sin(angle) * radius);
      context.quadraticCurveTo(Math.cos(angle + Math.PI / 4) * waist, Math.sin(angle + Math.PI / 4) * waist,
        Math.cos(next) * radius, Math.sin(next) * radius);
    }
    context.closePath();
  }
  function drawStar(context, object, animate) {
    const { x, y, size } = object;
    const pulse = animate ? 1 + Math.sin(object.age * 2.3 + object.phase) * .09 : 1;
    context.save(); context.translate(x, y);
    let gradient = context.createRadialGradient(0, 0, 0, 0, 0, size * 2.5);
    gradient.addColorStop(0, 'rgba(255,233,160,.42)'); gradient.addColorStop(1, 'rgba(255,214,120,0)');
    context.beginPath(); context.arc(0, 0, size * 2.5, 0, TAU); context.fillStyle = gradient; context.fill();
    context.rotate(animate ? object.age * .35 : 0); context.scale(pulse, pulse);
    context.save(); context.rotate(Math.PI / 4); sparkle(context, size * .95, size * .16);
    context.fillStyle = 'rgba(255,246,214,.7)'; context.fill(); context.restore();
    sparkle(context, size * 1.65, size * .2);
    gradient = context.createRadialGradient(0, 0, 0, 0, 0, size * 1.65);
    gradient.addColorStop(0, '#ffffff'); gradient.addColorStop(.35, '#fff1b8'); gradient.addColorStop(1, '#ffc95c');
    context.fillStyle = gradient; context.fill();
    context.restore();
  }

  function drawTrail(context, object, color) {
    object.trail.forEach((point, index) => {
      const life = 1 - index / object.trail.length;
      context.beginPath(); context.arc(point.x, point.y, 2.5 + (1 - life) * 7, 0, TAU);
      context.fillStyle = `rgba(${color},${(life * .2).toFixed(3)})`; context.fill();
    });
  }
  function flame(context, x, y, length, half, horizontal) {
    const gradient = horizontal ? context.createLinearGradient(x, y, x - length, y) : context.createLinearGradient(x, y, x, y + length);
    gradient.addColorStop(0, 'rgba(255,246,214,.98)'); gradient.addColorStop(.35, 'rgba(255,176,84,.85)'); gradient.addColorStop(1, 'rgba(255,90,70,0)');
    context.beginPath();
    if (horizontal) { context.moveTo(x, y - half); context.quadraticCurveTo(x - length, y, x, y + half); }
    else { context.moveTo(x - half, y); context.quadraticCurveTo(x, y + length, x + half, y); }
    context.closePath(); context.fillStyle = gradient; context.fill();
  }
  function drawRocket(context, object, now) {
    drawTrail(context, object, '214,226,244');
    const flicker = 1 + Math.sin(now * .047 + object.phase) * .16 + Math.sin(now * .113) * .08;
    context.save(); context.translate(object.x, object.y); context.rotate(object.tilt); context.scale(object.size / 20, object.size / 20);
    flame(context, 0, 14, 26 * flicker, 6.5, false);
    context.fillStyle = '#ec5f67';
    for (const side of [-1, 1]) {
      context.beginPath(); context.moveTo(side * 7.5, 2); context.quadraticCurveTo(side * 17, 8, side * 16, 20);
      context.lineTo(side * 7, 14); context.closePath(); context.fill();
    }
    context.beginPath();
    context.moveTo(0, -29); context.bezierCurveTo(9, -21, 10.5, -6, 8.5, 14); context.lineTo(-8.5, 14);
    context.bezierCurveTo(-10.5, -6, -9, -21, 0, -29); context.closePath();
    let gradient = context.createLinearGradient(-10, 0, 10, 0);
    gradient.addColorStop(0, '#9fb0c9'); gradient.addColorStop(.38, '#f7faff'); gradient.addColorStop(.72, '#dde5f1'); gradient.addColorStop(1, '#8395b2');
    context.fillStyle = gradient; context.fill();
    context.save(); context.clip();
    context.fillStyle = '#ec5f67'; context.fillRect(-11, -30, 22, 11.5);
    context.fillStyle = 'rgba(46,58,88,.3)'; context.fillRect(-11, 10, 22, 4);
    context.restore();
    context.lineWidth = 1; context.strokeStyle = 'rgba(34,44,72,.5)'; context.stroke();
    context.beginPath(); context.arc(0, -5, 4.8, 0, TAU); context.fillStyle = '#56688a'; context.fill();
    gradient = context.createRadialGradient(-1.3, -6.4, .4, 0, -5, 3.6);
    gradient.addColorStop(0, '#e2fbff'); gradient.addColorStop(1, '#2d86c9');
    context.beginPath(); context.arc(0, -5, 3.5, 0, TAU); context.fillStyle = gradient; context.fill();
    context.fillStyle = '#d24d58'; context.fillRect(-1.2, 5, 2.4, 10);
    context.restore();
  }
  function drawUfo(context, object, now) {
    const beam = Math.max(0, (Math.sin(object.age * .45 + object.phase) - .9) / .1);
    context.save(); context.translate(object.x, object.y); context.rotate(object.tilt); context.scale(object.size / 20, object.size / 20);
    if (beam > 0) {
      const gradient = context.createLinearGradient(0, 6, 0, 52);
      gradient.addColorStop(0, `rgba(170,255,214,${(.42 * beam).toFixed(3)})`); gradient.addColorStop(1, 'rgba(170,255,214,0)');
      context.beginPath(); context.moveTo(-8, 6); context.lineTo(8, 6); context.lineTo(22, 52); context.lineTo(-22, 52);
      context.closePath(); context.fillStyle = gradient; context.fill();
    }
    // Glass dome with its pilot.
    context.beginPath(); context.ellipse(0, -2, 11.5, 11, 0, Math.PI, 0); context.closePath();
    context.fillStyle = 'rgba(126,214,240,.34)'; context.fill();
    context.beginPath(); context.arc(0, -4, 5, 0, TAU); context.fillStyle = '#86e3a9'; context.fill();
    context.fillStyle = '#17213a';
    context.beginPath(); context.ellipse(-1.9, -4.4, 1.3, 1.9, 0, 0, TAU); context.fill();
    context.beginPath(); context.ellipse(1.9, -4.4, 1.3, 1.9, 0, 0, TAU); context.fill();
    context.beginPath(); context.ellipse(0, -2, 11.5, 11, 0, Math.PI, 0);
    context.strokeStyle = 'rgba(230,250,255,.7)'; context.lineWidth = 1.2; context.stroke();
    context.beginPath(); context.arc(-4, -8, 4, Math.PI * 1.1, Math.PI * 1.45);
    context.strokeStyle = 'rgba(255,255,255,.75)'; context.lineWidth = 1.6; context.stroke();
    // Saucer.
    let gradient = context.createLinearGradient(0, -5, 0, 10);
    gradient.addColorStop(0, '#f2f6fd'); gradient.addColorStop(.55, '#b8c3d8'); gradient.addColorStop(1, '#6f7c99');
    context.beginPath(); context.ellipse(0, 2, 27, 8, 0, 0, TAU); context.fillStyle = gradient; context.fill();
    context.lineWidth = 1; context.strokeStyle = 'rgba(30,40,70,.45)'; context.stroke();
    context.beginPath(); context.ellipse(0, 4.4, 26, 3.4, 0, 0, Math.PI); context.fillStyle = '#56627f'; context.fill();
    const lit = Math.floor(now / 150) % 5;
    for (let light = 0; light < 5; light++) {
      context.beginPath(); context.arc(-18 + light * 9, 4.2, light === lit ? 2.3 : 1.7, 0, TAU);
      context.fillStyle = light === lit ? '#ffd36e' : 'rgba(140,236,255,.8)'; context.fill();
    }
    context.restore();
  }
  function drawFighter(context, object, now) {
    const flicker = 1 + Math.sin(now * .05 + object.phase) * .18;
    context.save(); context.translate(object.x, object.y); context.rotate(object.tilt);
    context.scale(object.size / 20 * object.pass.direction, object.size / 20);
    flame(context, -18, 0, 18 * flicker, 3.4, true);
    context.fillStyle = '#5876ad';
    for (const side of [-1, 1]) {
      context.beginPath(); context.moveTo(4, side * 3); context.lineTo(-11, side * 16); context.lineTo(-17, side * 16);
      context.lineTo(-11, side * 3); context.closePath(); context.fill();
    }
    context.beginPath(); context.moveTo(26, 0); context.quadraticCurveTo(10, -6.5, -18, -5); context.lineTo(-21, 0);
    context.lineTo(-18, 5); context.quadraticCurveTo(10, 6.5, 26, 0); context.closePath();
    let gradient = context.createLinearGradient(0, -6, 0, 6);
    gradient.addColorStop(0, '#f6f9ff'); gradient.addColorStop(1, '#93a6c6');
    context.fillStyle = gradient; context.fill();
    context.lineWidth = 1; context.strokeStyle = 'rgba(34,44,72,.5)'; context.stroke();
    gradient = context.createLinearGradient(4, -4, 12, 1);
    gradient.addColorStop(0, '#c9f6ff'); gradient.addColorStop(1, '#2f8fd0');
    context.beginPath(); context.ellipse(8, -1.4, 6.5, 2.7, 0, 0, TAU); context.fillStyle = gradient; context.fill();
    context.fillStyle = '#ec5f67'; context.fillRect(-13, -1, 10, 2);
    context.restore();
  }
  function drawCruiser(context, object, now) {
    const flicker = 1 + Math.sin(now * .043 + object.phase) * .14;
    context.save(); context.translate(object.x, object.y); context.rotate(object.tilt);
    context.scale(object.size / 20 * object.pass.direction, object.size / 20);
    flame(context, -28, -4.5, 16 * flicker, 2.8, true);
    flame(context, -28, 4.5, 16 * flicker, 2.8, true);
    context.beginPath(); context.moveTo(-10, -9); context.lineTo(-4, -15); context.lineTo(8, -15); context.lineTo(12, -8); context.closePath();
    context.fillStyle = '#9ba9c4'; context.fill();
    context.beginPath(); context.moveTo(31, 0); context.lineTo(15, -8.5); context.lineTo(-14, -10.5); context.lineTo(-28, -7.5);
    context.lineTo(-28, 7.5); context.lineTo(-14, 10.5); context.lineTo(15, 8.5); context.closePath();
    const gradient = context.createLinearGradient(0, -11, 0, 11);
    gradient.addColorStop(0, '#eef3fb'); gradient.addColorStop(.6, '#aab7cf'); gradient.addColorStop(1, '#6c7a97');
    context.fillStyle = gradient; context.fill();
    context.lineWidth = 1; context.strokeStyle = 'rgba(34,44,72,.5)'; context.stroke();
    context.beginPath(); context.moveTo(-4, -10); context.lineTo(-4, 10); context.moveTo(10, -9); context.lineTo(10, 9); context.stroke();
    context.fillStyle = '#9ff0ff';
    for (let port = 0; port < 6; port++) { context.beginPath(); context.arc(-20 + port * 6, -1.5, 1.2, 0, TAU); context.fill(); }
    context.fillStyle = '#ec5f67'; context.fillRect(-26, 3.5, 22, 1.8);
    context.restore();
  }

  function drawAim(context, object, now) {
    const radius = hitRadius(object) * .78;
    context.save(); context.translate(object.x, object.y); context.rotate(now * .002);
    context.strokeStyle = 'rgba(255,214,140,.75)'; context.lineWidth = 1.6; context.setLineDash?.([6, 7]);
    context.beginPath(); context.arc(0, 0, radius, 0, TAU); context.stroke();
    context.setLineDash?.([]);
    context.restore();
  }

  function drawBursts(context, now) {
    for (let index = bursts.length - 1; index >= 0; index--) {
      const burst = bursts[index];
      const span = burst.kind === 'ship' ? 920 : burst.kind === 'text' ? 950 : burst.kind === 'miss' ? 380 : 1080;
      const progress = (now - burst.time) / span;
      if (progress >= 1) { bursts.splice(index, 1); continue; }
      if (progress < 0) continue;
      const fade = 1 - progress;
      context.save();
      context.globalAlpha = fade;
      context.translate(burst.x, burst.y);
      if (burst.kind === 'text') {
        const rise = -12 - progress * 30, scale = progress < .15 ? .7 + progress * 2 : 1;
        context.translate(0, rise); context.scale(scale, scale);
        context.font = `700 ${burst.big ? 17 : 15}px "Segoe UI", "Microsoft YaHei UI", sans-serif`;
        context.textAlign = 'center'; context.textBaseline = 'middle';
        context.lineJoin = 'round'; context.lineWidth = 4; context.strokeStyle = 'rgba(8,12,30,.72)';
        context.strokeText(burst.text, 0, 0);
        context.fillStyle = burst.color; context.fillText(burst.text, 0, 0);
      } else if (burst.kind === 'miss') {
        context.strokeStyle = 'rgba(200,232,255,.7)'; context.lineWidth = 1.5;
        context.beginPath(); context.arc(0, 0, 4 + progress * 16, 0, TAU); context.stroke();
      } else if (burst.kind === 'ship') {
        context.strokeStyle = '#ffb56f';
        context.lineWidth = 3;
        context.beginPath(); context.arc(0, 0, 10 + progress * 67, 0, TAU); context.stroke();
        if (progress < .42) {
          context.fillStyle = '#fff4d1';
          context.beginPath(); context.arc(0, 0, 18 * (1 - progress / .42), 0, TAU); context.fill();
        }
        for (let spark = 0; spark < 20; spark++) {
          const angle = spark * Math.PI / 10 + burst.phase;
          const distance = 8 + progress * (42 + spark % 5 * 9);
          const x = Math.cos(angle) * distance, y = Math.sin(angle) * distance;
          const length = (7 + spark % 4 * 3) * fade;
          context.strokeStyle = spark % 3 === 0 ? '#fff1c5' : spark % 2 === 0 ? '#ffca76' : '#ff845e';
          context.lineWidth = spark % 4 === 0 ? 3 : 2;
          context.beginPath(); context.moveTo(x, y);
          context.lineTo(x + Math.cos(angle) * length, y + Math.sin(angle) * length); context.stroke();
        }
      } else {
        context.beginPath(); context.arc(0, 0, 12 + progress * 56, 0, TAU);
        context.strokeStyle = '#ffe2a1'; context.lineWidth = 2.2; context.stroke();
        context.fillStyle = '#ffe7b5';
        for (let spark = 0; spark < 16; spark++) {
          const angle = spark * Math.PI / 8 + burst.phase;
          const distance = progress * (27 + spark % 4 * 9);
          context.beginPath();
          context.arc(Math.cos(angle) * distance, Math.sin(angle) * distance, Math.max(.7, 3.1 * fade), 0, TAU);
          context.fill();
        }
      }
      context.restore();
    }
  }

  function paint(context, width, height, now, animate) {
    if (!enabled()) { lastPaint = 0; return; }
    const elapsed = animate && lastPaint ? clamp((now - lastPaint) / 1000, 0, .05) : 0;
    lastPaint = now;
    // Cards occlude the flying targets, as objects behind a glass panel would.
    context.save();
    context.beginPath();
    context.rect(0, 0, width, height);
    for (const rect of blockedRects) context.rect(rect.left, rect.top, rect.width, rect.height);
    context.clip('evenodd');
    for (const object of objects) {
      if (now < object.hiddenUntil) continue;
      object.age += elapsed;
      object.spin += elapsed * (object.kind === 'meteor' ? 1.6 : 0);
      if (object.kind !== 'star') {
        object.progress += elapsed / object.duration;
        if (object.progress >= 1) { object.progress -= 1; newPass(object, width, height); object.trail.length = 0; object.x = -200; }
      }
      locate(object, width, height);
      if (object.kind === 'rocket' && elapsed) {
        object.trail.unshift({ x: object.x - Math.sin(object.tilt) * object.size * 1.9, y: object.y + Math.cos(object.tilt) * object.size * 1.9 });
        if (object.trail.length > 9) object.trail.pop();
      }
      if (object.x < -150 || object.x > width + 150 || object.y < -150 || object.y > height + 150) continue;
      if (object.kind === 'meteor') drawMeteor(context, object);
      else if (object.kind === 'star') drawStar(context, object, animate);
      else if (object.kind === 'rocket') drawRocket(context, object, now);
      else if (object.kind === 'ufo') drawUfo(context, object, now);
      else if (object.kind === 'fighter') drawFighter(context, object, now);
      else drawCruiser(context, object, now);
    }
    if (animate) {
      updateAim(now);
      if (aimed) drawAim(context, aimed, now);
      drawBursts(context, now);
    }
    context.restore();
  }

  function updateOcclusion() {
    const rect = surface.getBoundingClientRect();
    cardRect = rect.width > 0 ? { left: rect.left, right: rect.left + rect.width, top: rect.top, bottom: rect.top + rect.height } : null;
    blockedRects = Array.from(panel.querySelectorAll('.surface'))
      .map(element => element.getBoundingClientRect())
      .filter(rect => rect.width > 0 && rect.height > 0)
      .map(rect => ({ left: rect.left, top: rect.top, width: rect.width, height: rect.height }));
    window.expenseDesktopEffects?.repaint();
  }
  function scheduleOcclusion() {
    if (occlusionScheduled) return;
    occlusionScheduled = true;
    requestAnimationFrame(() => { occlusionScheduled = false; if (enabled()) updateOcclusion(); });
  }

  // ---------- Sticker aliens ----------
  const alienDesigns = [
    { body: '#8fe3b4', light: '#d4fbe2', shade: '#3f9c76', eyes: 2, top: 'antennae', cheek: '#ff9bb8', tip: '#ffd36e' },
    { body: '#bba4f7', light: '#ece4ff', shade: '#7356c4', eyes: 1, top: 'antenna', cheek: '#ff9ecf', tip: '#8ff0ff' },
    { body: '#86cff6', light: '#ddf4ff', shade: '#3c84bf', eyes: 3, top: 'horns', cheek: '#ffa4c0', tip: '#ffe08a' },
    { body: '#ffb595', light: '#ffe8dc', shade: '#d06b55', eyes: 2, top: 'ears', cheek: '#ff7f9c', tip: '#fff1a8' }
  ];
  const sideTurn = { bottom: 0, top: 180, left: 90, right: -90 };
  function alienMarkup(design, id) {
    const head = 'M60 28C87 28 104 46 104 69C104 92 86 107 60 107C34 107 16 92 16 69C16 46 33 28 60 28Z';
    const crown = {
      antennae: '<path d="M45 33Q41 20 33 13"/><path d="M75 33Q79 20 87 13"/>',
      antenna: '<path d="M60 28Q63 15 60 7"/>',
      horns: '', ears: ''
    }[design.top];
    const crownShapes = {
      antennae: '<circle cx="33" cy="12" r="7"/><circle cx="87" cy="12" r="7"/>',
      antenna: '<circle cx="60" cy="7" r="8"/>',
      horns: '<path d="M30 46Q26 25 39 23Q42 35 43 40Z"/><path d="M90 46Q94 25 81 23Q78 35 77 40Z"/>',
      ears: '<path d="M21 66Q2 55 5 40Q18 44 27 56Z"/><path d="M99 66Q118 55 115 40Q102 44 93 56Z"/>'
    }[design.top];
    const hands = '<ellipse cx="31" cy="99" rx="12" ry="8.5"/><ellipse cx="89" cy="99" rx="12" ry="8.5"/>';
    const silhouette = `${crownShapes}<path d="${head}"/>${hands}`;
    const eyes = design.eyes === 1 ? [[60, 66, 17, 18, 9.5]] : design.eyes === 3 ?
      [[41, 71, 8.5, 10, 5], [60, 59, 9.5, 11, 5.5], [79, 71, 8.5, 10, 5]] : [[44, 68, 11, 13.5, 6.5], [76, 68, 11, 13.5, 6.5]];
    const sclera = eyes.map(([cx, cy, rx, ry]) => `<ellipse cx="${cx}" cy="${cy}" rx="${rx}" ry="${ry}"/>`).join('');
    const pupils = eyes.map(([cx, cy, , , r]) =>
      `<circle cx="${cx}" cy="${cy + 1}" r="${r}" fill="#1b2140"/><circle cx="${cx - r * .38}" cy="${cy - r * .32}" r="${r * .36}" fill="#fff"/>`).join('');
    const knocked = eyes.map(([cx, cy, , , r]) =>
      `<path d="M${cx - r} ${cy - r}L${cx + r} ${cy + r}M${cx + r} ${cy - r}L${cx - r} ${cy + r}"/>`).join('');
    const mouthY = design.eyes === 1 ? 92 : 89;
    return `<svg class="cosmos-alien__art" viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" focusable="false">
      <defs><radialGradient id="${id}" cx="38%" cy="30%" r="80%">
        <stop offset="0" stop-color="${design.light}"/><stop offset=".45" stop-color="${design.body}"/><stop offset="1" stop-color="${design.shade}"/>
      </radialGradient></defs>
      <g class="alien-head">
        <g class="alien-sticker" stroke="#f8fbff" stroke-width="10" stroke-linejoin="round" stroke-linecap="round"><g fill="none">${crown}</g><g fill="#f8fbff">${crownShapes}<path d="${head}"/></g></g>
        <g fill="none" stroke="${design.shade}" stroke-width="4" stroke-linecap="round">${crown}</g>
        <g fill="url(#${id})" stroke="${design.shade}" stroke-width="2">${crownShapes}<path d="${head}"/></g>
        ${design.top === 'antennae' || design.top === 'antenna' ? `<g fill="${design.tip}">${design.top === 'antenna' ? '<circle cx="60" cy="7" r="4"/>' : '<circle cx="33" cy="12" r="3.5"/><circle cx="87" cy="12" r="3.5"/>'}</g>` : ''}
        <path d="M33 50Q41 37 55 33" fill="none" stroke="#fff" stroke-opacity=".55" stroke-width="5" stroke-linecap="round"/>
        <g class="alien-eyes">
          <g fill="#fff" stroke="${design.shade}" stroke-width="1.5">${sclera}</g>
          <g class="alien-pupils">${pupils}</g>
          <g class="alien-lids" fill="${design.body}">${sclera}</g>
        </g>
        <g class="alien-ko" fill="none" stroke="#1b2140" stroke-width="3.2" stroke-linecap="round">${knocked}
          <path d="M50 ${mouthY + 1}q5 -5 10 0t10 0"/></g>
        <g class="alien-face">
          <ellipse cx="${design.eyes === 3 ? 30 : 31}" cy="85" rx="7" ry="4" fill="${design.cheek}" opacity=".55"/>
          <ellipse cx="${design.eyes === 3 ? 90 : 89}" cy="85" rx="7" ry="4" fill="${design.cheek}" opacity=".55"/>
          <path d="M52 ${mouthY}Q60 ${mouthY + 8} 68 ${mouthY}" fill="none" stroke="#2a2342" stroke-width="3" stroke-linecap="round"/>
        </g>
      </g>
      <g class="alien-hands">
        <g fill="#f8fbff" stroke="#f8fbff" stroke-width="10">${hands}</g>
        <g fill="${design.body}" stroke="${design.shade}" stroke-width="2">${hands}</g>
        <path d="M25 95v6M31 94v7M37 95v6M83 95v6M89 94v7M95 95v6" stroke="${design.shade}" stroke-width="1.6" stroke-linecap="round" opacity=".7"/>
      </g>
    </svg>`;
  }

  function addAliens() {
    if (document.getElementById('cosmos-edge-aliens')) return;
    const container = document.createElement('div');
    container.id = 'cosmos-edge-aliens';
    alienDesigns.forEach((design, index) => {
      const element = document.createElement('span');
      element.className = 'cosmos-alien';
      element.setAttribute('role', 'button');
      element.setAttribute('aria-label', '攻击探头的外星人');
      element.setAttribute('aria-hidden', 'true');
      element.tabIndex = -1;
      element.style.setProperty('--alien-blink-delay', `${(index * 1.37) % 4.2}s`);
      element.innerHTML = alienMarkup(design, `cosmos-alien-skin-${index}`);
      const state = { element, side: null, timer: 0, running: false, visibleRect: null, spawns: 0 };
      element.addEventListener('pointerdown', event => {
        if (event.button !== 0 || !element.classList.contains('is-peeking') || !enabled()) return;
        event.preventDefault();
        event.stopPropagation();
        hitAlien(state, performance.now());
      });
      element.addEventListener('keydown', event => {
        if (!['Enter', ' '].includes(event.key) || !element.classList.contains('is-peeking') || !enabled()) return;
        event.preventDefault();
        hitAlien(state, performance.now());
      });
      aliens.push(state);
      container.append(element);
    });
    document.body.append(container);
  }

  // Aliens grip one of the four window edges, clear of the cards, the scores, the bonus stars and each other.
  function placeAlien(state) {
    const width = innerWidth, height = innerHeight;
    const directions = ['top', 'right', 'bottom', 'left'];
    const cards = blockedRects.length ? blockedRects : Array.from(panel.querySelectorAll('.surface'))
      .map(element => element.getBoundingClientRect());
    const obstacles = cards.map(rect => ({ left: rect.left, top: rect.top,
      right: rect.right ?? rect.left + rect.width, bottom: rect.bottom ?? rect.top + rect.height }));
    for (const score of [shipScore, starScore, alienScore]) {
      if (score && !score.hidden) obstacles.push(score.getBoundingClientRect());
    }
    // A top visitor may lean over the empty middle of the title bar, never over its title or buttons.
    for (const element of document.querySelectorAll('.topbar :is(h1, h2, button, a)')) {
      const rect = element.getBoundingClientRect();
      if (rect.width > 0) obstacles.push({ left: rect.left, top: rect.top, right: rect.left + rect.width, bottom: rect.top + rect.height });
    }
    for (const star of objects.filter(object => object.kind === 'star' && object.x >= 0)) {
      obstacles.push({ left: star.x - 38, right: star.x + 38, top: star.y - 38, bottom: star.y + 38 });
    }
    for (const other of aliens) {
      if (other !== state && other.element.classList.contains('is-peeking') && other.visibleRect) {
        obstacles.push(other.visibleRect);
      }
    }
    const overlaps = (a, b) => a.left < b.right + 12 && a.right > b.left - 12 &&
      a.top < b.bottom + 12 && a.bottom > b.top - 12;
    let chosen;
    for (let attempt = 0; attempt < 32; attempt++) {
      const pool = attempt < 24 ? directions.filter(side => side !== state.side) : directions;
      const side = pool[Math.floor(Math.random() * pool.length)];
      const size = Math.round(width < 760 ? 70 + Math.random() * 30 : 88 + Math.random() * 38);
      // The visible part ends just below the hands, so they appear to grip the edge.
      const peek = Math.round(size * .8);
      const along = side === 'left' || side === 'right' ? height : width;
      const margin = side === 'left' || side === 'right' ? Math.min(215, Math.max(100, height * .3)) :
        Math.min(140, Math.max(18, width * .12));
      const start = Math.min(margin, Math.max(12, along - size - 12));
      const end = Math.max(start, along - size - (side === 'left' || side === 'right' ? 16 : margin));
      const position = Math.round(start + Math.random() * (end - start));
      const rect = side === 'left' ? { left: 0, right: peek, top: position, bottom: position + size } :
        side === 'right' ? { left: width - peek, right: width, top: position, bottom: position + size } :
          side === 'top' ? { left: position, right: position + size, top: 0, bottom: peek } :
            { left: position, right: position + size, top: height - peek, bottom: height };
      if (obstacles.some(obstacle => overlaps(rect, obstacle))) continue;
      chosen = { side, size, peek, position, rect };
      break;
    }
    if (!chosen) return false;
    const { side, size, peek, position, rect } = chosen;
    state.side = side;
    state.visibleRect = rect;
    state.element.className = `cosmos-alien cosmos-alien--${side} is-repositioning`;
    state.element.style.setProperty('--alien-size', `${size}px`);
    state.element.style.setProperty('--alien-peek', `${peek}px`);
    state.element.style.setProperty('--alien-position', `${position}px`);
    state.element.style.setProperty('--alien-turn', `${sideTurn[side]}deg`);
    state.element.style.setProperty('--look-x', '0px');
    state.element.style.setProperty('--look-y', '0px');
    state.element.setAttribute('aria-label', `攻击从${{ right: '右', left: '左', bottom: '下', top: '上' }[side]}边探头的外星人`);
    state.element.dataset.spawn = String(++state.spawns);
    state.element.dataset.spawnLocation = `${side}:${rect.left}:${rect.top}`;
    return true;
  }

  // Pupils follow the pointer, turned into the sticker's own frame.
  function lookAt(x, y) {
    for (const state of aliens) {
      if (!state.visibleRect || !state.element.classList.contains('is-peeking')) continue;
      const rect = state.visibleRect;
      const dx = x - (rect.left + rect.right) / 2, dy = y - (rect.top + rect.bottom) / 2;
      const distance = Math.hypot(dx, dy) || 1, reach = Math.min(1, distance / 160) * 3.4;
      const turn = -sideTurn[state.side] * Math.PI / 180;
      const ux = dx / distance * reach, uy = dy / distance * reach;
      state.element.style.setProperty('--look-x', `${(ux * Math.cos(turn) - uy * Math.sin(turn)).toFixed(2)}px`);
      state.element.style.setProperty('--look-y', `${(ux * Math.sin(turn) + uy * Math.cos(turn)).toFixed(2)}px`);
    }
  }

  function setAlienVisible(state, visible) {
    if (!visible && document.activeElement === state.element) {
      if (enabled()) surface.focus({ preventScroll: true });
      else state.element.blur();
    }
    state.element.classList.toggle('is-peeking', visible);
    state.element.tabIndex = visible ? 0 : -1;
    state.element.setAttribute('aria-hidden', visible ? 'false' : 'true');
  }
  function queueAlien(state, delay, callback) {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => { state.timer = 0; if (state.running) callback(); }, delay);
  }
  function showAlien(state) {
    if (!moving()) return;
    state.element.classList.remove('is-hit');
    if (!placeAlien(state)) {
      queueAlien(state, 1200 + Math.random() * 1800, () => showAlien(state));
      return;
    }
    queueAlien(state, 60, () => {
      state.element.classList.remove('is-repositioning');
      setAlienVisible(state, true);
      if (pointer) lookAt(pointer.x, pointer.y);
      queueAlien(state, 2400 + Math.random() * 2200, () => {
        setAlienVisible(state, false);
        queueAlien(state, 1700 + Math.random() * 3900, () => showAlien(state));
      });
    });
  }
  function syncAliens() {
    for (const state of aliens) {
      if (!enabled() || !moving()) {
        clearTimeout(state.timer);
        state.timer = 0;
        state.running = false;
        state.element.classList.remove('is-hit');
        if (enabled() && !state.side) placeAlien(state);
        state.element.classList.remove('is-repositioning');
        setAlienVisible(state, enabled());
      } else if (!state.running) {
        state.running = true;
        setAlienVisible(state, false);
        queueAlien(state, 450 + Math.random() * 2700, () => showAlien(state));
      }
    }
  }

  // ---------- Scoring ----------
  const labels = { ship: '星舰', star: '星星', alien: '外星人' };
  const colors = { ship: '#ffc27a', star: '#ffe38a', alien: '#a6f5c8' };
  function bump(element) {
    element?.animate?.([{ transform: 'scale(1.4)' }, { transform: 'scale(1)' }], { duration: 300, easing: 'cubic-bezier(.2,.8,.2,1)' });
  }
  function score(kind, x, y, now) {
    if (kind === 'ship') ships++; else if (kind === 'star') stars++; else invaders++;
    combo = now - lastHitAt <= COMBO_WINDOW ? combo + 1 : 1;
    lastHitAt = now;
    shipCount.textContent = String(ships);
    starCount.textContent = String(stars);
    if (alienCount) alienCount.textContent = String(invaders);
    shipScore.setAttribute('aria-label', `星舰命中 ${ships}`);
    starScore.setAttribute('aria-label', `星星命中 ${stars}`);
    alienScore?.setAttribute('aria-label', `外星人命中 ${invaders}`);
    shipScore.hidden = false; starScore.hidden = false;
    if (alienScore) alienScore.hidden = false;
    panel.classList.add('cosmos-scored');
    bump({ ship: shipCount, star: starCount, alien: alienCount }[kind]);
    announcement.textContent = `${labels[kind]}命中${combo > 1 ? `，${combo} 连击` : ''}。星舰 ${ships}，星星 ${stars}，外星人 ${invaders}。`;
    if (moving()) {
      if (bursts.length >= 12) bursts.shift();
      bursts.push({ kind: 'text', x, y, time: now, big: combo > 1, color: combo > 1 ? '#ffd0f0' : colors[kind],
        text: combo > 1 ? `连击 ×${combo}` : `+1 ${labels[kind]}` });
    }
  }
  function showSnowBurst(x, y) {
    const container = document.getElementById('cosmos-edge-aliens');
    if (!container) return;
    const activeBursts = container.querySelectorAll('.cosmos-snowburst');
    if (activeBursts.length >= 4) activeBursts[0].remove();
    const flakes = Array.from({ length: 20 }, (_, index) => {
      const angle = index * TAU / 20;
      const distance = 33 + index % 4 * 11;
      const px = Math.round(90 + Math.cos(angle) * distance);
      const py = Math.round(90 + Math.sin(angle) * distance);
      return `<g transform="translate(${px} ${py}) rotate(${index * 23})"><path d="M-5 0H5 M-2.5 -4.3L2.5 4.3 M2.5 -4.3L-2.5 4.3"/></g>`;
    }).join('');
    const burst = document.createElement('span');
    burst.className = 'cosmos-snowburst';
    burst.setAttribute('aria-hidden', 'true');
    burst.style.left = `${x}px`;
    burst.style.top = `${y}px`;
    burst.innerHTML = `<svg viewBox="0 0 180 180" xmlns="http://www.w3.org/2000/svg" focusable="false">
      <circle cx="90" cy="90" r="56" fill="none" stroke="#80dcff" stroke-width="2" opacity=".8"/>
      <circle cx="90" cy="90" r="11" fill="#defaff" opacity=".9"/>
      <g fill="none" stroke="#a4ecff" stroke-width="1.7" stroke-linecap="round">${flakes}</g>
    </svg>`;
    burst.addEventListener('animationend', () => burst.remove(), { once: true });
    container.append(burst);
  }
  function hit(object, now) {
    const ship = !['meteor', 'star'].includes(object.kind);
    const { x, y } = object;
    if (moving()) {
      if (bursts.length >= 12) bursts.shift();
      bursts.push({ x, y, time: now - 120, phase: object.phase, kind: ship ? 'ship' : 'star' });
      object.hiddenUntil = now + 650;
    }
    score(ship ? 'ship' : 'star', x, y, now);
    // The next pass starts from the edge with a new lane.
    if (object.kind !== 'star') { object.progress = 0; object.pass = null; object.trail.length = 0; object.x = -200; }
    object.age += 3.1;
    if (aimed === object) aimed = null;
    window.expenseDesktopEffects?.repaint();
  }
  function hitAlien(state, now) {
    if (!enabled() || !state.element.classList.contains('is-peeking')) return;
    const rect = state.visibleRect || state.element.getBoundingClientRect();
    const right = rect.right ?? rect.left + rect.width, bottom = rect.bottom ?? rect.top + rect.height;
    // Keep the snowburst inside the visible viewport while it still touches the visitor.
    const x = clamp((rect.left + right) / 2, 48, innerWidth - 48);
    const y = clamp((rect.top + bottom) / 2, 48, innerHeight - 48);
    score('alien', x, y, now);
    if (moving()) {
      showSnowBurst(x, y);
      state.element.classList.add('is-hit');
      setAlienVisible(state, false);
      queueAlien(state, 3300, () => showAlien(state));
    }
    window.expenseDesktopEffects?.repaint();
  }

  const hitRadius = object => object.kind === 'meteor' ? Math.max(24, object.size * 3) :
    object.kind === 'star' ? 30 : object.kind === 'rocket' ? 36 : object.kind === 'cruiser' ? 40 : 34;
  const occluded = (x, y) => blockedRects.some(rect => x >= rect.left && x <= rect.left + rect.width &&
    y >= rect.top && y <= rect.top + rect.height);
  function targetAt(x, y, now) {
    if (occluded(x, y)) return null;
    let selected = null, best = Infinity;
    for (const object of objects) {
      if (now < object.hiddenUntil) continue;
      const distance = Math.hypot(x - object.x, y - object.y);
      if (distance < hitRadius(object) && distance < best) { selected = object; best = distance; }
    }
    return selected;
  }
  function updateAim(now) {
    const next = pointer && now - pointer.at < 2500 ? targetAt(pointer.x, pointer.y, now) : null;
    if (next === aimed) return;
    aimed = next;
    root.classList.toggle('cosmos-aiming', Boolean(next));
  }

  function sync() {
    const active = enabled();
    root.classList.toggle('cosmos-active', active);
    surface.tabIndex = active ? 0 : -1;
    if (active) {
      surface.setAttribute('aria-label', '更新账单。按 Enter 或空格捕捉星空中的目标');
      surface.setAttribute('aria-keyshortcuts', 'Enter Space');
      addAliens();
      syncAliens();
      updateOcclusion();
    } else {
      surface.removeAttribute('aria-label');
      surface.removeAttribute('aria-keyshortcuts');
      lastPaint = 0;
      bursts.length = 0;
      aimed = null;
      root.classList.remove('cosmos-aiming');
      syncAliens();
      document.querySelectorAll('.cosmos-snowburst').forEach(burst => burst.remove());
    }
    window.expenseDesktopEffects?.repaint();
  }

  const controls = 'button, a, input, select, textarea, summary, dialog, .topbar, .cosmos-score, [role="button"]';
  document.addEventListener('pointerdown', event => {
    if (!enabled() || event.button !== 0 || event.target?.closest?.(controls)) return;
    const now = performance.now();
    const target = targetAt(event.clientX, event.clientY, now);
    if (target) hit(target, now);
    else if (moving() && !occluded(event.clientX, event.clientY) && !event.target?.closest?.('.surface, .update-notice')) {
      if (bursts.length >= 12) bursts.shift();
      bursts.push({ kind: 'miss', x: event.clientX, y: event.clientY, time: now });
    }
  }, { passive: true });
  document.addEventListener('pointermove', event => {
    if (!enabled() || event.pointerType === 'touch') return;
    pointer = { x: event.clientX, y: event.clientY, at: performance.now() };
    if (moving()) lookAt(pointer.x, pointer.y);
  }, { passive: true });
  surface.addEventListener('keydown', event => {
    if (!enabled() || event.target !== surface || !['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    const now = performance.now();
    const ready = objects.filter(object => now >= object.hiddenUntil);
    const onScreen = ready.filter(object => object.x >= 0 && object.x <= innerWidth && object.y >= 0 && object.y <= innerHeight);
    const target = (onScreen.length ? onScreen : ready)
      .sort((a, b) => Math.hypot(a.x - innerWidth / 2, a.y - innerHeight / 2) -
        Math.hypot(b.x - innerWidth / 2, b.y - innerHeight / 2))[0];
    if (target) hit(target, now);
  });
  window.expenseDesktopCosmosWallpaper = {
    paint, wantsFrame: enabled, setEnabled(value) { gameEnabled = value === true; sync(); },
    snapshot: () => ({
      objects: objects.map(({ kind, x, y, size, progress }) => ({ kind, x, y, size, progress })),
      score: { ships, stars, aliens: invaders }, combo,
      aliens: aliens.map(state => ({ side: state.side, peeking: state.element.classList.contains('is-peeking'), rect: state.visibleRect }))
    })
  };
  new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ['data-desktop-visual-mode', 'data-motion'] });
  new MutationObserver(sync).observe(panel, { attributes: true, attributeFilter: ['class'] });
  document.addEventListener('visibilitychange', sync);
  addEventListener('scroll', scheduleOcclusion, { passive: true });
  addEventListener('resize', () => { scheduleOcclusion(); objects.forEach(object => { object.pass = null; }); }, { passive: true });
  new ResizeObserver(scheduleOcclusion).observe(surface);
  reduced.addEventListener('change', sync);
  contrast.addEventListener('change', sync);
  sync();
})();
