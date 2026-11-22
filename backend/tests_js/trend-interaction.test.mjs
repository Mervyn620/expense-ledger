import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function harness() {
  const context = vm.createContext({ Intl, URLSearchParams, console });
  vm.runInContext(fs.readFileSync('app/static/trend.js', 'utf8'), context);
  vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8').replace(/initialize\(\);\s*$/, ''), context);
  const run = code => vm.runInContext(code, context);
  const json = code => JSON.parse(JSON.stringify(run(code)));
  return { run, json };
}

function layoutHarness() {
  const result = harness();
  result.run(`
    const frames = new Map(); let frameId = 0; let notifyResize; let disconnected = false;
    const window = {
      requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
      cancelAnimationFrame(id) { frames.delete(id); },
    };
    class ResizeObserver {
      constructor(callback) { notifyResize = callback; }
      observe() {}
      disconnect() { disconnected = true; }
    }
    const chart = { clientWidth: 0, clientHeight: 0, isConnected: true, scrollLeft: 0,
      innerHTML: '', contains: () => false, querySelectorAll: () => [] };
    const elements = new Map();
    const document = { activeElement: null, getElementById(id) {
      if (id === 'trendChart') return chart;
      if (!elements.has(id)) elements.set(id, { hidden: true, dataset: {}, querySelectorAll: () => [] });
      return elements.get(id);
    }};
    updateTrendZoomControls = () => {};
    state.overview = { trend: Array.from({length: 5}, (_, i) => ({month: '2026-0' + (i + 5), total: 10 * i})), selected_month: '2026-09' };
    let redraws = 0;
    const dispose = observeTrendLayout(chart, () => {
      redraws++;
      renderTrend(state.overview.trend, state.overview.selected_month);
    });
    const flush = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback()); };
    const svgWidth = () => Number(chart.innerHTML.match(/<svg class="trend-svg" width="(\\d+)"/)?.[1] || 0);
  `);
  return result;
}

test('hidden initial chart waits for layout and fills the container without a zoom action', () => {
  const { run } = layoutHarness();
  run('renderTrend(state.overview.trend, state.overview.selected_month); notifyResize(); flush();');
  assert.equal(run('chart.innerHTML'), '');
  assert.equal(run('redraws'), 0);
  run('chart.clientWidth=900; chart.clientHeight=300; notifyResize(); flush();');
  assert.equal(run('svgWidth()'), 892);
  assert.equal(run('redraws'), 1);
});

test('container-only resize is coalesced and redraw does not create a resize loop', () => {
  const { run } = layoutHarness();
  run('chart.clientWidth=400; chart.clientHeight=280; notifyResize(); flush();');
  run('chart.clientWidth=720; notifyResize(); chart.clientWidth=950; notifyResize(); flush();');
  assert.equal(run('svgWidth()'), 942);
  assert.equal(run('redraws'), 2);
  run('notifyResize(); flush(); notifyResize(); flush();');
  assert.equal(run('redraws'), 2);
});

test('returning from a hidden view uses the latest data and preserves zoom', () => {
  const { run } = layoutHarness();
  run('chart.clientWidth=800; chart.clientHeight=280; notifyResize(); flush();');
  run('chart.clientWidth=0; chart.clientHeight=0; notifyResize(); flush();');
  run('state.trendZoom=1.5; state.overview.selected_month="2026-08"; renderTrend(state.overview.trend,state.overview.selected_month);');
  run('chart.clientWidth=800; chart.clientHeight=320; notifyResize(); flush();');
  assert.equal(run('svgWidth()'), 1188);
  assert.equal(run('chart.innerHTML.includes(\'class="trend-month selected" data-trend-month="2026-08"\')'), true);
  assert.equal(run('redraws'), 2);
});

test('observer teardown cancels pending work', () => {
  const { run } = layoutHarness();
  run('chart.clientWidth=800; chart.clientHeight=280; notifyResize(); dispose(); flush();');
  assert.equal(run('disconnected'), true);
  assert.equal(run('redraws'), 0);
});

test('selection defaults to two, preserves empty and permits more than six', () => {
  const { run, json } = harness();
  run('const catalog = Array.from({length:8}, (_,i)=>({id:String(i)}));');
  assert.deepEqual(json('trendSelection(catalog,null)'), ['0','1']);
  assert.deepEqual(json('trendSelection(catalog,[])'), []);
  assert.equal(run('trendSelection(catalog,catalog.map(x=>x.id)).length'), 8);
  assert.deepEqual(json('trendSelection(catalog,["missing","2"])'), ['2']);
});

test('style is independent of ranking and selection; negative values fit axis', () => {
  const { json } = harness();
  assert.deepEqual(json('trendStyle("a",[{id:"b"},{id:"a"}])'), json('trendStyle("a",[{id:"a"},{id:"b"}])'));
  for (const values of [[-14, 8], [-5,-1], [0,0], [0.01,0.02]]) {
    const bounds = json(`trendBounds(${JSON.stringify(values)})`);
    assert.ok(bounds.min <= Math.min(...values));
    assert.ok(bounds.max >= Math.max(...values));
    assert.ok(bounds.max > bounds.min);
  }
});

test('comparison filters do not modify month or total history', () => {
  const { run, json } = harness();
  run('state.month="2026-07"; state.trendMode="platform"; state.trendSelected.platform=["a"]; state.overview={trend_series:{platform:[{id:"a",values:[10,20]},{id:"b",values:[5,6]}]}};');
  assert.equal(run('selectedTrendSeries([{total:15},{total:26}]).length'), 1);
  assert.equal(run('state.month'), '2026-07');
  run('state.trendMode="total"');
  assert.deepEqual(json('selectedTrendSeries([{total:15},{total:26}])[0].values'), [15,26]);
});

test('latest month request wins even when old response arrives last', async () => {
  const { run } = harness();
  run('const pending=[]; api=()=>new Promise(resolve=>pending.push(resolve)); renderOverview=p=>{state.month=p.selected_month;state.overview=p;};');
  const july = run('loadOverview("2026-07")');
  const august = run('loadOverview("2026-08")');
  run('pending[1]({selected_month:"2026-08"})');
  assert.equal(await august, true);
  run('pending[0]({selected_month:"2026-07"})');
  assert.equal(await july, false);
  assert.equal(run('state.month'), '2026-08');
});

test('old failed request cannot replace a successful latest selection', async () => {
  const { run } = harness();
  run('const pending=[]; api=()=>new Promise((resolve,reject)=>pending.push({resolve,reject})); renderOverview=p=>{state.month=p.selected_month;};');
  const old = run('loadOverview("2026-07")');
  const latest = run('loadOverview("2026-08")');
  run('pending[1].resolve({selected_month:"2026-08"});pending[0].reject(new Error("old request failed"));');
  assert.equal(await latest, true);
  assert.equal(await old, false);
  assert.equal(run('state.month'), '2026-08');
});
