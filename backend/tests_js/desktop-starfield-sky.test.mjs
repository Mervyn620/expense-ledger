import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync('app/static/desktop-starfield.js', 'utf8');

// Records every dot, fill colour and meteor stroke the starfield paints.
function sky(theme) {
  const calls = { arcs: [], fills: [], strokes: 0 };
  let fillStyle = '';
  const context = {
    clearRect() {}, beginPath() {}, fillRect() {}, save() {}, restore() {}, moveTo() {}, lineTo() {},
    stroke() { calls.strokes++; },
    arc(x, y, r) { calls.arcs.push({ x, y, r, color: fillStyle }); },
    fill() { calls.fills.push(fillStyle); },
    createLinearGradient() { return { addColorStop() {} }; },
    set fillStyle(value) { fillStyle = value; }, get fillStyle() { return fillStyle; },
  };
  const root = { dataset: { desktopVisualMode: 'particle', theme } };
  const window = { __expenseDesktopVisualMode: 'particle' };
  const document = { documentElement: root, hidden: false, readyState: 'complete', body: { append() {} }, getElementById() { return null; },
    createElement() { return { setAttribute() {}, getContext() { return context; } }; }, addEventListener() {} };
  vm.runInNewContext(source, { window, document, matchMedia: () => ({ matches: false, addEventListener() {} }), innerWidth: 1200, innerHeight: 800,
    performance: { now: () => 0 }, requestAnimationFrame() { return 1; }, cancelAnimationFrame() {}, addEventListener() {}, MutationObserver: class { observe() {} } });
  const frame = seconds => {
    calls.arcs.length = 0; calls.fills.length = 0; calls.strokes = 0;
    window.expenseDesktopEffects.setHostFrame({ width: 1200, height: 800, x: 0, y: 0, seconds, colors: ['#07101e', '#102642', '#080f23'] });
    return calls;
  };
  return { frame };
}

test('stars start on the wallpaper positions and then drift slowly', () => {
  const s = sky('cloud');
  const settled = s.frame(6.25).arcs.slice(0, 160).map(p => [p.x, p.y]);
  const later = s.frame(36.25).arcs.slice(0, 160).map(p => [p.x, p.y]);
  const moved = later.map(([x, y], i) => Math.hypot(x - settled[i][0], y - settled[i][1]));
  assert.ok(moved.some(d => d > 2), 'some stars drift');
  assert.ok(moved.every(d => d <= 2 * 10 * 1.3), 'drift stays within the small ellipse');
  // Nearer (larger) stars drift further than the faint background stars on average.
  const near = moved.filter((_, i) => i % 19 === 0), far = moved.filter((_, i) => i % 19 && i % 7);
  assert.ok(near.reduce((a, b) => a + b) / near.length > far.reduce((a, b) => a + b) / far.length);
});

test('star tones follow the palette', () => {
  const jade = sky('jade').frame(6.25).arcs.slice(0, 160).map(p => p.color);
  assert.ok(jade.some(c => c.startsWith('rgba(131,192,146,')), 'jade accent');
  assert.ok(jade.some(c => c.startsWith('rgba(232,244,236,')), 'jade main');
  const unknown = sky('missing').frame(6.25).arcs.slice(0, 160).map(p => p.color);
  assert.ok(unknown.some(c => c.startsWith('rgba(255,255,255,')), 'unknown theme falls back to the default sky');
});

test('the nearest stars get a soft halo after the dots', () => {
  const calls = sky('cloud').frame(6.25);
  assert.equal(calls.arcs.length, 160 + Math.ceil(160 / 19));
  assert.ok(calls.arcs.slice(160).every(p => p.r === 6.5));
});

test('shooting stars are occasional and never during the launch', () => {
  const s = sky('cloud');
  for (let t = 0; t <= 8.2; t += .25) assert.equal(s.frame(t).strokes, 0, `no meteor at ${t}s`);
  let active = 0, samples = 0;
  for (let t = 8.3; t < 8.3 + 11 * 20; t += .1) { samples++; if (s.frame(t).strokes) active++; }
  assert.ok(active > 0, 'meteors appear');
  assert.ok(active / samples < .15, 'meteors are brief and rare');
});
