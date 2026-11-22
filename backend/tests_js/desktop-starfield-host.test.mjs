import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync('app/static/desktop-starfield.js','utf8');
function fixture(host) {
  let arcs=0,frames=0,draws=0,active=false;
  const points=[],gradients=[];
  const context={clearRect(){},beginPath(){},arc(x,y,r){arcs++;points.push({x,y,r});},fill(){},fillRect(){},createLinearGradient(...coords){const gradient={coords,stops:[],addColorStop(...stop){this.stops.push(stop);}};gradients.push(gradient);return gradient;}};
  const root={dataset:{desktopVisualMode:'particle'}};
  const window={__expenseDesktopVisualMode:'particle',__expenseDesktopHostStarfield:host,expenseDesktopCosmosWallpaper:{wantsFrame:()=>active,paint(){draws++;}}};
  const document={documentElement:root,hidden:false,readyState:'complete',body:{append(){}},getElementById(){return null;},createElement(){return {setAttribute(){},getContext(){return context;}};},addEventListener(){}};
  vm.runInNewContext(source,{window,document,matchMedia:()=>({matches:false,addEventListener(){}}),innerWidth:800,innerHeight:600,performance:{now:()=>0},requestAnimationFrame(){frames++;return frames;},cancelAnimationFrame(){},addEventListener(){},MutationObserver:class{observe(){}}});
  return {window,points,gradients,get arcs(){return arcs;},get frames(){return frames;},get draws(){return draws;},activate(){active=true;window.expenseDesktopEffects.repaint();}};
}
test('web page paints stars even if an old host requests transparency',()=>{const f=fixture(true);assert.ok(f.arcs>=160);assert.ok(f.frames>0);});
test('native host retains interactive wallpaper animation on update page',()=>{const f=fixture(true);f.activate();assert.ok(f.arcs>=160);assert.ok(f.frames>0);assert.ok(f.draws>0);});
test('older host keeps its original animated star canvas',()=>{const f=fixture(false);assert.ok(f.arcs>=160);assert.ok(f.frames>0);});

test('native frame aligns web stars and opaque gradient to window coordinates',()=>{
 const f=fixture(false); f.points.length=0;
 assert.equal(f.window.expenseDesktopEffects.setHostFrame({width:1300,height:850,x:208,y:1,seconds:6.25,colors:['#07101e','#102642','#080f23']}),true);
 assert.ok(Math.abs(f.points[0].x-444.1)<1e-8); assert.ok(Math.abs(f.points[0].y-294.35)<1e-8);
 assert.equal(f.points[0].r,2.1);
 assert.deepEqual(f.gradients[0].coords,[-208,-1,1092,849]);
 assert.deepEqual(f.gradients[0].stops,[[0,'#07101e'],[.52,'#102642'],[1,'#080f23']]);
});
test('invalid native frames cannot replace the usable wallpaper',()=>{
 const f=fixture(false);
 for(const value of [null,{}, {width:0,height:800,x:0,y:0,seconds:0,colors:['#000000','#000000','#000000']}, {width:1200,height:800,x:0,y:NaN,seconds:0,colors:['#000000','#000000','#000000']}]) assert.equal(f.window.expenseDesktopEffects.setHostFrame(value),false);
 assert.equal(f.gradients.length,0);
});
