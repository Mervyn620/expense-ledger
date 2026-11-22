import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import {createScotiaLoginWaiter, scotiaAccountReady, READINESS_EXPRESSION, OBSERVER_SOURCE} from "../scripts/browser/scotia-login-wait.mjs";
const ready = {secure: true, account: true, login: false, verification: false, blocked: false};
const tick = () => new Promise(resolve => setImmediate(resolve));
function fixture(initial = {...ready, account: false}) {
  let state = initial;
  const calls = [];
  const cdp = {async call(method, params) {
    calls.push({method, params});
    if (method === "Page.addScriptToEvaluateOnNewDocument") return {identifier: "observer-1"};
    if (method === "Runtime.evaluate" && params.expression === READINESS_EXPRESSION) return {result: {value: state}};
    return {};
  }};
  const waiter = createScotiaLoginWaiter(cdp, {timeoutMs: 1000});
  return {waiter, cdp, calls, setState: value => {state = value;},
    signal: () => waiter.onMessage({method: "Runtime.bindingCalled", params: {name: "__expenseScotiaChanged", payload: "changed"}})};
}
test("requires a secure account control and no login or verification challenge", () => {
  assert.equal(scotiaAccountReady(ready), true);
  for (const state of [{...ready, secure: false}, {...ready, account: false}, {...ready, login: true}, {...ready, verification: true}, {...ready, blocked: true}, {}]) {
    assert.equal(scotiaAccountReady(state), false);
  }
});
test("already logged-in page completes immediately and removes hooks", async () => {
  const f = fixture(ready);
  assert.equal(await f.waiter.wait(), "logged_in");
  assert.ok(f.calls.some(c => c.method === "Page.removeScriptToEvaluateOnNewDocument"));
  assert.ok(f.calls.some(c => c.method === "Runtime.removeBinding"));
});
test("manual login and MFA wait for an event; no periodic inspection or duplicate continuation", async () => {
  const f = fixture();
  let completions = 0;
  const result = f.waiter.wait().then(value => {completions++; return value;});
  await tick();
  const count = () => f.calls.filter(c => c.params?.expression === READINESS_EXPRESSION).length;
  const before = count();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(count(), before);
  f.setState({...ready, verification: true}); f.signal(); await tick();
  assert.equal(completions, 0);
  f.setState(ready); f.signal(); f.signal();
  assert.equal(await result, "logged_in");
  f.signal(); await tick(); assert.equal(completions, 1);
});
test("a redirect event resumes inspection after its old execution context disappears", async () => {
  const f = fixture(); const call = f.cdp.call; let first = true;
  f.cdp.call = async (method, params) => {
    if (params?.expression === READINESS_EXPRESSION && first) { first = false; throw new Error("Execution context was destroyed."); }
    return call(method, params);
  };
  const result = f.waiter.wait(); await tick();
  f.setState(ready); f.waiter.onMessage({method: "Page.frameNavigated"});
  assert.equal(await result, "logged_in");
});
test("closing the page rejects instead of starting reconciliation", async () => {
  const f = fixture(); const result = f.waiter.wait(); await tick(); f.waiter.onClose();
  await assert.rejects(result, /page closed/);
});
test("single deadline fails without treating elapsed time as successful login", async () => {
  const f = fixture(); const waiter = createScotiaLoginWaiter(f.cdp, {timeoutMs: 15});
  await assert.rejects(waiter.wait(), /not completed/);
});
test("observer reacts to DOM changes once per state and transmits no page data", async () => {
  let mutation, hasAccount = false, disconnected = false;
  const messages = [];
  const node = {getClientRects: () => [1]};
  const context = {
    location: {protocol: "https:", hostname: "secure.scotiabank.com", pathname: "/my-accounts"},
    document: {body: {innerText: "Account content never leaves the page"},
      querySelectorAll: selector => selector.startsWith("#summary") && hasAccount ? [node] : [],
      addEventListener() {}, removeEventListener() {}},
    getComputedStyle: () => ({visibility: "visible", display: "block"}), queueMicrotask,
    MutationObserver: class { constructor(cb) {mutation = cb;} observe() {} disconnect() {disconnected = true;} },
    addEventListener() {}, removeEventListener() {}, __expenseScotiaChanged: value => messages.push(value),
  };
  context.window = context; context.top = context;
  vm.runInNewContext(OBSERVER_SOURCE, context);
  assert.deepEqual(messages, ["changed"]);
  mutation(); await tick(); assert.equal(messages.length, 1);
  hasAccount = true; mutation(); await tick(); assert.deepEqual(messages, ["changed", "changed"]);
  context.__expenseScotiaObserver(); assert.equal(disconnected, true);
});
