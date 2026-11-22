import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import {
  amazonCardFingerprint, canReuseAmazonDetail, collectAmazonDetails, mergeAmazonOrders,
} from "../scripts/collectors/amazon-order-utils.mjs";
import {
  createWalmartHistoryBoundary, walmartFullScanDue,
} from "../scripts/collectors/walmart-order-utils.mjs";
import { collectCandidates } from "../scripts/collectors/collect-walmart-orders.mjs";
import { ordersPageExpression } from "../scripts/collectors/export-amazon-orders.mjs";
import { navigateToContent, waitForContent } from "../scripts/browser/content-ready.mjs";

const now = Date.parse("2026-09-11T12:00:00Z");
const card = {
  order_id: "701-1234567-1234567", date: "2026-09-01", total: 11.3,
  status: "Delivered September 3", items: [{ asin: "B012345678", title: "Fixture", quantity: 1 }],
};
function cachedOrder(source = card) {
  return {
    ...source, item_total: "10.00", items: source.items.map(item => ({ ...item, line_total: 10 })),
    payment: { card_last4: "1234" },
    detail_cache: { version: 1, fingerprint: amazonCardFingerprint(source), checked_at: new Date(now - 1000).toISOString() },
  };
}

test("Amazon reuses complete unchanged delivered detail without extending its age", async () => {
  const cached = cachedOrder();
  const result = await collectAmazonDetails([card], [cached], () => { throw new Error("unexpected fetch"); }, { now });
  assert.equal(result.reused, 1);
  const merged = mergeAmazonOrders([cached], result.collected);
  assert.deepEqual(merged, [cached]);
});

test("Amazon changed amounts, items, dates and fulfillment bypass the cache", () => {
  const cached = cachedOrder();
  for (const change of [
    { total: 12 }, { date: "2026-09-02" }, { status: "Delivered; refund issued" },
    { status: "Arriving tomorrow" }, { status: "Delivered; Not yet shipped" },
    { status: "Cancelled" }, { status: "Delivered; Return requested" },
    { items: [{ ...card.items[0], quantity: 2 }] },
    { items: [{ ...card.items[0], title: "Different item" }] },
  ]) assert.equal(canReuseAmazonDetail({ ...card, ...change }, cached, { now }), false, JSON.stringify(change));
});

test("Amazon never caches risky statuses even when their fingerprints match", () => {
  for (const status of ["", "Not yet shipped", "Delivered; Refunded", "Cancelled", "Delivered; Return started", "Livré; Remboursement", "Delivered; Arriving tomorrow"]) {
    const source = { ...card, status };
    assert.equal(canReuseAmazonDetail(source, cachedOrder(source), { now }), false, status);
  }
});

test("Amazon legacy, incomplete, expired, future-dated or forced caches refresh", () => {
  for (const change of [
    { detail_cache: undefined }, { payment: {} }, { items: [] },
    { items: [{ ...card.items[0], line_total: 0 }] }, { item_total: "20.00" },
    { detail_cache: { ...cachedOrder().detail_cache, checked_at: new Date(now - 86400000).toISOString() } },
    { detail_cache: { ...cachedOrder().detail_cache, checked_at: new Date(now + 1).toISOString() } },
    { detail_cache: { ...cachedOrder().detail_cache, checked_at: "invalid" } },
  ]) assert.equal(canReuseAmazonDetail(card, { ...cachedOrder(), ...change }, { now }), false);
  assert.equal(canReuseAmazonDetail(card, cachedOrder(), { now, refreshAll: true }), false);
});

test("Amazon mixed batch requests only new or changed detail and preserves old records", async () => {
  const second = { ...card, order_id: "702-1234567-1234567", total: 20 };
  const third = { ...card, order_id: "703-1234567-1234567" };
  const existing = [cachedOrder(), cachedOrder({ ...second, total: 11.3 })];
  const requests = [];
  const result = await collectAmazonDetails([card, second, third], existing, async source => {
    requests.push(source.order_id);
    return cachedOrder(source);
  }, { now });
  assert.deepEqual(requests, [second.order_id, third.order_id]);
  assert.equal(result.reused, 1);
  assert.equal(mergeAmazonOrders(existing, result.collected).length, 3);
  await assert.rejects(collectAmazonDetails([third], [], async () => { throw new Error("blocked"); }), /blocked/);
});

const makePage = (date, id) => ({ url: "https://www.walmart.ca/orders", candidates: [{ orderId: id, date, total: "10.00" }] });
const pages = ["2026-09-10", "2026-08-20", "2026-08-19", "2026-08-18", "2026-08-17", "2026-08-16"]
  .map((date, i) => makePage(date, String(600000000000000 + i)));
const minDate = "2026-09-01";

async function scan(fixture, options = {}) {
  let index = 0;
  let clicks = 0;
  const cdp = { call: async (_method, args) => {
    if (args.expression.includes("next page")) {
      const clicked = index < fixture.length - 1;
      if (clicked) { index++; clicks++; }
      return { result: { value: JSON.stringify({ clicked }) } };
    }
    return { result: { value: JSON.stringify(fixture[index]) } };
  } };
  const policy = { pace: async () => {}, inspectPage: async () => {} };
  const result = await collectCandidates(cdp, policy, createWalmartHistoryBoundary({ minDate, ...options }), settings => {
    let time = 0;
    return waitForContent({ ...settings, now: () => time, sleepFn: async ms => { time += ms; } });
  });
  return { ...result, clicks };
}

