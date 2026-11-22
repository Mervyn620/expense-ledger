SCHEMA_VERSION = 9

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS pending_csv_publications (
    filename TEXT PRIMARY KEY,
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    card_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    network TEXT,
    issuer TEXT,
    last4 TEXT,
    kind TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    order_id TEXT NOT NULL,
    date TEXT NOT NULL,
    year INTEGER,
    month TEXT,
    merchant TEXT,
    fulfillment TEXT,
    category TEXT,
    item_summary TEXT,
    quantity TEXT,
    item_total REAL NOT NULL DEFAULT 0,
    tax REAL NOT NULL DEFAULT 0,
    fees REAL NOT NULL DEFAULT 0,
    tip REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'CAD',
    source_url TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(platform, order_id)
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY,
    order_pk INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    line_key TEXT NOT NULL,
    category TEXT,
    product_name TEXT NOT NULL,
    quantity TEXT,
    price REAL,
    delivery_status TEXT,
    product_link TEXT,
    currency TEXT NOT NULL DEFAULT 'CAD',
    updated_at TEXT NOT NULL,
    UNIQUE(order_pk, line_key)
);

CREATE TABLE IF NOT EXISTS order_payments (
    id INTEGER PRIMARY KEY,
    order_pk INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    payment_index INTEGER NOT NULL DEFAULT 1,
    card_id TEXT REFERENCES cards(card_id),
    payment_method TEXT,
    amount REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'CAD',
    status TEXT,
    source TEXT,
    payment_channel TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(order_pk, payment_index)
);

CREATE TABLE IF NOT EXISTS payment_evidence (
    id INTEGER PRIMARY KEY,
    order_pk INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    payment_method TEXT,
    card_network TEXT,
    card_last4 TEXT,
    status TEXT,
    evidence_snippet TEXT,
    page_url TEXT,
    captured_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(order_pk, source)
);

