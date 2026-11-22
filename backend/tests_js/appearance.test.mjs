import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync('app/static/appearance.js', 'utf8');
function boot(saved, blocked = false, host = {}) {
  const root = { dataset: {}, classList: {add() {}} };
  const window = {...host};
  vm.runInNewContext(source, {
    window,
    document: { documentElement: root, addEventListener() {} },
    localStorage: { getItem() { if (blocked) throw new Error('Storage denied'); return saved; } },
  });
  return { data: root.dataset, normalize: value => JSON.parse(JSON.stringify(window.LedgerAppearance.normalize(value))) };
}

test('missing, damaged and denied storage cannot prevent initial page rendering', () => {
  const expected = { wallpaper: 'cloud', theme: 'cloud', transparency: 'glass', motion: 'full' };
  for (const saved of [null, '', '{broken', 'null', '42', '[]']) assert.deepEqual(boot(saved).data, expected);
  assert.deepEqual(boot(null, true).data, expected);
});

test('wallpaper follows its theme unless an independent color was saved', () => {
  for (const wallpaper of ['cloud', 'indigo', 'jade', 'ink']) {
    assert.equal(boot(JSON.stringify({ wallpaper, color: 'auto' })).data.theme, wallpaper);
    assert.equal(boot(JSON.stringify({ wallpaper, color: 'ink' })).data.theme, 'ink');
  }
  assert.deepEqual(boot(JSON.stringify({ wallpaper: 'none', color: 'auto' })).data,
    { wallpaper: 'none', theme: 'cloud', transparency: 'glass', motion: 'full' });
});

test('saved accessibility preferences apply before the page body is rendered', () => {
  assert.deepEqual(boot(JSON.stringify({ wallpaper: 'jade', color: 'indigo', solid: true, reducedMotion: true })).data,
    { wallpaper: 'jade', theme: 'indigo', transparency: 'solid', motion: 'reduced' });
});

test('legacy wallpaper and independent color migrate without resetting accessibility preferences', () => {
  for (const [old, replacement] of Object.entries({ alpine: 'cloud', snow: 'indigo', lake: 'jade', coast: 'ink' })) {
    assert.deepEqual(boot(JSON.stringify({ wallpaper: old, color: 'auto', solid: true, reducedMotion: true })).data,
      { wallpaper: replacement, theme: replacement, transparency: 'solid', motion: 'reduced' });
    assert.equal(boot(JSON.stringify({ wallpaper: 'none', color: old })).data.theme, replacement);
  }
});

test('untrusted preference values are constrained to the built-in choices', () => {
  const { normalize } = boot(null);
  assert.deepEqual(normalize({ wallpaper: 'https://example.invalid/track', color: '__proto__', solid: 'true', reducedMotion: 1 }),
    { wallpaper: 'cloud', color: 'auto', solid: false, reducedMotion: false });
  assert.deepEqual(normalize({ wallpaper: 'none', color: 'jade', solid: true, reducedMotion: false, unexpected: 'ignored' }),
    { wallpaper: 'none', color: 'jade', solid: true, reducedMotion: false });
});

