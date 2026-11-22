# Expense Ledger

A local-first personal finance system for Windows that collects orders from Canadian shopping and delivery platforms, imports bank transactions, and reconciles the two. It reconciles **without guessing**: an expense is counted only when the evidence supports it, and anything ambiguous goes to a review queue.

> 中文简介：一个本地优先的 Windows 记账系统。它自动采集 Walmart、Amazon、大统华、Uber Eats、饭团等平台的订单，导入银行流水（Scotiabank 可自动采集，其他银行导入 OFX/CSV 文件），然后逐笔对账。设计原则是“证据不足就不猜”：拆分付款、退款、冲正都按规则处理，处理不了的进入人工核对队列。

**Interface language / 界面语言:** Chinese or English, chosen on first launch and switchable any time in Settings → Experience → Language. Ledger data (merchant and item names, notes, categories) always shows as recorded. 首次启动时选择中文或英文，之后可在 设置 → 使用体验 → 语言 中切换；商户名、商品名、备注和分类保持原样。

![stack](https://img.shields.io/badge/Python-FastAPI-blue) ![stack](https://img.shields.io/badge/C%23-WinUI%203-purple) ![stack](https://img.shields.io/badge/Node-CDP%20collectors-green) ![stack](https://img.shields.io/badge/SQLite-ledger-lightgrey)

## Why it exists

Spending is split across five or six platforms, two currencies, a credit card, a debit card, and digital wallets. Bank statements only show `UBER* EATS 7.88`. Platform order pages show items but not which card paid. Neither source alone answers "what did I actually spend, on what, and with which card".

## What it does

| Stage | What happens |
|---|---|
| **Collect** | Node.js collectors drive a dedicated Chrome profile over the Chrome DevTools Protocol to export order history from Walmart, Amazon, T&T, Uber Eats, Fantuan and The Ordinary. They use randomized pacing, bounded order windows and an exclusive workflow lock. They never read cookies, and they sanitize page text (addresses, emails, phone numbers, card numbers) before anything is cached. |
| **Import** | Per-platform importers normalize orders, items, refunds and payment evidence into one SQLite schema with idempotent upserts. They keep durable `order_events` and `collection_observations`, so every row can be traced to the run that saw it. |
| **Bank statements** | Scotiabank can be collected from online banking. Any other bank works by importing its own **OFX/QFX or CSV** export ([guide](backend/docs/bank-statements.md)). Each source is read by a bank adapter, and the matching engine is shared. Re-imports never duplicate rows. When a CSV's dates could be either month/day or day/month, the import stops and asks instead of guessing. Only the last four digits of an account number are stored. |
| **Assign payments** | A payment is attributed to a card only on explicit evidence (wallet name or card last-4). A generic "credit/debit card" label is marked *inferred* and stays visibly distinct from *confirmed*. |
| **Reconcile** | Bank charges are matched to platform orders by amount, currency, merchant and posting window. Same-day split charges, next-day corrections, "Uber Cash + one card" split tenders, memberships and in-store vs. online purchases each follow an explicit rule. Ambiguous matches stay in review, and confirmed manual links survive later re-runs. |
| **First run** | An empty workspace opens to a three-step guide. Step 1 asks where bank data comes from (Scotiabank collection, another bank's statement files, or later). Step 2 asks which shopping platforms you use; you only tick them here and log in the first time each one is refreshed. Step 3 asks for the ledger start date. Every step can be skipped. A desktop self-check mode runs the whole flow in an isolated workspace. |
| **Report** | Item-level category allocation (a whole-order adjustment is spread only when it balances exactly), refunds attributed back to the purchase month, data-coverage reports (last attempt, last success, date range actually observed), monthly and annual exports, and optional LLM analysis over an exported summary. |

## Architecture

```text
dedicated Chrome profile (CDP) ─┐
                                ├─> sanitized caches ─> platform importers ─> SQLite (schema-versioned)
optional VPS collector ─────────┘                                               │
  (checksum-verified release, atomic symlink swap, rollback on failed probe)    ▼
                                                     payment assignment + bank reconciliation
                                                                                │
                              FastAPI (loopback only) ◄─────────────────────────┤
                                     │                                          ▼
          WinUI 3 desktop host + WebView2 dashboard               report exports + validation
```

* **Backend**: Python 3.12 and FastAPI, served on loopback only, with Host/Origin checks and strict response headers. A single-job background queue kills the whole process tree when a job times out. The UI reads through query-only connections, and user writes are limited to a few narrowly scoped tables.
* **Desktop**: C# / .NET 10 / WinUI 3. Native pages handle orders, payments, cash notes, settings and maintenance, and a WebView2 page renders the charts. The host ships an embedded Python and Node runtime and has built-in self-verification modes (`STARTUP`, `LAYOUT`, `GLASS` and others) that check the running UI.
* **Collectors**: Node.js ES modules over raw CDP, with no Puppeteer or Playwright dependency.

More detail: [backend/docs/architecture.md](backend/docs/architecture.md), [ledger reliability](backend/docs/ledger-reliability.md), [collectors](backend/docs/collectors.md).

## Repository layout

```text
backend/app/            FastAPI app, repository layer, analysis, static dashboard
backend/scripts/        collectors (Node), importers, ledger sync, reconciliation, reporting, maintenance
backend/tests/          Python test suite (unittest)
backend/tests_js/       Node test suite (node --test)
backend/config/         collector manifest, platform list, workflow, merchant rules, example payment cards
desktop/                WinUI 3 host
tests/                  C# test programs for the desktop logic
tools/                  build, runtime setup, desktop test runner
```

## Running the tests

```powershell
cd backend
python -m pip install -r requirements-app.txt
python -m unittest discover -s tests -p "test_*.py"   # 467 tests
node --test tests_js/*.test.mjs                        # 202 tests
```

C# desktop tests: `tools/run-desktop-tests.ps1` (needs the .NET 10 SDK).

## Privacy note

This is a sanitized public snapshot of a private project, with fresh git history. The real ledger database, browser profiles, collected pages, machine-specific configuration and internal working notes are not included. Card and account digits in the example config and tests are placeholders (`1111`, `2222`, `3333`).

To run it for real you need your own shopping accounts and bank exports. The project is published mainly to show how it is designed and engineered.
