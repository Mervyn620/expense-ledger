import importlib.util,json,sqlite3,tempfile,unittest
from pathlib import Path
from contextlib import closing
from decimal import Decimal
SCRIPT=Path(__file__).resolve().parents[1]/'scripts/importers/tnt.py'
spec=importlib.util.spec_from_file_location('tnt_import',SCRIPT);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class TntImportTest(unittest.TestCase):
    def payload(self):
        return {'contract':'expense-tnt-orders/v1','platform':'tnt_ca','orders':[{'order_id':'9001234','date':'2026-10-04','fulfillment':'scheduled','currency':'CAD','item_total':'30.00','tax':'1.00','fees':'3.00','tip':'0.00','total':'34.00','source_url':'https://www.tntsupermarket.com/eng/sales/order/view/order_id/9001234','items':[{'item_id':'1','title':'Example grocery','quantity':2,'line_total':'30.00','product_url':'https://www.tntsupermarket.com/eng/example.html'}]}]}
    def root(self,temp):
        root=Path(temp);(root/'data').mkdir();(root/'config').mkdir();(root/'config/payment_cards.json').write_text(json.dumps({'default_card_id':'unassigned','cards':{'unassigned':{'display_name':'Needs Review','kind':'unknown'}}}));return root
    def write(self,root,payload): (root/'data/tnt_orders.json').write_text(json.dumps(payload))
    def test_repeated_import_and_status_update_replace_children(self):
        with tempfile.TemporaryDirectory() as temp:
            root=self.root(temp);payload=self.payload();self.write(root,payload);module.import_file(root)
            payload['orders'][0]['fulfillment']='delivered';payload['orders'][0]['items'][0]['quantity']=1;self.write(root,payload);module.import_file(root)
            with closing(sqlite3.connect(root/'data/expenses.sqlite')) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM orders').fetchone()[0],1)
                self.assertEqual(conn.execute('SELECT count(*) FROM order_items').fetchone()[0],1)
                self.assertEqual(conn.execute('SELECT fulfillment,total FROM orders').fetchone(),('delivered',34.0))
                self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
                self.assertEqual(conn.execute('SELECT payment_channel,status FROM order_payments').fetchone(),('online','needs_review'))
    def test_cancelled_order_removes_prior_order_without_duplicate_spend(self):
        with tempfile.TemporaryDirectory() as temp:
            root=self.root(temp);self.write(root,self.payload());module.import_file(root)
            self.write(root,{'contract':'expense-tnt-orders/v1','platform':'tnt_ca','orders':[{'order_id':'9001234','date':'2026-10-04','excluded':'cancelled'}]});module.import_file(root)
            with closing(sqlite3.connect(root/'data/expenses.sqlite')) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM orders').fetchone()[0],0);self.assertEqual(conn.execute('SELECT count(*) FROM order_items').fetchone()[0],0)
    def test_invalid_batch_does_not_modify_existing_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            root=self.root(temp);self.write(root,self.payload());module.import_file(root);before=(root/'data/expenses.sqlite').read_bytes()
            invalid=self.payload();invalid['orders'][0]['total']='999.00';self.write(root,invalid)
            with self.assertRaises(ValueError):module.import_file(root)
            self.assertEqual((root/'data/expenses.sqlite').read_bytes(),before)
    def test_schema_and_duplicate_ids_are_rejected(self):
        for payload in [{'contract':'raw','platform':'tnt_ca','orders':[]},dict(self.payload(),platform='other')]:
            with self.assertRaises(ValueError):module.normalize_payload(payload)
        payload=self.payload();payload['orders']*=2
        with self.assertRaises(ValueError):module.normalize_payload(payload)
    def test_duplicate_product_lines_preserve_total_quantity_and_price(self):
        payload=self.payload();line=payload["orders"][0]["items"][0];line["line_total"]="20.00"
        payload["orders"][0]["items"].append(dict(line,item_id="2",quantity=1,line_total="10.00"))
        _,items,_=module.normalize_payload(payload)
        self.assertEqual(len(items),1);self.assertEqual(Decimal(items[0]["quantity"]),3);self.assertEqual(Decimal(items[0]["price"]),30)
    def test_discount_and_item_amounts_must_reconcile(self):
        payload=self.payload();payload["orders"][0]["items"][0]["line_total"]="35.00"
        with self.assertRaises(ValueError):module.normalize_payload(payload)
        payload["orders"][0]["discount_amount"]="-5.00"
        self.assertEqual(len(module.normalize_payload(payload)[0]),1)
    def test_job_dispatch_passes_only_requested_platforms(self):
        from app.jobs import JobManager
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            manager=JobManager(root=Path(temp))
            with patch.object(manager,'_start') as start:
                manager.start_refresh('stores',collector_backend='local',collector_platforms=['walmart_ca','fantuan','tnt_ca'])
            self.assertEqual(start.call_args.args[2][-2:],['-CollectorPlatforms','walmart_ca,fantuan,tnt_ca'])
            self.assertIn('大统华',start.call_args.args[1])
if __name__=='__main__':unittest.main()
