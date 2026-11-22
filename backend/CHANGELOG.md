# Expense Ledger Changelog

This file is the canonical project-level release and upgrade history. It follows
the spirit of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses
semantic versioning for releases made after this log was introduced.

The repository did not previously have release tags. Git proves that
`package.json` was introduced at `1.0.0`, remained there through commit
`019a92d`, and changed to `1.17.0` in checkpoint commit `323f043`. The
intermediate `1.15.0` through `1.16.1` working-version milestones were not
committed independently. Consequently, the dated commits after `1.0.0` are
preserved below as **unversioned development history** instead of being assigned
invented `1.1.0` through `1.14.0` numbers. Development before Git was initialized
has been recovered from the original Codex task records and is preserved as
**pre-Git development history** for the same reason.

## Release Index

| Version or phase | Date | Status | Evidence |
|---|---|---|---|
| 1.21.0 | 2026-11-21 | Optional parallel store collection; tag pending | Synchronized version files, regression tests, two live parallel refreshes |
| 1.20.0 | 2026-11-21 | First-run setup and empty-workspace start; tag pending | Synchronized version files, regression tests, isolated fresh-workspace desktop verification |
| 1.19.0 | 2026-11-21 | Bank adapters and OFX/CSV statement import; tag pending | Synchronized version files, regression tests, real-ledger reconciliation comparison |
| 1.18.1 | 2026-09-02 | Previous validated release candidate; tag pending | Synchronized version files, regression tests, and live collector validation |
| 1.18.0 | 2026-08-12 | Previous validated release candidate; tag pending | Synchronized version files and retained validation records |
| 1.17.0 | 2026-07-25 | Previous tagged release | Git tag `v1.17.0` and synchronized version files |
| 1.16.1 | 2026-07-25 | Recorded intermediate milestone; no dedicated commit or tag | Retained version, validation, and task records |
| 1.16.0 | 2026-07-25 | Recorded intermediate milestone; no dedicated commit or tag | Retained version, validation, and task records |
| 1.15.0 | 2026-07-18 | Recorded intermediate milestone; no dedicated commit or tag | Retained version, validation, and task records |
| Unversioned development | 2026-06-28 to 2026-07-01 | Committed after 1.0.0 without version bumps | 11 Git commits |
| 1.0.0 | 2026-06-27 | First versioned local application | `fd69c1b` through `0429599` |
| Pre-versioned baseline | 2026-06-24 | Initial maintenance repository | `5fd36a1` through `703d8e5` |
| Pre-Git development | 2026-05-30 to 2026-06-22 | Recovered original implementation history | Original Codex tasks and recorded validations |

## Evidence And Versioning Boundaries

- Git commits are the canonical evidence for changes that were committed.
- The `1.15.0` through `1.16.1` intermediate working-version milestones are
  supported by retained version, validation, and task records, but are not
  independently recoverable as Git revisions.
- The 2026-05-30 through 2026-06-22 history was recovered from original Codex
  task records. Recorded patch applications, final reports, and test results
  were cross-checked against the files that entered the first Git baseline.
- No version number is assigned retrospectively without a contemporary version
  record. Dated phases below are historical milestones, not invented
  semantic-version releases, and versions without a dedicated revision are
  identified explicitly.
- Only sanitized engineering history is copied here. Browser sessions,
  credentials, raw page data, and private accounting records remain outside
  the release log. See `docs/release-history-evidence.md` for provenance.

## Unreleased

### Fixed

- Monthly charts defer drawing while their container is hidden and observe actual
  container size changes. Initial layout, returning to Overview and parent resizing
  now redraw at the available width without requiring a zoom click. Size updates
  are coalesced and unchanged dimensions do not trigger redraw loops.

- Store refresh now records its preparation stage immediately and bounds backend
  checks (45 seconds) and ledger preflight/backup (120 seconds). Process cleanup
  also has finite waits, so a stalled check cannot leave the task loading forever.
- Refresh failures before script initialization replace stale success state using
  the new job's timestamps, while preserving current script failure details.

### Changed

- Route conversational questions such as “九月的钱花在哪了”, spending breakdowns, explanations and saving advice to interpretation instead of treating every month mention as a total request. Explicit amount questions retain the fast local calculation path.
- Give chat full-scope platform/category concentration and largest-order facts, guidance to explain spending patterns before offering optional actions, and a labelled local breakdown when AI returns only a total or fails fact validation. Preserve date, platform and requested category scope, refund accounting and incomplete-month safeguards.
- Allow one bounded rewrite of an invalid or incomplete AI spending breakdown before falling back, retaining strict numeric fact references and readable platform names.
- Preserve verified explanations after that rewrite by dropping whole invalid prose paragraphs, with an explicit filtered-validation status; unknown facts and insufficient explanations still fall back. Validate the provider path with a synthetic temporary ledger.

- Compact the AI analysis page with a content-sized result panel, an illustrated empty state, and selectable analysis cards that immediately name the requested period while loading. Preserve explicit pending, success, configuration, and failure states.
- Replace the oversized question form with a single-line composer that grows with text, a compact send control, and Ctrl/Cmd+Enter submission. Hide the repeated ledger range until a response has meaningful metadata; share dashboard glass tiers and theme colors across analysis cards.

- Unified colored-theme headers with dark translucent glass and readable light
  headings while retaining light controls. Brighter glass uses 60% auxiliary / 66%
  chart surfaces and restrained edge highlights; graphite keeps its material palette.
- Expanded the trend viewport from 280px to 308px, tightened chart/footer spacing,
  and added a subtle selected-point glow. Distribution bars are 5px, use theme-color
  variations and aligned amount/percentage columns, with quieter scrollbars.
- Refined recent orders with a tinted header, comfortable rows, subtle hover/focus
  feedback and separators, and a reusable glass button for viewing all orders.

- Reduced colored-theme glass blur to 14px on desktop and 12px on mobile,
  replacing the pale 12% wallpaper wash with a restrained 5–6% dark tint. Jade
  uses more neutral lake-green glass and a deeper teal sidebar. Graphite styling,
  and solid/high-contrast accessibility settings are preserved.

- Replaced native single-select visuals with a shared glass popover for month,
  order filters and dynamically rendered refund fields. Kept native form values,
  validation and change events; added keyboard navigation, type-ahead, selected
  checkmarks, outside/Escape dismissal and bounded open/close motion. Form resets
  synchronize after native values restore; removed fields clean up their popovers.
