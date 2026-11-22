import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalizeWalmartProductUrl,
  isCancelledWalmartSnapshot,
  isWalmartDocumentReady,
  parseWalmartOrderCandidate,
  parseWalmartOrderDate,
  resolveWalmartBackfillOrderId,
  walmartCandidateKey,
  walmartOrderNeedsRefresh,
} from "../scripts/collectors/walmart-order-utils.mjs";

test("accepts rendered interactive or complete Walmart documents", () => {
  assert.equal(isWalmartDocumentReady({
    readyState: "interactive",
    bodyLength: 2049,
  }), true);
  assert.equal(isWalmartDocumentReady({
    readyState: "complete",
    bodyLength: 101,
  }), true);
  assert.equal(isWalmartDocumentReady({
    readyState: "loading",
    bodyLength: 2049,
  }), false);
  assert.equal(isWalmartDocumentReady({
    readyState: "complete",
    bodyLength: 100,
  }), false);
});

test("canonicalizes only Walmart Canada product links", () => {
  assert.equal(
    canonicalizeWalmartProductUrl(
      "https://www.walmart.ca/en/ip/great-value-eggs/6000201234567?athbdg=L1100#details",
    ),
    "https://www.walmart.ca/en/ip/great-value-eggs/6000201234567",
  );
  assert.equal(
    canonicalizeWalmartProductUrl("/en/ip/great-value-eggs/6000201234567"),
    "https://www.walmart.ca/en/ip/great-value-eggs/6000201234567",
  );
  assert.equal(
    canonicalizeWalmartProductUrl("https://example.com/en/ip/not-walmart/6000201234567"),
    "",
  );
  assert.equal(
    canonicalizeWalmartProductUrl("https://www.walmart.ca/default/orders/600000102347560"),
    "",
  );
});

test("parses a sanitized Walmart order-card fixture", () => {
  const candidate = parseWalmartOrderCandidate(
    {
      text: "Delivered on Jul 10 Order total $43.23 View details",
      hrefs: ["https://www.walmart.ca/default/orders/600000102347560"],
      aria: "View details Delivered on Jul 10",
    },
    new Date("2026-07-15T12:00:00Z"),
  );

  assert.deepEqual(candidate, {
    orderId: "600000102347560",
    aria: "View details Delivered on Jul 10",
    date: "2026-07-10",
    total: "43.23",
  });
});

test("rolls a future-looking month into the previous year", () => {
  assert.equal(
    parseWalmartOrderDate("Delivered on Dec 20", new Date("2026-01-05T12:00:00Z")),
    "2025-12-20",
  );
});

test("recognizes a cancelled order without treating missing items as a failure", () => {
  assert.equal(isCancelledWalmartSnapshot({
    lines: ["Jun 30, 2026", "Cancelled", "Total", "$0.00"],
    itemCandidates: [],
  }), true);
  assert.equal(isCancelledWalmartSnapshot({
    lines: ["Jul 10, 2026", "Delivered", "Total", "$49.46"],
    itemCandidates: [],
  }), false);
});

test("deduplicates repeated fulfillment cards by Walmart order id", () => {
  assert.equal(
    walmartCandidateKey({
      orderId: "600000091571882",
      date: "2026-05-14",
      total: "57.13",
      aria: "View details of Delivery, Delivered on May 14",
    }),
    walmartCandidateKey({
      orderId: "600000091571882",
      date: "2026-05-14",
      total: "57.13",
      aria: "View details of Delivery from store, Delivered on May 14",
    }),
  );
});

test("resolves only a unique known-order backfill by exact total and nearby date", () => {
  const known = [
    { orderId: "known-one", date: "2026-06-18", total: "37.17" },
    { orderId: "known-two", date: "2026-06-10", total: "35.18" },
  ];
  assert.equal(
    resolveWalmartBackfillOrderId(
      { date: "2026-06-19", total: "37.17" },
      known,
    ),
    "known-one",
  );
  assert.equal(
    resolveWalmartBackfillOrderId(
      { date: "2026-06-19", total: "37.17" },
      [...known, { orderId: "ambiguous", date: "2026-06-19", total: "37.17" }],
    ),
    "",
  );
  assert.equal(
    resolveWalmartBackfillOrderId(
      { date: "2026-06-19", total: "37.17" },
      known,
      new Set(["known-one"]),
    ),
    "",
  );
});

test("Walmart refreshes unsettled orders and changed totals while retaining completed cache",()=>{
 assert.equal(walmartOrderNeedsRefresh({total:"20.00"},{fulfillment:"pending",total:"20.00"}),true);
 assert.equal(walmartOrderNeedsRefresh({total:"22.19"},{fulfillment:"delivered",total:"20.00"}),true);
 assert.equal(walmartOrderNeedsRefresh({total:"20.00"},{fulfillment:"delivered",total:"20.00"}),false);
 assert.equal(walmartOrderNeedsRefresh({},{fulfillment:"delivered",total:"20.00"}),false);
});
