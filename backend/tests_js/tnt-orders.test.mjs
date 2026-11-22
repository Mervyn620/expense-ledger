import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeTntOrder,mergeTntOrders,productUrl} from '../scripts/collectors/tnt-order-utils.mjs';
const payload=(changes={})=>({code:200,data:{entity_id:'9001234',increment_id:'TEST001',created_at:'2026-10-01 08:30:00',order_currency_code:'CAD',status:'processing',state:'processing',subtotal:'30.00',discount_amount:'-2.00',tax_amount:'1.20',shipping_amount:'0.00',pickupfee:'3.00',additional_fee_total:'0.50',tips:'1.00',grand_total:'33.70',total_paid:'33.70',delivery_date_time:'2026-10-06 14:00 - 17:00',customer_email:'private@example.invalid',billing_address:{secret:'omit'},payment:{token:'omit'},items:[{item_id:'1',order_id:'9001234',name:'Example grocery',qty_ordered:2,row_total:'30.00',product_url:'https://www.tntsupermarket.com/eng/example.html?tracking=private'}],...changes}});
test('paid future delivery remains scheduled and fees/discounts reconcile',()=>{
 const order=normalizeTntOrder(payload(),'9001234','2026-10-04');
 assert.equal(order.fulfillment,'scheduled');assert.equal(order.item_total,'28.00');assert.equal(order.fees,'3.50');assert.equal(order.total,'33.70');assert.equal(order.items[0].quantity,2);
 assert.equal(order.items[0].product_url,'https://www.tntsupermarket.com/eng/example.html');
 assert.doesNotMatch(JSON.stringify(order),/private|token|billing_address|customer_email|payment/);
});
test('processing, shipping and delivered states do not become interchangeable',()=>{
 assert.equal(normalizeTntOrder(payload(),'9001234','2026-10-07').fulfillment,'processing');
 assert.equal(normalizeTntOrder(payload({erp_shipped_at:'2026-10-04 12:00:00'}),'9001234','2026-10-04').fulfillment,'in_transit');
 assert.equal(normalizeTntOrder(payload({status:'complete'}),'9001234','2026-10-04').fulfillment,'delivered');
});
test('unpaid and cancelled records become explicit exclusion records',()=>{
 assert.equal(normalizeTntOrder(payload({status:'pending_payment',total_paid:null}),'9001234').excluded,'unpaid');
 assert.equal(normalizeTntOrder(payload({status:'canceled'}),'9001234').excluded,'cancelled');
});
test('wrong order, malformed amount, incomplete items and non-CAD fail without guesses',()=>{
 assert.throws(()=>normalizeTntOrder(payload(),'9001235'),/does not match/);
 for(const changes of [{grand_total:'NaN'},{grand_total:'31.00'},{tax_amount:null},{items:[]},{order_currency_code:'USD'},{items:[{item_id:'1',order_id:'9001234',name:'Test',qty_ordered:1,row_total:'1.00'}]}])assert.throws(()=>normalizeTntOrder(payload(changes),'9001234'));
});
test('refund observation does not subtract from the original gross amount',()=>{
 const order=normalizeTntOrder(payload({total_refunded:'5.00'}),'9001234');
 assert.equal(order.total,'33.70');assert.match(order.notes,/5.00/);
});
test('repeated scan replaces the same stable order and preserves older cache',()=>{
 const order=normalizeTntOrder(payload(),'9001234');
 const changed={...order,fulfillment:'delivered'};
 const merged=mergeTntOrders([{...order,order_id:'9001000'},order],[changed,changed]);
 assert.equal(merged.length,2);assert.equal(merged.find(o=>o.order_id==='9001234').fulfillment,'delivered');
 assert.equal(productUrl('https://evil.invalid/secret.html'),'');
});
