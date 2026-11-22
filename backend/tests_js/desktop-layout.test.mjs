import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync('app/static/desktop-layout.js', 'utf8');
function fixture(enabled = true, pending = null) {
  const values = new Map(), listeners = [], root = {dataset: {}, style: {setProperty(key,value) { values.set(key,value); }}};
  const window = {__expenseDesktopWallpaperLayout: enabled, __expenseDesktopLayoutFrame: pending};
  const context = vm.createContext({window, innerWidth: 1112, document: {documentElement: root, readyState: 'complete', querySelector() { return null; }}, addEventListener(name, fn) {listeners.push({name,fn});}});
  return {window,root,values,listeners,context,run() {vm.runInContext(source,context);}};
}
const frame = {width:1200,height:800,x:88,y:0,webWidth:1112};
test('ordinary browsers and older native hosts never enable the new layout', () => {
  for (const enabled of [false,undefined]) {
    const f=fixture(enabled===undefined?null:enabled); f.run();
    assert.equal(f.window.expenseDesktopLayout,undefined); assert.deepEqual(f.root.dataset,{}); assert.equal(f.listeners.length,0);
  }
});
test('wallpaper covers the entire native window, offset by the native rail', () => {
  const f=fixture();f.run();assert.equal(f.window.expenseDesktopLayout.setHostFrame(frame),true);
  assert.equal(f.values.get('--host-wallpaper-width'),'1200px');assert.equal(f.values.get('--host-wallpaper-height'),'800px');
  assert.equal(f.values.get('--host-wallpaper-x'),'88px');assert.equal(f.root.dataset.desktopWallpaperFrame,'aligned');
});
test('WebView zoom preserves the same physical wallpaper crop', () => {
  const f=fixture();f.run();f.window.expenseDesktopLayout.setHostFrame(frame);
  f.context.innerWidth=556;f.listeners.find(l=>l.name==='resize').fn();
  assert.equal(f.values.get('--host-wallpaper-width'),'600px');assert.equal(f.values.get('--host-wallpaper-x'),'44px');
});
test('resized native frames replace the prior wallpaper geometry', () => {
  const f=fixture();f.run();f.window.expenseDesktopLayout.setHostFrame(frame);
  f.context.innerWidth=812;f.window.expenseDesktopLayout.setHostFrame({...frame,width:900,height:600,webWidth:812});
  assert.equal(f.values.get('--host-wallpaper-width'),'900px');assert.equal(f.values.get('--host-wallpaper-height'),'600px');
});
test('invalid geometry cannot overwrite the last usable crop', () => {
  const f=fixture();f.run();f.window.expenseDesktopLayout.setHostFrame(frame);
  for(const candidate of [null,{}, {...frame,width:NaN},{...frame,height:Infinity},{...frame,webWidth:0},{...frame,width:-1},{...frame,x:40000}])
    assert.equal(f.window.expenseDesktopLayout.setHostFrame(candidate),false);
  assert.equal(f.values.get('--host-wallpaper-width'),'1200px');
});
test('a pending frame and repeated initialization do not lose alignment or duplicate listeners', () => {
  const f=fixture(true,frame);f.run();const api=f.window.expenseDesktopLayout;f.run();
  assert.equal(f.window.expenseDesktopLayout,api);assert.deepEqual(f.listeners.map(l=>l.name).sort(),['keydown','resize']);assert.equal(f.values.get('--host-wallpaper-x'),'88px');
});

test('interface size: CSS zoom follows the host and the wallpaper frame divides it back out', () => {
  const f=fixture();f.window.__expenseDesktopZoom=1.15;f.run();
  assert.equal(f.root.style.zoom,'1.15');
  f.window.expenseDesktopLayout.setHostFrame(frame);
  assert.equal(f.values.get('--host-wallpaper-width'),`${frame.width/1.15}px`);
  f.window.__expenseDesktopZoom=1;f.window.expenseDesktopApplyZoom();
  assert.equal(f.root.style.zoom,'');assert.equal(f.values.get('--host-wallpaper-width'),`${frame.width}px`);
  f.window.__expenseDesktopZoom=9;f.window.expenseDesktopApplyZoom();
  assert.equal(f.root.style.zoom,'');
});
test('interface size: Ctrl+plus/minus/0 ask the host for the next size', () => {
  const f=fixture();const posted=[];f.window.chrome={webview:{postMessage(m){posted.push(m);}}};f.run();
  const keydown=f.listeners.find(l=>l.name==='keydown').fn;let prevented=0;
  for(const key of ['=','+','-','0','a']) keydown({ctrlKey:true,altKey:false,metaKey:false,key,preventDefault(){prevented++;}});
  keydown({ctrlKey:false,key:'-',preventDefault(){prevented++;}});
  assert.deepEqual(posted.map(m=>m.step),[1,1,-1,0]);assert.equal(prevented,4);
});
