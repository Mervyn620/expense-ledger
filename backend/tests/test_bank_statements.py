"""Generic bank statements: adapters, OFX/CSV import, and reconciliation of imported rows."""
import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from scripts.importers import bank_statement
from scripts.importers.bank_statement import StatementError, import_statement, parse_csv, parse_ofx
from scripts.reconciliation import scotia
from scripts.reconciliation.bank_adapters import ADAPTERS, adapter_for, is_bank_only_order


GENERIC = ADAPTERS["generic"]
SCOTIA = ADAPTERS["scotia"]

OFX_BANK = """OFXHEADER:100
DATA:OFXSGML
VERSION:102

<OFX>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<CURDEF>CAD
<BANKACCTFROM><BANKID>000300000<ACCTID>5012345678901234<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN><TRNTYPE>POS<DTPOSTED>20261103120000[-5:EST]<TRNAMT>-12.50<FITID>A1<NAME>POS PURCHASE COFFEE CO</STMTTRN>
<STMTTRN><TRNTYPE>XFER<DTPOSTED>20261104<TRNAMT>-200.00<FITID>A2<NAME>E-TRANSFER SENT</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261105<TRNAMT>1500.00<FITID>A3<NAME>PAYROLL</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261106<TRNAMT>-35.24<FITID>A4<NAME>UBER* EATS<MEMO>TORONTO ON</STMTTRN>
</BANKTRANLIST>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""

OFX_CARD = """<?xml version="1.0"?>
<OFX><CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS>
<CURDEF>CAD</CURDEF>
<CCACCTFROM><ACCTID>4500111122223333</ACCTID></CCACCTFROM>
<BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20261110</DTPOSTED><TRNAMT>-48.10</TRNAMT><FITID>C1</FITID><NAME>GROCERY MART</NAME></STMTTRN>
<STMTTRN><TRNTYPE>CREDIT</TRNTYPE><DTPOSTED>20261112</DTPOSTED><TRNAMT>300.00</TRNAMT><FITID>C2</FITID><NAME>PAYMENT - THANK YOU</NAME></STMTTRN>
</BANKTRANLIST>
</CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1></OFX>
"""


class AdapterTest(unittest.TestCase):
    def test_scotia_rules_are_unchanged(self):
        self.assertTrue(SCOTIA.is_purchase("Pos Purchase Opos Walmart.Ca"))
        self.assertTrue(SCOTIA.is_transfer("Withdrawal Free Interac E-Transfer"))
        self.assertEqual(SCOTIA.payment_channel("Pos Purchase Fpos Shop"), "in_store")
        self.assertEqual(SCOTIA.merchant_key("Correction Opos Shop  Name"), "shop name")
        self.assertIs(adapter_for("any bare description"), SCOTIA)
        self.assertIs(adapter_for({"description": "x"}), SCOTIA)

    def test_generic_rows_are_selected_by_source_or_adapter(self):
        self.assertIs(adapter_for({"source": "statement_ofx"}), GENERIC)
        self.assertIs(adapter_for({"bank_adapter": "generic"}), GENERIC)

    def test_generic_purchase_transfer_and_payment_rules(self):
        purchase = {"description": "POS PURCHASE COFFEE CO", "kind": "pos"}
        self.assertTrue(GENERIC.is_purchase(purchase))
        self.assertEqual(GENERIC.merchant_key(purchase), "coffee co")
        for description in ("E-TRANSFER SENT", "PAYMENT - THANK YOU", "MB-CREDIT CARD/LOC PAY.",
                            "ATM WITHDRAWAL"):
            self.assertTrue(GENERIC.is_transfer({"description": description}), description)
            self.assertFalse(GENERIC.is_purchase({"description": description}), description)
        self.assertFalse(GENERIC.is_purchase({"description": "SOMETHING", "kind": "xfer"}))
        self.assertTrue(GENERIC.is_purchase({"description": "CSV ROW WITHOUT TYPE", "kind": ""}))

    def test_account_exclude_terms_keep_rows_out_of_spending(self):
        row = {"description": "WS INVESTMENTS CONTRIB", "exclude_terms": ["ws investments"]}
        self.assertTrue(GENERIC.is_transfer(row))

    def test_credit_card_statement_channel_and_fees(self):
        card = {"statement_type": "credit_card"}
        self.assertEqual(GENERIC.payment_channel({"description": "GROCERY"}, card), "card_statement")
        self.assertEqual(GENERIC.payment_channel({"description": "MONTHLY FEE"}, card), "account_debit")
        self.assertTrue(GENERIC.is_correction({"description": "REVERSAL GROCERY"}))

    def test_bank_only_identity(self):
        self.assertTrue(is_bank_only_order("bank", "bank-1"))
        self.assertTrue(is_bank_only_order("scotiabank", "scotia-1"))
        self.assertFalse(is_bank_only_order("uber_eats", "abc"))


class ParserTest(unittest.TestCase):
    def test_ofx_bank_statement(self):
        meta, rows = parse_ofx(OFX_BANK)
        self.assertEqual(meta, {"currency": "CAD", "statement_type": "bank_account",
                                "last4": "1234", "institution": ""})
        self.assertEqual([row["date"] for row in rows], ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"])
        self.assertEqual(rows[3]["description"], "UBER* EATS TORONTO ON")
        self.assertEqual((rows[0]["kind"], rows[0]["amount"], rows[0]["reference"]), ("pos", -12.5, "A1"))

    def test_ofx_credit_card_statement(self):
        meta, rows = parse_ofx(OFX_CARD)
        self.assertEqual((meta["statement_type"], meta["last4"]), ("credit_card", "3333"))
        self.assertEqual(len(rows), 2)

    def test_csv_with_header_and_debit_credit_columns(self):
        text = "Date,Description,Withdrawals,Deposits,Balance\n2026-11-03,COFFEE CO,12.50,,100\n2026-11-04,REFUND,,4.00,104\n"
        _, rows = parse_csv(text)
        self.assertEqual([(row["date"], row["amount"]) for row in rows], [("2026-11-03", -12.5), ("2026-11-04", 4.0)])

    def test_headerless_csv_needs_columns(self):
        text = "11/03/2026,COFFEE CO,12.50,,100.00\n11/24/2026,GROCERY,30.00,,70.00\n"
        with self.assertRaises(StatementError):
            parse_csv(text)
        _, rows = parse_csv(text, columns=["date", "description", "debit", "credit", "balance"])
        self.assertEqual([row["date"] for row in rows], ["2026-11-03", "2026-11-24"])  # month/day inferred

    def test_ambiguous_dates_are_not_guessed(self):
        text = "Date,Description,Amount\n03/04/2026,SHOP,-1.00\n05/06/2026,SHOP,-2.00\n"
        with self.assertRaises(StatementError):
            parse_csv(text)
        _, rows = parse_csv(text, date_format="%d/%m/%Y")
        self.assertEqual(rows[0]["date"], "2026-04-03")

    def test_purchases_positive_flips_card_exports(self):
        text = "Transaction Date,Description,Amount\n2026-11-10,GROCERY MART,48.10\n2026-11-12,PAYMENT - THANK YOU,-300.00\n"
        _, rows = parse_csv(text, purchases_positive=True)
        self.assertEqual([row["amount"] for row in rows], [-48.1, 300.0])

    def test_descriptions_mask_card_and_reference_numbers(self):
        self.assertEqual(bank_statement.clean_description("VISA DEBIT 4506********1234  SHOP"), "VISA DEBIT •••• SHOP")
        self.assertEqual(bank_statement.clean_description("REF 1234567890 SHOP"), "REF •••• SHOP")


class ImportTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def stored(self, account):
        return json.loads((self.root / "data/bank_statements" / f"{account}.json").read_text(encoding="utf-8-sig"))

    def test_reimport_is_idempotent_and_overlap_adds_only_new_rows(self):
        first = import_statement(OFX_BANK, account_id="rbc_chequing", filename="a.ofx",
                                 institution="RBC", root=self.root)
        self.assertEqual((first["added"], first["total_rows"]), (4, 4))
        again = import_statement(OFX_BANK, account_id="rbc_chequing", filename="a.ofx", root=self.root)
        self.assertEqual((again["added"], again["already_present"], again["total_rows"]), (0, 4, 4))
        extra = OFX_BANK.replace("</BANKTRANLIST>",
                                 "<STMTTRN><TRNTYPE>POS<DTPOSTED>20261107<TRNAMT>-5.00<FITID>A5<NAME>BAKERY</STMTTRN>\n</BANKTRANLIST>")
        self.assertEqual(import_statement(extra, account_id="rbc_chequing", filename="b.ofx", root=self.root)["added"], 1)
        payload = self.stored("rbc_chequing")
        self.assertEqual((payload["institution"], payload["last4"], payload["adapter"]), ("RBC", "1234", "generic"))
        self.assertNotIn("5012345678901234", json.dumps(payload))

    def test_csv_duplicates_on_one_day_stay_distinct(self):
        text = "Date,Description,Amount\n2026-11-03,TIM HORTONS,-2.10\n2026-11-03,TIM HORTONS,-2.10\n"
        summary = import_statement(text, account_id="td_chequing", filename="td.csv", root=self.root)
        self.assertEqual(summary["added"], 2)
        self.assertEqual(import_statement(text, account_id="td_chequing", filename="td.csv", root=self.root)["added"], 0)

    def test_invalid_account_id_is_rejected(self):
        with self.assertRaises(StatementError):
            import_statement(OFX_BANK, account_id="RBC Chequing!", root=self.root)


class ReconcileImportedStatementsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "data").mkdir()
        (self.root / "config").mkdir()
        self.database = self.root / "data/expenses.sqlite"
        config = {
            "cards": {"unassigned": {"display_name": "Needs Review"},
                      "account_debit": {"display_name": "Account debit"},
                      "rbc_visa_card": {"display_name": "RBC Visa 3333", "last4": "3333", "kind": "credit_card"}},
            "bank_accounts": {"rbc_visa": {"institution": "RBC", "display_name": "RBC Visa 3333",
                                           "statement_type": "credit_card",
                                           "default_payment_instrument_id": "rbc_visa_card"}},
        }
        (self.root / "config/payment_cards.json").write_text(json.dumps(config), encoding="utf-8")
        paths = {"ROOT": self.root, "DB_PATH": self.database,
                 "SCOTIA_PATH": self.root / "data/scotia_transactions.json",
                 "TRANSACTIONS_PATH": self.root / "data/transactions.csv",
                 "DELIVERY_ITEMS_PATH": self.root / "data/delivery_items.csv",
                 "BANK_ITEMS_PATH": self.root / "data/bank_items.csv",
                 "ASSIGNMENTS_PATH": self.root / "data/payment_assignments.csv",
                 "PAYMENT_CONFIG_PATH": self.root / "config/payment_cards.json",
                 "REPORT_DIR": self.root / "exports/reconciliation",
                 "REPORT_CSV": self.root / "exports/reconciliation/scotia.csv",
                 "REPORT_MD": self.root / "exports/reconciliation/scotia.md",
                 "_MERCHANT_RULES": copy.deepcopy(scotia.DEFAULT_MERCHANT_RULES)}
        patcher = patch.multiple(scotia, **paths)
        patcher.start()
        self.addCleanup(patcher.stop)

    def query(self, sql):
        with closing(sqlite3.connect(self.database)) as conn:
            return conn.execute(sql).fetchall()

    def test_statement_only_ledger_without_scotia(self):
        import_statement(OFX_BANK, account_id="rbc_chequing", filename="a.ofx", institution="RBC", root=self.root)
        stats = scotia.reconcile()
        self.assertEqual((stats["bank_transactions"], stats["transfers"], stats["credits"], stats["imported"]),
                         (4, 1, 1, 2))
        orders = self.query("SELECT platform, merchant, total FROM orders ORDER BY total")
        self.assertEqual(orders, [("bank", "COFFEE CO", 12.5), ("bank", "UBER* EATS TORONTO ON", 35.24)])
        self.assertTrue(all(order_id.startswith("bank-") for (order_id,) in self.query("SELECT order_id FROM orders")))
        self.assertEqual(self.query("SELECT institution FROM bank_accounts"), [("RBC",)])
        statuses = dict(self.query("SELECT bt.description, br.match_status FROM bank_reconciliations br "
                                   "JOIN bank_transactions bt ON bt.id=br.bank_transaction_pk"))
        self.assertEqual(statuses["E-TRANSFER SENT"], "excluded_transfer")

    def test_credit_card_statement_confirms_the_card(self):
        import_statement(OFX_CARD, account_id="rbc_visa", filename="visa.qfx", root=self.root)
        scotia.reconcile()
        self.assertEqual(self.query("SELECT card_id, status, source FROM order_payments"),
                         [("rbc_visa_card", "confirmed", "generic_card_statement")])
        self.assertEqual(self.query("SELECT display_name, last4 FROM bank_accounts"), [("RBC Visa 3333", "3333")])

    def test_scotia_and_imported_statements_reconcile_together(self):
        scotia.SCOTIA_PATH.write_text(json.dumps({"account_id": "test_account", "transactions": [
            {"transaction_id": "s1", "date": "2026-11-03", "description": "Pos Purchase Apos Example Shop",
             "amount": "9.99", "direction": "debit", "currency": "CAD"}]}), encoding="utf-8")
        import_statement(OFX_CARD, account_id="rbc_visa", filename="visa.qfx", root=self.root)
        stats = scotia.reconcile()
        self.assertEqual(stats["imported"], 2)
        self.assertEqual(self.query("SELECT platform, order_id FROM orders ORDER BY platform"),
                         [("bank", self.query("SELECT order_id FROM orders WHERE platform='bank'")[0][0]),
                          ("scotiabank", "scotia-s1")])
        self.assertEqual(sorted(self.query("SELECT account_id, institution FROM bank_accounts")),
                         [("rbc_visa", "RBC"), ("test_account", "Scotiabank")])

    def test_generic_charge_matches_a_platform_order(self):
        rules = {"platforms": {"uber_eats": {"terms": ["uber"], "required_any": ["eats"], "merchant": "Uber Eats",
                                             "category": "food_delivery", "requires_order_match": True}}}
        bank = {"transaction_id": "g1", "date": "2026-11-06", "description": "UBER* EATS TORONTO",
                "amount": "35.24", "direction": "debit", "currency": "CAD", "bank_adapter": "generic", "kind": "debit"}
        order = {"platform": "uber_eats", "order_id": "u1", "date": "2026-11-05", "total": "35.24", "currency": "CAD"}
        with patch.object(scotia, "_MERCHANT_RULES", rules):
            plans, _ = scotia.plan_existing_order_matches([bank], [order])
        self.assertEqual((plans["g1"]["order"]["order_id"], plans["g1"]["method"]),
                         ("u1", "platform_amount_unique_nearest_date_3d"))


class StatementApiTest(unittest.TestCase):
    def test_import_and_list(self):
        from app import main
        with tempfile.TemporaryDirectory() as temp, patch.object(main, "ROOT", Path(temp)):
            client = TestClient(main.app, base_url="http://127.0.0.1")
            response = client.post("/api/bank-statements/import", json={
                "account_id": "rbc_chequing", "filename": "a.ofx", "content": OFX_BANK, "institution": "RBC"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["added"], 4)
            listing = client.get("/api/bank-statements").json()["accounts"]
            self.assertEqual([(a["account_id"], a["rows"], a["date_range"]) for a in listing],
                             [("rbc_chequing", 4, ["2026-11-03", "2026-11-06"])])
            bad = client.post("/api/bank-statements/import", json={
                "account_id": "rbc_chequing", "filename": "x.csv", "content": "no,header\n1,2\n"})
            self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()
