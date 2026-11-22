# Expense Ledger

This directory is the integrated backend and WebView content for the current
Windows client. Use the parent project's `Start-Expenses.ps1` and read
`../AGENTS.md`; this is not a parallel primary web application. Commands below
are backend maintenance interfaces and must be run from this directory.

Local bookkeeping for Walmart Canada, Amazon Canada, Uber Eats, Fantuan, The
Ordinary Canada, and read-only Scotiabank reconciliation.

Project release history, including the recovered pre-Git implementation phase,
is maintained in `CHANGELOG.md`. Its evidence boundaries are documented in
`docs/release-history-evidence.md`. Operational state and handoff notes remain
in `MAINTENANCE_LOG.md` and `AGENT_HANDOFF.md`.

## Quick Agent Takeover

The ledger, analysis and price-watch pages share an **外观** button for wallpaper
and color selection. Four bundled high-resolution wallpapers can follow their paired theme or
use an independent color. Preferences save in this browser; increased card
opacity, reduced motion and restore-default controls are included. Design and
extension guidance: [docs/appearance.md](docs/appearance.md).

For a new Codex conversation, start with:

- `AGENT_HANDOFF.md`: concise operating instructions and safety boundaries.
- `MAINTENANCE_LOG.md`: current maintenance state, upgrade progress, and next
  actions.

## Safety Boundary

- Browser automation uses only `.codex-shop-browser/chrome-profile` on CDP port
  `9223`.
- The normal Chrome profile is never read, copied, restarted, or used.
- Logins, passwords, one-time codes, cookies, balances, and raw banking HTML are
  not stored.
- Browser collectors retain only sanitized accounting fields.

## Main Command

On a new Windows installation or after moving this folder, run the personal
bootstrap first:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-expense-ledger.ps1 -InstallShortcut
```

The bootstrap is repeatable and preserves an existing ledger. It creates or
repairs the isolated Python runtime, initializes a missing empty SQLite ledger,
checks Node.js and Chrome availability, validates localhost startup, creates a
verified recovery baseline for the current workspace identity when needed, and
writes a sanitized status report to `.codex\bootstrap-status.json`. Add
`-OpenLoginPages` to open supported store and bank pages in a new dedicated
browser profile. Website passwords, CAPTCHA, and two-factor verification remain
manual. Python 3.11+ is the seed prerequisite; browser collection additionally
needs Node.js 20+ and Google Chrome.

Open the local application:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-expense-app.ps1
```

The app runs only on `http://127.0.0.1:8765`. It provides monthly summaries,
paginated order and item details, product-name search, payment-source
breakdowns, a read-only queue of Scotia platform charges held for review,
controlled refreshes, and one-click integrity validation. The app reads SQLite
through query-only connections. Imported orders, payments, and bank transactions
still change through the weekly workflow. User actions may edit cash notes or
explicitly link/unlink bank-backed refunds; they cannot overwrite source amounts.

The trend chart switches between total spending, selected platforms and selected
payment methods. Open the selection control to search/check one or more series;
choices persist locally. Clicking a month (or pressing Enter/Space on it) changes
the dashboard month while preserving historical lines. Checkboxes only change
the chart. Payment comparisons attribute each net order to its primary method,
consistent with the existing breakdown. See `docs/trend-interaction-plan.md`.

The daily dashboard focuses on spending, with common refresh actions under
**更新**. Diagnostic tools, detailed task logs, recent changes, backend selection,
and **数据检查状态与统计口径** are under **维护** (sidebar bottom, or the Update
footer on mobile). This panel separates last purchase from last successful
collection/import. Order details keep user-confirmed refund controls collapsed;
full per-order audit history remains in the backend API. Totals there and in AI analysis are **net spending by
purchase month**; existing CSV/Power BI exports keep original gross order totals.
See [ledger reliability](docs/ledger-reliability.md) for the exact accounting
boundary and limitations.