function nativeHarness({saved = null, capability = true, bridge = 'available', blocked = false, snapshot = null} = {}) {
  const clone = value => JSON.parse(JSON.stringify(value));
  const messages = [], writes = [], documentEvents = new Map(), windowEvents = new Map();
  const root = {dataset: {}};
  const control = (dataset = {}) => ({dataset, events:new Map(), attributes:{}, checked:false, textContent:'',
    addEventListener(event, listener) {this.events.set(event, listener)},
    setAttribute(name, value) {this.attributes[name] = value}});
  const wallpapers = ['cloud', 'ink', 'jade', 'indigo', 'none'].map(wallpaperChoice => control({wallpaperChoice}));
  const colors = ['auto', 'cloud', 'ink', 'jade', 'indigo'].map(colorChoice => control({colorChoice}));
  const controls = new Map(['#appearanceSaveStatus', '#solidSurfaces', '#reduceAppearanceMotion', '.appearance-close', '.appearance-reset'].map(key => [key, control()]));
  const dialog = {
    ...control(),
    querySelector(selector) {return controls.get(selector)},
    querySelectorAll(selector) {return selector === '[data-wallpaper-choice]' ? wallpapers : colors},
  };
  const window = {addEventListener(event, listener) {windowEvents.set(event, listener)}};
  if (capability !== null) window.__expenseDesktopNativeAppearance = capability;
  window.__expenseDesktopAppearanceSnapshot = snapshot;
  if (bridge !== 'absent') window.chrome = {webview: {postMessage(message) {
    if (bridge === 'throws') throw new Error('Host unavailable');
    messages.push(clone(message));
  }}};
  if (bridge === 'nonfunction') window.chrome.webview.postMessage = true;
  if (bridge === 'no-post-message') delete window.chrome.webview.postMessage;
  vm.runInNewContext(source, {
    window,
    document: {
      documentElement: root, body: {append() {}},
      addEventListener(event, listener) {documentEvents.set(event, listener)},
      createElement(tagName) {assert.equal(tagName, 'dialog'); return dialog},
      querySelectorAll() {return []},
    },
    localStorage: {
      getItem() {if (blocked) throw new Error('Storage denied'); return saved},
      setItem(key, value) {if (blocked) throw new Error('Storage denied'); saved = value; writes.push({key, value:JSON.parse(value)})},
    },
  });
  return {
    window, messages, writes, root, dialog, controls,
    current() {return clone(window.LedgerAppearance.current())},
    ready() {documentEvents.get('DOMContentLoaded')()},
    choose(kind, value) {
      const choices = kind === 'wallpaper' ? wallpapers : colors;
      const selected = choices.find(item => item.dataset[`${kind}Choice`] === value);
      selected.events.get('click')();
    },
    toggle(selector, checked) {controls.get(selector).events.get('change')({target:{checked}})},
    reset() {controls.get('.appearance-reset').events.get('click')()},
    storage(value, key = 'expense-ledger-appearance-v1') {saved = value; windowEvents.get('storage')({key})},
  };
}

test('native appearance initializes with only the four normalized preferences', () => {
  const fixture = nativeHarness({saved:JSON.stringify({wallpaper:'snow', color:'jade', solid:true, reducedMotion:1, secret:'ignored'})});
  const expected = {wallpaper:'indigo', color:'jade', solid:true, reducedMotion:false};
  assert.deepEqual(fixture.messages, [{type:'appearance', appearance:expected}]);
  assert.deepEqual(fixture.current(), expected);
  assert.deepEqual(fixture.root.dataset, {wallpaper:'indigo', theme:'jade', transparency:'solid', motion:'full'});
  fixture.ready();
  assert.match(fixture.dialog.innerHTML, /自动保存到此应用/);
  assert.equal(fixture.messages.length, 1);
});

test('native appearance changes and reset notify the host while saving the same normalized preferences', () => {
  const fixture = nativeHarness();
  fixture.ready();
  fixture.choose('wallpaper', 'jade');
  fixture.choose('color', 'ink');
  fixture.toggle('#solidSurfaces', true);
  fixture.toggle('#reduceAppearanceMotion', true);
  const changed = {wallpaper:'jade', color:'ink', solid:true, reducedMotion:true};
  assert.deepEqual(fixture.messages.at(-1), {type:'appearance', appearance:changed});
  assert.deepEqual(fixture.current(), changed);
  assert.deepEqual(fixture.writes.at(-1).value, changed);
  assert.equal(fixture.messages.length, 5);
  assert.equal(fixture.controls.get('#appearanceSaveStatus').textContent, '外观已保存');
  fixture.reset();
  assert.deepEqual(fixture.messages.at(-1), {type:'appearance', appearance:{wallpaper:'cloud', color:'auto', solid:false, reducedMotion:false}});
  assert.equal(fixture.messages.length, 6);
});

