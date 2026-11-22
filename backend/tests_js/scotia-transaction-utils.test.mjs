import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeScotiaDate,
  normalizeScotiaTransaction,
  scotiaPeriodMatches,
  scotiaTransactionKey,
} from "../scripts/collectors/scotia-transaction-utils.mjs";

test("normalizes a sanitized Scotia transaction-row fixture", () => {
  const transaction = normalizeScotiaTransaction({
    posted_date_text: "Jul. 11 2026",
    description: "SAMPLE MERCHANT",
    amount: -12.34,
  });

  assert.equal(transaction.date, "2026-07-11");
  assert.equal(transaction.amount, "12.34");
  assert.equal(transaction.direction, "debit");
  assert.equal(transaction.transaction_id.length, 20);
});

test("date and transaction keys are stable", () => {
  assert.equal(normalizeScotiaDate("Jan 2 2026"), "2026-01-02");
  const row = { date: "2026-01-02", description: "Merchant", amount: 2.5 };
  assert.equal(scotiaTransactionKey(row), scotiaTransactionKey({ ...row }));
});

test("statement periods use the shared Scotia date normalizer", () => {
  const now = new Date("2026-07-18T12:00:00Z");
  assert.equal(scotiaPeriodMatches("Jul. 11 2026", "currentStatementPeriod", now), true);
  assert.equal(scotiaPeriodMatches("Jun. 30 2026", "2026-06-01", now), true);
  assert.equal(scotiaPeriodMatches("May 13 2026", "2026-06-01", now), false);
});
