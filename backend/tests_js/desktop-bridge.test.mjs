import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync('app/static/desktop-bridge.js', 'utf8');
function harness(readyState = 'loading', operations = true) {
  const elements = [], classes = new Set(), listeners = [];
  const document = {
    readyState,
    documentElement: {classList: {add(value) { classes.add(value); }}},
    head: {append(element) { elements.push(element); }},
    getElementById(id) { return elements.find(element => element.id === id) ?? null; },
    querySelector(selector) { assert.equal(selector, '[data-panel="operations"]'); return operations ? {} : null; },
    createElement(tagName) {
      assert.notEqual(tagName.toLowerCase(), 'style', 'Keep CSP: no inline styles');
      return {tagName: tagName.toUpperCase()};
    },
    addEventListener(event, callback, options) {
      assert.equal(event, 'DOMContentLoaded'); assert.equal(options.once, true); listeners.push(callback);
    },
  };
  const window = new EventTarget();
  const context = vm.createContext({window, document});
  return {
    elements, classes, listeners, window, document,
    run() { vm.runInContext(source, context); },
    ready() { document.readyState = 'interactive'; for (const callback of listeners.splice(0)) callback(); },
  };
}
function assertInstalled(fixture, operations = true) {
  assert.equal(fixture.window.__expenseDesktopHost, true);
  assert.deepEqual([...fixture.classes], ['expense-native']);
  assert.deepEqual(fixture.elements.map(e => e.id), operations
    ? ['expense-desktop-starfield-script', 'expense-desktop-cosmos-script'] : ['expense-desktop-starfield-script']);
  for (const element of fixture.elements) {
    assert.equal(element.tagName, 'SCRIPT');
    assert.equal(new URL(element.src, 'http://127.0.0.1:8785').origin, 'http://127.0.0.1:8785');
    assert.equal(element.defer, true);
  }
}
test('desktop bridge waits for DOM readiness before installing local effects', () => {
  const fixture = harness(); fixture.run();
  assert.equal(fixture.window.__expenseDesktopHost, true);
  assert.deepEqual(fixture.elements, []); assert.equal(fixture.classes.size, 0);
  fixture.ready(); assertInstalled(fixture);
});
for (const state of ['interactive', 'complete']) {
  test(`desktop bridge installs when document is ${state}`, () => {
    const fixture = harness(state); fixture.run(); assertInstalled(fixture); assert.equal(fixture.listeners.length, 0);
  });
}
test('repeated bridge initialization installs each effect once', () => {
  const fixture = harness(); fixture.run(); fixture.run(); fixture.ready(); fixture.run(); fixture.run();
  assertInstalled(fixture); assert.equal(fixture.listeners.length, 0);
});
test('analysis and price pages do not load the update-page interaction script', () => {
  const fixture = harness('complete', false); fixture.run(); assertInstalled(fixture, false);
});
test('an existing starfield does not suppress a missing update-page interaction script', () => {
  const fixture = harness('complete');
  fixture.elements.push({tagName: 'SCRIPT', id: 'expense-desktop-starfield-script', src: '/static/desktop-starfield.js', defer: true});
  fixture.run(); fixture.run(); assertInstalled(fixture);
});
test('early sidebar CSS is host-gated and ordinary pages never execute the desktop bridge', () => {
  for (const page of ['index', 'analysis', 'price-watch']) {
    const html = fs.readFileSync(`app/static/${page}.html`, 'utf8');
    assert.match(html, /<link[^>]+href="\/static\/desktop-bridge\.css/);
    assert.doesNotMatch(html, /<script[^>]+src="[^"<>]*desktop-bridge\.js/);
    assert.doesNotMatch(html, /<html[^>]+class="[^"<>]*expense-native/);
  }
  const css = fs.readFileSync('app/static/desktop-bridge.css', 'utf8');
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]+)\}/g)];
  assert.deepEqual(rules.map(rule => rule[1].trim()), [
    'html.expense-native .sidebar', 'html.expense-native .app-shell', 'html.expense-native .workspace',
  ]);
  assert.match(rules[0][2], /display:\s*none\s*!important/);
  assert.match(rules[1][2], /grid-template-columns:\s*minmax\(0,\s*1fr\)\s*!important/);
});

test('new native layout waits for the document root before installing scripts', () => {
  const fixture = harness('loading');
  fixture.window.__expenseDesktopWallpaperLayout = true;
  const root = fixture.document.documentElement;
  root.dataset = {};
  fixture.document.documentElement = null;
  assert.doesNotThrow(() => fixture.run());
  assert.equal(fixture.window.__expenseDesktopHost, true);
  assert.equal(fixture.elements.length, 0);
  fixture.document.documentElement = root;
  fixture.ready(); fixture.run();
  assert.equal(root.dataset.desktopLayout, 'wallpaper');
  assert.equal(fixture.elements.filter(e => e.id === 'expense-desktop-layout-script').length, 1);
  assert.equal(fixture.elements.length, 3);
});