The **分析** entry opens the separate `/analysis` page. It offers monthly,
yearly, trend, and free-form ledger chat through DeepSeek without coupling AI
calls to the ledger UI or write pipeline. The backend precomputes product-item
leaderboards for purchase count, quantity, and spend before prompting the model
so factual ranking questions do not depend on the model counting raw rows.
Merchant, category, platform, payment, completed-month average, and same-day
partial-month comparisons are also computed locally. The current incomplete
month is excluded from monthly averages; an optional month-end projection is
shown separately as a low-confidence conditional estimate.
Scotiabank bank-only placeholder rows are excluded from those product rankings.
The model context carries a versioned schema, generation timestamp, scoped data
quality counts, and explicit analysis rules. Bank-recorded placeholders remain
available for total, merchant, and category spending but are typed as
`bank_placeholder` and cannot enter product rankings. Missing or invalid item
quantities are reported as incomplete and excluded from quantity rankings
instead of being inferred as one.
Missing item prices remain unknown and are excluded from item-spend rankings
instead of being converted to zero. Fixed analysis output is checked against
the deterministic fact set before display, and unsupported numeric or
overconfident claims are hidden with a visible limitation note.
Daily-average spending questions are also answered locally from the actual
first and latest ledger dates, not inferred from month labels.
Current-month largest-transaction and category spend/list questions are
likewise computed locally with an explicit as-of date for an incomplete month,
so they do not rely on the model to count rows.
Configure the key once with a hidden prompt:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\configure-deepseek.ps1
```

The configuration script restarts the app unless `-NoRestart` is passed. The
key is read only by the local backend from the current Windows user's
environment and is never returned to the browser. The request includes
accounting fields useful for recommendations: merchant, item names and prices,
order fees, platform, and payment aliases. It excludes order IDs,
source/product links, notes, login data, cookies, and all card digits, including
last-four values.
Content-addressed monthly/yearly/trend analysis responses are cached under
ignored `.app-runtime\` so unchanged ledger data does not consume API tokens
again. Free-form chat history is page-scoped only: the open `/analysis` tab
passes recent turns as context, but chat history is not written to local
storage or the server cache and is cleared when the page is closed. Override
the default `deepseek-v4-flash` model with `DEEPSEEK_MODEL`.

Create a desktop shortcut once:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-expense-app-shortcut.ps1
```

Stop the background app:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop-expense-app.ps1
```

Check startup health or repair a stopped app:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-expense-app.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-expense-app.ps1 -Repair
```

Startup protection includes:

- an isolated `.app-runtime` Python environment with automatic dependency
  installation when needed, a pinned transitive dependency lock, and a manifest
  hash that triggers repair only when dependencies change;
- a per-workspace single-instance lock for repeated or simultaneous clicks;
- stale PID/state detection and restart recovery;
- automatic fallback from port `8765` through `8774` when another program owns
  the preferred port;
- an application identity and real service PID in `/api/health`;
- startup state and diagnostic logs under `.codex\`.

Ledger protection includes:

- a cross-process refresh lock, so the app, automation, and manual commands
  cannot write the ledger concurrently;
- a safety preflight for disk space, writable directories, JSON configuration,
  SQLite integrity, foreign keys, and required tables;
- verified snapshots before each refresh and after each successful validation,
  retained under ignored `backups\` with a maximum of 24 archives;
- atomic replacement for normalized CSV/JSON caches, reports, and workflow
  status files;
- a 10-second SQLite busy timeout and a 15-minute background-task timeout with
  full child-process cleanup;
- loopback Host/Origin checks and restrictive browser security headers.

Run the same full one-click validation used by the application:

```powershell
python .\scripts\maintenance\validate_all.py
```

Run the non-destructive application stability suite:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-stability-suite.ps1
```

This combines Python and Node regression tests, SQLite `integrity_check` and
`foreign_key_check`, ledger invariants, localhost HTTP/security probes, and a
bounded concurrent read test with latency and process-memory thresholds. It
never starts collectors, browser login, bank reconciliation, or DeepSeek
analysis. Current results are written to
`exports/current/stability-status.json` and `.md`; the latest 100 compact runs
are retained under ignored `.codex\stability-history.json`.

Verify the latest recovery snapshot:

```powershell
python .\scripts\maintenance\safety.py verify
```

Emergency restore is explicit and never automatic. Stop the local app first,
then provide a verified archive:

```powershell
python .\scripts\maintenance\safety.py restore --path .\backups\expense-ledger-YYYYMMDD-HHMMSS-post-refresh.zip --confirm RESTORE
```