test('appearance storage changes and clearing notify the host without writing storage again', () => {
  const fixture = nativeHarness();
  fixture.ready();
  fixture.storage(JSON.stringify({wallpaper:'none', color:'indigo', solid:true, reducedMotion:true, ignored:42}));
  assert.deepEqual(fixture.messages.at(-1), {type:'appearance', appearance:{wallpaper:'none', color:'indigo', solid:true, reducedMotion:true}});
  assert.equal(fixture.controls.get('#solidSurfaces').checked, true);
  fixture.storage(null, 'other-preference');
  assert.equal(fixture.messages.length, 2);
  assert.equal(fixture.current().wallpaper, 'none');
  fixture.storage(null, null);
  assert.deepEqual(fixture.messages.at(-1), {type:'appearance', appearance:{wallpaper:'cloud', color:'auto', solid:false, reducedMotion:false}});
  assert.equal(fixture.controls.get('#solidSurfaces').checked, false);
  assert.equal(fixture.messages.length, 3);
  assert.deepEqual(fixture.writes, []);
});

test('browsers and older hosts retain web appearance without native messages', () => {
  for (const capability of [null, false]) {
    const fixture = nativeHarness({capability});
    fixture.ready();
    fixture.choose('wallpaper', 'ink');
    fixture.toggle('#solidSurfaces', true);
    assert.deepEqual(fixture.messages, []);
    assert.equal(fixture.root.dataset.wallpaper, 'ink');
    assert.equal(fixture.root.dataset.transparency, 'solid');
    assert.equal(fixture.writes.length, 2);
    assert.match(fixture.dialog.innerHTML, /自动保存到此浏览器/);
  }
});

test('missing or failed native bridges do not interrupt appearance rendering, saving, or storage updates', () => {
  for (const bridge of ['absent', 'no-post-message', 'nonfunction', 'throws']) {
    const fixture = nativeHarness({bridge});
    fixture.ready();
    fixture.choose('color', 'jade');
    assert.equal(fixture.root.dataset.theme, 'jade');
    assert.equal(fixture.writes.length, 1);
    fixture.storage(JSON.stringify({wallpaper:'ink', solid:true}));
    assert.equal(fixture.root.dataset.theme, 'ink');
    assert.equal(fixture.root.dataset.transparency, 'solid');
    assert.deepEqual(fixture.messages, []);
  }
});

test('current appearance returns independent snapshots and cannot change the preferences', () => {
  const fixture = nativeHarness();
  const first = fixture.window.LedgerAppearance.current();
  first.wallpaper = 'ink';
  first.extra = 'must not leak';
  const second = fixture.window.LedgerAppearance.current();
  assert.notEqual(first, second);
  assert.deepEqual(fixture.current(), {wallpaper:'cloud', color:'auto', solid:false, reducedMotion:false});
  fixture.ready();
  fixture.choose('wallpaper', 'jade');
  assert.equal(second.wallpaper, 'cloud');
  assert.deepEqual(fixture.messages.at(-1).appearance, {wallpaper:'jade', color:'auto', solid:false, reducedMotion:false});
});

test('denied storage still applies and forwards native preferences without claiming a successful save', () => {
  const fixture = nativeHarness({blocked:true});
  fixture.ready();
  fixture.choose('wallpaper', 'ink');
  assert.equal(fixture.root.dataset.wallpaper, 'ink');
  assert.deepEqual(fixture.messages.at(-1).appearance, {wallpaper:'ink', color:'auto', solid:false, reducedMotion:false});
  assert.deepEqual(fixture.writes, []);
  assert.match(fixture.controls.get('#appearanceSaveStatus').textContent, /未允许保存/);
});

test('desktop tone reaches the first paint without changing the selected ledger palette', () => {
 const saved=JSON.stringify({wallpaper:'jade',color:'auto'});
 for(const tone of ['light','dark','unexpected']) {
  const state=boot(saved,false,{__expenseDesktopHost:true,__expenseDesktopVisualMode:'particle',__expenseDesktopUiTone:tone}).data;
  assert.equal(state.desktopTone,tone==='dark'?'dark':'light');
  assert.equal(state.theme,'jade');assert.equal(state.wallpaper,'jade');
  assert.equal(state.desktopVisualMode,'particle');assert.equal(state.transparency,'glass');
 }
});
test('native effects fallback is shared while a normal browser keeps its own appearance', () => {
 const desktop=boot(null,false,{__expenseDesktopHost:true,__expenseDesktopGlassEnabled:false}).data;
 assert.equal(desktop.desktopGlass,'opaque');assert.equal(desktop.desktopTone,'light');
 const normal=boot(null,false,{__expenseDesktopGlassEnabled:false,__expenseDesktopUiTone:'dark'}).data;
 assert.equal(normal.desktopGlass,undefined);assert.equal(normal.desktopTone,undefined);
});


