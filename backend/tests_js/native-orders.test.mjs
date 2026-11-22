import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function harness({native = true, host = true, fallback = false} = {}) {
  const context = vm.createContext({Intl, URLSearchParams, console});
  vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8').replace(/initialize\(\);\s*$/, ''), context);
  const run = code => vm.runInContext(code, context);
  run(`
    const messages = [], reads = [], elements = new Map(); let orderLoads = 0;
    const element = () => ({attributes: {'aria-hidden': 'true'}, classList:{add(){}, remove(){}, toggle(){}}, dataset:{},
      setAttribute(k,v){this.attributes[k]=v}, getAttribute(k){return this.attributes[k]}, addEventListener(){}, focus(){}, querySelectorAll(){return []}});
    const document = {body:element(), activeElement:null, querySelectorAll(){return []}, querySelector(){return null},
      getElementById(id){if(id==='refundForm')return null; if(!elements.has(id))elements.set(id,element()); return elements.get(id)}};
    const window = {__expenseDesktopHost:${host}, __expenseDesktopNativeOrders:${native}, __expenseDesktopOrdersFallback:${fallback},
      location:{search:${JSON.stringify(fallback ? '?nativeFallback=1&order=7' : '')}},
      chrome:{webview:{postMessage(message){messages.push(message)}}}, requestAnimationFrame(){}, matchMedia(){return {matches:true}}, scrollTo(){}};
    api = async path => { reads.push(path); if(path==='/api/health') return {status:'ok'};
      if(path==='/api/jobs')return {}; if(path==='/api/filters')return {};
      if(path.startsWith('/api/overview'))return {};
      return {order:{id:7,merchant:'Fixture',total:75.25,gross_total:100.25,refund_total:25,currency:'USD'},items:[],refunds:[],bank_matches:[],refund_candidates:[]}; };
    loadOrders = async () => {orderLoads++}; bindEvents=()=>{}; renderScotiaAction=()=>{};
    renderOverview=()=>{state.month='2026-09'}; populateFilters=()=>{}; renderJobs=()=>{};
    state.month='2026-09';
  `);
  return {run, json:code => JSON.parse(JSON.stringify(run(code)))};
}

test('native overview order links preserve month without reading/opening a web drawer', async () => {
  const {run,json} = harness(); await run('openOrder(42)');
  assert.deepEqual(json('messages'), [{type:'native-order',orderPk:42,month:'2026-09'}]);
  assert.deepEqual(json('reads'), []);
});
test('native view-all delegates once and retains the hidden overview state', () => {
  const {run,json} = harness(); run("switchView('orders')");
  assert.deepEqual(json('messages'), [{type:'native-orders',month:'2026-09'}]);
  assert.equal(run('state.view'),'overview'); assert.equal(run('orderLoads'),0);
});
for (const host of [false,true]) test(`normal browser / 0.2 host keeps web orders (host=${host})`, async () => {
  const {run,json} = harness({native:false,host}); run("switchView('orders')"); await run('openOrder(7)');
  assert.equal(run('state.view'),'orders'); assert.equal(run('orderLoads'),1);
  assert.deepEqual(json('reads'), ['/api/orders/7']);
  assert.equal(run("document.getElementById('detailDrawer').getAttribute('aria-hidden')"),'false');
  assert.equal(json('messages').some(x=>x.type.startsWith('native-')),false);
});
test('explicit refund fallback keeps the drawer and refresh after a refund in web UI', async () => {
  const {run,json} = harness({fallback:true}); await run('initialize()'); await run('openOrder(7)');
  assert.equal(run("document.getElementById('detailDrawer').getAttribute('aria-hidden')"),'false');
  assert.equal(json('messages').some(x=>x.type==='native-order'),false);
  run('closeDrawer()'); assert.equal(json('messages').at(-1).type,'order-fallback-closed');
});
test('leaving fallback clears the old drawer without a spurious return message', async () => {
  const {run,json} = harness({fallback:true}); await run('initialize()');
  run("window.expenseDesktopNavigate('overview', {ordersFallback:false})");
  assert.equal(run("document.getElementById('detailDrawer').getAttribute('aria-hidden')"),'true');
  assert.equal(json('messages').some(x=>x.type==='order-fallback-closed'),false);
  await run('openOrder(12)'); assert.equal(json('messages').at(-1).type,'native-order');
});

test('delayed refund completion after navigation updates data without returning to orders', async () => {
  const {run,json} = harness({fallback:true}); await run('initialize()');
  run('loadOverview = async () => {}; const mutationRequest = state.orderDetailRequest;');
  run("window.expenseDesktopNavigate('overview', {ordersFallback:false})");
  await run('finishOrderMutation(7, mutationRequest)');
  assert.equal(run('state.view'),'overview');
  assert.equal(json('messages').some(x=>x.type==='native-order'),false);
  assert.deepEqual(json('messages').at(-1), {type:'order-updated',orderPk:7});
});
test('refund completion in an open fallback refreshes the same drawer', async () => {
  const {run,json} = harness({fallback:true}); await run('initialize()');
  run('loadOverview = async () => {}; const mutationRequest = state.orderDetailRequest;');
  await run('finishOrderMutation(7, mutationRequest)');
  assert.equal(json('reads').filter(x=>x==='/api/orders/7').length,2);
  assert.equal(json('messages').some(x=>x.type==='native-order'),false);
});
test('late drawer response cannot overwrite a newer order', async () => {
  const {run} = harness({native:false});
  run(`let releaseFirst; const originalApi = api;
    api = path => path === '/api/orders/1' ? new Promise(resolve => {releaseFirst = resolve}) : originalApi(path);
    const firstRead = openOrder(1);`);
  await run('openOrder(2)');
  run("releaseFirst({order:{merchant:'Obsolete'},items:[]})"); await run('firstRead');
  assert.equal(run("document.getElementById('drawerMerchant').textContent"),'Fixture');
});
