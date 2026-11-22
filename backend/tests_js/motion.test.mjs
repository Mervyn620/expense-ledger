import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../app/static/motion.js', import.meta.url), 'utf8');
function element() {
  return { isConnected: true, children: [], attributes: {}, textContent: '', getClientRects: () => [1],
    getAttribute(key) { return this.attributes[key] ?? null; },
    setAttribute(key, value) { this.attributes[key] = value; },
    replaceChildren(...nodes) { this.children = nodes; nodes.forEach(node => { node.parent = this; }); },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); },
    animate(keyframes, timing) {
      let finish;
      return { keyframes, timing, finished: new Promise(resolve => { finish = resolve; }), cancel() { finish(); } };
    },
  };
}
function harness() {
  let now = 0, id = 0, observer;
  const pending = new Map(), listeners = {};
  const root = { dataset: {} };
  const document = { documentElement: root, hidden: false, createElement: element, addEventListener: (name, fn) => { listeners[name] = fn; } };
  const window = {};
  vm.runInNewContext(source, {
    document, window, matchMedia: () => ({ matches: false, addEventListener() {} }),
    MutationObserver: class { constructor(fn) { observer = fn; } observe() {} },
    performance: { now: () => now },
    requestAnimationFrame: fn => { pending.set(++id, fn); return id; },
    cancelAnimationFrame: key => pending.delete(key),
  });
  const tick = time => { now = time; const callbacks = [...pending.values()]; pending.clear(); callbacks.forEach(fn => fn(now)); };
  const bar = { value: 100, dataset: { barKey: 'a' }, attributes: {}, setAttribute(key, value) { this.attributes[key] = value; } };
  const container = { isConnected: true, getClientRects: () => [1], querySelectorAll: () => [bar] };
  return { motion: window.LedgerMotion, root, document, listeners, bar, container, pending, tick, reduce: () => { root.dataset.motion = 'reduced'; observer(); } };
}

test('progress exposes final data while painting, then settles exactly', () => {
  const h = harness();
  h.motion.bars(h.container, new Map());
  assert.equal(h.bar.value, 0);
  assert.equal(h.bar.attributes['aria-valuenow'], '100');
  h.tick(200); assert.ok(h.bar.value > 0 && h.bar.value < 100);
  h.tick(600); assert.equal(h.bar.value, 100); assert.equal(h.pending.size, 0);
});

test('unchanged metric updates keep the existing numeric layer', () => {
  const h = harness(), value = element();
  const card = { ...element(), querySelector: () => value };
  const container = { querySelectorAll: () => [card] };
  h.motion.metrics(container, ['$10.00'], true);
  const layer = value.children[0];
  h.motion.metrics(container, ['$10.00']);
  assert.equal(value.children[0], layer);
  assert.equal(value.children.length, 1);
});

test('rapid metric transitions cannot remove the latest value when old callbacks finish', async () => {
  const h = harness(), value = element();
  const container = { querySelectorAll: () => [{ ...element(), querySelector: () => value }] };
  h.motion.metrics(container, ['$10.00'], true);
  h.motion.metrics(container, ['$20.00']);
  h.motion.metrics(container, ['$30.00']);
  await Promise.resolve();
  assert.equal(value.getAttribute('aria-label'), '$30.00');
  assert.equal(value.children[0].textContent, '$30.00');
  assert.equal(value.children.length, 2);
  h.reduce(); await Promise.resolve();
  assert.equal(value.children.length, 1);
  assert.equal(value.children[0].textContent, '$30.00');
});
test('rapid update replaces the active interpolation without a stale callback', () => {
  const h = harness(); h.motion.bars(h.container, new Map()); h.tick(100);
  const previous = new Map([['a', h.bar.value]]);
  h.bar.value = 20; h.motion.bars(h.container, previous);
  assert.equal(h.pending.size, 1);
  h.tick(700); assert.equal(h.bar.value, 20); assert.equal(h.pending.size, 0);
});
test('enabling reduced motion mid-transition settles immediately and prevents future frames', () => {
  const h = harness(); h.motion.bars(h.container, new Map()); h.tick(100); h.reduce();
  assert.equal(h.bar.value, 100); assert.equal(h.pending.size, 0);
  h.bar.value = 40; h.motion.bars(h.container, new Map());
  assert.equal(h.bar.value, 40); assert.equal(h.pending.size, 0);
});
test('hidden document and detached panels release animation frames', () => {
  for (const detach of [false, true]) {
    const h = harness(); h.motion.bars(h.container, new Map());
    if (detach) { h.container.isConnected = false; h.tick(100); }
    else { h.document.hidden = true; h.listeners.visibilitychange(); }
    assert.equal(h.bar.value, 100); assert.equal(h.pending.size, 0);
  }
});
