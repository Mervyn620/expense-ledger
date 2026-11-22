import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../app/static/analysis.js', import.meta.url), 'utf8');
function element() {
  const classes = new Set();
  return {
    dataset: {}, attributes: {}, style: {}, listeners: {}, children: [],
    textContent: '', hidden: false, disabled: false, value: '', scrollHeight: 44,
    classList: { add: value => classes.add(value), toggle: (value, enabled) => enabled ? classes.add(value) : classes.delete(value) },
    setAttribute(key, value) { this.attributes[key] = value; },
    addEventListener(key, listener) { this.listeners[key] = listener; },
    replaceChildren(...children) { this.children = children; },
    appendChild(child) { this.children.push(child); },
    append(...children) { this.children.push(...children); },
    focus() {},
  };
}
async function boot(status = { configured: true, model: 'test-model', periods: { latest_month: '2026-09', latest_year: '2026' } }, provider = 'deepseek') {
  const nodes = new Map();
  const get = id => { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); };
  get('chatMeta').hidden = true;
  const buttons = ['month', 'year', 'trend'].map(mode => Object.assign(element(), { dataset: { mode } }));
  const providers = ['deepseek', 'chatgpt'].map(provider => Object.assign(element(), { dataset: { provider } }));
  const messages = [];
  const requests = [];
  const context = vm.createContext({
    document: { getElementById: get, querySelectorAll: selector => selector === '[data-mode]' ? buttons : selector === '[data-provider]' ? providers : [], createElement: element },
    localStorage: { getItem: () => provider, setItem() {} },
    window: { __expenseDesktopHost: true, __expenseDesktopChatGptAutoPaste: true, chrome: { webview: { postMessage: message => messages.push(message) } }, addEventListener() {}, clearTimeout() {}, setTimeout() {} },
    fetch: async (path, options) => {
      if (path.endsWith('/status')) {
        if (status instanceof Error) throw status;
        return { ok: true, json: async () => status };
      }
      return new Promise((resolve, reject) => requests.push({ path, options, resolve: data => resolve({ ok: true, json: async () => data }), reject }));
    },
  });
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  return { get, buttons, providers, messages, requests, context };
}

test('mode selection immediately names the period; pending and successful results keep an accessible selection', async () => {
  const h = await boot();
  assert.equal(h.get('chatMeta').hidden, true);
  assert.ok(h.buttons.every(button => !button.disabled));
  const run = h.buttons[0].listeners.click();
  assert.equal(h.get('resultTitle').textContent, '2026年9月 · 月度花销分析');
  assert.equal(h.get('analysisResult').attributes['aria-busy'], 'true');
  assert.deepEqual(h.buttons.map(b => b.attributes['aria-pressed']), ['true', 'false', 'false']);
  assert.ok(h.buttons.every(button => button.disabled));
  assert.deepEqual(JSON.parse(h.requests[0].options.body), { mode: 'month' });
  h.requests[0].resolve({ result: { title: '月度分析结果', summary: '测试摘要', highlights: ['测试发现'] }, meta: { mode: 'month', period: '2026-09', orders: 2, total: 20 } });
  await run;
  assert.equal(h.get('analysisResult').dataset.state, 'ready');
  assert.equal(h.get('analysisResult').attributes['aria-busy'], 'false');
  assert.equal(h.get('resultEmpty').hidden, true);
  assert.equal(h.get('resultContent').hidden, false);
  assert.ok(h.get('resultContent').children.length > 0);
  assert.ok(h.buttons.every(button => !button.disabled));
});

test('failed analysis clears busy state, preserves the chosen period and allows retry', async () => {
  const h = await boot();
  const first = h.buttons[1].listeners.click();
  h.requests[0].reject(new Error('请求超时'));
  await first;
  assert.equal(h.get('resultTitle').textContent, '2026年 · 年度花销分析');
  assert.equal(h.get('emptyMessage').textContent, '请求超时');
  assert.equal(h.get('analysisResult').dataset.state, 'error');
  assert.equal(h.get('analysisResult').attributes['aria-busy'], 'false');
  assert.ok(h.buttons.every(button => !button.disabled));
  const retry = h.buttons[1].listeners.click();
  assert.equal(h.get('analysisResult').dataset.state, 'loading');
  h.requests[1].reject(new Error('服务不可用'));
  await retry;
});

