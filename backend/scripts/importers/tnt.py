"""Import only the sanitized T&T contract, with transactional order/item replacement."""
import json
import os
import re
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
CODE_ROOT=Path(__file__).resolve().parents[2]
_WORKSPACE=str(os.environ.get('EXPENSE_COLLECTOR_WORKSPACE_ROOT') or '').strip()
ROOT=Path(_WORKSPACE).resolve() if _WORKSPACE else CODE_ROOT
if _WORKSPACE and (not Path(_WORKSPACE).is_absolute() or Path(_WORKSPACE).is_symlink() or ROOT!=Path.cwd().resolve()):
    raise RuntimeError('Collector workspace environment is invalid.')
sys.path.insert(0,str(CODE_ROOT/'scripts/ledger'))
from db import import_orders_with_csv
PLATFORM='tnt_ca'
def money(value):
    try:
        result=Decimal(str(value))
        if not result.is_finite(): raise ValueError('T&T amount is invalid.')
        return result.quantize(Decimal('0.01'))
    except (InvalidOperation,TypeError): raise ValueError('T&T amount is invalid.') from None
def normalize_payload(payload):
    if payload.get('contract')!='expense-tnt-orders/v1' or payload.get('platform')!=PLATFORM or not isinstance(payload.get('orders'),list):
        raise ValueError('T&T export contract is invalid.')
    transactions=[];items=[];excluded=set();seen=set()
    for order in payload['orders']:
        order_id=str(order.get('order_id',''))
        if not re.fullmatch(r'[1-9]\d{0,15}',order_id) or order_id in seen: raise ValueError('T&T order id is invalid or duplicated.')
        seen.add(order_id)
        if order.get('excluded'):
            if order['excluded'] not in {'cancelled','unpaid'}: raise ValueError('T&T exclusion is invalid.')
            excluded.add((PLATFORM,order_id));continue
        order_date=str(order.get('date',''))
        date.fromisoformat(order_date)
        if order.get('currency')!='CAD' or order.get('fulfillment') not in {'scheduled','processing','delivered','in_transit'}: raise ValueError('T&T currency or fulfillment is invalid.')
        amounts={key:money(order.get(key)) for key in ('item_total','tax','fees','tip','total')}
        if amounts['total']<=0 or any(value<0 for value in amounts.values()) or abs(sum(amounts[key] for key in ('item_total','tax','fees','tip'))-amounts['total'])>Decimal('0.02'): raise ValueError('T&T amounts do not reconcile.')
        lines=order.get('items')
        if not isinstance(lines,list) or not lines or len(lines)>500: raise ValueError('T&T items are invalid.')
        names=[];item_ids=set()
        for item in lines:
            title=str(item.get('title') or '').strip();item_id=str(item.get('item_id') or '')
            quantity=Decimal(str(item.get('quantity',0)))
            line=money(item.get('line_total'))
            if not title or not item_id or item_id in item_ids or not quantity.is_finite() or quantity<=0 or line<0: raise ValueError('T&T item is invalid.')
            item_ids.add(item_id);names.append(title)
            link=item.get('product_url') or ''
            if link and not re.fullmatch(r'https://www\.tntsupermarket\.com/[^?#]*\.html',link): raise ValueError('T&T product URL is invalid.')
            items.append({'date':order_date,'platform':PLATFORM,'order_id':order_id,'category':'groceries','product_name':title,'quantity':str(quantity),'price':str(line),'delivery_status':order['fulfillment'],'product_link':link,'currency':'CAD'})
        discount=money(order.get('discount_amount','0.00'))
        if abs(sum(money(item['line_total']) for item in lines)+discount-amounts['item_total'])>Decimal('0.02'): raise ValueError('T&T item totals do not reconcile.')
        # Combine identical product lines instead of losing a duplicate in the ledger's line-key upsert.
        combined={}
        for row in items[-len(lines):]:
            key=(row['product_name'],row['product_link'])
            if key in combined:
                combined[key]['quantity']=str(Decimal(combined[key]['quantity'])+Decimal(row['quantity']))
                combined[key]['price']=str(money(combined[key]['price'])+money(row['price']))
            else: combined[key]=dict(row)
        items[-len(lines):]=combined.values()
        expected=f'https://www.tntsupermarket.com/eng/sales/order/view/order_id/{order_id}'
        if order.get('source_url')!=expected: raise ValueError('T&T source URL is invalid.')
        transactions.append({'date':order_date,'platform':PLATFORM,'order_id':order_id,'merchant':'T&T Supermarket','fulfillment':order['fulfillment'],'category':'groceries','item':'; '.join(names),'quantity':str(sum(Decimal(str(item['quantity'])) for item in lines)),**{key:str(value) for key,value in amounts.items()},'currency':'CAD','source_url':expected,'notes':order.get('notes','')})
    return transactions,items,excluded
def import_file(root=ROOT):
    root=Path(root);path=root/'data/tnt_orders.json'
    if not path.exists(): return {'orders':0,'items':0,'excluded':0}
    payload=json.loads(path.read_text(encoding='utf-8-sig'))
    transactions,items,excluded=normalize_payload(payload)
    if transactions or excluded:
        import_orders_with_csv(transactions,items,root=root,item_filename='tnt_items.csv',delete_order_keys=excluded)
    return {'orders':len(transactions),'items':len(items),'excluded':len(excluded)}
if __name__=='__main__': print(json.dumps(import_file()))
