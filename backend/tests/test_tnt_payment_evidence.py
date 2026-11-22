import json,tempfile,unittest,importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('payment_import_for_tnt',ROOT/'scripts/importers/payment_evidence.py')
payment_import=importlib.util.module_from_spec(spec);spec.loader.exec_module(payment_import)
from tnt_payment_evidence import load_tnt_payment_evidence
class TntPaymentEvidenceTest(unittest.TestCase):
    def test_order_title_cannot_override_a_card_tail_from_the_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.cache(root, payment_method="Credit Card")
            receipt = {"platform": "tnt_ca", "order_id": "9001234", "payment_method": "card",
                       "card_last4": "2222", "status": "confirmed", "source": "tnt_ca_payment_dom"}
            path = root / "data/payment_evidence.json"
            path.write_text(json.dumps({"evidence": [receipt]}), encoding="utf-8")
            self.assertEqual(payment_import.load_evidence(path), [receipt])

    def cache(self,root,**changes):
        payment={'payment_method':'WeChat Pay','card_network':'','card_last4':'','status':'method_only','source':'tnt_order_details_api',**changes}
        payload={'contract':'expense-tnt-orders/v1','platform':'tnt_ca','generated_at':'2026-10-01T10:00:00Z','orders':[{'order_id':'9001234','payment':payment}]}
        (root/'data').mkdir(exist_ok=True);(root/'data/tnt_orders.json').write_text(json.dumps(payload),encoding='utf-8')
    def test_api_title_maps_to_wallet_with_original_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.cache(root)
            evidence=payment_import.load_evidence(root/'data/payment_evidence.json')
            cards={'unassigned':{'display_name':'Unknown'},'wechat_pay':{'display_name':'WeChat Pay','kind':'digital_wallet','network':'Digital wallet','last4':''}}
            rows,stats=payment_import.update_assignments([{'platform':'tnt_ca','order_id':'9001234','total':'30.00'}],[],evidence,cards,'unassigned')
            self.assertEqual(rows[0]['card_id'],'wechat_pay');self.assertEqual(rows[0]['assignment_status'],'confirmed');self.assertEqual(rows[0]['assignment_source'],'tnt_order_details_api')
            self.assertEqual(stats['confirmed'],1);self.assertFalse((root/'data/expenses.sqlite').exists())
    def test_gateway_only_or_arbitrary_title_cannot_confirm_wallet(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for method in ['alphapaydirect','Unexpected']:
                self.cache(root,payment_method=method)
                with self.assertRaises(ValueError):load_tnt_payment_evidence(root)
    def test_sensitive_fields_and_inconsistent_network_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for changes in [{'token':'secret'},{'card_last4':'1234'},{'card_network':'Visa'}]:
                self.cache(root,**changes)
                with self.assertRaises(ValueError):load_tnt_payment_evidence(root)
    def test_existing_wallet_evidence_and_fantuan_unresolved_card_are_kept(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.cache(root)
            old={'platform':'fantuan','order_id':'example-fantuan','payment_method':'信用卡/借记卡','status':'method_only'}
            (root/'data/payment_evidence.json').write_text(json.dumps({'evidence':[old]}),encoding='utf-8')
            evidence=payment_import.load_evidence(root/'data/payment_evidence.json')
            self.assertEqual(len(evidence),2);self.assertEqual(evidence[0]['platform'],'fantuan')
if __name__=='__main__':unittest.main()
