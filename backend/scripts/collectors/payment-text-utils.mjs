// Titles identify the payment method; a gateway name never identifies a wallet.
export function canonicalPaymentMethod(value) {
  const text=String(value||'').replace(/\s+/g,' ').trim();
  if(/^(?:wechat(?:\s*pay)?|微信(?:支付)?|微信錢包|微信钱包)$/i.test(text))return 'WeChat Pay';
  if(/^(?:alipay|支付宝(?:支付)?|支付寶(?:支付)?)$/i.test(text))return 'Alipay';
  if(/^(?:credit\s*card|信用卡)$/i.test(text))return 'Credit Card';
  if(/^(?:credit(?:\s*card)?\s*[/／]\s*debit\s*card|信用卡\s*[/／]\s*借[记記]卡)$/i.test(text))return 'Credit/Debit Card';
  if(/^(?:debit\s*card|借[记記]卡)$/i.test(text))return 'Debit Card';
  if(/^visa$/i.test(text))return 'Visa';
  if(/^master\s*card$/i.test(text))return 'Mastercard';
  if(/^(?:amex|american express)$/i.test(text))return 'Amex';
  return '';
}
export function isResolvedPaymentEvidence(row) {
  if (!['confirmed','method_only'].includes(row?.status)) return false;
  if(/^\d{4}$/.test(String(row?.card_last4||'')))return true;
  return ['WeChat Pay','Alipay'].includes(canonicalPaymentMethod(row?.payment_method));
}
export function normalizeTntPayment(payment) {
  const method=canonicalPaymentMethod(payment?.payment_title);
  if(!method)return null;
  return {payment_method:method,card_network:['Visa','Mastercard','Amex'].includes(method)?method:'',card_last4:'',status:'method_only',source:'tnt_order_details_api'};
}