- Removed chart column hover/focus boxes. Selected dates now use only a thin
  vertical guide, filled data points and an emphasized x-axis label; keyboard
  focus is shown on the label without a full-height overlay.

- Replaced the wallpaper picker with four user-supplied full-resolution JPEGs
  (6000–7680px wide): cloud dawn, black sand, alpine lake and blue-violet curves.
  Added coordinated dusk-blue, graphite, lake-teal and indigo palettes. Existing
  wallpaper/color IDs migrate automatically while accessibility preferences are
  preserved; source images are copied without resizing or recompression.

- Added material hierarchy: 62% secondary cards, 72% chart surfaces, 84% primary
  metric and 92% floating panels, with stronger secondary text. Sidebar uses 54%
  dark glass and a softly lit selection indicator; headers retain 56% tint.
  Preserved 28px blur (20px mobile), inner highlights and opaque fallbacks.
- Unified finite motion across all three pages: button lift/press/sheen, staggered
  metric entry, real-value number crossfades, progress growth, line reveal/morph,
  cursor/keyboard chart guides, glass tooltips, sliding navigation and 220ms panel
  entry. Card reflections track fine pointers at most once per animation frame.
  Reduced motion settles active transitions immediately; hidden/detached views
  release frame jobs. Added four lifecycle/interruption regression tests.

### Fixed

- Stabilized dashboard metrics during month changes: cards now update in place,
  reserve numeric/note space and constrain comparison icons to 16px. Removed the
  loading-opacity flash and vertical number movement; equal-duration crossfades
  affect only changed values. Added regression coverage for unchanged values,
  interrupted transitions and reduced-motion cleanup.

- Suppressed Chromium's default black SVG month focus ring after pointer clicks
  as well as keyboard focus. Keyboard users retain an underlined date label;
  chart focus and month selection behavior are unchanged.

- The Windows shortcut launcher can now reuse a healthy app started outside the
  launcher when its saved state is missing or stale. It records the configured
  Python runtime instead of passing an empty mandatory argument, and tolerates
  legacy state records with an empty Python path. Dependency hashing uses .NET
  directly so Windows PowerShell can also start under an inherited PowerShell 7
  module path where `Get-FileHash` is unavailable.

### Added

- Local wallpaper and color preferences across the ledger, analysis and
  price-watch pages. The shared **外观** dialog offers the four user-provided
  wallpapers, a plain background, coordinated dusk-blue/graphite/lake-teal/
  indigo palettes, independent color selection and automatic browser-local
  persistence. Settings synchronize across open pages. Increased surface opacity
  and reduced motion can be enabled independently; restore-default is available.
- Shared frosted surfaces, restrained shadows, typography and button motion.
  Primary actions lift 2px on hover, brighten their border and play one soft
  left-to-right highlight sweep. Reusable `.glass-button` styling supports future
  pages. Disabled controls stay still; system and application reduced-motion
  preferences suppress movement and sweep effects. Small-screen headers and
  chart controls adapt without horizontal overflow. See `docs/appearance.md`.

- Dashboard trends now switch between total spending, selected platforms and
  selected payment methods. Searchable checkboxes support one, several or all
  series, including an explicit empty selection; choices persist across reloads.
  Month clicks and keyboard activation update the dashboard while retaining
  history and chart selections. Older responses cannot overwrite a newer month.
  Shared monthly tooltips, accessible data tables, legend emphasis and zoom are
  supported, with bounded chart height after exiting fullscreen.
- Historical series use net order spending with refunds in the purchase month;
  stable payment IDs and primary-payment attribution prevent duplicated totals.
  Added isolated regressions for missing months, unknown methods, same-name
  cards, split payments, refunds, negative values and out-of-order responses.
- Installed and verified the pinned UI UX Pro Max skill in Codex; recorded the
  reusable design reference and implementation in `docs/trend-interaction-plan.md`.

- Local order-change history, bounded per-source collection/import observations,
  and restart-safe job summaries. Existing orders receive an upgrade baseline;
  old events and missing collection history are not fabricated.
- Explicit, user-confirmed bank-credit refund links with duplicate, amount,
  date, currency, and conflicting-import guards. Order details expose original
  amounts, linked refunds, bank matches, and the local audit trail.
- Expandable AI answer evidence linking to local order details, plus fixed,
  parameterized query plans for supported month/platform comparisons.

### Changed

- Rebalanced UI proportions using the shadcn/ui dashboard and Item source as
  layout references. Update uses a centered content area with two action columns
  on desktop and one on narrow screens, larger titles/descriptions and consistent
  icon spacing. Shared content width, section/table typography, overview values
  and mobile navigation labels are adjusted for readability. See `docs/ui-layout.md`
  for the upstream commit, dimensions and responsive checks.
- Simplified the everyday ledger interface: overview focuses on spending, and
  Run becomes Update with four common actions and a concise result. Data checks,
  accounting-scope notes, change history, environment switching, rebuild tools
  and detailed task logs are available under Maintenance. Maintenance requests
  load on entry rather than during dashboard initialization. Empty unmatched
  charge panels stay hidden; actionable update failures remain visible.
- Order details show spending and items first, show refund totals only when
  present, and keep refund/bank matching controls in a collapsed section.
  Per-order audit history remains available through the backend API.
- Walmart collection can stop at a verified observed date boundary, with
  conservative fallback and a full history scan at least weekly. Amazon reuses
  complete unchanged delivered details for up to 24 hours, with explicit refresh
  overrides. Both collectors use stable content readiness for navigation instead
  of fixed settling waits; pacing and session/challenge guards remain active.
- VPS source bundles include the shared content-readiness dependency and retain
  compatibility with earlier exact release manifests for rollback.
- Dashboard and AI spending facts now use net amounts attributed to the original
  purchase month. Original orders, bank records, CSV exports, and existing
  Power BI views remain gross; item-level refund allocation is not inferred.
- Distinguish last purchase from last successful collection/import. Withhold
  month-end extrapolation when source observations are absent, stale, or failed.
- Align handoff documentation with routine three-store collection, separate
  Amazon/The Ordinary actions, The Ordinary's local-only override, and local-only
  Walmart price checks. Historical VPS test outcomes are not current health claims.

