# Power BI Connection

This workspace exposes the SQLite ledger through a 64-bit ODBC DSN.

## Local DSN

- DSN name: `expenses_sqlite`
- Driver: `SQLite3 ODBC Driver`
- Database: `data/expenses.sqlite`

Recreate or verify the DSN:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\reporting\setup_powerbi_odbc.ps1
```

## Recommended Tables

Use these Power BI friendly views first:

- `powerbi_orders`: one row per order with card/payment fields.
- `powerbi_order_items`: item-level rows joined to order and card fields.
- `powerbi_monthly_summary`: monthly totals by platform, category, and card.
- `powerbi_payment_review`: orders whose payment assignment still needs review.
- `powerbi_bank_reconciliation`: sanitized Scotia transaction match status.

The normalized source tables are still available:

- `orders`
- `order_items`
- `order_payments`
- `cards`
- `bank_transactions`
- `bank_reconciliations`

## Suggested Model

If you import normalized tables instead of the `powerbi_*` views, create these
relationships:

```text
orders[id] 1 -> * order_items[order_pk]
orders[id] 1 -> * order_payments[order_pk]
cards[card_id] 1 -> * order_payments[card_id]
bank_transactions[id] 1 -> * bank_reconciliations[bank_transaction_pk]
orders[id] 1 -> * bank_reconciliations[order_pk]
```

## Useful Measures

```DAX
Total Spend = SUM(powerbi_orders[total])

Order Count = DISTINCTCOUNT(powerbi_orders[order_pk])

Average Order = DIVIDE([Total Spend], [Order Count])

Item Spend = SUM(powerbi_order_items[item_price])
```
