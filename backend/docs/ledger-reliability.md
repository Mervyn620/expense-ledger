# Local ledger reliability

These changes are in **Unreleased**, not a retroactive amendment to 1.18.1.
The source ledger remains on the local computer. No new VPS service, scraping
schedule, browser session transfer, or AI credential is introduced.

## Import commit and CSV recovery

Schema 6 adds `pending_csv_publications`. The five browser-backed order
importers now use one shared transaction for order updates, cancellations,
items, payments, audit events, and the complete replacement CSV contents.
Constraint or database-commit failure rolls back the import before either CSV
is published. Repeated imports replace all item rows for the affected orders,
including an incoming order whose item list has become empty.

After commit, SQLite is authoritative. Each CSV is published with a synced
sibling temporary file and atomic rename. A file-lock, disk, or process failure
can leave the CSV set partly published; the committed outbox remains until
every file succeeds. Retrying repeats the same contents without replaying the
business changes. Publication holds a database writer lock so competing order
imports cannot publish an older generation over a newer one.

The weekly workflow recovers pending publications after its verified preflight
backup and before collection. Importers, payment readers, and ledger sync also
recover before consuming staging CSVs; a recovery error stops consumption.
Sync reads the recovered CSV set under the same writer lock as its database
update. Read-only validation reports pending publications as a failure and
does not repair or mutate the ledger. External programs reading raw CSV files
directly do not share this recovery guard.

For an interrupted publication, close any program locking the CSV and run:

```powershell
.app-runtime/Scripts/python.exe -B scripts/ledger/sync.py --recover-csv
```

This publishes already committed contents only; it does not collect orders or
re-import old CSVs. Normal refresh upgrades schema 5 to 6 through the existing
backup-first workflow. The migration has been tested on a SQLite backup copy
of the real ledger without changing its existing rows or the source database.

## Data observation and run history

The dashboard separates the latest purchase date, last successful check/import,
and latest recorded attempt for each source. A successful bounded scan does not
prove that every historical order was retrieved. Coverage is always `bounded`
or `unknown`; `complete_through` is deliberately unset. On-demand stores remain
visible even when skipped by routine collection. Absence of an order is not
proof of no spending.

The weekly workflow records observations after creating matching run-state and
weekly-status files. Collection success without import success is not a fresh
local ledger. Existing files can seed only the retained matching run, not an
invented history. Timestamps are normalized to UTC for ordering and displayed
locally. A failure to save observations marks the workflow failed explicitly.

`data/job_history.json` atomically retains the latest 100 job summaries. It is
included in the existing local snapshot policy. Raw output, command arguments,
and arbitrary string metrics are excluded. An unfinished run restored after an
app restart is marked failed/interrupted and is never relaunched automatically.
This is application job history, not a reconstruction of historical CLI runs.

## Order history

Scotia reconciliation updates bank-only orders by their existing
`(platform, order_id)` key instead of deleting and rebuilding them. Unchanged
orders keep their primary keys, bank links, refund links, payment rows, and
payment evidence; timestamp-only refreshes do not add order events. Existing
historical delete/recreate events are retained rather than rewritten.

Only previously generated orders for bank transactions actually reviewed in
the current input can be removed or reclassified. A correction, non-purchase
classification, or authoritative platform match can retire an affected
bank-only order. Absence from a bounded export does not delete historical
orders. A refund constraint conflict stops and rolls back the reconciliation.

Order changes, bank transactions, reconciliation links, and staging CSV
publications share a single transaction. Publication uses the schema-6 outbox
described above, including `bank_items.csv` and `payment_assignments.csv`.
Unmatched collector-backed charges continue to require review; no new matching
tolerance or automatic refund association is introduced.

SQLite triggers record initial observations, material order-field changes,
removal, and refund linkage changes. Events use platform/order source keys and
survive deletion of cancelled orders. Updating only an import timestamp creates
no new event. A rolled-back change leaves no event. Existing rows receive only
an upgrade baseline, not a fabricated creation date or historical timeline.

Order details display up to 100 events; the Run page shows the most recent 50
non-baseline changes. Removed rows remain visible in the latter view. Historical
item-by-item changes and historical reasons for cancellation are not reconstructed.

## Refund accounting boundary

There are three separate facts: a merchant order, a bank transaction, and a
user-confirmed refund association. A confirmed payment method alone is not
evidence of a final bank debit. The order drawer shows bank reconciliation
separately from payment-source status.

Refund linkage requires a real bank **credit**, matching currency, a date not
earlier than the purchase, no other order/refund assignment, and an amount no
greater than the order's remaining net amount. Each bank credit is linked in
full, once; multiple partial credits are supported, but splitting one credit
across several orders is not. Matching amount/date produces candidates only:
the user must verify the merchant/refund and check the confirmation box.

- `orders.total`, `order_payments.amount`, reconciliation, CSV exports, and
  existing `powerbi_*` views retain original gross values.
- `expense_orders` exposes `gross_total`, `refund_total`, and net `total`.
  Dashboard totals, order lists, and AI spending facts use this view.
- Refunds are attributed back to the **original purchase month**, not the month
  of cash arrival. This is a spending view, not a bank cash-flow statement.