### Fixed

- Scotia reconciliation updates stable bank-order keys instead of deleting and
  rebuilding all generated orders. Repeats preserve refund/bank/evidence links
  and do not create false deletion/creation events. Reviewed reclassifications
  and bank updates commit with recoverable CSVs; missing historical rows in a
  bounded export are retained.
- Five order importers commit ledger changes and a durable CSV publication
  outbox together. Refund conflicts and database failures no longer publish
  CSVs ahead of SQLite; interrupted file publication is recovered before the
  next import or sync. Read-only validation reports pending publications.
- AI chat renders complete, labelled backend facts from model-selected references
  instead of accepting interchangeable numeric literals. Invalid model content
  returns a marked local summary with a structured validation reason. Cautious
  statements such as inability to infer waste or forecast spending no longer
  trigger a rejection just because they contain a restricted keyword.
- Share calendar-month parsing between factual chat and AI analysis. Bare numeric
  and Chinese months use the current year; explicit years and month comparisons
  keep their requested scope. Analysis and share questions no longer collapse
  to an unrelated all-ledger total or a total-only answer.
- Long-running analysis no longer retains the calendar date from app startup.
- Current-month/year chat does not silently substitute an older latest-purchase
  period when there are no records in the requested calendar period.
- A late observation-history write failure cannot leave the refresh marked passed;
  job-history write failures cannot leave an unstarted task holding the running lock.

### Validation

- 2026-09-11: 55 application/analysis tests passed. Update layout checked at
  320, 390, 768, 1024, 1440 and 1920px without horizontal overflow; desktop/mobile
  screenshots and overview/orders were reviewed with no browser console errors.
- 2026-09-11: 34 application tests passed after updating interface label
  expectations. Live local-browser checks covered overview, Update, Maintenance,
  maintenance data loading, and the 390px mobile layout without console errors.
- 2026-09-11: 317 Python tests and 70 Node tests passed; the collector manifest
  validated. A six-page Walmart fixture reached the same in-window candidates
  after three pages, while incomplete/disordered/backfill fixtures retained full
  scans. Amazon fixtures verified unchanged-cache reuse, expiry, changed/partial
  orders and failure propagation. Live collection timing and VPS deployment were
  not performed for this change.
- 2026-09-10: 317 Python tests passed, including 10 Scotia stability regressions.
  Two reconciliations of a real-ledger SQLite backup copy reviewed 91 bank rows
  and 50 bank-only orders, preserving all 109 order IDs/amounts and adding zero
  order events. Integrity and foreign-key checks passed; the source was unchanged.
- 2026-09-10: 307 Python tests and 58 Node tests passed, including 13 import-publication tests
  covering refund conflicts, commit failure, interrupted publication, recovery,
  concurrent imports, and read-only validation. A real-ledger SQLite backup
  copy upgraded from schema 5 to 6 with every existing row preserved, integrity
  `ok`, and zero foreign-key violations; the source database was unchanged.
- 2026-09-10: 61 analysis, API, month-scope, and ledger-evolution tests passed.
  A read-only check with the real ledger and a mock model preserved the database
  hash, accepted cautious prose, and suppressed a July amount labelled as August.
  Real external-model validation remains unperformed.
- 2026-09-08: 272 Python tests and 58 Node tests passed. The existing stability
  suite passed with no failures or warnings: 1,684 local HTTP requests over
  10 seconds at three workers, P95 35.11 ms, and an unchanged database file.
- Verified a pre-migration snapshot, upgraded schema 4 to 5, and compared all
  nine original business tables without changes. SQLite integrity and foreign-key
  checks passed; no real refund was linked during verification.
- Verified dashboard coverage, order baseline/refund details, expandable local
  answer evidence, evidence-to-order navigation, and persisted validation-job
  history after restarting the app. No browser console errors were observed on
  these paths. Authenticated store/VPS collection was not rerun for this change.

## 1.21.0 - 2026-11-21

Stores can be collected at the same time (desktop 0.20.0 setting
"并发采集", off by default).

### Added

- `runner.py run-many`: one `run --phase collect` subprocess per store, up
  to `parallel_collection.max_workers` (default 3) at once, started 1.5 s
  apart. Output is reported per store in order. Imports stay sequential.
- `ensure-shop-tabs.mjs --separate-windows`: in parallel mode every store gets
  its own window of the dedicated browser, so no store runs in a throttled
  background tab.
- `config/workflow.json` `parallel_collection` (repository default, off),
  overridden per machine by the untracked `config/collection.local.json`
  that `GET/POST /api/collection-settings` and the desktop toggle write.

### Fixed

- `collector-health.json` is updated under a cross-process lock, so one
  store's block or cooldown record can no longer overwrite another's.
- Node atomic writes retry when Windows reports the destination busy.

### Verified

- Two live refreshes of Walmart, Uber Eats and Fantuan in parallel passed
  (50 s and 35 s end to end; collection 22.6 s and 20.7 s, the time of the
  slowest store, instead of about 35 s one after another).

## 1.20.0 - 2026-11-21

A new user can open the app on an empty workspace and set it up from a welcome
guide (desktop 0.19.0).

### Added

- `GET /api/setup` and `POST /api/setup` (`app/setup_state.py`): bank source
  (`scotia`, `statements` or `later`), shopping platforms and ledger start
  date. The start date is written to `collection_floor` and
  `payment_evidence_floor` in `config/workflow.json`; the choice is kept in
  `config/setup.json`. The guide is offered only when setup was never completed
  and the workspace has no ledger data, so existing ledgers never see it.
- The web update page hides the Scotia login action when the bank source is
  imported statements.

### Fixed

- A workspace without `data/expenses.sqlite` could not open: the overview
  failed before anything else loaded. The backend now initializes an empty
  ledger on first launch. An existing ledger is never touched.
- A new workspace had no `config/collector_backends.json`, so the collection
  settings could not be read. Workspace initialization now writes a local-only
  default and never replaces an existing file.

## 1.19.0 - 2026-11-21

Reconciliation is no longer limited to Scotiabank. Bank-specific reading of
statement rows moved into adapters, and any bank's own OFX/QFX or CSV export
can be imported and reconciled with the same rules.

### Added

