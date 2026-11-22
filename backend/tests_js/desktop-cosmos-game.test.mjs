import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync('app/static/desktop-cosmos-wallpaper.js', 'utf8');

const WIDTH = 1400, HEIGHT = 900;
const CARD = { left: 270, top: 170, width: 860, height: 380 };
const TITLE = { left: 60, top: 36, width: 60, height: 36 };
const BUTTONS = { left: 1060, top: 34, width: 280, height: 40 };

class Element {
  constructor(tag = 'div', rect = null) {
    this.tag = tag; this.rect = rect; this.children = []; this.listeners = {}; this.attributes = {};
    this.dataset = {}; this.hidden = false; this.textContent = ''; this.innerHTML = ''; this.tabIndex = -1;
    this.classes = new Set();
    const element = this;
    this.classList = {
      add: (...names) => names.forEach(name => element.classes.add(name)),
      remove: (...names) => names.forEach(name => element.classes.delete(name)),
      contains: name => element.classes.has(name),
      toggle: (name, force) => { const on = force ?? !element.classes.has(name); on ? element.classes.add(name) : element.classes.delete(name); return on; }
    };
    this.styles = {};
    this.style = { setProperty: (name, value) => { element.styles[name] = value; } };
  }
  get className() { return [...this.classes].join(' '); }
  set className(value) { this.classes = new Set(String(value).split(/\s+/).filter(Boolean)); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  dispatch(type, event) { for (const handler of this.listeners[type] || []) handler({ target: this, preventDefault() {}, stopPropagation() {}, ...event }); }
  append(child) { this.children.push(child); child.parent = this; }
  remove() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest() { return null; }
  focus() {} blur() {} animate() {}
  getBoundingClientRect() {
    const rect = this.rect || { left: 0, top: 0, width: 0, height: 0 };
    return { ...rect, right: rect.left + rect.width, bottom: rect.top + rect.height };
  }
}

function game({ appMotion = 'full' } = {}) {
  let now = 0, timers = [];
  const root = new Element('html');
  root.dataset = { desktopVisualMode: 'particle', motion: appMotion };
  const surface = new Element('section', CARD);
  const panel = new Element('section');
  panel.classList.add('active');
  panel.querySelector = selector => selector === '.update-surface' ? surface : null;
  panel.querySelectorAll = selector => selector === '.surface' ? [surface] : [];
  const ids = Object.fromEntries(['cosmosShipScore', 'cosmosStarScore', 'cosmosAlienScore', 'cosmosShipCount', 'cosmosStarCount',
    'cosmosAlienCount', 'cosmosAnnouncement'].map(id => [id, Object.assign(new Element('output'), { hidden: true })]));
  const body = new Element('body');
  const topbarParts = [new Element('h1', TITLE), new Element('button', BUTTONS)];
  const documentListeners = {};
  const document = {
    documentElement: root, body, hidden: false, activeElement: null,
    getElementById: id => ids[id] || body.children.find(child => child.id === id) || null,
    querySelector: selector => selector === '[data-panel="operations"]' ? panel : null,
    querySelectorAll: selector => selector.startsWith('.topbar') ? topbarParts : [],
    createElement: tag => new Element(tag),
    addEventListener: (type, handler) => { (documentListeners[type] ||= []).push(handler); }
  };
  const window = { __expenseDesktopGameMode: true, __expenseDesktopHost: true };
  vm.runInNewContext(source, {
    window, document, innerWidth: WIDTH, innerHeight: HEIGHT,
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    performance: { now: () => now },
    setTimeout: (callback, delay) => { timers.push({ at: now + delay, callback }); return timers.length; },
    clearTimeout: () => {}, requestAnimationFrame: () => 1, addEventListener() {},
    MutationObserver: class { observe() {} }, ResizeObserver: class { observe() {} }
  });
  const api = window.expenseDesktopCosmosWallpaper;
  const context = new Proxy({}, { get: (target, name) => name in target ? target[name] :
    name.startsWith('create') ? () => ({ addColorStop() {} }) : () => {} });
  const advance = ms => {
    const end = now + ms;
    while (now < end) {
      now = Math.min(end, now + 33);
      const due = timers.filter(timer => timer.at <= now);
      timers = timers.filter(timer => timer.at > now);
      due.forEach(timer => timer.callback());
      api.paint(context, WIDTH, HEIGHT, now, true);
    }
  };
  const press = key => surface.dispatch('keydown', { key });
  const aliens = () => body.children.find(child => child.id === 'cosmos-edge-aliens')?.children ?? [];
  const fire = (type, event) => (documentListeners[type] || []).forEach(handler => handler({ target: body, ...event }));
  return { api, advance, press, aliens, ids, fire, time: () => now };
}

// Records every pass of one kind as a list of on-screen positions.
function passes(kind, seconds = 60) {
  const { api, advance } = game();
  const result = [];
  let current = null, lastProgress = -1;
  for (let frame = 0; frame < seconds * 30; frame++) {
    advance(33);
    for (const object of api.snapshot().objects.filter(item => item.kind === kind)) {
      if (object.progress < lastProgress || !current) { current = []; result.push(current); }
      lastProgress = object.progress;
      if (object.x > 0 && object.x < WIDTH && object.y > 0 && object.y < HEIGHT) current.push(object);
      break;
    }
  }
  // The first pass starts mid-flight and the last is cut off by the end of the run.
  return result.slice(1, -1).filter(pass => pass.length > 20);
}
const span = (points, axis) => Math.max(...points.map(point => point[axis])) - Math.min(...points.map(point => point[axis]));

test('rockets climb vertically with only a small sideways sway, in the open columns beside the card', () => {
  const all = passes('rocket', 80);
  assert.ok(all.length >= 3, 'several rocket passes');
  for (const pass of all) {
    assert.ok(pass[0].y - pass.at(-1).y > HEIGHT * .7, 'climbs most of the window');
    assert.ok(span(pass, 'x') < 70, `sway stays small (${span(pass, 'x').toFixed(1)} px)`);
    assert.ok(pass.every(point => point.x < CARD.left || point.x > CARD.left + CARD.width), 'flies where it can be seen');
  }
  assert.ok(new Set(all.map(pass => Math.round(pass[0].x / 20))).size > 1, 'each pass picks a new column');
});

test('saucers cross side to side with a vertical bob and alternate direction', () => {
  const all = passes('ufo', 110);
  assert.ok(all.length >= 3);
  const directions = all.map(pass => Math.sign(pass.at(-1).x - pass[0].x));
  for (const pass of all) {
    assert.ok(Math.abs(pass.at(-1).x - pass[0].x) > WIDTH * .7, 'crosses the window');
    const bob = span(pass, 'y');
    assert.ok(bob > 10 && bob < 120, `vertical bob (${bob.toFixed(1)} px)`);
  }
  assert.ok(directions.includes(1) && directions.includes(-1), 'both directions');
});

test('meteors slide down to the left at a steady slant with a slight wobble', () => {
  const all = passes('meteor', 40);
  assert.ok(all.length >= 4);
  for (const pass of all) {
    const dx = pass.at(-1).x - pass[0].x, dy = pass.at(-1).y - pass[0].y;
    assert.ok(dx < 0 && dy > 0, 'travels to the lower left');
    const angle = Math.atan2(dy, -dx) * 180 / Math.PI;
    assert.ok(angle > 24 && angle < 48, `slant ${angle.toFixed(1)} degrees`);
  }
});

test('ships fly horizontally: the fighter to the right, the cruiser to the left', () => {
  for (const [kind, sign] of [['fighter', 1], ['cruiser', -1]]) {
    for (const pass of passes(kind, 60)) {
      assert.equal(Math.sign(pass.at(-1).x - pass[0].x), sign, kind);
      assert.ok(span(pass, 'y') < 40, `${kind} keeps its lane`);
    }
  }
});

test('aliens peek in from the four window edges, clear of the card, the title and the buttons', () => {
  const { api, advance } = game();
  const seen = new Set();
  const clear = (a, b) => a.right <= b.left || a.left >= b.left + b.width || a.bottom <= b.top || a.top >= b.top + b.height;
  for (let step = 0; step < 120; step++) {
    advance(500);
    for (const alien of api.snapshot().aliens.filter(alien => alien.peeking)) {
      seen.add(alien.side);
      const { rect, side } = alien;
      assert.ok(['top', 'right', 'bottom', 'left'].includes(side));
      assert.ok(side === 'top' ? rect.top === 0 : side === 'bottom' ? rect.bottom === HEIGHT : side === 'left' ? rect.left === 0 : rect.right === WIDTH, 'touches its edge');
      for (const blocked of [CARD, TITLE, BUTTONS]) assert.ok(clear(rect, blocked), `${side} alien stays clear`);
    }
  }
  assert.deepEqual([...seen].sort(), ['bottom', 'left', 'right', 'top']);
});

test('hits score per kind, chain into combos and count aliens', () => {
  const { api, advance, press, aliens, ids } = game();
  advance(1000);
  press('Enter'); advance(500); press('Enter');
  let state = api.snapshot();
  assert.equal(state.score.ships + state.score.stars, 2);
  assert.equal(state.combo, 2, 'second hit within the window is a combo');
  assert.match(ids.cosmosAnnouncement.textContent, /2 连击/);
  advance(3000); press(' ');
  assert.equal(api.snapshot().combo, 1, 'combo resets after a pause');
  let target;
  for (let step = 0; step < 40 && !target; step++) { advance(250); target = aliens().find(alien => alien.classList.contains('is-peeking')); }
  assert.ok(target, 'an alien peeks');
  target.dispatch('pointerdown', { button: 0 });
  state = api.snapshot();
  assert.equal(state.score.aliens, 1);
  assert.equal(ids.cosmosAlienCount.textContent, '1');
  assert.equal(ids.cosmosAlienScore.hidden, false);
  assert.ok(target.classList.contains('is-hit') && !target.classList.contains('is-peeking'), 'hit alien ducks away');
});

test('aliens follow the pointer with their eyes in their own rotated frame', () => {
  const { api, advance, aliens, fire } = game();
  let alien;
  for (let step = 0; step < 80 && !alien; step++) { advance(250); alien = aliens().find(item => item.classList.contains('is-peeking') && item.classList.contains('cosmos-alien--left')); }
  assert.ok(alien, 'a left-edge alien peeks');
  assert.equal(alien.styles['--alien-turn'], '90deg');
  const rect = api.snapshot().aliens.find(item => item.side === 'left' && item.peeking).rect;
  // Straight to the right of a sticker turned 90 degrees is "up" in the sticker's own frame.
  fire('pointermove', { clientX: 700, clientY: (rect.top + rect.bottom) / 2, pointerType: 'mouse' });
  assert.ok(Math.abs(parseFloat(alien.styles['--look-x'])) < .05, alien.styles['--look-x']);
  assert.ok(parseFloat(alien.styles['--look-y']) < -3, alien.styles['--look-y']);
});

test('with the in-app reduced-motion switch the aliens sit still and nothing respawns', () => {
  const { api, advance, aliens } = game({ appMotion: 'reduced' });
  advance(6000);
  const placed = api.snapshot().aliens;
  assert.ok(placed.every(alien => alien.peeking && alien.side), 'aliens shown in place');
  assert.ok(aliens().every(alien => alien.dataset.spawn === '1'), 'no repeated placement');
});