Restore creates a `before-restore` rollback snapshot when the current database
is still readable, validates the staged database/configuration, and rolls back
files if replacement fails. A successful restore also removes stale managed
JSON/CSV files that are not present in the selected archive, so old caches do
not leak into the restored state. Snapshots contain only normalized `data\` and
`config\` files; browser profiles, cookies, raw pages, and exports are excluded.

Create a portable personal profile before moving to another computer or path:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\export-expense-profile.ps1
```

By default this writes a verified ZIP under the current Windows user's
`Documents\Expense Ledger Backups` folder. It contains the normalized SQLite
ledger and JSON/CSV configuration, including card aliases, but excludes the
dedicated browser profile, cookies, API keys, raw pages, logs, and generated
exports. Treat the ZIP as private financial data.

On the initialized target workspace, import it explicitly:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\import-expense-profile.ps1 -Path "C:\path\ExpenseLedgerProfile.zip" -Confirm
```

Import validates checksums, configuration, and SQLite integrity and creates a
rollback snapshot before replacement. Cross-path imports require the explicit
workspace-move confirmation built into the wrapper and establish a compatible
baseline in the target workspace. Store and bank websites must be logged in
again because browser credentials are intentionally never migrated.

Refresh existing local data, rebuild the SQLite ledger, export reports, and
validate dedupe:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\weekly-expense-refresh.ps1
```

Collect recent shop orders first:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\weekly-expense-refresh.ps1 -CollectFromBrowser
```

Order collection supports two runtime backends:

```powershell
python .\scripts\collectors\backend_control.py status --refresh
python .\scripts\collectors\backend_control.py select --mode auto
python .\scripts\collectors\backend_control.py select --mode local
python .\scripts\collectors\backend_control.py select --mode vps
python .\scripts\collectors\vps_bundle_client.py diagnose
```

The final command runs a read-only TLS consistency check across Walmart,
Amazon, The Ordinary, Uber Eats, and Fantuan. It compares the local route with the temporary
VPS home-exit route and atomically writes the sanitized result to
`.codex\tls-consistency.json`. The result contains protocol, ALPN, certificate
lifetime, timing, and runtime-version metadata only; it does not fetch account
pages or retain IP addresses, cookies, credentials, or page content.

The dashboard header shows one simple **VPS / 本地** button for collectors that
support both environments. Each collection task locks its resolved backend at
start. The routine action collects Walmart, Uber Eats, and Fantuan. Amazon and
The Ordinary are deliberately excluded from routine runs and have separate
on-demand buttons for use only after a recent purchase.
`auto` remains available to scripts for compatibility and prefers a VPS when
it reports `collect_bundle_v1`; otherwise it uses the local collector. An
explicitly selected unavailable environment is rejected instead of silently
running elsewhere.

The selector never changes storage. Sanitized collector output, imports, and
the authoritative `data/expenses.sqlite` all remain local; the
database is never uploaded or replicated. Scotia collection and reconciliation
remain local. Browser profiles, cookies, passwords, and raw pages are not
synchronized between environments; each collection environment has its own
manually authenticated sessions.

The current VPS deployment reports `ready` with `collect_bundle_v1`,
`collect_bundle_v2`, and sanitized storage health. Amazon, Fantuan, Uber Eats,
and Walmart use separate manually authenticated, owner-only browser profiles
on the VPS. The Ordinary is local-only because its Google OAuth confirmation
was not sufficiently stable on the remote profile. A remote collection opens
a temporary loopback-only home-exit tunnel, runs only the requested VPS-capable collectors
sequentially, returns a checksum-verified ZIP containing only allowlisted
sanitized JSON, and then closes the browser, CDP, VNC, and proxy listeners.
`auto` therefore prefers the VPS while it remains healthy; if it is unavailable,
`auto` falls back to the complete local collector. Explicit `vps` mode still
fails closed instead of silently changing execution environments.

The remote service never receives `data/expenses.sqlite`, Scotia data, local
browser profiles, passwords, or arbitrary project files. The local client
validates the response contract, SHA-256 digest, ZIP member paths, file sizes,
and JSON payloads before atomically replacing the allowlisted caches. A partial
remote run may return usable component output. Transport remains successful so
validated caches can be imported, while the bundle, workflow, job, reports,
toast, and dashboard remain explicitly `partial`. Every component states
whether its data is live, preserved cache, or unavailable.

When the latest VPS run is partial, the Run page groups primary and payment
components by platform. Failed VPS-capable platforms can be retried without
rerunning the other stores. A `login_required` platform exposes **打开 VPS 登录** and
**验证并重试**: the first opens only the loopback VNC session, and the second
verifies the authenticated page, closes the session/tunnel, and starts one
bounded platform retry through the normal import, SQLite, report, and validation
pipeline. Blocked or cooling-down platforms are not blindly retried.

VPS collector releases are immutable and atomically switched. Inspect the
exact non-secret source allowlist without contacting the VPS:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy-vps-collector.ps1 -Plan
```

