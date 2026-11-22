# Walmart Price Watch

Walmart price checking is local-only. It uses the dedicated shop browser on
CDP port `9223` and does not follow the dashboard's **VPS / 本地** order
collection selector.

The authoritative SQLite ledger, purchased-item cache, deal snapshot, and all
price-watch status remain on this computer. Scotia is unrelated to this
workflow.

## Active Collection Strategy

1. Open `https://www.walmart.ca/en/my-items` in the already authenticated,
   dedicated local Walmart browser profile.
2. Confirm that the rendered page shows `MISSISSAUGA (SQUARE ONE), ON`.
3. Read the purchased-item cards on that one page.
4. Keep only rows with a valid current price and a higher rendered `Was` price.
5. Require one exact normalized name match in `data/walmart_items.csv`.
6. Reuse the locally saved canonical product name, product key, and Walmart
   link.

The collector does not open individual product pages. An ambiguous or unknown
name is rejected rather than guessed.

Open `/price-watch` and press **使用本机检查折扣**, or run:

```powershell
.\.app-runtime\Scripts\python.exe .\scripts\price_watch\on_demand.py --backend local
```

The button starts only the local collector. There is no VPS sync button,
application-startup pull, sign-in receiver, or scheduled price collection.

## Why VPS Price Checking Was Retired

Live verification on 2026-07-25 separated network routing from browser state:

- the visible authenticated VPS Chrome profile retained Square One across a
  restart and exposed rendered `Was` prices while routed through the temporary
  home-network exit;
- the independent headless worker, using the same account profile and the same
  home-network exit, still failed closed with `wrong_store`.

The remaining workaround would require keeping and automating a long-lived
visible remote browser session. That adds brittle VNC/CDP lifecycle state
without improving the accounting workflow enough to justify it, so the active
VPS price path was removed.

This retirement does not affect VPS order collection. The dashboard runtime
selector still chooses where Amazon, Fantuan, Uber Eats, and Walmart order
collection runs. It never changes data storage.

## Failure Contract

Local results distinguish:

- `completed`: Square One was confirmed and the page was parsed;
- `login_required`: the dedicated Walmart profile needs manual login;
- `wrong_store`: Square One was not confirmed, so no new prices were accepted;
- `blocked`: Walmart displayed a verification or access-block page;
- `failed`: the page did not satisfy the collector contract.

These states are never reported as “no discounts.” Failed runs preserve the
last valid local snapshot and write a sanitized status under `.codex`.

The collector uses the dedicated browser's ordinary TLS behavior. It does not
read the user's main Chrome profile, copy cookie databases, spoof TLS
fingerprints, solve challenges, or retry a block aggressively.

## Persisted Data

Active local files:

- `data/walmart_items.csv`: purchased-item facts and canonical saved links;
- `data/walmart_deals_local.json`: latest verified local result;
- `.codex/price-watch-on-demand-status.json`: latest sanitized job status.

The old `data/walmart_deals.json`, VPS price-watch service files, client
scripts, and remote SQLite state are retained only for rollback and release
history. No application route, timer, or startup shortcut invokes them.

## Verified State On 2026-07-25

The real local Square One page exposed 50 purchased-item price rows and four
valid `Was` discounts. All four names matched exactly one locally purchased
product, so their original saved links were reused.

Both VPS timers, `expense-price-watch.timer` and
`expense-price-watch-trigger.timer`, are inactive. The Windows sign-in
price-result receiver has been removed.
