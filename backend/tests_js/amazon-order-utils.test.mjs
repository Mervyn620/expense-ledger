import assert from "node:assert/strict";
import test from "node:test";
import {
  extractAmazonOrderId,
  mergeAmazonOrders,
  normalizeFulfillment,
  parseAmazonDate,
  parseMoney,
  reconcileAmounts,
} from "../scripts/collectors/amazon-order-utils.mjs";


test("parses Amazon Canada dates and amounts", () => {
  assert.equal(parseAmazonDate("Order placed June 28, 2026"), "2026-06-28");
  assert.equal(parseAmazonDate("Commande passee le 28 juin 2026"), "2026-06-28");
  assert.equal(parseMoney("Order total: CAD $1,234.56"), 1234.56);
  assert.equal(parseMoney("Promotion applied: -$5.25"), -5.25);
});

test("extracts order ids and normalizes fulfillment", () => {
  assert.equal(
    extractAmazonOrderId("Order # 701-1234567-1234567"),
    "701-1234567-1234567",
  );
  assert.equal(normalizeFulfillment("Delivered June 29"), "delivered");
  assert.equal(normalizeFulfillment("Not yet shipped"), "pending");
  assert.equal(normalizeFulfillment("Order cancelled"), "cancelled");
});

test("reconciles order components exactly", () => {
  assert.deepEqual(
    reconcileAmounts({
      total: 56.49,
      subtotal: 49.99,
      tax: 6.5,
      shipping: 0,
      promotions: 0,
    }),
    {
      total: 56.49,
      subtotal: 49.99,
      tax: 6.5,
      fees: 0,
    },
  );
  assert.deepEqual(
    reconcileAmounts({
      total: 50,
      tax: 5,
      shipping: 5,
      promotions: 10,
    }),
    {
      total: 50,
      subtotal: 50,
      tax: 5,
      fees: -5,
    },
  );
});

test("merges by Amazon order id and keeps the latest value", () => {
  const rows = mergeAmazonOrders(
    [{
      order_id: "701-1234567-1234567",
      date: "2026-06-20",
      total: "10.00",
    }],
    [{
      order_id: "701-1234567-1234567",
      date: "2026-06-20",
      total: "12.00",
    }, {
      order_id: "702-1234567-1234567",
      date: "2026-06-29",
      total: "8.00",
    }],
  );
  assert.equal(rows.length, 2);
  assert.equal(rows[0].order_id, "702-1234567-1234567");
  assert.equal(rows[1].total, "12.00");
});