After committing and validating a clean worktree, omit `-Plan` to stage,
checksum, validate, activate, and health-check the release. A failed post-switch
health check restores the previous `current` symlink. Rollback requires an
explicit validated release ID. Profiles, runtime, logs, cookies, SQLite, Scotia
data, and other local data are never release members.

Also reconcile sanitized Scotiabank transactions:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\weekly-expense-refresh.ps1 -CollectFromBrowser -ReconcileScotia
```

The application's Scotia action is a two-stage button. The first click opens
the official login page in the dedicated shop profile and allows Chrome's
password manager to autofill it. The login helper only observes whether the
relevant fields are filled; it never reads, exports, logs, or stores their
contents. It submits only a completely filled login step and stops at
two-factor verification, CAPTCHA, or another security challenge. After manual
verification, the same button's second click runs Scotia-only reconciliation
without rerunning any store collector.

Open the dedicated login/order tabs without collecting:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\weekly-expense-refresh.ps1 -OpenLoginPages
```

The default collection floor is May 1, 2026. Incremental runs use overlapping
windows so late-posted orders are still detected.

Workflow dates, overlap windows, port, and collector limits are configured in
`config/workflow.json`.

## Collector Reliability

Collectors reuse the manually logged-in dedicated Chrome profile rather than
creating a second browser identity. The weekly workflow operates one platform
at a time and only requests recent records that are not already normalized.

The shared policy in `scripts/browser/collection-policy.mjs` provides:

- bounded action pacing and page-settle jitter to avoid synchronized bursts;
- limited retries only for timeouts, connection resets, and HTTP 5xx failures;
- explicit TLS, certificate, proxy, DNS, timeout, and connection error
  classification; certificate and TLS protocol failures are not retried;
- immediate stop on login pages, CAPTCHA/challenge pages, HTTP 401, 403, or 429;
- a per-platform circuit breaker under `.codex\collector-health.json`;
- shorter maintenance cooldowns after repeated ordinary failures;
- configurable order caps, CDP timeouts, backoff, and cooldown values in
  `config/workflow.json`;
- stable-cache preference, so a failed refresh cannot replace previously
  confirmed payment evidence.

Visible-page snapshots and collector text pass through the shared accounting
sanitizer, which removes contact details, addresses, card-like numbers, URL
credentials, fragments, and unapproved query parameters before persistence.
Sanitized Walmart and Scotia fixtures cover their pure parsers without using a
live account.

This policy does not solve CAPTCHAs, spoof browser fingerprints, rotate
proxies, or copy cookies. A blocked platform is left idle until its cooldown
expires; a login-required platform is left for manual login in the dedicated
browser. Collector health is exported to `weekly-status.json`,
`weekly-status.md`, and `run-report.md`.

## Source Of Truth

`data/expenses.sqlite` is the normalized accounting ledger and report source.

The database contains:

- `orders`: one row per `platform + order_id`.
- `order_items`: one-to-many item rows with stable line keys.
- `order_payments`: payment/card assignment rows.
- `payment_evidence`: sanitized payment method and last-four evidence.
- `cards`: configured local card aliases.
- `bank_accounts`, `bank_transactions`, `bank_reconciliations`: sanitized
  read-only bank reconciliation data.
- `cash_withdrawal_notes`: user-entered purpose notes attached to detected ATM
  withdrawals. These annotations remain outside order and overview totals.

Files under `data/` are sanitized collector caches and compatibility staging
files. Importers merge them idempotently, then `scripts/ledger/sync.py`
reconciles the complete SQLite view.

## Module Layout