test("Walmart stops after two older pages and supports pagination with unchanged URLs", async () => {
  const incremental = await scan(pages);
  const full = await scan(pages, { fullHistory: true });
  assert.equal(incremental.pages, 3);
  assert.equal(incremental.stopReason, "date_boundary");
  assert.equal(full.pages, 6);
  assert.equal(full.stopReason, "history_end");
  assert.deepEqual(incremental.candidates.filter(row => row.date >= minDate), full.candidates.filter(row => row.date >= minDate));
  assert.equal(incremental.clicks, 2);
  assert.equal(full.clicks, 5);
});

test("Walmart retries one stalled pagination click without skipping order pages", async () => {
  const fixture = [makePage("2026-10-20", "600000000000011"), makePage("2026-10-10", "600000000000012")];
  let index = 0;
  let clicks = 0;
  const cdp = { call: async (_method, args) => {
    if (args.expression.includes("next page")) {
      clicks += 1;
      if (clicks === 2) index = 1;
      return { result: { value: JSON.stringify({ clicked: clicks <= 2 }) } };
    }
    return { result: { value: JSON.stringify(fixture[index]) } };
  } };
  const policy = { pace: async () => {}, inspectPage: async () => {} };
  const result = await collectCandidates(cdp, policy, () => false, settings => {
    let time = 0;
    return waitForContent({ ...settings, timeoutMs: 500, now: () => time,
      sleepFn: async ms => { time += ms; } });
  });
  assert.equal(clicks, 3);
  assert.equal(result.pages, 2);
  assert.deepEqual(result.candidates.map(row => row.orderId),
    ["600000000000011", "600000000000012"]);
});

test("Amazon last order page has no fabricated next URL", () => {
  const document = {
    querySelectorAll: () => [],
    querySelector: () => null,
    body: { innerText: "Your Orders" },
  };
  const location = { origin: "https://www.amazon.ca", pathname: "/gp/css/order-history" };
  const result = JSON.parse(vm.runInNewContext(ordersPageExpression(), { document, location, URL }));
  assert.equal(result.next_url, "");
  assert.equal(result.orders.length, 0);
});

test("Walmart pending backfills and unobserved recent orders prevent early stopping", async () => {
  assert.equal((await scan(pages, { backfillIds: [pages[5].candidates[0].orderId] })).pages, 6);
  assert.equal((await scan(pages, { knownOrders: [{ orderId: "missing", date: "2026-09-10" }] })).pages, 6);
});

test("Walmart unknown dates or IDs and out-of-order dates disable the shortcut", async () => {
  for (const candidate of [
    { orderId: "unknown", date: "", total: "10" },
    { orderId: "", date: "2026-08-30", total: "10" },
    { orderId: "disordered", date: "2026-09-11", total: "10" },
  ]) {
    const fixture = pages.map(page => structuredClone(page));
    fixture[1].candidates = [candidate];
    assert.equal((await scan(fixture)).pages, 6);
  }
});

test("Walmart full-history scans run on first use, expiry and clock rollback", () => {
  assert.equal(walmartFullScanDue(undefined, now), true);
  assert.equal(walmartFullScanDue(new Date(now - 7 * 86400000).toISOString(), now), true);
  assert.equal(walmartFullScanDue(new Date(now + 1000).toISOString(), now), true);
  assert.equal(walmartFullScanDue(new Date(now - 86400000).toISOString(), now), false);
});

test("content readiness waits through partial renders and completes after stable content", async () => {
  const fixture = [[], ["one"], ["one", "two"], ["one", "two"]];
  let time = 0;
  let reads = 0;
  const result = await waitForContent({
    read: async () => fixture[Math.min(reads++, fixture.length - 1)], ready: value => value.length > 0,
    inspect: async () => {}, label: "fixture", now: () => time, sleepFn: async ms => { time += ms; },
  });
  assert.deepEqual(result, ["one", "two"]);
  assert.equal(time, 750);
});

test("content readiness times out empty content and propagates login or challenge errors", async () => {
  let time = 0;
  const settings = {
    read: async () => [], ready: () => false, inspect: async () => {}, label: "fixture",
    timeoutMs: 1000, now: () => time, sleepFn: async ms => { time += ms; },
  };
  await assert.rejects(waitForContent(settings), /Timed out/);
  time = 0;
  const blocked = new Error("login_required");
  await assert.rejects(waitForContent({ ...settings, inspect: async () => { throw blocked; } }), error => error === blocked);
});

test("navigation ignores old populated DOM and retains pacing without fixed settle", async () => {
  let document = { readyState: "complete", value: "old-order" };
  let reads = 0;
  let paced = 0;
  let inspected = 0;
  const cdp = { call: async (method, args) => {
    if (method === "Page.navigate") return {};
    if (args.expression.startsWith("JSON.stringify")) {
      if (++reads === 2) document = { readyState: "complete", value: "new-order" };
    }
    return { result: { value: vm.runInNewContext(args.expression, { document, location: { href: "https://store.example/orders" } }) } };
  } };
  const policy = { pace: async () => { paced++; }, retry: async (_label, op) => op(), inspectPage: async () => { inspected++; } };
  const state = await navigateToContent(cdp, policy, "https://store.example/orders", {
    expression: "document.value", ready: Boolean, label: "fixture",
  });
  assert.equal(state.content, "new-order");
  assert.equal(paced, 1);
  assert.equal(inspected, 2);
  assert.equal(reads, 3);
});
