import assert from "node:assert/strict";
import test from "node:test";

import {
  browserSanitizerSource,
  sanitizeAccountingText,
  sanitizeAccountingUrl,
} from "../scripts/browser/sanitize.mjs";

test("redacts common contact, card, postal, and street-address data", () => {
  const value = sanitizeAccountingText(
    "Deliver to 123 Example Street Toronto ON M5V 2T6, bill@example.ca, 416-555-1212, card ending 1111",
  );

  assert.doesNotMatch(value, /Example Street|M5V|example\.ca|416-555|1111/);
  assert.match(value, /\[address\]|\[postal-code\]/);
  assert.match(value, /\[email\]/);
  assert.match(value, /\[phone\]/);
  assert.match(value, /\[last4\]/);
});

test("redacts Canadian street suffixes used by delivery addresses", () => {
  const value = sanitizeAccountingText(
    "Pickup location near 123 Example Crescent Mississauga ON A1A 1A1",
  );

  assert.doesNotMatch(value, /123 Example Crescent|A1A 1A1/);
  assert.match(value, /\[address\]/);
  assert.match(value, /\[postal-code\]/);
});

test("removes credentials, fragments, and unapproved query parameters from URLs", () => {
  const value = sanitizeAccountingUrl(
    "https://user:secret@example.ca/orders/123?token=private&mod=receipt&modctx=order-1#account",
  );

  assert.equal(value, "https://example.ca/orders/123?mod=receipt&modctx=order-1");
});

test("browser helper source applies the same URL and text boundary", () => {
  const helpers = new Function(
    `${browserSanitizerSource}; return { sanitizeAccountingText, sanitizeAccountingUrl };`,
  )();

  assert.equal(
    helpers.sanitizeAccountingUrl("https://example.ca/orders?token=x&ps=1#secret"),
    "https://example.ca/orders?ps=1",
  );
  assert.doesNotMatch(
    helpers.sanitizeAccountingText("Shipping address 88 King Road A1A 1A1"),
    /King Road|A1A/,
  );
});