- `scripts/weekly-expense-refresh.ps1`: stable orchestration entrypoint.
- `scripts/bootstrap-expense-ledger.ps1`: repeatable personal setup and
  dependency doctor.
- `scripts/export-expense-profile.ps1`, `scripts/import-expense-profile.ps1`:
  checked cross-machine ledger/configuration migration.
- `app/analysis.py`: read-only AI context and DeepSeek client.
- `app/analysis_cache.py`: content-addressed fixed-analysis cache.
- `app/settings.py`: shared application identity, version, and service defaults.
- `app/static/analysis.*`: standalone analysis page.
- `scripts/start-shop-browser.ps1`, `scripts/stop-shop-browser.ps1`: public
  dedicated-browser controls.
- `scripts/browser/`: generic CDP page/tab/click utilities.
- `scripts/collectors/`: platform-specific browser collectors using the shared
  pacing, retry, and circuit-breaker policy.
- `scripts/deploy-vps-collector.ps1`: plan, deploy, and rollback immutable VPS
  collector releases with staged validation and atomic activation.
- `scripts/price_watch/`: active local on-demand checks plus retained, inactive
  VPS rollback tooling and result-contract validation.
- `scripts/importers/`: sanitized source-to-ledger importers.
- `scripts/ledger/`: schema, upsert, payment-assignment, and full-sync logic.
- `scripts/reconciliation/`: bank-to-order matching.
- `scripts/reporting/`: SQLite report export and integrity validation.
- `scripts/maintenance/`: preflight, verified backup, restore, and aggregate
  one-click validation.
- `tests/`: focused dedupe, parsing, payment, and reconciliation tests.

See `docs/architecture.md` for module contracts and data flow.

## Outputs

Current bookkeeping views:

- `exports/current/accounting-dashboard.md`
- `exports/current/accounting-ledger.csv`
- `exports/current/card-order-items.csv`
- `exports/current/card-assignment-review.csv`
- `exports/current/card-order-detail.md`
- `exports/current/weekly-status.json`
- `exports/current/weekly-status.md`
- `exports/current/run-state.json`
- `exports/current/run-report.md`
- `data/walmart_deals_local.json`: latest validated local Square One Walmart
  deals.

`run-state.json` is written at workflow start and completion. Check it before
trusting an older report after an interrupted or failed automation run.

Aggregate reports:

- `exports/summaries/yearly-summary.csv`
- `exports/summaries/monthly-summary.csv`
- `exports/summaries/monthly-card-summary.csv`
- `exports/summaries/monthly-category-summary.csv`
- `exports/summaries/card-source-summary.csv`
- `exports/summaries/card-category-summary.csv`
- `exports/summaries/card-spending-summary.md`

Bank reconciliation:

- `exports/reconciliation/scotia.csv`
- `exports/reconciliation/scotia.md`

The Scotia reconciler excludes credits, purchase/correction pairs, and Interac
transfers. It links exact Walmart/delivery-platform charges to existing orders.
For weighted-item or split settlements, it links two to four same-day charges
only when their combined amount is close to one unique order within one day;
the bounded tolerance is 2% with a $1 floor and $3 cap. It does not use broad
fuzzy matching. The reconciler then imports unmatched net purchases such as subscriptions, telecom, dining,
software, retail, coffee, and bank fees as categorized bank-only expenses.
Unmatched charges that appear to belong to a supported order platform are held
out of the ledger and shown in the app's **待核对平台扣款** table instead.

Exporting a month or custom range writes to a separate period directory and
does not overwrite the full current view:

```powershell
python .\scripts\reporting\export_reports.py --month 2026-05
python .\scripts\reporting\export_reports.py --start-date 2026-05-01 --end-date 2026-05-31
```

## Browser And Collector Commands

Start or stop the isolated browser:

```powershell
.\scripts\start-shop-browser.ps1 -Platform walmart_ca
.\scripts\stop-shop-browser.ps1
```

Capture a sanitized visible page for diagnosis:

```powershell
node .\scripts\browser\export-visible-page.mjs --match walmart --platform walmart_ca
```

Platform collectors:

