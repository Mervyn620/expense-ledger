import importlib.util,json,subprocess,shutil,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts/reconciliation/source_refresh.py'
import sys
sys.path.insert(0,str(SCRIPT.parent))
spec=importlib.util.spec_from_file_location('source_refresh',SCRIPT)
planner=importlib.util.module_from_spec(spec);spec.loader.exec_module(planner)
MANIFEST=json.loads((ROOT/'config/collectors.json').read_text(encoding='utf-8-sig'))

class SourcePlanTest(unittest.TestCase):
    def row(self,key='bank-1',description='Pos Purchase Opos Walmart.ca',amount='25.00',**extra):
        return dict(transaction_id=key,date='2026-10-08',description=description,amount=amount,direction='debit',**extra)
    def plan(self,rows,orders=(),attempted=()):
        return planner.build_plan({'transactions':rows},list(orders),MANIFEST,'2026-05-01',attempted)
    def order(self,platform='walmart_ca',key='order-1',total='25.00'):
        return dict(platform=platform,order_id=key,date='2026-10-07',total=total)
    def test_absent_and_instore_membership_credit_transfer_need_no_pages(self):
        rows=[self.row('a','Pos Purchase Walmart Store'),self.row('b','Pos Purchase Amazon Prime Membership'),self.row('c','Withdrawal Interac e-transfer')]
        credit=self.row('d');credit['direction']='credit';rows.append(credit)
        self.assertEqual(self.plan(rows)['refresh_platforms'],[])
    def test_cached_exact_match_needs_no_browser_and_does_not_mutate(self):
        rows=[self.row()];orders=[self.order()];before=json.dumps([rows,orders])
        plan=self.plan(rows,orders)
        self.assertEqual(plan['refresh_platforms'],[])
        self.assertEqual(next(x for x in plan['platforms'] if x['collector_id']=='walmart_ca')['action'],'cached_match')
        self.assertEqual(json.dumps([rows,orders]),before)
    def test_only_missing_merchants_are_selected_with_posting_window(self):
        plan=self.plan([self.row(),self.row('b','Pos Purchase Amazon.ca','30.00')],[self.order()])
        self.assertEqual(plan['refresh_platforms'],['amazon_ca'])
        self.assertEqual(next(x for x in plan['platforms'] if x['collector_id']=='amazon_ca')['min_date'],'2026-10-05')
    def test_unique_split_charge_reuses_existing_order(self):
        self.assertEqual(self.plan([self.row('a',amount='15.00'),self.row('b',amount='10.00')],[self.order()])['refresh_platforms'],[])
    def test_reversed_pair_and_unsupported_platform_do_not_open_pages(self):
        credit=self.row('b');credit['direction']='credit';credit['description']='Correction Opos Walmart.ca'
        self.assertEqual(self.plan([self.row('a'),credit,self.row('c','Pos Purchase DoorDash')])['refresh_platforms'],[])
    def test_ambiguous_matches_remain_review_without_guess_or_extra_page(self):
        plan=self.plan([self.row()],[self.order(),self.order(key='order-2')])
        self.assertEqual(plan['refresh_platforms'],[])
        self.assertEqual(next(x for x in plan['platforms'] if x['collector_id']=='walmart_ca')['action'],'review')
    def test_already_attempted_collection_is_not_retried_by_reconciliation(self):
        self.assertEqual(self.plan([self.row()],attempted=['walmart_ca'])['refresh_platforms'],[])
    def test_invalid_bank_snapshot_rejected_before_actions(self):
        for rows in [[self.row(),self.row()], [self.row(amount='NaN')]]:
            with self.assertRaises(ValueError): self.plan(rows)
    @unittest.skipUnless(shutil.which('powershell.exe'),'Windows workflow integration')
    def test_powershell_dispatch_opens_only_bank_then_missing_platform(self):
        script=ROOT/'scripts/reconciliation/source-refresh.ps1'
        code=". '"+str(script).replace("'","''")+"'\n"
        code+='$rows=@([pscustomobject]@{id="walmart_ca";tab_platform="walmart_ca"},[pscustomobject]@{id="fantuan";tab_platform="fantuan"})\n'
        code+='$initial=@(Get-ReconciliationBrowserPlatforms -CollectStores $false -OpenStores $false -Reconcile $true -Collectors $rows)\n'
        code+='$plan=[pscustomobject]@{contract="expense-reconciliation-source-plan/v1";read_only=$true;platforms=@([pscustomobject]@{collector_id="fantuan";action="refresh";min_date="2026-10-05"},[pscustomobject]@{collector_id="walmart_ca";action="cached_match"})}\n'
        code+='$needed=@(Get-NeededReconciliationCollectors -Plan $plan -Collectors $rows)\n'
        code+='@{initial=$initial;needed=@($needed|ForEach-Object {$_.id})}|ConvertTo-Json -Compress\n'
        result=subprocess.run(['powershell.exe','-NoProfile','-Command',code],check=True,capture_output=True,text=True)
        self.assertEqual(json.loads(result.stdout),{'initial':['scotia'],'needed':['fantuan']})
if __name__=='__main__':unittest.main()
