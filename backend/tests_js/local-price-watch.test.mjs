import assert from "node:assert/strict";
import test from "node:test";

import {
  MY_ITEMS_URL,
  REQUEST_CONTRACT,
  normalizeRequest,
  parseMyItemsRows,
  parseMyItemsText,
  selectWalmartTarget,
} from "../scripts/price_watch/local_collector.mjs";

test("validates the one-page local price-watch request", () => {
  const request = normalizeRequest({
    contract: REQUEST_CONTRACT,
    store_id: "3055",
    store_name: "MISSISSAUGA (SQUARE ONE), ON",
    settle_ms: 8000,
    max_scrolls: 12,
  });

  assert.equal(request.store_id, "3055");
  assert.equal(request.settle_ms, 8000);
  assert.equal(request.max_scrolls, 12);
});

test("parses My Items Was labels and ignores non-deals", () => {
  assert.deepEqual(
    parseMyItemsText(
      "Grace 100% Pure Coconut Water, 1L Bottle $3.27 Was $4.98 33¢/100ml",
    ),
    {
      source_name: "Grace 100% Pure Coconut Water, 1L Bottle",
      current_price: 3.27,
      regular_price: 4.98,
      on_sale: true,
    },
  );
  assert.equal(
    parseMyItemsText("Great Value Eggs $3.97"),
    null,
  );
  assert.equal(
    parseMyItemsText("Great Value Eggs $4.97 Was $4.97"),
    null,
  );
});

test("deduplicates deal labels from the one rendered page", () => {
  const label = "Smucker's Apricot Spread 500ml $5.77 Was $6.47 $1.15/100ml";
  const rows = parseMyItemsRows([label, label]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].source_name, "Smucker's Apricot Spread 500ml");
});

test("prefers an existing My Items tab and never another account tab", () => {
  const target = selectWalmartTarget([
    {
      type: "page",
      url: "https://auth.scotiaonline.scotiabank.com/online",
      webSocketDebuggerUrl: "ws://local/scotia",
    },
    {
      type: "page",
      url: "https://www.walmart.ca/en/orders",
      webSocketDebuggerUrl: "ws://local/walmart-orders",
    },
    {
      type: "page",
      url: MY_ITEMS_URL,
      webSocketDebuggerUrl: "ws://local/walmart-items",
    },
  ]);

  assert.equal(target.webSocketDebuggerUrl, "ws://local/walmart-items");
  assert.equal(
    selectWalmartTarget([{
      type: "page",
      url: "https://www.amazon.ca/",
      webSocketDebuggerUrl: "ws://local/amazon",
    }]),
    undefined,
  );
});