- `scripts/reconciliation/bank_adapters.py`: `ScotiaAdapter` (the existing
  rules, unchanged) and `GenericAdapter` for imported statements. The generic
  adapter treats transfers, card payments and investment contributions as
  non-spending, and accounts can list extra `exclude_terms`.
- `scripts/importers/bank_statement.py`: OFX/QFX (SGML and XML) and CSV import
  into `data/bank_statements/<account_id>.json`. CSV headers are recognized by
  common bank names, headerless files take `--columns`, and day/month order is
  inferred only when the file proves it; otherwise the import stops and asks
  for `--date-format`. Only the account's last four digits are kept, and long
  digit runs in descriptions are masked. Re-imports and overlapping exports do
  not duplicate rows.
- `GET /api/bank-statements` and `POST /api/bank-statements/import`.
- Credit-card statements (`statement_type: credit_card`) confirm the paying
  card for each bank-only expense.

### Changed

- `scripts/reconciliation/scotia.py` reconciles the Scotia cache and every
  imported statement in one pass; each row is read by its own adapter and
  stored under its own account and institution. Generic bank-only expenses use
  platform `bank` and order ids `bank-<transaction_id>`; Scotia keeps
  `scotiabank` / `scotia-`.
- Every refresh reconciles imported statements (they need no bank login).
- Ledger schema 9 rebuilds `classified_order_items` and
  `order_category_amounts` so `bank` rows are treated like Scotia bank-only
  rows. No table changes.
- Review queue, data coverage, analysis labels, validation checks and the
  order drawer recognize `bank` rows.

### Verified

- Old and new reconciliation run on copies of the live ledger produced
  identical reconciliation rows, bank-only orders, payment assignments,
  accounts and report (139 bank rows).
- Schema 8 to 9 migration on a ledger copy kept all category allocations
  identical.

## 1.18.1 - 2026-09-02

This patch release records the current validated local application state after
live store collection, ledger rebuild, and regression verification.

### Added

### Changed

- Split low-frequency shopping from the routine store refresh. The normal
  action now collects Walmart, Uber Eats, and Fantuan only; Amazon and The
  Ordinary each have a separate on-demand button for runs after an actual
  purchase.
- Routed The Ordinary through the local dedicated browser even when the main
  selector is set to VPS. Amazon continues to follow the selected backend,
  while all imports, reports, and SQLite storage remain local.

### Fixed

- Fixed Walmart variable-weight receipt parsing so duplicate rendered product
  blocks collapse to one purchased item, concatenated weight calculations do
  not contaminate product names, and the importer uses the net item subtotal
  after savings. Added a regression fixture for the affected discounted pork
  order and verified its item subtotal plus tax reconciles to the paid total.
- Kept unresolved VPS collector failures in a separate per-platform recovery
  backlog. A later unrelated on-demand success no longer hides an earlier
  `login_required` or failed platform, while a successful rerun of that same
  platform clears its recovery entry.
- Fixed PowerShell singleton-array collapse that let an Amazon-only VPS task
  skip live collection, import an older sanitized cache, and still appear to
  pass. Amazon-only and The Ordinary-only selections now remain arrays and
  always enter their intended backend branch.
- Stopped ordinary routine and on-demand runs from inheriting unrelated failed
  components from an older VPS recovery summary. Only an explicit recovery
  continuation merges attempts into the previous recovery set.
- Preserved and displayed Amazon fulfillment state in the order list and
  detail drawer, so pending and shipped purchases are visible before delivery
  instead of looking like completed orders or disappearing from the UI.
- Excluded terminally cancelled Fantuan orders from CSV and SQLite accounting
  while retaining their sanitized source records for audit. Existing orders
  now have their status refreshed from the live order cards, and a later
  cancellation removes the order, items, and payment assignment atomically.
- Made current-month largest-expense and category spend/list chat questions
  deterministic and month-scoped. These common factual questions no longer
  depend on DeepSeek to count ledger rows, and partial-month answers identify
  the latest ledger date.
- Added one bounded The Ordinary detail-session recovery. If a rendered order
  card is still visible but its same-origin detail request has fallen back to
  login, the local collector reloads the orders entry, performs the existing
  allowlisted Google recovery only when needed, and retries that detail once.
  Manual/challenge states still stop without a loop.
- Fixed the immutable VPS release allowlist so every relative JavaScript
  dependency used by Walmart, Amazon, Uber Eats, and payment-evidence
  collectors is deployed. Added a dependency-closure regression test to stop
  future releases from passing validation with missing modules. Release-tree
  validation now also recognizes the two preceding exact allowlists as bounded
  rollback targets, so consecutive allowlist expansions can be deployed
  atomically without weakening manifest checks.

## 1.18.0 - 2026-08-12

This entry describes the current validated release candidate. Its final release
commit and Git tag are pending live deployment verification.

### Added

- Added an on-demand, sanitized TLS consistency diagnostic for the five store
  hosts. It compares the local route with the VPS home-exit route using only
  TLS version, ALPN, cipher strength, certificate lifetime, bounded timing, and
  browser/runtime versions; it does not request account pages or retain IPs,
  cookies, credentials, or page content.
- Added The Ordinary Canada as a fifth manifest-defined order collector for
  both local and VPS backends. It reads order IDs from the authenticated order
  history, requests the same-origin detail drawer sequentially, and persists
  only normalized order, item, official product-link, and card last-four
  evidence. Address, contact, full payment, Cookie, and Authorization fields
  are never retained.
- Added idempotent The Ordinary ledger import, dashboard labels, and Scotia
  merchant matching for `DECIEM` and `THE ORDINARY` descriptors.
- Added a bounded recovery panel for the latest VPS run. Ordinary component
  failures can be retried by platform, while `login_required` opens only that
  platform's loopback VNC session and requires explicit verification before a
  targeted retry.
- Added an immutable VPS release tool with an exact source allowlist,
  path/size/SHA-256 manifest, staged Python/Node/manifest checks, collection
  and login-session guards, atomic `current` switching, automatic health
  rollback, explicit rollback, and bounded release retention.
- Added sanitized VPS disk-health telemetry and owner-only application-log
  rotation. Disk use warns at 80%; 90% use or less than 1 GiB free is critical,
  without making an otherwise healthy collector unavailable.

### Changed

