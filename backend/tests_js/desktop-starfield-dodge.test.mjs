import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync('app/static/desktop-starfield.js', 'utf8');

// A desktop page (overview, update) with the given system and in-app motion settings.
function page({ host = true, systemReduced = false, appMotion = 'full' } = {}) {
  const arcs = [];
  const listeners = {};
  const context = {
    clearRect() {}, beginPath() {}, fillRect() {}, save() {}, restore() {}, moveTo() {}, lineTo() {}, stroke() {}, fill() {},
    arc(x, y, r) { arcs.push({ x, y, r }); },
    createLinearGradient() { return { addColorStop() {} }; },
  };
  const root = { dataset: { desktopVisualMode: 'particle', theme: 'indigo', motion: appMotion } };
  const window = { __expenseDesktopVisualMode: 'particle', __expenseDesktopHost: host };
  const document = { documentElement: root, hidden: false, readyState: 'complete', body: { append() {} }, getElementById() { return null; },
    createElement() { return { setAttribute() {}, getContext() { return context; } }; }, addEventListener() {} };
  vm.runInNewContext(source, { window, document, matchMedia: query => ({ matches: query.includes('reduced-motion') && systemReduced, addEventListener() {} }),
    innerWidth: 1200, innerHeight: 800, performance: { now: () => 0 }, requestAnimationFrame() { return 1; }, cancelAnimationFrame() {},
    addEventListener(type, handler) { listeners[type] = handler; }, MutationObserver: class { observe() {} } });
  const frame = seconds => {
    arcs.length = 0;
    window.expenseDesktopEffects.setHostFrame({ width: 1200, height: 800, x: 0, y: 0, seconds, colors: ['#07101e', '#102642', '#080f23'] });
    return arcs.filter(arc => arc.r < 3).map(arc => ({ ...arc }));
  };
  return { frame, listeners };
}

test('Windows animation setting no longer freezes the Web stars inside the desktop host', () => {
  const { frame } = page({ systemReduced: true });
  const settled = frame(6.25), later = frame(40);
  assert.ok(later.some((star, i) => Math.hypot(star.x - settled[i].x, star.y - settled[i].y) > .5), 'stars drift like the native pages');
});

test('the in-app reduced-motion switch still stops the Web stars', () => {
  const { frame } = page({ appMotion: 'reduced' });
  assert.deepEqual(frame(40), frame(6.25));
});

test('outside the desktop host the system reduced-motion preference still applies', () => {
  const { frame } = page({ host: false, systemReduced: true });
  assert.deepEqual(frame(40), frame(6.25));
});

test('stars move out of the pointer\'s way on Web pages', () => {
  const { frame, listeners } = page({ systemReduced: true });
  const before = frame(6.25);
  const star = before[0];
  listeners.pointermove({ clientX: star.x + 10, clientY: star.y, pointerType: 'mouse' });
  const after = frame(6.25);
  const distance = Math.hypot(after[0].x - (star.x + 10), after[0].y - star.y);
  assert.ok(distance > 110 && distance < 130, `star cleared the pointer (${distance.toFixed(1)} px)`);
  const far = before.findIndex(s => Math.hypot(s.x - star.x - 10, s.y - star.y) > 300);
  assert.ok(Math.abs(after[far].x - before[far].x) < .01, 'distant stars stay put');
  listeners.blur();
  assert.ok(Math.abs(frame(6.25)[0].x - star.x) < .01, 'stars settle back when the pointer leaves');
});
