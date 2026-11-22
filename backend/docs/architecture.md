# Expense Ledger Architecture

## Pipeline

```text
local dedicated browser ─┐
                         ├─> sanitized data caches
VPS isolated profiles ───┘        |
  (checksum-verified bundle)       v
                           platform importers
                                  |
                                  v
                            local SQLite upsert
                                  |
                                  v
                 payment assignment and bank reconciliation
                                  |
                                  v
                    SQLite report exports -> validation

local Walmart tab ──> local-only price snapshot
```

The runtime selector controls the Amazon/Fantuan/Uber/Walmart order collection
phases. The Ordinary is an explicit local-only override. Routine runs include
Walmart, Uber Eats, and Fantuan; Amazon and The Ordinary are started separately
only after a recent purchase. In VPS mode, the local client opens a temporary
loopback-only home-exit tunnel, sends allowlisted sanitized inputs, and accepts
only the validated collection bundle. Walmart price checking is a separate,
local-only task that uses the Walmart tab of the dedicated shop profile.
Importers, price-result persistence, reconciliation, reports, safety snapshots,
and the authoritative SQLite ledger remain local. Scotia never enters the
remote path. Browser profiles are independent per environment and are excluded
from backups and transport.

The full bundle contract v1 remains compatible. Recovery uses v2 with an exact
selected-platform set, dynamic expected components, selected output ownership,
and explicit `live`, `cache_preserved`, or `none` freshness. A primary failure
marks its dependent payment component skipped; the local client rejects any
member owned by an unrequested platform. Partial transport can therefore retain
valid caches without presenting the collection as a live success.
Selected collection and recovery continuation are intentionally separate. A
normal routine or on-demand run replaces component state with exactly the
requested platforms; only an explicit retry operation is allowed to merge a
new attempt into the previous partial recovery set.
The local client also maintains a bounded per-platform recovery backlog apart
from the latest bundle summary. This lets an Amazon-only success remain an
exact Amazon result without concealing a still-unresolved Uber login failure;
only a later Uber result can replace or clear the Uber recovery entry.

VPS source releases live under immutable `releases/<id>` directories. A staged
release must match its SHA-256 manifest and pass Python compile, Node syntax,
and collector-manifest checks while the collection lock is held and no manual
login session is active. Linux atomically replaces the `current` symlink; a
failed readiness probe immediately restores the previous pointer. Profiles,
runtime, logs, and authentication markers remain outside releases.

## Public Interfaces

The stable user and automation interface is
`scripts/weekly-expense-refresh.ps1`. Browser start and stop scripts are also
public. Files below the module subdirectories are internal implementation
interfaces and may evolve without changing the weekly command.

The local application is served by `app/main.py` on loopback only. It reads
ledger and bank data through query-only connections and starts refresh or
validation commands through a single-job background queue. User-initiated
data writes are narrowly scoped to `cash_withdrawal_notes` and confirmed
`order_refunds` links; the browser UI never edits orders, payments, or imported
bank transactions. Host and Origin checks reject non-loopback actions, and
responses include restrictive content, framing, MIME, and referrer policies.
Background tasks have a fixed timeout and terminate their complete process tree
when the app exits or a timeout occurs.

Schema 5 adds durable `order_events`, `collection_observations`, and
`order_refunds`. The query-only `expense_orders` view exposes gross amount,
linked refunds, and net amount without changing source tables or existing
Power BI views. AI and application totals use this net purchase-month view;
existing reconciliation and CSV validation continue to compare gross source
amounts. See [ledger reliability](ledger-reliability.md).

`scripts/start-expense-app.ps1` owns process reliability. It serializes starts
with a per-workspace mutex, creates the ignored `.app-runtime` environment,
repairs missing dependencies, validates the application identity, records the
actual Uvicorn PID, recovers stale state, searches ports `8765` through `8774`,
and writes diagnostics under `.codex/`. `scripts/check-expense-app.ps1` checks
or repairs this runtime without touching ledger data.

