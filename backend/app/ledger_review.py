"""Human decisions against the current ledger, serialized with imports."""
import sqlite3
from collections import defaultdict
from contextlib import closing

from scripts.reconciliation import scotia


def available_orders(conn):
    return [dict(row) for row in conn.execute("""
        SELECT id AS order_pk,platform,order_id,date,merchant,total,currency FROM orders o
        WHERE platform NOT IN ('scotia','scotiabank','bank') AND NOT EXISTS (
            SELECT 1 FROM bank_reconciliations br WHERE br.order_pk=o.id AND br.match_status='matched_order')
    """)]


def review_queue(conn):
    by_amount = defaultdict(list)
    for order in available_orders(conn):
        for amount in scotia.charge_amounts(order, None):
            by_amount[(order["currency"], amount)].append(order)
    rows = []
    for raw in conn.execute("""
        SELECT bt.*,br.match_method,br.match_status,br.notes FROM bank_transactions bt
        JOIN bank_reconciliations br ON br.bank_transaction_pk=bt.id
        WHERE br.match_status IN ('unmatched_platform_review','bank_only_imported')
        ORDER BY bt.date DESC, bt.id DESC
    """):
        bank = dict(raw)
        candidates = scotia.review_candidates(bank, by_amount.get((bank["currency"], scotia.money(bank["amount"])), ()))
        if not candidates and bank["match_status"] == "bank_only_imported":
            continue
        profile = scotia.merchant_profile(bank)
        rows.append({key: bank[key] for key in ("transaction_id","date","description","amount",
                                                "currency","match_status","match_method","notes")})
        rows[-1].update(merchant=profile["merchant"], detected_platform=profile["platform"],
                        category=profile["category"], candidates=candidates)
    return rows


def remove_bank_placeholder(conn, root, order_pk, transaction_id):
    order = conn.execute("SELECT platform,order_id FROM orders WHERE id=?", (order_pk,)).fetchone()
    expected = {("scotia", f"scotia-{transaction_id}"), ("scotiabank", f"scotia-{transaction_id}"),
                ("bank", f"bank-{transaction_id}")}
    if not order or tuple(order) not in expected:
        raise ValueError("原银行占位记录已变化，请刷新核对。")
    if conn.execute("SELECT 1 FROM order_refunds WHERE order_pk=?", (order_pk,)).fetchone():
        raise ValueError("银行占位记录已关联退款，请先核对并撤销该退款关联。")
    for filename, fields in (("transactions.csv", scotia.TRANSACTION_FIELDS),
                             ("delivery_items.csv", scotia.ITEM_FIELDS),
                             ("bank_items.csv", scotia.ITEM_FIELDS),
                             ("payment_assignments.csv", scotia.ASSIGNMENT_FIELDS)):
        rows = scotia.read_csv(root / "data" / filename, fields, recover=False)
        kept = [r for r in rows if scotia.tx_key_tuple(r) != tuple(order)]
        if len(kept) != len(rows):
            scotia.queue_csv_publications(conn, root, [(filename, fields, kept)])
    conn.execute("DELETE FROM orders WHERE id=?", (order_pk,))


def write_connection(db_path):
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=rw", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("BEGIN IMMEDIATE")
    return conn


def confirm_match(repository, transaction_id, platform, order_id):
    root = repository.db_path.parent.parent
    with closing(write_connection(repository.db_path)) as conn:
        scotia._publish_csv_publications(conn, root)
        row = conn.execute("""
            SELECT bt.*,br.match_status,br.match_method,br.order_pk FROM bank_transactions bt
            JOIN bank_reconciliations br ON br.bank_transaction_pk=bt.id WHERE bt.transaction_id=?
        """, (transaction_id,)).fetchone()
        target = conn.execute("SELECT id FROM orders WHERE platform=? AND order_id=?",
                              (platform, order_id)).fetchone()
        if row and target and row["match_method"] == "manual_confirmed" and row["order_pk"] == target[0]:
            conn.commit()
            return
        if not row or not target or row["match_status"] not in {"unmatched_platform_review","bank_only_imported"}:
            raise ValueError("该扣款或订单的状态已变化，请刷新核对。")
        valid = scotia.review_candidates(dict(row), available_orders(conn))
        if not any(o["order_pk"] == target[0] for o in valid):
            raise ValueError("该订单已被匹配，或商户、日期、币种、金额与候选条件不符。")
        if row["match_status"] == "bank_only_imported":
            remove_bank_placeholder(conn, root, row["order_pk"], transaction_id)
        conn.execute("""UPDATE bank_reconciliations SET order_pk=?,match_status='matched_order',
                     match_method='manual_confirmed',notes='',updated_at=? WHERE bank_transaction_pk=?""",
                     (target[0], scotia.now_iso(), row["id"]))
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        scotia._publish_csv_publications(conn, root)
        conn.commit()


def set_product_rule(repository, order_pk, item_pk, product_name, category):
    with closing(write_connection(repository.db_path)) as conn:
        item = conn.execute("""
            SELECT o.platform,lower(trim(i.product_name)) FROM classified_order_items i
            JOIN orders o ON o.id=i.order_pk WHERE i.order_pk=? AND i.id=? AND i.product_name=? AND i.is_product=1
        """, (order_pk,item_pk,product_name)).fetchone()
        if not item:
            raise ValueError("商品行已变化，请重新打开订单。")
        if category is None:
            conn.execute("DELETE FROM product_category_rules WHERE platform=? AND product_key=?", tuple(item))
        else:
            conn.execute("""INSERT INTO product_category_rules VALUES (?,?,?) ON CONFLICT(platform,product_key)
                DO UPDATE SET category=excluded.category""", (*item, category))
        conn.commit()