- Added a bounded The Ordinary session-recovery step shared by the local and
  VPS collectors. When the site session has expired but the dedicated browser
  still has a usable Google identity session, the collector may click the one
  exact, visible, same-site `Continue with Google` action once. Account
  selection, verification, CAPTCHA, credentials, Cookie stores, and browser
  storage remain manual and unread.
- Made the VPS collector prefer a project-isolated Chrome for Testing runtime.
  The deployed runtime now uses the same Chrome 150 major version as the local
  dedicated browser without replacing the VPS system browser.
- Expanded the dual-backend bundle contract, login manager, seed allowlist,
  checksum-verified output allowlist, and TLS target set from four stores to
  five stores.
- Preserved `login_required`, `blocked`, and `cooldown` component states in
  collector-run records instead of flattening every nonzero exit to `failed`.
- Serialized VPS bundle collection and manual VNC login-session changes with an
  owner-only lock. Each full bundle now records bounded platform progress,
  expects all five collectors plus three payment components, and has a
  10-minute remote budget plus a 660-second application safety timeout.
- Added sanitized VPS progress to the local backend status contract so a
  running application task can show its current platform or finalization stage
  and completed component count without exposing remote paths or account data.
- Added a backward-compatible selective bundle v2 contract. A user-triggered
  recovery requests only the failed platforms, dynamically validates their
  primary/payment components, imports only their owned outputs, and then runs
  the normal SQLite, reporting, and safety pipeline.
- Separated transport success, live collection completeness, data freshness,
  ledger validation, and overall workflow status. A checksum-valid partial
  bundle remains usable, but the job, run-state, report, toast, and dashboard
  now remain `partial` instead of claiming a complete live success.

### Fixed

- Stabilized The Ordinary startup before authentication and order parsing. The
  collector now waits for a late `/orders` to `/login` redirect, waits for the
  exact Google action to render, ignores non-challenge forms that merely carry
  `data-sitekey`, reuses the exact Canadian login tab, and prefers a rendered
  order-history tab over stale restored tabs. VPS startup removes only tab
  restore metadata while preserving authentication and site storage.
- Made The Ordinary Google recovery tolerate a bounded 60-second callback and
  adopt only a newly created HTTPS tab that has already returned to the exact
  The Ordinary Canada orders URL. Google/account-selection tabs, lookalike
  hosts, pre-existing tabs, OTPs, and challenges remain unread and untouched.
  This fixes an intermittent full-bundle `login_required` after the former
  30-second recovery window expired.
- Reused an already rendered Fantuan base page and recovered only a bounded
  `Page.navigate` CDP timeout when the exact allowed page was demonstrably
  reached. This removes the false Fantuan failure that caused the original VPS
  application task to run until its old 900-second timeout.
- Made an application refresh timeout atomically finalize a stale
  `exports/current/run-state.json` as failed. The dashboard can no longer remain
  indefinitely in `running` after its worker has exited.
- Made Walmart order-page navigation wait up to 30 seconds for rendered
  `interactive` or `complete` documents instead of falsely timing out after a
  short wait for `complete` only. Login and challenge classification still runs
  before any rendered page is accepted.
- Treated a checksum-verified VPS `partial` bundle as a successful transport
  while retaining each failed component's explicit run status. The application
  no longer adds a misleading generic Python exit-code warning on top of the
  actionable component warning.
- Overlaid current component-run results on cross-run collector health so a
  current VPS failure or success cannot be hidden by an older local health
  snapshot. The concise run report now derives warnings from current collection
  records as well.
- Classified certificate and TLS protocol failures separately from transient
  DNS, proxy, connection-reset, timeout, and HTTP 5xx failures. Non-transient
  TLS failures now stop immediately instead of consuming bounded retries.
- Prevented failed components from returning undeclared VPS leftovers as fresh
  results. The service clears only selected allowlisted runtime outputs absent
  from the current seed and labels every component `live`, `cache_preserved`,
  or `none`; payment-only failures are no longer invisible.
- Converted the version-history checks to the project's actual `unittest`
  runner, so version files and the dated changelog heading can no longer drift
  without failing the normal regression suite.

### Verification

- Full Python regression suite: 238 passed.
- Full Node collector suite: 52 passed.
- Live TLS consistency diagnostic: 5/5 store hosts passed with TLS 1.3,
  HTTP/2, zero browser-major gap, and bounded clock skew.
- Complete application safety validation passed.
- Two live VPS-backed full refreshes completed all eight remote collection and
  payment components, including The Ordinary, with no warnings in 192.4 and
  190.5 seconds. The second run was triggered through the application and
  finalized both the job and run-state as `passed`. Direct local and VPS The
  Ordinary checks each passed repeatedly; local and VPS Fantuan checks also
  passed twice. Ledger invariants, verified backup, raw cleanup, and runtime
  shutdown all passed.
- A 2026-08-12 application-triggered VPS refresh passed all 8/8 components in
  198.8 seconds after the OAuth callback fix; The Ordinary itself passed in
  5.1 seconds. Two additional cold-start The Ordinary checks passed in 5.4 and
  5.7 seconds.

## 1.17.0 - 2026-07-25

This entry describes the current committed release and is represented by Git
tag `v1.17.0`.

### Changed

- Retired VPS Walmart price checking after live verification showed that the
  visible authenticated Chrome profile retained Square One while the
  independent headless worker, through the same temporary home-network exit,
  still returned `wrong_store`.
- Made Walmart price checking local-only through the dedicated shop browser.
  Removed all VPS-specific controls and startup synchronization from the price
  window; it no longer follows the order-collection backend selector.
- Limited the dashboard **VPS / 本地** selector to the four-platform order
  collector. VPS readiness now requires only `collect_bundle_v1`, so retiring
  price checking does not disable remote Amazon, Fantuan, Uber Eats, or Walmart
  order collection.
- Disabled both VPS price-watch timers and removed the Windows sign-in result
  receiver. Retired scripts and remote state remain available only as rollback
  evidence; no active product path invokes them.

### Fixed

- Removed the false coupling that made an otherwise healthy VPS order collector
  unavailable whenever the price-watch worker was unhealthy.
- Prevented stale or failed VPS price snapshots from overriding the verified
  local-only price page.

### Verification

- Confirmed the visible VPS browser was authenticated, showed Square One, and
  exposed rendered `Was` prices both before and after a browser-session restart.