test('automatic desktop brightness follows the static palette before first paint and ignores stale manual tone', () => {
  for (const [wallpaper, tone] of [['cloud','dark'],['jade','dark'],['ink','dark'],['indigo','dark']]) {
    const state=boot(JSON.stringify({wallpaper,color:'auto'}),false,{__expenseDesktopHost:true,__expenseDesktopAutomaticTone:true,__expenseDesktopUiTone:tone==='dark'?'light':'dark'}).data;
    assert.equal(state.desktopTone,tone); assert.equal(state.theme,wallpaper);
    const particle=boot(JSON.stringify({wallpaper,color:'auto'}),false,{__expenseDesktopHost:true,__expenseDesktopAutomaticTone:true,__expenseDesktopVisualMode:'particle'}).data;
    assert.equal(particle.desktopTone,'dark'); assert.equal(particle.wallpaper,wallpaper);
  }
});

test('mode changes restore automatic tone without changing wallpaper, palette or accessibility preferences', () => {
  const fixture=nativeHarness({saved:JSON.stringify({wallpaper:'jade',color:'auto',solid:true,reducedMotion:true})});
  fixture.window.__expenseDesktopHost=true; fixture.window.__expenseDesktopAutomaticTone=true;
  const before=fixture.current(), messages=fixture.messages.length;
  fixture.window.__expenseDesktopVisualMode='particle'; fixture.window.LedgerAppearance.syncDesktopMode();
  assert.equal(fixture.root.dataset.desktopTone,'dark');
  fixture.window.__expenseDesktopVisualMode='static'; fixture.window.LedgerAppearance.syncDesktopMode();
  assert.equal(fixture.root.dataset.desktopTone,'dark'); // 0.17: every palette is dark glass
  assert.deepEqual(fixture.current(),before); assert.equal(fixture.messages.length,messages);
});


test('a desktop with no usable origin storage restores native preferences before first paint', () => {
  const snapshot={wallpaper:'jade',color:'indigo',solid:true,reducedMotion:true};
  for(const blocked of [false,true]) {
    const state=boot(null,blocked,{__expenseDesktopHost:true,__expenseDesktopNativeAppearance:true,__expenseDesktopAutomaticTone:true,__expenseDesktopAppearanceSnapshot:snapshot}).data;
    assert.equal(state.wallpaper,'jade'); assert.equal(state.theme,'indigo');
    assert.equal(state.desktopTone,'dark'); assert.equal(state.transparency,'solid'); assert.equal(state.motion,'reduced');
  }
});

test('an existing native cache wins at startup while legacy and normal browser preferences are preserved', () => {
  const snapshot={wallpaper:'jade',color:'auto'};
  const host={__expenseDesktopNativeAppearance:true,__expenseDesktopAppearanceSnapshot:snapshot};
  assert.equal(boot(JSON.stringify({wallpaper:'ink',color:'auto'}),false,host).data.wallpaper,'jade');
  assert.equal(boot(JSON.stringify({wallpaper:'ink',color:'auto'}),false,{__expenseDesktopNativeAppearance:true,__expenseDesktopAppearanceSnapshot:null}).data.wallpaper,'ink');
  for(const capability of [undefined,false]) {
    assert.equal(boot(null,false,{__expenseDesktopNativeAppearance:capability,__expenseDesktopAppearanceSnapshot:snapshot}).data.wallpaper,'cloud');
  }
});


test('live storage changes still apply after restoring a cached initial desktop appearance', () => {
  const fixture=nativeHarness({saved:JSON.stringify({wallpaper:'ink'}),snapshot:{wallpaper:'jade',color:'auto'}});
  assert.equal(fixture.current().wallpaper,'jade'); fixture.ready();
  fixture.storage(JSON.stringify({wallpaper:'indigo',color:'auto',solid:true}));
  assert.equal(fixture.current().wallpaper,'indigo'); assert.equal(fixture.current().solid,true);
  assert.equal(fixture.messages.at(-1).appearance.wallpaper,'indigo');
});