`scripts/weekly-expense-refresh.ps1` owns the write-side reliability boundary.
A separate per-workspace mutex prevents overlapping app, scheduled, or manual
refreshes. Before any importer runs, `scripts/maintenance/safety.py` checks
free space, directory access, JSON configuration, SQLite quick-check results,
foreign keys, and required tables, then creates a verified pre-refresh
snapshot. A successful run creates a second post-refresh snapshot. Twenty-four
snapshots are retained under ignored `backups/`; raw pages and browser data are
never included.

The app exposes Scotia login preparation and Scotia reconciliation as separate
internal job modes behind one two-stage UI action. `scripts/prepare-scotia-login.ps1`
opens only the official Scotia hosts in the dedicated profile, and
`scripts/browser/prepare-scotia-login.mjs` permits Chrome password-manager
autofill without returning field contents through CDP. It submits only when
every visible login field is already populated and stops for OTP, CAPTCHA, or
security challenges. The second mode runs `-ReconcileScotia` without
`-CollectFromBrowser`, so it does not repeat unrelated store collection.

Application endpoints:

- `GET /api/overview`, `/api/orders`, `/api/orders/{id}`, and
  `/api/reconciliation/review` expose sanitized bookkeeping and held-review
  views. Order search includes item product names and list reads are paginated.
- `GET /api/cash-withdrawals` exposes detected ATM withdrawals without adding
  them to overview totals. `PATCH /api/cash-withdrawals/{id}` writes or clears
  only the associated purpose note.
- `GET /api/analysis/status`, `POST /api/analysis/run`, and
  `POST /api/analysis/chat` serve the standalone DeepSeek analysis page.
- `POST /api/jobs/refresh` accepts `local`, `stores`, `scotia_login`, `scotia`,
  or `all`.
- `POST /api/jobs/validate` runs the existing integrity validator.
- `GET /api/jobs` exposes local task status and sanitized command output.

