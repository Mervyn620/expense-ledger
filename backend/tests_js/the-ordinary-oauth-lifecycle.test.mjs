import assert from "node:assert/strict";
import test from "node:test";

import {
  runOrdinaryDetailWithRecovery,
  runOrdinaryOAuthRecovery,
} from "../scripts/collectors/the-ordinary-auth.mjs";

const LOGIN_FACTS = {
  host_allowed: true,
  login_page: true,
  path: "/en-ca/login",
  google_button_count: 1,
  google_url_allowed: true,
  challenge_visible: false,
};

const ORDERS_FACTS = {
  host_allowed: true,
  login_page: false,
  path: "/en-ca/orders",
};

function page(id, url, socket = `ws://${id}`) {
  return { id, type: "page", url, webSocketDebuggerUrl: socket };
}

function harness({ tabFrames, factsByCdp = new Map(), timeoutMs = 20 }) {
  const originalCdp = {
    name: "original",
    calls: [],
    async call(method) { this.calls.push(method); },
    close() {},
  };
  const connected = [];
  const closed = [];
  let tabIndex = 0;
  let clock = 0;
  const defaultFacts = [LOGIN_FACTS];
  factsByCdp.set(originalCdp, factsByCdp.get(originalCdp) || defaultFacts);
  const factIndexes = new Map();

  return {
    originalCdp,
    connected,
    closed,
    options: {
      cdp: originalCdp,
      port: 9224,
      currentTabId: "login",
      timeoutMs,
      now: () => clock,
      sleep: async (milliseconds) => { clock += milliseconds; },
      listTabs: async () => {
        const value = tabFrames[Math.min(tabIndex, tabFrames.length - 1)] || [];
        tabIndex += 1;
        return value;
      },
      evaluateAuthFacts: async (cdp) => {
        const rows = factsByCdp.get(cdp) || [];
        const index = factIndexes.get(cdp) || 0;
        factIndexes.set(cdp, index + 1);
        return rows[Math.min(index, Math.max(0, rows.length - 1))] || {};
      },
      clickGoogle: async (cdp) => {
        assert.equal(cdp, originalCdp);
        return { clicked: true };
      },
      connectCdp: async (socket) => {
        connected.push(socket);
        const callback = {
          name: socket,
          calls: [],
          async call(method) { this.calls.push(method); },
          close() { closed.push(socket); },
        };
        factsByCdp.set(callback, [ORDERS_FACTS]);
        return callback;
      },
      waitForDocument: async () => {},
    },
  };
}

test("OAuth recovery accepts the original tab returning to orders", async () => {
  const facts = new Map();
  const state = harness({
    tabFrames: [
      [page("login", "https://theordinary.com/en-ca/login")],
      [page("login", "https://theordinary.com/en-ca/orders")],
    ],
    factsByCdp: facts,
  });
  facts.set(state.originalCdp, [LOGIN_FACTS, ORDERS_FACTS]);

  const result = await runOrdinaryOAuthRecovery(state.options);

  assert.equal(result.restored, true);
  assert.equal(result.cdp, state.originalCdp);
  assert.equal(result.tabId, "login");
  assert.deepEqual(state.connected, []);
});

test("OAuth recovery adopts only a new exact same-site orders callback", async () => {
  const state = harness({
    tabFrames: [
      [page("login", "https://theordinary.com/en-ca/login")],
      [
        page("login", "https://theordinary.com/en-ca/login"),
        page("google", "https://accounts.google.com/signin/v2/challenge"),
        page("callback", "https://www.theordinary.com/en-ca/orders", "ws://callback"),
      ],
    ],
  });

  const result = await runOrdinaryOAuthRecovery(state.options);

  assert.equal(result.restored, true);
  assert.equal(result.cdp.name, "ws://callback");
  assert.equal(result.tabId, "callback");
  assert.deepEqual(state.connected, ["ws://callback"]);
  assert.deepEqual(result.cdp.calls, ["Page.enable", "Runtime.enable"]);
});

test("OAuth recovery observes an external-only flow without attaching to it", async () => {
  const state = harness({
    tabFrames: [
      [page("login", "https://theordinary.com/en-ca/login")],
      [page("login", "https://accounts.google.com/signin/v2/challenge")],
    ],
  });

  const result = await runOrdinaryOAuthRecovery(state.options);

  assert.equal(result.restored, false);
  assert.equal(result.manual, true);
  assert.equal(result.timed_out, true);
  assert.deepEqual(state.connected, []);
});

test("OAuth recovery rejects HTTPS lookalikes and HTTP callback targets", async () => {
  const state = harness({
    tabFrames: [
      [page("login", "https://theordinary.com/en-ca/login")],
      [
        page("lookalike", "https://theordinary.com.example/en-ca/orders"),
        page("http", "http://theordinary.com/en-ca/orders"),
      ],
    ],
  });

  const result = await runOrdinaryOAuthRecovery(state.options);

  assert.equal(result.restored, false);
  assert.equal(result.timed_out, true);
  assert.deepEqual(state.connected, []);
});

test("OAuth recovery exits at its injected timeout", async () => {
  const state = harness({
    tabFrames: [
      [page("login", "https://theordinary.com/en-ca/login")],
      [],
    ],
    timeoutMs: 7,
  });

  const result = await runOrdinaryOAuthRecovery(state.options);

  assert.deepEqual(
    {
      attempted: result.attempted,
      restored: result.restored,
      manual: result.manual,
      timed_out: result.timed_out,
    },
    { attempted: true, restored: false, manual: true, timed_out: true },
  );
});

test("detail recovery is skipped for an authenticated response", async () => {
  const cdp = { name: "orders" };
  let recoveryCalls = 0;
  const result = await runOrdinaryDetailWithRecovery({
    cdp,
    fetchDetail: async () => ({ login_signal: false, schema_ok: true }),
    recover: async () => {
      recoveryCalls += 1;
      return { cdp, manual: false };
    },
  });

  assert.equal(result.cdp, cdp);
  assert.equal(result.retried, false);
  assert.equal(result.recovery, null);
  assert.equal(recoveryCalls, 0);
});

test("detail login response gets one bounded recovery and retry", async () => {
  const staleCdp = { name: "stale" };
  const recoveredCdp = { name: "recovered" };
  const attempts = [];
  const result = await runOrdinaryDetailWithRecovery({
    cdp: staleCdp,
    fetchDetail: async (target) => {
      attempts.push(target.name);
      return attempts.length === 1
        ? { login_signal: true, schema_ok: false }
        : { login_signal: false, schema_ok: true };
    },
    recover: async (target) => {
      assert.equal(target, staleCdp);
      return { cdp: recoveredCdp, manual: false, restored: true };
    },
  });

  assert.deepEqual(attempts, ["stale", "recovered"]);
  assert.equal(result.cdp, recoveredCdp);
  assert.equal(result.detail.schema_ok, true);
  assert.equal(result.retried, true);
});

test("manual detail recovery never repeats the detail request", async () => {
  const cdp = { name: "orders" };
  let attempts = 0;
  const result = await runOrdinaryDetailWithRecovery({
    cdp,
    fetchDetail: async () => {
      attempts += 1;
      return { login_signal: true, schema_ok: false };
    },
    recover: async () => ({ cdp, manual: true, attempted: true }),
  });

  assert.equal(attempts, 1);
  assert.equal(result.retried, false);
  assert.equal(result.recovery.manual, true);
});
