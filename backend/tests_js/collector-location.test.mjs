import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function fixture() {
  const context=vm.createContext({Intl,URLSearchParams});
  vm.runInContext(fs.readFileSync('app/static/app.js','utf8').replace(/initialize\(\);\s*$/, ''),context);
  const run=code=>vm.runInContext(code,context);
  run(`
    const elements=new Map(), reads=[];
    function element(){return {dataset:{},classList:{toggle(){}},setAttribute(k,v){this[k]=v;}};}
    const document={querySelectorAll(){return [];},querySelector(){return null;},getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);}};
    const window={matchMedia(){return {matches:true};},scrollTo(){}};
    const paused={resolved_backend:'local',selected_mode:'local',backends:{local:{selectable:true},vps:{selectable:false,status:'unavailable',message:'VPS运行环境已停用'}}};
    api=async path=>{reads.push(path);return paused;};
    loadJobs=async()=>{};showToast=()=>{};
  `);
  return {run};
}
test('opening updates loads the collection location where its button is shown',async()=>{
  const {run}=fixture();run("switchView('operations')");await run('Promise.resolve()');
  assert.equal(run("reads.includes('/api/collector-backends')"),true);
  assert.equal(run("document.getElementById('backendToggleLabel').textContent"),'本地');
});
test('paused VPS keeps a truthful local label without offering an unavailable switch',()=>{
  const {run}=fixture();run('renderCollectorBackends(paused)');
  assert.equal(run("document.getElementById('backendToggleButton').disabled"),true);
  assert.match(run("document.getElementById('backendToggleButton').title"),/本地运行.*暂停维护/);
  assert.equal(run("document.getElementById('backendToggleButton')['aria-label']"),'订单采集当前使用本地运行环境');
});
test('unavailable VPS selection still offers recovery to the ready local environment',()=>{
  const {run}=fixture();run("renderCollectorBackends({...paused,resolved_backend:null,selected_mode:'vps'})");
  assert.equal(run("document.getElementById('backendToggleButton').dataset.targetBackend"),'local');
  assert.equal(run("document.getElementById('backendToggleButton').disabled"),false);
  run("state.activeJobId='running';renderCollectorBackends({...paused,resolved_backend:null,selected_mode:'vps'})");
  assert.equal(run("document.getElementById('backendToggleButton').disabled"),true);
});