`app/analysis.py` is an optional read-only integration boundary. Its data
builder reads SQLite through the existing query-only repository and sends
item-level accounting context, merchant names, totals, and payment aliases to
DeepSeek. It also precomputes product item leaderboards by order count, total
quantity, and spend; Scotiabank bank-only placeholder rows are excluded from
those product/item rankings. The context schema includes `schema_version`,
`generated_at`, scoped `data_quality`, and machine-readable `analysis_rules`.
Orders and items are typed so bank-recorded placeholders still contribute to
authoritative `orders.total`, merchant, and category analysis without being
treated as products. Missing or invalid quantities are marked partial and the
affected product is excluded from quantity rankings rather than defaulting to
one. Missing prices remain nullable and exclude the affected product from spend
rankings rather than becoming zero. Merchant/category/payment shares,
completed-month averages, category-by-month totals, same-day comparisons, and
a conditional low-confidence current-month projection are deterministic facts.
Current-month largest-transaction and category total/list questions are also
answered directly from a month-scoped fact set rather than delegated to the
model.
The model provides interpretation and suggestions, while a post-generation
validator rejects unsupported money, percentages, category-month mappings,
unqualified partial-month trends, and overconfident value judgments. The
generated timestamp is omitted from the content cache fingerprint so
unchanged fixed analyses still reuse cached responses. It also precomputes actual ledger date-range
spending averages so daily-average answers do not infer full months or future
dates from month labels. Superlative item and daily-average questions use the
computed facts instead of asking the model to count raw rows. It deliberately
omits source URLs, order
IDs, notes, browser state, and all payment-card digits, including last-four
values. Free-form chat selects month, year, or trend context from the question
and includes the user's current page-session history through the same read-only
boundary when a model response is needed. The API key remains
server-side in `DEEPSEEK_API_KEY`. A content-addressed cache under ignored
`.app-runtime\` is used for fixed monthly/yearly/trend analysis only. Free-form
chat turns are not cached server-side or stored in browser local storage, so
closing the `/analysis` page clears the conversation. The separate `/analysis`
assets do not share application state or JavaScript with the main ledger page.

## Module Contracts

### Personal bootstrap and migration

`scripts/bootstrap-expense-ledger.ps1` is the repeatable fresh-machine entrypoint.
It delegates Python environment repair to the existing app launcher, invokes
`scripts/maintenance/initialize_workspace.py` to create only missing runtime
directories and schema, checks Node.js and Chrome, and then runs the normal app
health check. Initialization never replaces an existing SQLite ledger. When a
moved workspace has no compatible local snapshot, bootstrap creates and verifies
a new baseline tied to the current workspace identity.

The isolated `.app-runtime` is also preferred by the weekly workflow, so app,
importer, reporting, and maintenance Python dependencies share one pinned
environment. System Python is only the seed needed to create that runtime.

Normal recovery snapshots remain workspace-bound. Cross-path migration requires
the separate `--allow-workspace-move` safety mode and confirmation word
`MIGRATE`, exposed through the export/import PowerShell wrappers. The same
checksum, staged configuration, SQLite validation, atomic replacement, and
rollback behavior is retained. Browser profiles and secrets are never members
of either backup format. Restore tracks the full managed JSON/CSV set and
removes stale files that are absent from the archive, with those deletions also
covered by rollback.

### Browser

`scripts/browser/` contains platform-neutral CDP utilities. `cdp-client.mjs`
owns the shared WebSocket connection, HTTP/CDP timeouts, and request lifecycle.
`collection-policy.mjs` owns bounded pacing, transient retry, block/login
classification, and per-platform circuit state. Other scripts may navigate,
click, scroll, or capture a sanitized visible page. They must only connect to
the dedicated CDP port and must not access Chrome profile files.
`sanitize.mjs` is the common persistence boundary for visible text and URLs;
it redacts contact, address, and card-like values and removes URL credentials,
fragments, and unapproved query parameters.

### Collectors

`scripts/collectors/` contains platform-specific browser behavior. Shared CSV
cache parsing lives in `csv.mjs`; platform DOM/XHR logic remains isolated in
each collector. A collector writes only sanitized accounting fields to `data/`
or temporary snapshots to `raw/`. Collectors run sequentially in the shared
browser. Confirmed cached evidence takes precedence over a later failed
response.

`config/collectors.json` is the formal `expense-collector/v1` component
manifest. `scripts/collectors/runner.py` validates the manifest, safely renders
standard context arguments, invokes collect/import phases, and writes
sanitized phase status. The weekly workflow discovers enabled order collectors
from this manifest rather than naming platform scripts. Browser tab metadata
comes from `config/platforms.json`. The complete extension contract is in
`docs/collectors.md`.

DOM-to-record normalization that can be isolated from the live page belongs in
small platform utility modules. Sanitized Walmart and Scotia fixtures exercise
those parsers directly, while the live scripts inject the same helper source in
the browser context.

Amazon Canada is the reference collector for a complete store adapter.
`export-amazon-orders.mjs` reads the website's default recent-order view and
applies the dynamic ledger overlap date locally. It uses stable IDs and
semantic labels first, known structural selectors second, and order
ID/ASIN/text heuristics last. The order-summary, payment, and purchased-item
regions are resolved independently so one drifting component does not corrupt
the others. Sponsored and recommendation links are rejected. Item prices are
treated as purchase-time values only when their sum reconciles to the stored
order subtotal. Sanitized orders are cached in `data/amazon_orders.json`,
item staging is isolated in `data/amazon_items.csv`, and card evidence reuses
the shared `data/payment_evidence.json` contract.
Pending and shipped orders use the same stable identity and are imported before
delivery. Their fulfillment state is updated in place on later collection and
is exposed by both the repository API and dashboard.

Collector policy deliberately stops instead of bypassing CAPTCHA/challenge
pages, login boundaries, HTTP 401, 403, or 429 responses. Explicit blocks open
a long cooldown. Timeouts, connection resets, and HTTP 5xx responses receive
limited exponential-backoff retries; repeated ordinary failures open a shorter
maintenance cooldown. Runtime state is sanitized and stored only under
`.codex/collector-health.json`.

### Importers

`scripts/importers/` parses one sanitized source format. Each importer:

1. normalizes order and item fields;
2. merges compatibility staging files with stable keys;
3. upserts affected SQLite orders and replaces only their item rows.

### Ledger

`scripts/ledger/schema.py` owns SQLite tables, indexes, and Power BI views.
`scripts/ledger/db.py` owns idempotent upsert behavior. `platform + order_id`
is the order uniqueness boundary. Item line keys are stable within an order.
SQLite writes use transactions and a busy timeout. Normalized CSV files are
written to temporary files, flushed, and atomically replaced so an interrupted
writer cannot truncate the previous good generation.
`scripts/ledger/payment_assignments.py` owns card matching and default
review-state behavior. `scripts/ledger/runtime_state.py` exposes the small
read-only order state needed by weekly collection windows, so orchestration
does not use compatibility CSV files as its runtime authority. Schema version
upgrades recreate Power BI views when their definitions change.

### Reconciliation

`scripts/reconciliation/` links sanitized bank transactions to orders. Bank
rows are read-only inputs. Clearly reversed charges are excluded, exact
platform/amount/date matches link to existing orders. The reconciler also
recognizes a group of two to four same-day platform charges as one split order
only when there is exactly one unused order within one day and the combined
amount is within the bounded weighted-item adjustment tolerance (2%, with a
$1 floor and $3 cap). It does not perform unrestricted fuzzy matching.
Unmatched net purchases create categorized bank-only ledger orders. Credits, correction
pairs, and transfers are not counted as expenses. Bank item staging is kept in
`data/bank_items.csv`, separate from delivery item staging. Merchant/category
rules are configured in `config/merchant_rules.json`. An unmatched charge that
looks like a supported platform order is held as `unmatched_platform_review`
instead of being imported; the local app exposes that queue read-only.

### Reporting

`scripts/reporting/export_reports.py` reads only SQLite and writes generated
views. `scripts/reporting/status_report.py` generates the concise run report
from the latest exported status. `scripts/reporting/validate.py` checks staging
and database duplicates, component totals, foreign-key relationships, payment
totals, and bank links. Every validator invocation atomically replaces
`exports/current/validation-status.json`; CLI, weekly refresh, and app-triggered
validation therefore expose the same current result.

`scripts/maintenance/validate_all.py` is the application-level validation
entrypoint. It combines the safety preflight, accounting validator, and latest
snapshot checksum verification. It also validates the collector component
manifest before reporting success. JSON collectors, Markdown reports, CSV
exports, and workflow state also use atomic replacement.

`scripts/run-stability-suite.ps1` is the read-only reliability entrypoint. It
repairs localhost startup when necessary, then runs regression tests, full
SQLite integrity and foreign-key checks, ledger invariants, functional
endpoint/security checks, and a bounded concurrent HTTP read test. The suite
asserts that the SQLite file is unchanged and applies latency, error, and
memory-growth thresholds. It never invokes collectors, authentication,
reconciliation, or AI analysis. Results are atomically written to
`exports/current/stability-status.json` and `.md`.

The weekly entrypoint writes `exports/current/run-state.json` before work
starts and marks it `passed` or `failed` at completion. This prevents a stale
successful report from being mistaken for the current automation result.

## Generated Output Policy

- `exports/current/`: current detailed views and weekly status.
- `exports/summaries/`: aggregate monthly, yearly, card, source, and category
  views.
- `exports/reconciliation/`: bank reconciliation evidence.
- `exports/periods/`: explicit month or date-range exports.

Generated reports are replaceable views. `data/expenses.sqlite` remains the
authoritative normalized ledger.
