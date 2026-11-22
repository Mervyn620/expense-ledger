"""Convert only the sanitized T&T payment-title contract into shared evidence."""
import json,re
from pathlib import Path

def load_tnt_payment_evidence(root):
    path=Path(root)/'data/tnt_orders.json'
    if not path.exists():return {}
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('contract')!='expense-tnt-orders/v1' or data.get('platform')!='tnt_ca':
        raise ValueError('T&T cache contract is invalid for payment evidence.')
    result={}
    for order in data.get('orders',[]):
        payment=order.get('payment')
        if order.get('excluded') or not payment:continue
        key=str(order.get('order_id',''))
        if not re.fullmatch(r'[1-9]\d{0,15}',key):raise ValueError('T&T payment order id is invalid.')
        allowed={'payment_method','card_network','card_last4','status','source'}
        if not isinstance(payment,dict) or set(payment)-allowed:raise ValueError('T&T payment contains fields outside the sanitized contract.')
        method=payment.get('payment_method')
        if method not in {'WeChat Pay','Alipay','card','Credit Card','Credit/Debit Card','Debit Card','Visa','Mastercard','Amex'} or payment.get('source')!='tnt_order_details_api' or payment.get('status')!='method_only' or payment.get('card_last4'):
            raise ValueError('T&T payment title contract is invalid.')
        network=method if method in {'Visa','Mastercard','Amex'} else ''
        if payment.get('card_network','')!=network:raise ValueError('T&T payment network is inconsistent.')
        result['tnt_ca|'+key]={'platform':'tnt_ca','order_id':key,'source':'tnt_order_details_api',
            'payment_method':method,'card_network':network,'card_last4':'','status':'method_only',
            'evidence_snippet':'Payment Method: '+method,
            'page_url':'https://www.tntsupermarket.com/eng/sales/order/view/order_id/'+key,
            'captured_at':data.get('generated_at','')}
    return result


def merge_tnt_evidence(rows, root):
    for key, title in load_tnt_payment_evidence(root).items():
        current = rows.get(key, {})
        if current.get('card_last4'):
            continue
        if title['payment_method'] == 'card' and current.get('status') == 'method_only':
            continue
        rows[key] = title