CREATE TABLE IF NOT EXISTS bank_accounts (
    account_id TEXT PRIMARY KEY,
    institution TEXT NOT NULL,
    display_name TEXT NOT NULL,
    last4 TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bank_transactions (
    id INTEGER PRIMARY KEY,
    transaction_id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL REFERENCES bank_accounts(account_id),
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    direction TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CAD',
    source TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bank_reconciliations (
    id INTEGER PRIMARY KEY,
    bank_transaction_pk INTEGER NOT NULL UNIQUE REFERENCES bank_transactions(id) ON DELETE CASCADE,
    order_pk INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    match_status TEXT NOT NULL,
    match_method TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cash_withdrawal_notes (
    bank_transaction_pk INTEGER PRIMARY KEY REFERENCES bank_transactions(id) ON DELETE CASCADE,
    note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

-- User-entered wallet payments are independent of imported orders and bank
-- transactions. Integer minor units keep CNY and CAD exact and separate.
CREATE TABLE IF NOT EXISTS manual_wechat_payments (
    id INTEGER PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    paid_on TEXT NOT NULL,
    merchant TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL CHECK (currency IN ('CNY', 'CAD')),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_manual_wechat_paid_on
    ON manual_wechat_payments(paid_on, id);

CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(date);
CREATE INDEX IF NOT EXISTS idx_orders_month ON orders(month);
CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_pk);
CREATE INDEX IF NOT EXISTS idx_payments_order ON order_payments(order_pk);
CREATE INDEX IF NOT EXISTS idx_bank_transactions_date ON bank_transactions(date);
CREATE INDEX IF NOT EXISTS idx_bank_transactions_account ON bank_transactions(account_id);

-- Stable source keys deliberately survive deletion of a cancelled order.
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    order_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    observed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    before_json TEXT,
    after_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_order_events_source ON order_events(platform, order_id, id);

CREATE TABLE IF NOT EXISTS collection_observations (
    run_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    backend TEXT NOT NULL,
    status TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    requested_from TEXT,
    coverage TEXT NOT NULL DEFAULT 'bounded',
    message TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(run_id, platform, backend)
);

CREATE TABLE IF NOT EXISTS order_refunds (
    id INTEGER PRIMARY KEY,
    order_pk INTEGER NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    bank_transaction_pk INTEGER NOT NULL UNIQUE REFERENCES bank_transactions(id) ON DELETE RESTRICT,
    amount REAL NOT NULL CHECK(amount > 0),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS observed_date_ranges (
    run_id TEXT NOT NULL, platform TEXT NOT NULL, backend TEXT NOT NULL,
    first_date TEXT NOT NULL, last_date TEXT NOT NULL,
    PRIMARY KEY(run_id,platform,backend),
    FOREIGN KEY(run_id,platform,backend) REFERENCES collection_observations(run_id,platform,backend)
);

CREATE VIEW IF NOT EXISTS expense_orders AS
SELECT o.id, o.platform, o.order_id, o.date, o.year, o.month, o.merchant,
       o.fulfillment, o.category, o.item_summary, o.quantity, o.item_total,
       o.tax, o.fees, o.tip, o.currency, o.source_url, o.notes, o.updated_at,
       o.total AS gross_total,
       ROUND(COALESCE(r.amount, 0), 2) AS refund_total,
       ROUND(o.total - COALESCE(r.amount, 0), 2) AS total
FROM orders o
LEFT JOIN (SELECT order_pk, SUM(amount) AS amount FROM order_refunds GROUP BY order_pk) r
ON r.order_pk = o.id;

-- Corrections apply to this platform's product name across imports. Item row
-- ids can change when a receipt is refreshed.
CREATE TABLE IF NOT EXISTS product_category_rules (
    platform TEXT NOT NULL,
    product_key TEXT NOT NULL,
    category TEXT NOT NULL,
    PRIMARY KEY(platform, product_key)
);

CREATE VIEW IF NOT EXISTS classified_order_items AS
WITH grocery_phrases(phrase) AS (VALUES
 ('banana, sold in singles'), ('large 12 eggs'), ('greek 0% yogurt'), ('spring water 500ml'),
 ('cooked pancetta'), ('apple, gala, sold in singles'), ('smoked bacon'), ('pacific white shrimp'),
 ('large flake oats'), ('peaches yellow bulk'), ('black peppercorns grinder')
), inferred AS (
    SELECT i.id, 'groceries' AS category FROM order_items i JOIN orders o ON o.id=i.order_pk
    WHERE o.platform='walmart_ca' AND COALESCE(i.category,'') IN ('','uncategorized','mixed')
      AND EXISTS(SELECT 1 FROM grocery_phrases g WHERE instr(lower(i.product_name),g.phrase)>0)
)
SELECT i.*, COALESCE(r.category,g.category,NULLIF(NULLIF(i.category,''),'mixed'), 'uncategorized') AS effective_category,
       CASE WHEN r.category IS NOT NULL THEN 'user_rule' WHEN g.category IS NOT NULL THEN 'name_rule'
            ELSE 'source' END AS category_source,
       CASE WHEN o.platform IN ('scotia','scotiabank','bank') OR o.fulfillment='bank_reconciled'
         OR lower(trim(i.product_name)) IN ('bank-recorded purchase','bank recorded purchase',
              'bank-reconciled walmart charge','bank reconciled walmart charge') THEN 0 ELSE 1 END AS is_product
FROM order_items i JOIN orders o ON o.id=i.order_pk
LEFT JOIN inferred g ON g.id=i.id
LEFT JOIN product_category_rules r ON r.platform=o.platform AND r.product_key=lower(trim(i.product_name));

-- One or more signed allocations per order, expressed in its original currency.
-- A fully priced single-category basket can absorb its own fees and refunds.
-- For a mixed or incomplete receipt the residual remains explicitly unallocated.
CREATE VIEW IF NOT EXISTS order_category_amounts AS
WITH lines AS (
    SELECT i.order_pk, i.effective_category AS category, COUNT(*) AS n,
           COUNT(CASE WHEN i.currency=o.currency THEN i.price END) AS priced,
           SUM(CASE WHEN i.currency=o.currency THEN CAST(ROUND(i.price*100) AS INTEGER) END) AS cents
    FROM classified_order_items i JOIN orders o ON o.id=i.order_pk
    WHERE i.is_product=1 GROUP BY i.order_pk,i.effective_category
), receipts AS (
    SELECT order_pk, COUNT(*) AS categories, MIN(category) AS category,
           SUM(n) AS n, SUM(priced) AS priced, SUM(cents) AS cents
    FROM lines GROUP BY order_pk
), balances AS (
    SELECT o.id AS order_pk, CAST(ROUND(o.total*100) AS INTEGER) AS net,
           COALESCE(r.cents,0) AS priced_cents,
           CASE WHEN o.platform IN ('scotia','scotiabank','bank') OR o.fulfillment='bank_reconciled'
                THEN COALESCE(NULLIF(o.category,''),'uncategorized')
                WHEN r.categories=1 AND r.n=r.priced
                  AND r.cents=CAST(ROUND(o.item_total*100) AS INTEGER)
                  AND ROUND(o.gross_total*100)=ROUND((o.item_total+o.tax+o.fees+o.tip)*100)
                  THEN r.category
                ELSE 'unallocated' END AS residual_category
    FROM expense_orders o LEFT JOIN receipts r ON r.order_pk=o.id
), entries AS (
    SELECT order_pk,category,cents FROM lines WHERE priced>0
    UNION ALL
    SELECT order_pk,residual_category,net-priced_cents FROM balances
    WHERE net<>priced_cents OR order_pk NOT IN (SELECT order_pk FROM lines WHERE priced>0)
)
SELECT order_pk,category,SUM(cents) AS cents FROM entries GROUP BY order_pk,category;

-- Keep evidence valid even if a later importer updates the bank/order record.
CREATE TRIGGER IF NOT EXISTS refund_validate_insert BEFORE INSERT ON order_refunds
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM orders o JOIN bank_transactions bt ON bt.id=NEW.bank_transaction_pk
    WHERE o.id=NEW.order_pk AND bt.direction='credit' AND bt.currency=o.currency
      AND bt.date>=o.date AND ROUND(ABS(bt.amount),2)=NEW.amount
      AND ROUND(o.total-COALESCE((SELECT SUM(amount) FROM order_refunds WHERE order_pk=o.id),0),2)>=NEW.amount
      AND NOT EXISTS (SELECT 1 FROM bank_reconciliations b WHERE b.bank_transaction_pk=bt.id AND b.order_pk IS NOT NULL)
  ) THEN RAISE(ABORT,'Invalid or conflicting bank refund evidence') END;
END;
CREATE TRIGGER IF NOT EXISTS refund_no_update BEFORE UPDATE ON order_refunds
BEGIN
  SELECT RAISE(ABORT,'Unlink and relink a refund instead of overwriting its evidence');
END;
CREATE TRIGGER IF NOT EXISTS refund_guard_order_update BEFORE UPDATE ON orders
WHEN EXISTS (SELECT 1 FROM order_refunds r WHERE r.order_pk=OLD.id)
BEGIN
  SELECT CASE WHEN ROUND(NEW.total,2)<ROUND((SELECT SUM(amount) FROM order_refunds WHERE order_pk=OLD.id),2)
    OR EXISTS (SELECT 1 FROM order_refunds r JOIN bank_transactions bt ON bt.id=r.bank_transaction_pk
      WHERE r.order_pk=OLD.id AND (bt.currency<>NEW.currency OR bt.date<NEW.date))
    THEN RAISE(ABORT,'Order change conflicts with a confirmed refund; review refund links first') END;
END;
CREATE TRIGGER IF NOT EXISTS refund_guard_bank_update BEFORE UPDATE ON bank_transactions
WHEN EXISTS (SELECT 1 FROM order_refunds r WHERE r.bank_transaction_pk=OLD.id)
BEGIN
  SELECT CASE WHEN EXISTS (SELECT 1 FROM order_refunds r JOIN orders o ON o.id=r.order_pk
    WHERE r.bank_transaction_pk=OLD.id AND (NEW.direction<>'credit' OR NEW.currency<>o.currency
      OR NEW.date<o.date OR ROUND(ABS(NEW.amount),2)<>r.amount))
    THEN RAISE(ABORT,'Bank change conflicts with a confirmed refund; review refund links first') END;
END;
CREATE TRIGGER IF NOT EXISTS refund_guard_reconciliation_insert BEFORE INSERT ON bank_reconciliations
WHEN NEW.order_pk IS NOT NULL AND EXISTS (SELECT 1 FROM order_refunds r WHERE r.bank_transaction_pk=NEW.bank_transaction_pk)
BEGIN
  SELECT RAISE(ABORT,'Bank credit is already linked as a refund');
END;
CREATE TRIGGER IF NOT EXISTS refund_guard_reconciliation_update BEFORE UPDATE ON bank_reconciliations
WHEN NEW.order_pk IS NOT NULL AND EXISTS (SELECT 1 FROM order_refunds r WHERE r.bank_transaction_pk=NEW.bank_transaction_pk)
BEGIN
  SELECT RAISE(ABORT,'Bank credit is already linked as a refund');
END;

CREATE TRIGGER IF NOT EXISTS order_audit_insert AFTER INSERT ON orders
BEGIN
  INSERT INTO order_events(platform,order_id,event_type,after_json)
  VALUES(NEW.platform,NEW.order_id,'recorded',json_object('date',NEW.date,
    'fulfillment',NEW.fulfillment,'total',NEW.total,'currency',NEW.currency));
END;
CREATE TRIGGER IF NOT EXISTS order_audit_update AFTER UPDATE ON orders
WHEN OLD.total IS NOT NEW.total OR OLD.fulfillment IS NOT NEW.fulfillment
  OR OLD.date IS NOT NEW.date OR OLD.category IS NOT NEW.category
  OR OLD.item_total IS NOT NEW.item_total OR OLD.tax IS NOT NEW.tax
BEGIN
  INSERT INTO order_events(platform,order_id,event_type,before_json,after_json)
  VALUES(NEW.platform,NEW.order_id,'changed',
    json_object('date',OLD.date,'fulfillment',OLD.fulfillment,'total',OLD.total,
      'item_total',OLD.item_total,'tax',OLD.tax,'category',OLD.category),
    json_object('date',NEW.date,'fulfillment',NEW.fulfillment,'total',NEW.total,
      'item_total',NEW.item_total,'tax',NEW.tax,'category',NEW.category));
END;
CREATE TRIGGER IF NOT EXISTS order_audit_delete BEFORE DELETE ON orders
BEGIN
  INSERT INTO order_events(platform,order_id,event_type,before_json)
  VALUES(OLD.platform,OLD.order_id,'removed',json_object('date',OLD.date,
    'fulfillment',OLD.fulfillment,'total',OLD.total,'currency',OLD.currency));
END;
CREATE TRIGGER IF NOT EXISTS refund_audit_insert AFTER INSERT ON order_refunds
BEGIN
  INSERT INTO order_events(platform,order_id,event_type,after_json)
  SELECT platform,order_id,'refund_linked',json_object('amount',NEW.amount,
    'bank_transaction_pk',NEW.bank_transaction_pk) FROM orders WHERE id=NEW.order_pk;
END;
CREATE TRIGGER IF NOT EXISTS refund_audit_delete BEFORE DELETE ON order_refunds
BEGIN
  INSERT INTO order_events(platform,order_id,event_type,before_json)
  SELECT platform,order_id,'refund_unlinked',json_object('amount',OLD.amount,
    'bank_transaction_pk',OLD.bank_transaction_pk) FROM orders WHERE id=OLD.order_pk;
END;

CREATE VIEW IF NOT EXISTS powerbi_orders AS
SELECT
    o.id AS order_pk,
    o.date,
    o.year,
    o.month,
    o.platform,
    o.order_id,
    o.merchant,
    o.fulfillment,
    o.category,
    o.item_summary,
    o.quantity,
    o.item_total,
    o.tax,
    o.fees,
    o.tip,
    o.total,
    o.currency,
    COALESCE(p.card_id, 'unassigned') AS card_id,
    COALESCE(c.display_name, COALESCE(p.card_id, 'unassigned')) AS card_label,
    c.network AS card_network,
    c.issuer AS card_issuer,
    c.last4 AS card_last4,
    p.payment_method,
    p.payment_channel,
    p.status AS payment_status,
    p.source AS payment_source,
    p.notes AS payment_notes,
    o.source_url,
    o.notes AS order_notes,
    o.updated_at
FROM orders o
LEFT JOIN order_payments p ON p.order_pk = o.id AND p.payment_index = 1
LEFT JOIN cards c ON c.card_id = p.card_id;

CREATE VIEW IF NOT EXISTS powerbi_order_items AS
SELECT
    o.id AS order_pk,
    i.id AS item_pk,
    o.date,
    o.year,
    o.month,
    o.platform,
    o.order_id,
    o.merchant,
    o.category AS order_category,
    o.total AS order_total,
    COALESCE(p.card_id, 'unassigned') AS card_id,
    COALESCE(c.display_name, COALESCE(p.card_id, 'unassigned')) AS card_label,
    p.payment_method,
    p.payment_channel,
    p.status AS payment_status,
    i.line_key,
    i.category AS item_category,
    i.product_name,
    i.quantity AS item_quantity,
    i.price AS item_price,
    i.currency,
    i.delivery_status,
    i.product_link
FROM order_items i
JOIN orders o ON o.id = i.order_pk
LEFT JOIN order_payments p ON p.order_pk = o.id AND p.payment_index = 1
LEFT JOIN cards c ON c.card_id = p.card_id;

CREATE VIEW IF NOT EXISTS powerbi_monthly_summary AS
SELECT
    o.year,
    o.month,
    o.platform,
    o.category,
    COALESCE(p.card_id, 'unassigned') AS card_id,
    COALESCE(c.display_name, COALESCE(p.card_id, 'unassigned')) AS card_label,
    p.payment_channel,
    o.currency,
    COUNT(*) AS orders,
    ROUND(SUM(o.item_total), 2) AS item_total,
    ROUND(SUM(o.tax), 2) AS tax,
    ROUND(SUM(o.fees), 2) AS fees,
    ROUND(SUM(o.tip), 2) AS tip,
    ROUND(SUM(o.total), 2) AS total
FROM orders o
LEFT JOIN order_payments p ON p.order_pk = o.id AND p.payment_index = 1
LEFT JOIN cards c ON c.card_id = p.card_id
GROUP BY o.year, o.month, o.platform, o.category, p.card_id, c.display_name,
         p.payment_channel, o.currency;

CREATE VIEW IF NOT EXISTS powerbi_payment_review AS
SELECT *
FROM powerbi_orders
WHERE COALESCE(payment_status, '') IN ('', 'needs_review')
   OR card_id = 'unassigned';

CREATE VIEW IF NOT EXISTS powerbi_bank_reconciliation AS
SELECT
    bt.id AS bank_transaction_pk,
    bt.transaction_id,
    bt.account_id,
    ba.display_name AS account_label,
    bt.date AS bank_date,
    bt.description,
    bt.amount AS bank_amount,
    bt.direction,
    bt.currency AS bank_currency,
    br.match_status,
    br.match_method,
    br.notes AS reconciliation_notes,
    o.id AS order_pk,
    o.date AS order_date,
    o.platform,
    o.order_id,
    o.merchant,
    o.category,
    o.total AS order_total,
    o.currency AS order_currency
FROM bank_transactions bt
LEFT JOIN bank_accounts ba ON ba.account_id = bt.account_id
LEFT JOIN bank_reconciliations br ON br.bank_transaction_pk = bt.id
LEFT JOIN orders o ON o.id = br.order_pk;
"""


def create_schema(conn):
    existing_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if "order_payments" in existing_tables:
        payment_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(order_payments)")
        }
        if "payment_channel" not in payment_columns:
            conn.execute(
                "ALTER TABLE order_payments ADD COLUMN payment_channel TEXT"
            )

    conn.executescript(SCHEMA_SQL)
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if current_version >= SCHEMA_VERSION:
        return

    conn.execute("""
        INSERT INTO order_events(platform,order_id,event_type,after_json)
        SELECT o.platform,o.order_id,'baseline',json_object('date',o.date,
          'fulfillment',o.fulfillment,'total',o.total,'currency',o.currency)
        FROM orders o WHERE NOT EXISTS (
          SELECT 1 FROM order_events e WHERE e.platform=o.platform AND e.order_id=o.order_id)
    """)

    conn.executescript("""
    DROP VIEW IF EXISTS powerbi_bank_reconciliation;
    DROP VIEW IF EXISTS powerbi_payment_review;
    DROP VIEW IF EXISTS powerbi_monthly_summary;
    DROP VIEW IF EXISTS powerbi_order_items;
    DROP VIEW IF EXISTS powerbi_orders;
    DROP VIEW IF EXISTS order_category_amounts;
    DROP VIEW IF EXISTS classified_order_items;
    """)
    conn.executescript(SCHEMA_SQL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
