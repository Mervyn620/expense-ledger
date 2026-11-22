# Bank statements (any bank)

Scotiabank accounts can still be collected automatically from online banking.
Every other bank — and Scotia too, if you prefer not to automate the login —
works by importing the statement file the bank lets you download.

## 1. Download a statement

In your bank's online banking, download account or card activity as **OFX**,
**QFX** ("Quicken"), or **CSV** ("spreadsheet"). OFX/QFX is preferred: it
carries a transaction id and type for every row, so re-imports and transfers
are handled exactly.

## 2. Import it

```powershell
cd backend
.\.app-runtime\Scripts\python.exe scripts\importers\bank_statement.py C:\Downloads\activity.qfx --account rbc_visa --institution RBC --statement-type credit_card
```

| Option | Meaning |
|---|---|
| `--account` | Your own id for the account: lowercase letters, digits and `_` (e.g. `td_chequing`). One file per account is kept in `data/bank_statements/`. |
| `--institution` | Bank name shown in the ledger. |
| `--statement-type` | `bank_account` (default) or `credit_card`. OFX card statements are detected automatically. |
| `--columns` | For CSV files without a header row, the column order, e.g. `date,description,debit,credit,balance`. Use `skip` for columns to ignore. |
| `--date-format` | Needed only when a CSV's dates could be either month/day or day/month, e.g. `%m/%d/%Y`. |
| `--purchases-positive` | CSV amount column shows purchases as positive numbers (common for credit-card exports). |
| `--dry-run` | Show what would be imported without writing anything. |

The same is available to the app at `POST /api/bank-statements/import`
(`account_id`, `filename`, `content`, and the options above).

Importing the same file twice, or two exports that overlap, never duplicates
rows. Only the last four digits of the account number are stored, and long
digit runs in descriptions (card or reference numbers) are masked.

## 3. Reconcile

Run any refresh (for example **重建本地报表**). Imported statements are
reconciled together with the last Scotia export using the same rules: platform
orders are matched by amount, currency and date; corrections cancel out;
anything ambiguous goes to the review queue.

## Optional account settings

Add the account to `config/payment_cards.json` to control how it appears and
which card pays:

```json
"bank_accounts": {
  "rbc_visa": {
    "institution": "RBC",
    "display_name": "RBC Visa 3333",
    "statement_type": "credit_card",
    "default_payment_instrument_id": "rbc_visa_card",
    "exclude_terms": ["ws investments"]
  }
}
```

* On a `credit_card` statement every purchase is confirmed as paid by
  `default_payment_instrument_id`.
* On a bank account, purchases stay *needs review* for the paying card unless
  the description proves the channel.
* `exclude_terms`: descriptions containing these words are treated as
  transfers, not spending (for example contributions to an investment account).

## How rows are read

`scripts/reconciliation/bank_adapters.py` holds one adapter per kind of source.
The generic adapter counts a debit as spending only when it is not a transfer:
e-transfers, ATM withdrawals, credit-card payments, bill payments and
investment contributions are excluded, as are OFX transfer types. When in
doubt it excludes the row, because importing a card payment from a chequing
account would double-count the purchases already on the card statement.