- Confirmed the headless VPS worker still failed closed with `wrong_store` over
  the temporary home exit, establishing that the remaining defect was isolated
  browser state rather than public IP routing.
- Confirmed both VPS price-watch timers are inactive and no price-watch startup
  shortcut remains.
- Full Python regression suite: 154 passed.
- Full Node collector suite: 29 passed.
- Complete production-ledger safety validation passed with SQLite integrity
  `ok` and zero duplicates, orphans, or payment mismatches.
- Live local price-run and browser verification confirmed priced products,
  verified deals, `backend=local`, and no VPS sync control.

## 1.16.1 - 2026-07-25

This entry describes a previous recorded intermediate working-version
milestone. It does not have a dedicated commit or Git tag.

### Changed

- Made same-day price results shared across the local and VPS display modes.
  The newest successful Toronto-day snapshot is shown after switching backends,
  while the page still identifies whether the verified result came from Local
  or VPS.
- Made a dashboard-triggered VPS check wait for the queued My Items run and
  receive that run's final status instead of immediately pulling the previous
  snapshot.

### Fixed

- Prevented a later `wrong_store`, login, block, or parse failure from replacing
  a previously valid same-backend deal list with an empty list.
- Let a newer VPS result override a stale local `queued/passed` status, so a
  store mismatch is reported truthfully instead of appearing as a successful
  empty check.
- When one backend fails but the other has a verified result from the same
  Toronto day, kept the failure visible and displayed the shared valid deals
  with explicit source attribution.

### Verification

- Full Python regression suite: 151 passed.
- Full Node collector suite: 29 passed.
- Live API verification showed the current VPS `wrong_store` state together
  with the same-day locally verified deals, attributed to Local.

## 1.16.0 - 2026-07-25

This entry describes a previous recorded intermediate working-version
milestone. It does not have a dedicated commit or Git tag.

### Added

#### Remote collection

- Added a shared order-collection and price-watch runtime control plane with
  **Auto / VPS / Local** compatibility, sanitized backend health/capability
  status, atomic preference persistence, and task-start backend locking.
- Added a key-only SSH VPS collection service with sanitized status,
  independent per-platform profile readiness, strict request validation, and
  the complete `collect_bundle_v1` return contract.
- Added a checksum-verified remote bundle client that sends only allowlisted
  sanitized seeds, rejects unsafe ZIP members or invalid JSON, atomically
  applies returned caches with rollback, and never transfers SQLite or Scotia
  data.
- Added four owner-only, manually authenticated VPS browser profiles for
  Amazon, Fantuan, Uber Eats, and Walmart plus a temporary loopback-only
  login/collection runtime that closes browser, VNC, CDP, and proxy listeners
  after use.

#### Cash withdrawals

- Added a dedicated **Cash** navigation page below Orders. It lists sanitized
  Scotia ATM withdrawals by date and amount and supports quick purpose notes.
- Stored cash-purpose notes in a separate SQLite annotation table: notes never
  create orders or contribute to overview totals, and Interac e-Transfers are
  not misclassified as cash withdrawals.

#### Walmart product links and price watch

- Added canonical Walmart purchased-product link capture, bounded historical
  link backfill, and local persistence through the existing item ledger.
- Added an idempotent `expense-price-watch/v1` client that sends only new or
  changed product facts to a configured VPS and validates returned deals
  against products bought locally.
- Added a removable Windows sign-in shortcut that retrieves the latest VPS deal
  result without opening an inbound port or storing the bearer token in project
  files.
- Added an SSH-backed VPS price service with an idempotent cumulative SQLite
  watchlist, owner-only authenticated Walmart profile, manual request queue, and
  a name-and-price-only result contract.
- Added a dashboard **查折扣** entry and separate on-demand control window that
  follows the homepage's unified **VPS / 本地** selector.
- Added a local Walmart price-check path using only the dedicated shop
  browser's Walmart tab, with strict Store 3055 confirmation and the same
  locally validated result format as the VPS path.

### Changed

#### Backend architecture

- Kept all import, ledger sync, reports, snapshots, and SQLite persistence on
  the local computer regardless of collection backend. `auto` now prefers the
  ready VPS complete collector and falls back to the local dedicated browser
  when remote health is unavailable; explicit VPS mode fails closed.
- Installed an isolated, checksum-verified Node.js 24 LTS runtime for the VPS
  collector without replacing the older system Node.js used by other services.
- Kept Scotia reconciliation outside the backend selector and prohibited
  browser-profile or cookie synchronization between local and VPS sessions.
- Unified order collection and price checking behind one dashboard
  **VPS / 本地** toggle. Both capabilities must be ready before an environment
  can be selected; `auto` remains an internal compatibility mode.
- Verified a complete VPS-backed weekly refresh: all remote components, local
  imports, ledger validation, SQLite integrity, and foreign-key checks passed
  with no review rows or workflow warnings.

#### Run reporting

- Corrected the latest-run summary so a stale Scotia login-health snapshot is
  not reported as a warning for a VPS store-only run; the card now evaluates
  only collector components that actually participated in that run.

#### AI analysis

- Hardened AI analysis around a deterministic fact layer: completed-month
  averages, same-day partial-month comparisons, merchant/category/payment
  shares, and low-confidence conditional projections are now computed locally.
- Added explicit forecast and data-limit sections plus visible as-of date and
  item-detail coverage metadata to the analysis page.

#### Dashboard

- Replaced the dashboard's monthly spending bars with an accessible SVG line
  chart, exact point tooltips, selected-month highlighting, and 100% to 200%
  zoom controls with horizontal browsing.
- Added a conventional top-right expand control that displays only the spending
  trend surface in full screen, preserves chart zoom, and exits by button or
  Escape.
- Refined the trend chart with human-friendly axis steps, a selected-month band
  and guide, clearer value hierarchy, and keyboard-focusable data points.

#### Walmart product links and price watch

- Completed the one-time Walmart product-link backfill: all known Walmart
  orders and item rows now carry links for every distinct product represented
  in the ledger.
- Replaced the 74-page product crawler with one read of the authenticated
  Walmart My Items page. Only rendered current/`Was` price pairs are returned;
  exact local name matching restores the saved canonical product link.