- Item prices, tax, tips, and fees remain original values; no arbitrary refund
  allocation to individual items is made. They need not add to net spending.
- SQL constraints prevent a later import from silently invalidating confirmed
  refund evidence. Conflicting amount/date/currency changes stop for review;
  removing a refunded order is also refused. Unlink an incorrect association,
  then rerun the normal workflow. No genuine refund is inferred automatically.

`POST /api/orders/{id}/refunds` links a bank record; the corresponding DELETE
endpoint unlinks it while retaining an audit event. Mutations are refused while
an application bookkeeping job is active and remain behind existing local
Host/Origin checks. `GET /api/order-changes` is read-only.
Link requests require `confirmed: true` in addition to `bank_transaction_pk`;
the browser sends it only after the user checks the confirmation box.

## Verifiable analysis

Chat resolves the requested calendar period before choosing either a factual
query plan or AI interpretation. `8月` and `八月` use the current local calendar
year; explicit years, `去年`, `上个月` (including January rollover), month
comparisons, and month ranges are supported. The resolved period appears in
the answer metadata. Missing records never substitute a different month.
Analysis and category-share questions retain their interpretation intent;
their aggregates, item rankings, and order evidence use the same filtered
period.

AI chat now requests a JSON `parts` array of complete `fact_id` references and
qualitative `text`. `app/analysis_answers.py` builds the fact catalog from the
filtered ledger context; each referenced statement includes its own period,
dimension, count, and amount. The backend renders the complete statement so
the model cannot rename a July total as an August total. Numeric prose and
unknown or modified references are rejected. Qualitative explanations still
use conservative language checks; they are not a proof of every model claim.
Negated judgments such as inability to infer waste or make a forecast are
recognized within their own clause and do not excuse a later assertion.

Invalid model content produces an HTTP 200 response containing an explicitly
labelled local summary, `meta.source=computed_fallback`, and a bounded reason
code in `meta.validation`. The rejected output is neither shown nor retained
in diagnostics. This summary describes the available scope and does not claim
to answer unsupported filters in the original question. API configuration,
authentication, transport, and missing-ledger errors remain explicit errors.

Supported explicit-month, previous-month, platform, and two-platform comparison
questions use a small fixed query plan with parameterized SQL, never SQL written
by the model. Unsupported conditions fall back to the existing analysis path.
CAD queries do not sum other currencies. All totals are computed across the
full filtered result, even when evidence display is capped at 200 rows.

Conversational spending-location questions (including “钱花在哪了”, “钱去哪儿了”,
and “主要花在什么上”) bypass both factual shortcuts. Explanations, reviews and
saving-advice requests also reach the AI path. A month mention alone no longer
defaults to a sum. The chat prompt asks for a conclusion, relevant evidence,
interpretation and conditional actions, without equating a retail platform with
the purpose of its purchases. New `insight.*` facts derive rankings and the
largest-order share from full-scope aggregates. Ties are explicit and nonpositive
net totals do not produce concentration claims.

Spending-breakdown responses must contain a breakdown fact and explanatory prose;
a total-only reply gets `incomplete_interpretation`. A rejected or incomplete
breakdown gets at most one repair request (35-second transport timeout), with
the validation reason and format instructions but not rejected prose. A failed
repair leaves an explicitly local
breakdown with review suggestions. This is a minimum completeness check, not a
semantic proof of the model's explanation. Existing numeric/claim validation
remains active. Explicit category and platform breakdowns filter the full context
before computing facts. Tests in `test_analysis_spending_intent.py` use temporary
ledgers and mock transport; live verification is reported separately.

After the bounded repair, complete unsafe prose paragraphs may be omitted while
retaining verified fact references and useful qualitative paragraphs. Structure
and unknown fact IDs remain strict failures. Retained output must still meet the
breakdown completeness check; metadata reports `validation.status=filtered` and
an omitted paragraph count. No rejected text or invented quantity is displayed
or copied into diagnostics. This avoids losing an otherwise useful explanation
because the model repeated a count in prose.

Chat and analysis expose expandable order evidence with links to local order
details and source-check dates. The general analysis evidence is available
context, not a claim-by-claim proof of every model sentence, and may be truncated.
Predictions are withheld when source observations are missing, stale beyond
seven days, or failed. Even fresh bounded scans do not establish completeness;
any remaining projection is only a low-confidence conditional estimate.

## Verification

Use `.app-runtime/Scripts/python.exe -m unittest discover -s tests -q` and
`npm run test:collectors`. The full Windows suite needs permission to create
temporary symbolic links. New regression coverage is in
`tests/test_ledger_evolution.py`, `tests/test_analysis_month_scope.py`, and
`tests/test_analysis_answer_validation.py`; all tests use temporary ledgers.

Before upgrading a real database, create and verify a snapshot with the existing
`scripts/maintenance/safety.py checkpoint` and `verify` commands. Compare original
order/payment counts and totals before/after, then check SQLite integrity,
foreign keys, HTTP APIs, and the actual browser UI. Tests alone do not establish
the health of authenticated shopping sessions.