```powershell
python .\scripts\collectors\runner.py validate
python .\scripts\collectors\runner.py list --role orders --enabled
python .\scripts\collectors\runner.py run --id amazon_ca --phase collect --min-date 2026-05-01 --max-orders 12
python .\scripts\collectors\runner.py run --id amazon_ca --phase import --min-date 2026-05-01
python .\scripts\collectors\runner.py run --id the_ordinary_ca --phase collect --min-date 2026-05-01 --max-orders 12
python .\scripts\collectors\runner.py run --id the_ordinary_ca --phase import --min-date 2026-05-01
node .\scripts\collectors\export-uber-eats-receipts.mjs --min-date 2026-05-01
node .\scripts\collectors\export-payment-evidence.mjs --min-date 2026-05-01 --platforms walmart_ca,fantuan
node .\scripts\collectors\export-scotia-transactions.mjs --min-date 2026-05-01
```

Order collectors are declared in `config/collectors.json` under the
`expense-collector/v1` contract. The weekly workflow discovers enabled order
collectors from this manifest. See `docs/collectors.md` before adding a store.

The Amazon collector leaves the website's default order-history range unchanged
and applies the ledger overlap date locally. It stores only sanitized order,
purchase-time item price, and card-network/last-four fields. Item prices are
accepted only from the purchased-items area when they reconcile to the order
subtotal; current product-page and recommendation prices are ignored.
Orders that have not arrived yet are retained with `pending` or `shipped`
fulfillment and appear as `未送达` or `已发货` in the application. They remain
part of purchase accounting because Amazon has already recorded the order;
later collections update the same stable order instead of creating a duplicate.

The Ordinary on-demand collector always uses the local dedicated browser. It
reads stable `data-order-id` values from the authenticated
order-history cards and requests the site's same-origin order-detail drawer in
sequence. It stores only normalized totals, status, purchased item names,
official product links, and optional card network/last four. Shipping and billing
addresses, phone numbers, email addresses, full card data, request headers, and
raw HTML are excluded. Repeated imports replace the affected order's item rows
transactionally and remain unique by `the_ordinary_ca + order_id`.

Scotia collection is read-only and stores no balances or authentication data.

Walmart order snapshots retain canonical purchased-product names and `/ip/`
links. The separate **查折扣** window always uses the local dedicated browser,
reads the authenticated Walmart **My Items** page once, and accepts only
rendered current/`Was` price pairs after exact local name matching. It does not
follow the order backend selector. Both VPS price timers and the Windows
sign-in receiver are disabled; see `docs/price-watch.md` for the verified
retirement decision and local failure contract.

## Import And Validation Commands

Individual importers are available for diagnosis and fallback work:

```powershell
python .\scripts\importers\walmart_cdp.py
python .\scripts\importers\amazon.py
python .\scripts\importers\the_ordinary.py
python .\scripts\importers\uber_eats.py --min-date 2026-05-01
python .\scripts\importers\fantuan.py
python .\scripts\importers\uber_receipt_payments.py
python .\scripts\importers\payment_evidence.py
python .\scripts\reconciliation\scotia.py
```

Walmart XLSX fallback:

```powershell
python .\scripts\importers\walmart_xlsx.py --input .\downloads
```

Rebuild and verify:

```powershell
python .\scripts\ledger\refresh_assignments.py
python .\scripts\ledger\sync.py
python .\scripts\reporting\export_reports.py
python .\scripts\reporting\validate.py
python -m unittest discover -s tests -v
```

## Payment Cards

Configured aliases live in `config/payment_cards.json`:

- `mc_1111`: Mastercard ending 1111.
- `scotia_visa_2222`: Scotiabank Visa ending 2222.
- `scotia_chequing_3333`: sanitized Scotiabank chequing-account source.
- `wechat_pay`: WeChat Pay as its own payment source.
- `alipay`: 支付宝 as its own payment source for matching Fantuan evidence.
- `unassigned`: payment source still needing review.

`data/payment_assignments.csv` remains the editable compatibility layer for
manual confirmations. Automated evidence updates it when a known network and
last four digits are available.

## Classification Rules

Scotia bank-transaction merchant and category rules live in
`config/merchant_rules.json`. Add new bank-only merchant classifications there
instead of editing Python code.

## Temporary Data

`raw/`, `downloads/`, Python caches, and test caches are temporary. Successful
weekly runs remove sanitized raw platform snapshots unless
`-KeepRawSnapshots` is explicitly used.