- Disabled periodic VPS Walmart collection. The remaining trigger timer only
  polls for explicit manual requests, and the retired scheduled command is
  fail-closed with `status=disabled`.
- Removed the price page's separate local-only button so collection and price
  checking always use the same homepage backend selection.

#### SSH security

- Hardened the configured VPS SSH service to require public-key authentication.
  Password and keyboard-interactive authentication are disabled; normal and
  recovery access paths were verified before and after the reload.

### Fixed

#### Walmart store and price checks

- Updated the Walmart price-watch store selection for the current location
  dialog: dismiss obstructing modal overlays, select the exact Square One
  `pickup-store` radio result, save it, reload, and reject the page if that
  store is not retained.
- Corrected the separate price-watch window so a failed task with phase
  `complete` is titled **检查未完成** instead of the contradictory
  **检查完成 / 需要处理** combination.

#### Analysis and dashboard

- Prevented missing item prices from becoming zero-dollar facts, excluded
  incomplete prices from item-spend rankings, and added output validation that
  hides unsupported amounts, percentages, category-month mappings, and
  overconfident prediction language.
- Replaced CSP-blocked inline chart sizing with SVG attributes and native
  progress elements so trend and distribution values render proportionally
  without weakening the local application's content security policy.

#### Collector safety and price parsing

- Extended browser snapshot address redaction to Canadian street suffixes such
  as Crescent and prevented VPS block/wrong-store responses from being reported
  as a successful “no discounts” result.
- Added a mandatory home-exit preflight and `home_exit_offline` state; the
  on-demand collector cannot silently fall back to the VPS data-center address.
- Replaced a Node-version-dependent standard-input call in the local
  price collector and prevented it from navigating any non-Walmart account tab.
- Corrected cents parsing so values such as `94¢` become CAD 0.94, and required
  both a lower current price and higher `Was` price before `Rollback` is treated
  as a discount.
- Preserved the last valid deal snapshot on block, login, wrong-store, or parse
  failure instead of replacing it with an unsupported “no discounts” result.

### Verification

- Full Python regression suite: 149 passed.
- Full Node collector suite: 29 passed.
- Collector-manifest validation, complete ledger/runtime safety validation,
  PowerShell parsing, and diff whitespace validation passed.
- Post-change VPS checks confirmed key-only access, password rejection, reverse
  forwarding, and service isolation.
- A live local My Items run confirmed priced rows and exact matched discounts
  with reused local links. A live authenticated VPS run correctly rejected the
  page as `wrong_store` and persisted no unconfirmed price.

## 1.15.0 - 2026-07-18

This entry describes a previous recorded intermediate working-version
milestone. It does not have a dedicated commit or Git tag.

### Added

- Added repeatable Windows bootstrap and workspace initialization that preserve
  an existing ledger and can repair the isolated application runtime.
- Added verified cross-machine profile export/import with checksums, staged
  validation, rollback protection, and explicit workspace-move confirmation.
- Added a shared persistence sanitizer for visible browser text and URLs, with
  tests for contact, address, payment-card, credential, fragment, and query
  redaction.
- Added application-wide identity and version settings in `app/settings.py`.
- Added content-addressed fixed-analysis caching and a page-session-only
  DeepSeek chat mode.
- Added deterministic product leaderboards, date-range daily averages, data
  quality metadata, and typed bank-placeholder handling for AI analysis.
- Added the read-only platform-charge review API and user-interface table.
- Added Alipay as a first-class payment source for Fantuan evidence.
- Added pure Walmart and Scotia utility modules plus focused Node regression
  fixtures.

### Changed

- Pinned the complete application dependency set and aligned the app, weekly
  workflow, bootstrap, health check, and repair paths on the isolated runtime.
- Hardened application start/stop state, process cleanup, port ownership,
  diagnostic logging, and stale-process recovery.
- Improved order search, pagination, reconciliation messaging, run warnings,
  and responsive analysis/overview presentation.
- Removed stale ledger totals from the handoff document; generated status and
  validation artifacts are now the runtime authority.
- Hardened backup restore so stale managed caches absent from a verified
  archive are removed transactionally and covered by rollback.
- Reduced AI data exposure by excluding all payment-card digits, source links,
  order IDs, notes, browser state, credentials, and persisted chat history.

### Fixed

- Fixed Walmart collection so it follows all purchase-history pages, ignores
  repeated fulfillment child controls without stable order IDs, preserves
  sanitized charge history, and skips cancelled orders with no item details.
- Recovered item-level orders that were previously invisible beyond the first
  Walmart history page.
- Added conservative Walmart split-settlement reconciliation: two to four
  same-day charges may link to one unique order within one day only when their
  combined amount is within a bounded weighted-item adjustment tolerance of
  2%, with a CAD 1 floor and CAD 3 cap.
- Fixed Scotia statement-period filtering that referenced an undefined date
  normalizer.
- Separated a failed fresh Scotia export from a successful cached
  reconciliation and classified login/verification requirements accurately.
- Prevented authoritative platform charges without item-level orders from
  becoming bank-only placeholder expenses.
- Clarified that unmatched platform charges and collector warnings require
  review but do not make an otherwise successful ledger refresh fail.
- Corrected deterministic analysis behavior for product rankings, invalid
  quantities, daily averages, cache fingerprints, and concurrent model calls.

### Verification

- Full Python regression suite: 100 passed.
- Full Node collector suite: 22 passed.
- PowerShell parsing, JavaScript syntax, diff whitespace, SQLite integrity,
  foreign keys, duplicate checks, orphan checks, payment checks, and aggregate
  ledger validation passed.
- A live dedicated-browser Walmart pagination run recovered the missing order,
  and the final reconciliation review queue contained zero rows.

## Unversioned Development History

These changes were committed while the checked-in application version still
reported `1.0.0`.

### 2026-07-01

- `f29a210` - Held unmatched Walmart bank charges out of the ledger for manual
  review instead of inventing item-level orders.
- `93e0a06` - Separated payment instruments from bank accounts.
- `019a92d` - Rejected ambiguous bank-to-order matches rather than choosing an
  equally ranked candidate.

### 2026-06-30

- `192653a` - Added Amazon Canada order and payment collection.
- `2a46260` - Introduced the `expense-collector/v1` manifest, component runner,
  and manifest-driven collector discovery.
