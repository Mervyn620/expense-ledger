# Collector Component Contract

The store collection boundary is `expense-collector/v1`.

## Stable Interface

`config/collectors.json` is the executable manifest. Its shape is documented by
`config/collector-manifest.schema.json`, and
`scripts/collectors/runner.py` is the only orchestration-facing component
entrypoint.

Each enabled order collector declares:

- identity, display label, ledger platform, and dedicated-browser tab platform;
- supported capabilities;
- overlap-window and optional maximum-order configuration keys;
- a collect phase that writes only sanitized cache data;
- an import phase that idempotently upserts normalized orders and items;
- compatibility staging outputs.

Both phases receive a standard context:

| Value | Meaning |
|---|---|
| `port` | Dedicated shop-browser CDP port |
| `min_date` | Inclusive local incremental floor |
| `max_orders` | Optional bounded collection limit |
| `root` | Absolute workspace root |

Phase processes return `0` for success or no new data and nonzero for a
login, block, contract, or runtime failure. The runner writes a sanitized
status record to `.codex/collector-runs/<id>-<phase>.json`; it never stores
cookies, HTML, credentials, or complete command output.

Persisted visible-page text and URLs must pass through
`scripts/browser/sanitize.mjs`. The sanitizer removes common contact, address,
and card-like values plus URL credentials, fragments, and unapproved query
parameters. Collector summaries should be structured JSON; the runner compacts
pretty-printed JSON into a bounded, useful status message.

## Commands

```powershell
python .\scripts\collectors\runner.py validate
python .\scripts\collectors\runner.py list --role orders --enabled
python .\scripts\collectors\runner.py run --id amazon_ca --phase collect --min-date 2026-06-01 --max-orders 12
python .\scripts\collectors\runner.py run --id amazon_ca --phase import --min-date 2026-06-01
```

Use `--dry-run` to inspect the rendered entrypoint and arguments without
starting a component.

## Collection Runtime Backends

`config/collector_backends.json` defines the shared
`expense-collector-backends/v1` control plane for complete store order
collection. It does not change the component contract above, does not move the
ledger, and does not control local Walmart price checking.

The supported modes are:

| Mode | Behavior |
|---|---|
| `auto` | Prefer a VPS with `collect_bundle_v1`; otherwise use local |
| `local` | Require local order collection |
| `vps` | Require VPS for supported platforms; local-only overrides remain local |

Use `scripts/collectors/backend_control.py` to read status, change the persisted
selection, or resolve the backend that a task will lock at startup. Status
responses expose only labels, coarse health, capabilities, and sanitized
messages. SSH hosts, remote filesystem paths, credentials, and command output
are not returned through the app API.

The local backend runs the existing sequential collect phases and produces
sanitized caches. Walmart price checking is a separate local-only task that
reuses only a Walmart tab in the dedicated shop profile; it never navigates
Scotia, Amazon, The Ordinary, Fantuan, or Uber tabs. Import phases, ledger sync, price-result
persistence, reports, safety snapshots, and validation always run locally.
`data/expenses.sqlite` is the sole source of
truth and is neither uploaded to the VPS nor replicated.

Fantuan's sanitized cache retains the source status for audit, including
terminal cancellations. The importer excludes cancelled orders from accounting
and atomically removes a previously imported Fantuan order if a later live card
changes its status to cancelled. The collector refreshes statuses from the
order list without reopening already cached order details.

`config/workflow.json` lists Amazon and The Ordinary in
`on_demand_collectors`. The routine application action therefore collects only
Walmart, Uber Eats, and Fantuan; separate Amazon and The Ordinary buttons pass
an explicit platform selection. `config/collector_backends.json` lists The
Ordinary in `local_only_platforms`, so an Ordinary-only task uses the local
dedicated browser regardless of the global selector. Amazon continues to
follow that selector.

PowerShell orchestration must keep both local and VPS collector selections in
outer `@(...)` arrays. Without that boundary a one-platform selection becomes a
scalar object and `.Count` can incorrectly skip its collection branch. A new
selected run writes an exact-platform component summary. Only the explicit
collector-recovery path passes `--continue-recovery` and merges its result with
the prior partial recovery set.

The exact latest-run summary remains `.codex/collector-runs/vps-bundle.json`.
Unresolved actions are stored separately in the sanitized
`.codex/collector-runs/vps-recovery.json` backlog. Each new VPS attempt updates
only its requested platforms: a failure or login boundary remains actionable
across unrelated runs, while a live success removes that platform. This keeps
the latest-run report truthful without losing an older unresolved login.

Amazon collection includes orders that the account page marks pending or
shipped. The sanitized cache and importer preserve that fulfillment lifecycle;
a later run updates the same order to delivered or cancelled by stable order
ID. The UI therefore exposes pre-delivery purchases without treating them as
missing.

The VPS-side `vps_backend_service.py` implements sanitized status,
`collect_bundle_v1`, `collect_bundle_v2`, and `tls_diagnostics_v1`. A backend is
selectable only when it reports `ready`, the required collection capability,
configured independent authenticated profiles, and the required browser, Xvfb, Node, and
collector-source runtime. Merely reaching the SSH host is not sufficient.

`scripts/collectors/vps_bundle_client.py` is the local transport boundary. It
opens a temporary SSH reverse SOCKS listener bound to VPS loopback, verifies
that the home exit is reachable, and sends one strict request containing only
the configured date windows, record limits, and these allowlisted sanitized
seeds:

- `data/transactions.csv`
- `data/walmart_items.csv`
- `data/amazon_orders.json`
- `data/the_ordinary_orders.json`
- `data/payment_evidence.json`
- `data/fantuan_orders.json`
- `data/uber_eats_receipts.json`
- `.codex/walmart-product-link-attempts.json`

