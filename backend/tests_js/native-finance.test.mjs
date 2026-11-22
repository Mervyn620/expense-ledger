import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function harness({nativeFinance = true, nativeOrders = true, host = true, bridge = 'available'} = {}) {
  const context = vm.createContext({Intl, URLSearchParams, console});
  vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8').replace(/initialize\(\);\s*$/, ''), context);
  const run = code => vm.runInContext(code, context);
  run(`
    const messages = [], reads = [], domReads = [], elements = new Map();
    let cashRenders = 0, orderLoads = 0, scrolls = 0;
    const element = () => ({hidden:false, textContent:'before', dataset:{},
      classList:{toggle(){}, add(){}, remove(){}}});
    const document = {
      getElementById(id) {domReads.push(id); if(!elements.has(id))elements.set(id,element()); return elements.get(id)},
      querySelectorAll(selector) {domReads.push(selector); return []},
      querySelector(selector) {domReads.push(selector); return null},
    };
    const window = {__expenseDesktopHost:${host}, __expenseDesktopNativeOrders:${nativeOrders},
      chrome:{webview:{postMessage(message){messages.push(message)}}},
      matchMedia(){return {matches:true}}, scrollTo(){scrolls++}};
    api = async path => {reads.push(path); return {rows:[], total_count:0}};
    renderCashWithdrawals = () => {cashRenders++};
    loadOrders = async () => {orderLoads++};
    state.month = '2026-09'; state.orderOffset = 50;
    state.cash = {total:15}; state.cashRequest = 2;
  `);
  if (nativeFinance !== null) run(`window.__expenseDesktopNativeFinance = ${nativeFinance}`);
  if (bridge === 'no-chrome') run('delete window.chrome');
  if (bridge === 'no-webview') run('delete window.chrome.webview');
  if (bridge === 'no-post-message') run('delete window.chrome.webview.postMessage');
  return {run, json:code => JSON.parse(JSON.stringify(run(code)))};
}

for (const view of ['cash', 'sources']) {
  test(`native ${view} forwards the selected month and preserves hidden web state and DOM`, async () => {
    for (const month of ['2026-09', 'all', 'latest']) {
      const {run, json} = harness();
      run(`state.month = ${JSON.stringify(month)}`);
      const before = json('state');
      await run(`switchView('${view}')`);
      assert.deepEqual(json('messages'), [{type:'native-finance', view, month}]);
      assert.deepEqual(json('state'), before);
      assert.deepEqual(json('domReads'), []);
      assert.deepEqual(json('reads'), []);
      assert.equal(run('cashRenders'), 0);
      assert.equal(run('scrolls'), 0);
    }
  });

  for (const legacy of [
    {name:'browser', host:false, nativeOrders:false},
    {name:'0.2 host', host:true, nativeOrders:false},
    {name:'0.3 host', host:true, nativeOrders:true},
  ]) {
    test(`${legacy.name} retains the web ${view} view without finance capability`, async () => {
      const {run, json} = harness({...legacy, nativeFinance:null});
      await run(`switchView('${view}')`);
      assert.equal(run('state.view'), view);
      assert.equal(run('state.month'), '2026-09');
      assert.deepEqual(json('messages'), legacy.host ? [{type:'view', view}] : []);
      assert.deepEqual(json('reads'), view === 'cash' ? ['/api/cash-withdrawals?month=2026-09'] : []);
      assert.equal(run('cashRenders'), view === 'cash' ? 1 : 0);
      assert.equal(run('document.getElementById("pageTitle").textContent'), view === 'cash' ? '现金' : '付款');
      assert.equal(run('scrolls'), 1);
    });
  }

  test(`explicitly disabled finance capability retains web ${view}`, async () => {
    const {run, json} = harness({nativeFinance:false});
    await run(`switchView('${view}')`);
    assert.equal(run('state.view'), view);
    assert.deepEqual(json('messages'), [{type:'view', view}]);
    assert.deepEqual(json('reads'), view === 'cash' ? ['/api/cash-withdrawals?month=2026-09'] : []);
  });

  test(`finance capability without a usable WebView bridge retains web ${view}`, async () => {
    for (const bridge of ['no-chrome', 'no-webview', 'no-post-message']) {
      const {run, json} = harness({host:false, bridge});
      await run(`switchView('${view}')`);
      assert.equal(run('state.view'), view);
      assert.deepEqual(json('messages'), []);
      assert.deepEqual(json('reads'), view === 'cash' ? ['/api/cash-withdrawals?month=2026-09'] : []);
    }
  });
}

test('finance capability preserves native order navigation and the explicit refund fallback', async () => {
  const {run, json} = harness();
  run("switchView('orders')");
  assert.deepEqual(json('messages'), [{type:'native-orders', month:'2026-09'}]);
  assert.equal(run('state.view'), 'overview');
  assert.equal(run('orderLoads'), 0);
  run('window.__expenseDesktopOrdersFallback = true');
  await run("switchView('orders')");
  assert.equal(run('state.view'), 'orders');
  assert.equal(run('orderLoads'), 1);
  assert.deepEqual(json('messages').at(-1), {type:'view', view:'orders'});
});

test('invalid destinations remain inert when native finance is available', () => {
  const {run, json} = harness();
  run("switchView('cash-settings')");
  assert.equal(run('state.view'), 'overview');
  assert.deepEqual(json('messages'), []);
  assert.deepEqual(json('reads'), []);
  assert.deepEqual(json('domReads'), []);
});