- `3a066ac` - Added scrollable expense-distribution views.
- `488c1e3` - Kept unmatched bank-only expenses under the Scotiabank source
  instead of misclassifying them as collector orders.

### 2026-06-28

- `2b22ff3` - Added read-only DeepSeek ledger analysis.
- `b6beaa0` - Consolidated ledger refresh actions behind the weekly workflow.
- `8686fdb` - Added the two-stage Scotia login and reconciliation flow.
- `9468879` - Added the recurring stability test suite and retained status
  history.

## 1.0.0 - 2026-06-27

### Added

- `fd69c1b` - Added the loopback-only local expense ledger application,
  query-only repository, API, job control, responsive UI, and vendored icons.
- `7c67aa6` - Added application-grade safety preflight, verified backups,
  restore support, atomic writes, SQLite busy handling, security checks, and
  cross-process refresh locking.
- `0429599` - Added bounded collector pacing, retry/backoff, CDP timeouts,
  login/block detection, circuit breakers, health reporting, and sequential
  dedicated-browser collection.

### Fixed

- `a3dc5a0` - Made the structured validation status artifact authoritative and
  eliminated early success-state races.
- `1ece71f` - Hardened localhost application startup, dependency repair,
  single-instance locking, port fallback, health identity, process tracking,
  and retained diagnostics.

## Pre-versioned Baseline - 2026-06-24

- `5fd36a1` - Initialized the maintenance repository, handoff files, reporting
  status, and local rollback baseline.
- `ed48cee` - Centralized weekly workflow configuration.
- `63b687e` - Moved Scotia merchant and category rules into configuration.
- `0146576` - Split SQLite schema/view ownership from ledger database helpers.
- `703d8e5` - Updated the maintenance handoff after the refactor.

## Recovered Pre-Git Development History

This section restores the project history that predates the first usable Git
repository. It is ordered newest first, matching the rest of this changelog.

### 2026-06-22

- Added repeatable SQLite ODBC setup, Power BI documentation, and dedicated
  Power BI views for orders, items, monthly summaries, payment review, and bank
  reconciliation.
- Promoted Fantuan's WeChat Pay evidence to a first-class payment source rather
  than leaving those orders under a generic review placeholder.
- Refreshed existing period exports during weekly runs, strengthened ignored
  private/generated paths, and normalized payment-source configuration to
  ASCII-safe data.
- Verified ledger integrity and completed 23 passing unit tests at the end of
  the maintenance pass.

### 2026-06-21

- Fixed Walmart import so valid newer order-number prefixes were not collected
  and then silently skipped by an overly narrow identifier rule.
- Added a focused Walmart importer regression test and completed 21 passing
  unit tests with zero duplicate orders, duplicate items, or reconciliation
  failures.

### 2026-06-13

- Added sanitized, read-only Scotiabank transaction collection and
  reconciliation without retaining credentials, cookies, balances, full
  account/card numbers, or raw banking HTML.
- Expanded reconciliation beyond delivery-only matching so legitimate
  bank-recorded expenses such as software subscriptions could enter the ledger
  while transfers, credits, refunds, and reversals remained excluded.
- Refactored the implementation into `browser`, `collectors`, `importers`,
  `ledger`, `reconciliation`, and `reporting` modules with shared CDP and CSV
  helpers.
- Reorganized generated output into `current`, `summaries`, `reconciliation`,
  and monthly `periods` views and removed superseded scripts and artifacts.
- Verified SQLite integrity, zero duplicate/reconciliation failures, and 20
  passing unit tests after the expanded bank-ledger workflow.

### 2026-06-06 To 2026-06-07

- Added automatic Walmart order discovery, dynamic overlap windows for Uber
  Eats and Fantuan, reuse of confirmed payment evidence, and strict external
  command exit-code handling.
- Added machine-readable `weekly-status.json`, broader SQLite relationship and
  aggregate validation, and automatic cleanup of temporary raw snapshots.
- Reduced the observed full online refresh from roughly 99 seconds to roughly
  18 seconds in the recorded run by limiting collection to incremental work.
- Confirmed `data/expenses.sqlite` as the normalized source of truth while
  retaining CSV and Markdown as generated inspection/export formats.
- Completed 11 passing unit tests with zero duplicate orders, duplicate items,
  orphan rows, or amount reconciliation failures.

### 2026-05-31

- Introduced the normalized SQLite model for orders, item rows, payments,
  payment sources, and sanitized payment evidence.
- Changed Walmart, Uber Eats, Fantuan, and payment-evidence importers to upsert
  SQLite directly while preserving normalized CSV/JSON files as compatibility
  and manual-inspection caches.
- Added month, date-range, annual, payment-source, and category exports generated
  from SQLite.
- Fixed Walmart CDP import so updating Walmart data merged by
  `platform + order_id` instead of overwriting the entire transaction cache.
- Verified the weekly workflow, zero duplicate rows, and 10 passing unit tests.

### 2026-05-30

- Created the dedicated shop-browser profile and loopback CDP workflow so
  shopping automation never reads, copies, restarts, or reuses the main Chrome
  profile.
- Reviewed the open-source Walmart Invoice Exporter and adapted a local copy for
  Walmart Canada with narrower retained accounting fields.
- Added normalized Walmart order and item extraction, stable order/item dedupe,
  amount reconciliation, category summaries, and cleanup of raw page captures.
- Added maintainable Uber Eats and Fantuan collectors/importers with a May 2026
  collection floor, sanitized Fantuan cache, and a low-token weekly refresh
  entrypoint.
- Added payment-source configuration, per-order assignment, sanitized payment
  evidence, card/source/category summaries, and human-readable accounting
  dashboard exports.
- Closed the initial implementation phase with zero duplicates, zero amount
  reconciliation failures, and all recorded unit tests passing.

## Release Maintenance Rules

1. Record user-visible, data-model, collector, safety, or dependency changes
   under `Unreleased` in the same change set.
2. On release, replace `Unreleased` entries with `X.Y.Z - YYYY-MM-DD` and add a
   fresh empty `Unreleased` section.
3. Keep `package.json`, `package-lock.json`, and `app/settings.py` on the exact
   same version.
4. Run the Python and Node suites plus `scripts/maintenance/validate_all.py`.
5. Commit the release and create a `vX.Y.Z` Git tag so future history can be
   reconstructed without inference.
