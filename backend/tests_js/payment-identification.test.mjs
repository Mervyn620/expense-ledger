import test from 'node:test';
import assert from 'node:assert/strict';
import {canonicalPaymentMethod,normalizeTntPayment,isResolvedPaymentEvidence} from '../scripts/collectors/payment-text-utils.mjs';
import {pageExtractor,hostPattern} from '../scripts/collectors/export-payment-evidence.mjs';
test('T&T titles distinguish wallets sharing the same gateway and omit secrets',()=>{
 const input={payment_method:'alphapaydirect',payment_title:'WeChat Pay',token:'secret',card_number:'4111111111111111'};
 const output=normalizeTntPayment(input);
 assert.equal(output.payment_method,'WeChat Pay');assert.equal(output.source,'tnt_order_details_api');
 assert.doesNotMatch(JSON.stringify(output),/secret|token|411111|alphapaydirect/);
 assert.equal(normalizeTntPayment({...input,payment_title:'Alipay'}).payment_method,'Alipay');
 assert.equal(normalizeTntPayment({payment_method:'alphapaydirect'}),null);
});
test('generic card type remains unresolved and wallets are stable evidence',()=>{
 assert.equal(isResolvedPaymentEvidence({status:'method_only',payment_method:'信用卡/借记卡'}),false);
 assert.equal(isResolvedPaymentEvidence({status:'method_only',payment_method:'WeChat Pay'}),true);
 assert.equal(isResolvedPaymentEvidence({status:'confirmed',card_last4:'1234'}),true);
 assert.equal(canonicalPaymentMethod('AlphaPay'),'');
});
function extract(body,path='/eng/sales/order/view/order_id/9001234') {
 globalThis.document={body:{innerText:body},title:'Order example',querySelector:()=>null};
 globalThis.location={origin:'https://www.tntsupermarket.com',pathname:path,href:'https://www.tntsupermarket.com'+path+'?tracking=secret'};
 return pageExtractor({platform:'tnt_ca',order_id:'9001234',date:'2026-10-01',merchant:'Example',total:'30.00',currency:'CAD'});
}
test('DOM payment extraction binds the requested order and not the footer logos',()=>{
 const result=extract('Payment Method:\nWeChat Pay\nPayment Date:\n2026-10-01\nWe accept Visa Mastercard Alipay');
 assert.equal(result.payment_method,'WeChat Pay');assert.equal(result.card_last4,'');
 assert.equal(result.page_url,'https://www.tntsupermarket.com/eng/sales/order/view/order_id/9001234');
 assert.doesNotMatch(JSON.stringify(result),/tracking|secret/);
 assert.equal(extract('Payment Method:\nWeChat Pay','/eng/sales/order/view/order_id/9009999').status,'not_found');
});
test('DOM card evidence requires explicit masked tail and preserves network',()=>{
 const result=extract('Payment Method:\nVisa ending in 1234');
 assert.equal(result.card_last4,'1234');assert.equal(result.card_network,'Visa');assert.equal(result.status,'confirmed');
 assert.equal(extract('Payment Method:\nAlphaPay').status,'not_found');
});
test('only known platform hosts can be reused for evidence collection',()=>{
 assert.equal(hostPattern('tnt_ca').test('https://www.walmart.ca/orders/1'),false);
 assert.equal(hostPattern('tnt_ca').test('https://www.tntsupermarket.com/eng/order-history'),true);
 assert.throws(()=>hostPattern('other'),/Unsupported/);
});

test('payment fields retain the difference between credit, debit and combined card titles',()=>{
 for (const [label,method] of [['信用卡','Credit Card'],['Credit Card / Debit Card','Credit/Debit Card'],['借记卡','Debit Card']]) {
   assert.equal(normalizeTntPayment({payment_title:label}).payment_method,method);
   assert.equal(extract('Payment Method:\n'+label).payment_method,method);
 }
});