test('unconfigured or unreachable services show an inline error and disable unavailable actions', async () => {
  for (const status of [{ configured: false }, new Error('连接失败')]) {
    const h = await boot(status);
    assert.ok(h.buttons.every(button => button.disabled));
    assert.equal(h.get('chatQuestion').disabled, true);
    assert.equal(h.get('analysisResult').dataset.state, 'error');
    assert.equal(h.get('resultHeading').hidden, false);
    assert.ok(h.get('emptyMessage').textContent);
  }
});

test('composer grows within a bound, shrinks after clearing, and respects IME and submission shortcuts', async () => {
  const h = await boot(), input = h.get('chatQuestion');
  input.scrollHeight = 260;
  input.listeners.input();
  assert.equal(input.style.height, '176px');
  input.scrollHeight = 44;
  input.listeners.input();
  assert.equal(input.style.height, '44px');
  let submissions = 0;
  h.get('chatForm').requestSubmit = () => submissions++;
  const enter = { key: 'Enter', preventDefault() {} };
  input.listeners.keydown(enter);
  input.listeners.keydown({ ...enter, ctrlKey: true, isComposing: true });
  assert.equal(submissions, 0);
  input.listeners.keydown({ ...enter, ctrlKey: true });
  input.listeners.keydown({ ...enter, metaKey: true });
  assert.equal(submissions, 2);
  input.disabled = true;
  input.listeners.keydown({ ...enter, ctrlKey: true });
  assert.equal(submissions, 2);
});

const exportFixture = {file_path: 'test.md', file_name: 'test.md', file_sha256: 'abc', persistent: false,
  message: '请分析本月', content: '# 假账本', meta: {period: '本月', orders: 2, total_cad: 20, included_orders: 2}};

test('ChatGPT keeps all three actions available without DeepSeek; one click prepares then hands off', async () => {
  const h = await boot({configured: false}, 'chatgpt');
  assert.ok(h.buttons.every(button => !button.disabled));
  assert.equal(h.get('analysisResult').hidden, true);
  assert.equal(h.get('deepseekChat').hidden, true);
  for (const button of h.buttons) {
    const run = button.listeners.click();
    const request = h.requests.at(-1);
    assert.equal(request.path, '/api/analysis/chatgpt/prepare');
    assert.deepEqual(JSON.parse(request.options.body), {mode: button.dataset.mode});
    assert.ok(h.providers.every(item => item.disabled));
    await button.listeners.click();
    assert.equal(h.requests.at(-1), request);
    request.resolve(exportFixture);
    await run;
    assert.equal(h.messages.at(-1).type, 'chatgpt-handoff');
    assert.equal(h.messages.at(-1).fileSha256, 'abc');
    assert.ok(h.buttons.every(item => item.disabled));
    h.context.window.expenseChatGptHandoffResult('已附加', true, true);
    assert.ok(h.buttons.every(item => !item.disabled));
    assert.equal(h.get('chatgptPrepared').hidden, true);
  }
});

test('provider switch preserves DeepSeek behavior and failed preparation allows a retry', async () => {
  const h = await boot();
  h.providers[1].listeners.click();
  assert.equal(h.get('chatgptPanel').hidden, false);
  const run = h.buttons[0].listeners.click();
  h.requests[0].reject(new Error('本月尚无账本记录'));
  await run;
  assert.equal(h.messages.length, 0);
  assert.match(h.get('chatgptStatus').textContent, /本月尚无/);
  assert.ok(h.buttons.every(item => !item.disabled));
  h.providers[0].listeners.click();
  assert.equal(h.get('chatgptPanel').hidden, true);
  assert.equal(h.get('deepseekChat').hidden, false);
});


test('older native hosts cannot accidentally run the legacy open-new-tab handoff', async () => {
  const h = await boot({configured: false}, 'chatgpt');
  h.context.window.__expenseDesktopChatGptAutoPaste = false;
  await h.buttons[0].listeners.click();
  assert.equal(h.requests.length, 0);
  assert.equal(h.messages.length, 0);
  assert.match(h.get('chatgptStatus').textContent, /退出账本/);
});

test('ChatGPT year includes newer manual payments while DeepSeek keeps its own scope', async () => {
  const h = await boot({configured: true, model: 'test', periods: {latest_year: '2026'}, chatgpt_periods: {latest_year: '2027'}}, 'chatgpt');
  assert.equal(h.get('yearPeriod').textContent, '2027');
  h.providers[0].listeners.click();
  assert.equal(h.get('yearPeriod').textContent, '2026');
});
