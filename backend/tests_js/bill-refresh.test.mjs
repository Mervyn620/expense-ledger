import test from 'node:test';import assert from 'node:assert/strict';import vm from 'node:vm';import fs from 'node:fs';
function fixture(){
 class Element {constructor(){this.events={};this.disabled=false;this.open=false;this.focused=false;} addEventListener(type,fn){this.events[type]=fn;} focus(){this.focused=true;} showModal(){this.open=true;} close(){this.open=false;this.events.close?.({});} async fire(type,extra={}){return this.events[type]?.({preventDefault(){},...extra});}}
 const inputs=['walmart_ca','uber_eats','fantuan','amazon_ca','the_ordinary_ca','tnt_ca'].map(value=>Object.assign(new Element(),{value,checked:false}));
 const elements=Object.fromEntries(['billRefreshButton','billRefreshDialog','billRefreshForm','billRefreshConfirm'].map(id=>[id,new Element()]));const cancel=new Element();
 elements.billRefreshForm.querySelectorAll=selector=>selector.includes('input')?inputs:[cancel];
 let busy=false;const calls=[];const window={document:{getElementById:id=>elements[id]}};
 vm.runInNewContext(fs.readFileSync('app/static/bill-refresh.js','utf8'),{window});
 const controller=window.LedgerBillRefresh.bind({onConfirm:async platforms=>{calls.push([...platforms]);},isBusy:()=>busy});
 return {controller,inputs,elements,cancel,calls,window,busy:value=>{busy=value;},selected:()=>inputs.filter(i=>i.checked).map(i=>i.value)};
}
test('initial selection is Walmart/Fantuan and temporary choices reset on reopen',()=>{
 const f=fixture();f.controller.open();assert.deepEqual(f.selected(),['walmart_ca','fantuan']);f.inputs[5].checked=true;f.inputs[0].checked=false;f.controller.close();f.controller.open();assert.deepEqual(f.selected(),['walmart_ca','fantuan']);assert.equal(f.calls.length,0);
});
test('saved native defaults are read anew on each open, including empty and extras',()=>{
 const f=fixture();f.window.__expenseDesktopDefaultBillPlatforms=['uber_eats','tnt_ca'];f.controller.open();assert.deepEqual(f.selected(),['uber_eats','tnt_ca']);f.controller.close();f.window.__expenseDesktopDefaultBillPlatforms=[];f.controller.open();assert.deepEqual(f.selected(),[]);assert.equal(f.elements.billRefreshConfirm.disabled,true);
});
test('confirm starts only the selected platforms; cancel and busy states do not run',async()=>{
 const f=fixture();f.controller.open();f.inputs[5].checked=true;await f.elements.billRefreshForm.fire('submit');assert.deepEqual(f.calls,[['walmart_ca','fantuan','tnt_ca']]);assert.equal(f.elements.billRefreshDialog.open,false);
 f.controller.open();await f.cancel.fire('click');assert.equal(f.calls.length,1);f.busy(true);f.controller.open();assert.equal(f.elements.billRefreshDialog.open,false);
});
test('empty selection never falls through to the backend default platform list',async()=>{
 const f=fixture();f.controller.open();f.inputs.forEach(i=>{i.checked=false;});await f.elements.billRefreshForm.fire('submit');assert.equal(f.calls.length,0);assert.equal(f.elements.billRefreshConfirm.disabled,true);
});
test('an in-flight confirmation cannot enqueue a second job',async()=>{
 const f=fixture();let resolve;let count=0;
 const waiting=new Promise(r=>{resolve=r;});
 // Bind in a fresh fixture with a delayed transport.
 const doc={getElementById:id=>f.elements[id]};f.window.LedgerBillRefresh.bind({document:doc,onConfirm:async()=>{count++;await waiting;}});
 await f.elements.billRefreshButton.fire('click');const first=f.elements.billRefreshForm.fire('submit');await f.elements.billRefreshForm.fire('submit');assert.equal(count,1);resolve();await first;
});
