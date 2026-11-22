import assert from "node:assert/strict";
import test from "node:test";

import {
  extractOrdinaryOrderId,
  mergeOrdinaryOrders,
  normalizeFulfillment,
  normalizeProductUrl,
  parseMoney,
  parseOrdinaryDate,
  reconcileAmounts,
} from "../scripts/collectors/the-ordinary-order-utils.mjs";

test("The Ordinary date and money parsing accepts live page formats", () => {
  assert.equal(parseOrdinaryDate("Order Date August 7, 2026"), "2026-08-07");
  assert.equal(parseOrdinaryDate("June 28th, 2026"), "2026-06-28");
  assert.equal(parseMoney("CAD $29.72"), 29.72);
  assert.equal(parseMoney("- $5.00"), -5);
});

test("The Ordinary identifiers and product URLs are strictly bounded", () => {
  assert.equal(extractOrdinaryOrderId("12345678"), "12345678");
  assert.equal(extractOrdinaryOrderId("bad id"), "");
  assert.equal(
    normalizeProductUrl("/en-ca/product/example?utm_source=private#details"),
    "https://theordinary.com/en-ca/product/example",
  );
  assert.equal(normalizeProductUrl("https://example.com/product"), "");
});

test("The Ordinary amounts and fulfillment normalize to ledger values", () => {
  assert.deepEqual(
    reconcileAmounts({ total: 29.72, subtotal: 26.3, tax: 3.42 }),
    { total: 29.72, subtotal: 26.3, tax: 3.42, fees: 0 },
  );
  assert.equal(normalizeFulfillment("Delivered"), "delivered");
  assert.equal(normalizeFulfillment("Order processing"), "pending");
});

test("The Ordinary order merge is stable and replaces overlap rows", () => {
  const merged = mergeOrdinaryOrders(
    [{ order_id: "12345678", date: "2026-06-28", total: 20 }],
    [
      { order_id: "12345678", date: "2026-06-28", total: 21 },
      { order_id: "87654321", date: "2026-08-07", total: 30 },
    ],
  );
  assert.equal(merged.length, 2);
  assert.equal(merged[0].order_id, "87654321");
  assert.equal(merged[1].total, 21);
});