The remote service refuses unknown fields, paths, oversized files, and invalid
date or limit values. It runs each platform profile sequentially, then returns
only allowlisted sanitized JSON caches and raw collector snapshots in an
`expense-collector-bundle/v1` ZIP. The local client verifies the SHA-256 digest,
archive size, normalized member paths, symlink absence, per-member size, and
JSON syntax before applying files atomically with rollback. SQL, Scotia data,
credentials, browser profiles, raw authenticated pages, and arbitrary files
are excluded from both directions.

`vps_bundle_client.py diagnose` runs a separate read-only transport check. It
performs bounded TLS handshakes to the five allowlisted public store hosts from
the local route and from the VPS through the temporary reverse SOCKS home exit.
The ignored `.codex/tls-consistency.json` output retains only TLS version, ALPN,
cipher strength, certificate lifetime, timing, and browser/runtime versions.
It never requests an authenticated path, records a public IP, or inspects a
browser profile. The check also flags a browser-major gap greater than one and
clock skew greater than two minutes.

A `partial` bundle means the validated transport and allowlisted outputs
succeeded while at least one component did not. The local client applies that
usable bundle successfully and preserves the failed component's sanitized run
status; current-run reporting surfaces that component directly instead of
mislabeling the entire transport as a Python process failure.

Scotiabank is intentionally outside the dual-backend selector and remains a
local read-only reconciliation source. Cookie databases and browser profiles
must never be copied between local and VPS environments. Each backend uses its
own manually authenticated browser sessions.

## Adding A Store

1. Add its login/order URL to `config/platforms.json`.
2. Add one collector entry to `config/collectors.json`.
3. Implement a collector that writes a sanitized cache.
4. Implement an importer that uses `platform + order_id` uniqueness and stable
   item keys.
5. Move testable DOM-row normalization into a small utility module and add
   sanitized parser/importer fixtures.
6. Run the manifest validator and Node/Python regression suites.

The weekly workflow discovers the component automatically. It should not need
a platform-specific PowerShell edit.

## Reliability Boundary

The manifest improves configuration consistency, path safety, discoverability,
failure isolation, and maintenance visibility. It does not make unstable DOM
selectors stable by itself. Each adapter must still use semantic selectors,
fallbacks, reconciliation checks, polite pacing, and login/challenge stopping.

Scotiabank remains outside this store collector contract. It is a read-only
bank reconciliation source with different authentication and retention rules.

The Ordinary adapter uses `.account-order` cards and stable `data-order-id`
attributes from `https://theordinary.com/en-ca/orders`. For each bounded
candidate it requests the same-origin `Order-DetailsDrawer` endpoint from the
authenticated page context, with sequential pacing and the shared retry/cooldown
policy. The collector parses only totals, status, product names, official
product links, quantities, and optional card-network/last-four evidence. It
does not persist the detail HTML, addresses, contact details, request headers,
or browser authentication state.

The Walmart adapter normally stops after two consecutive pages older than the
incremental date floor, provided observed dates remain descending, all cards
have dates and stable IDs, and all known recent orders and pending product-link
backfills have been seen. Unknown dates/IDs or observed date disorder disable
this shortcut for the run. The first run and runs at least seven days after the
last complete history scan traverse the full list; `--full-history` forces this.
The local `.codex/walmart-history-scan.json` timestamp is recorded only after a
successful run that reached the end of history. Delivery-date ordering is a
heuristic, so periodic full scans remain necessary. The existing per-run order
limit and product-link backfill behavior still apply. It processes only cards with stable Walmart order IDs,
so repeated per-fulfillment "View details" controls cannot become duplicate or
misindexed orders. When available, the sanitized order snapshot also includes
the visible charge history used to diagnose temporary holds, weighted-item
adjustments, and split final charges.
Purchased-item containers also provide canonical product names and links.
Only `walmart.ca` `/ip/` links are accepted; query strings and fragments are
removed and recommendation links are excluded. Historical orders without any
saved link are backfilled within the normal per-run order cap, with local
attempt state preventing repeated visits to unavailable products.

## Collection performance (Unreleased)

Amazon still reads the current list cards on every run. Complete delivered
details are reused only when the card's ID, date, total, status and item
fingerprint are unchanged and the last actual detail fetch is less than 24 hours
old. Legacy/incomplete caches, changed cards, in-transit orders and visible
return/refund states are fetched again. Cache hits do not extend the expiry or
rewrite payment-evidence capture timestamps. `--refresh-all` bypasses reuse.
This helps repeated collections within a day; daily/weekly collections still
perform detail rechecks. Rechecks apply to orders within the requested date
window and order/page limits, not to the entire retained cache.

Walmart list/detail navigation, Walmart pagination and Amazon list navigation
wait for accounting content to appear and remain stable across two observations
250 ms apart instead of always sleeping through a fixed settling interval.
Navigation ignores the old document until it has been replaced; pagination
requires changed content even when the URL stays the same. Login/challenge
classification, action pacing, bounded retries and cooldowns remain active.
Empty/unready content times out instead of becoming a successful empty capture.
Amazon accepts an explicitly rendered empty-history message.

Collector health summaries include Walmart `pages`, `stop_reason` and
`full_history`, and Amazon `detail_requests` and `reused`. Compare these alongside
run duration on subsequent live runs; fixture reductions are not live timings.
The VPS release allowlist includes the shared readiness module, while previous
exact release file sets remain accepted for rollback. Local code changes do not
deploy the running VPS service.
