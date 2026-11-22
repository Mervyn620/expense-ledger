import assert from "node:assert/strict";
import test from "node:test";

import {
  findOrdinaryOAuthOrdersTab,
  isOrdinaryLoginTabUrl,
  isOrdinaryOrdersTabUrl,
  ordinaryOrdersTabScore,
} from "../scripts/collectors/the-ordinary-tab-selection.mjs";

test("The Ordinary prefers a tab with rendered order cards", () => {
  const base = {
    url: "https://theordinary.com/en-ca/orders",
    ready_state: "complete",
    visibility_state: "hidden",
    root_found: true,
    child_count: 1,
    valid_card_count: 0,
    empty_signal: false,
  };
  const rendered = { ...base, valid_card_count: 2 };
  assert.ok(ordinaryOrdersTabScore(rendered) > ordinaryOrdersTabScore(base));
});

test("The Ordinary accepts a proven empty history but rejects lookalikes", () => {
  assert.equal(
    ordinaryOrdersTabScore({
      url: "https://www.theordinary.com/en-ca/orders",
      ready_state: "interactive",
      visibility_state: "visible",
      root_found: true,
      child_count: 0,
      valid_card_count: 0,
      empty_signal: true,
    }) > 0,
    true,
  );
  assert.equal(
    isOrdinaryOrdersTabUrl("https://theordinary.com.example/en-ca/orders"),
    false,
  );
  assert.equal(
    ordinaryOrdersTabScore({
      url: "https://example.com/en-ca/orders",
      valid_card_count: 99,
    }),
    -1,
  );
});

test("The Ordinary reuses only its exact Canadian login page", () => {
  assert.equal(isOrdinaryLoginTabUrl("https://theordinary.com/en-ca/login?ru=1"), true);
  assert.equal(isOrdinaryLoginTabUrl("https://theordinary.com/en-us/login"), false);
  assert.equal(isOrdinaryLoginTabUrl("https://evil.example/en-ca/login"), false);
});

test("The Ordinary adopts only a new same-site OAuth orders callback tab", () => {
  const callback = findOrdinaryOAuthOrdersTab([
    {
      id: "login",
      type: "page",
      url: "https://theordinary.com/en-ca/login?rurl=1",
      webSocketDebuggerUrl: "ws://login",
    },
    {
      id: "google",
      type: "page",
      url: "https://accounts.google.com/signin/v2/challenge",
      webSocketDebuggerUrl: "ws://google",
    },
    {
      id: "callback",
      type: "page",
      url: "https://theordinary.com/en-ca/orders",
      webSocketDebuggerUrl: "ws://callback",
    },
  ], ["login"]);

  assert.equal(callback?.id, "callback");
  assert.equal(findOrdinaryOAuthOrdersTab([
    {
      id: "google",
      type: "page",
      url: "https://accounts.google.com/signin/v2/challenge",
      webSocketDebuggerUrl: "ws://google",
    },
    {
      id: "lookalike",
      type: "page",
      url: "https://theordinary.com.example/en-ca/orders",
      webSocketDebuggerUrl: "ws://lookalike",
    },
  ], []), null);
});

test("The Ordinary does not adopt a pre-existing orders tab as OAuth callback", () => {
  assert.equal(findOrdinaryOAuthOrdersTab([{
    id: "old-orders",
    type: "page",
    url: "https://theordinary.com/en-ca/orders",
    webSocketDebuggerUrl: "ws://old",
  }], ["old-orders"]), null);
});
