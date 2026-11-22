import {normalizeTntPayment} from "./payment-text-utils.mjs";
const text = value => String(value ?? "").replace(/\s+/g, " ").trim();
export function amount(value, optional = false) {
  if (optional && (value == null || value === "")) return 0;
  if (!/^-?\d+(?:\.\d+)?$/.test(String(value)) || !Number.isFinite(Number(value))) throw new Error("T&T amount schema is invalid.");
  return Math.round(Number(value) * 100);
}
export function orderId(value) {
  const id = text(value);
  if (!/^[1-9]\d{0,15}$/.test(id)) throw new Error("T&T order id is invalid.");
  return id;
}
export function productUrl(value) {
  try { const url = new URL(value); if (url.protocol === "https:" && url.hostname === "www.tntsupermarket.com" && /\.html$/.test(url.pathname)) return url.origin + url.pathname; } catch {}
  return "";
}
export function unwrap(response) {
  if (response?.code !== 200 || !response.data || typeof response.data !== "object") throw new Error("T&T order response schema is invalid.");
  return response.data;
}
export function normalizeTntOrder(response, expectedId, today = new Date().toLocaleDateString("en-CA", {timeZone:"America/Toronto"})) {
  const order = unwrap(response);
  const id = orderId(order.entity_id);
  if (id !== orderId(expectedId)) throw new Error("T&T detail does not match the requested order.");
  const dateTime = text(order.created_at);
  if (!/^20\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(dateTime) || Number.isNaN(Date.parse(dateTime.replace(" ", "T")))) throw new Error("T&T order date is invalid.");
  if (order.order_currency_code !== "CAD") throw new Error("T&T order currency is unsupported.");
  const status = text(order.status).toLowerCase();
  if (!/^[a-z_]+$/.test(status)) throw new Error("T&T order status is invalid.");
  const cancelled = ["canceled", "cancelled"].includes(status);
  const unpaid = ["pending", "pending_payment", "payment_review", "holded"].includes(status) && amount(order.total_paid, true) <= 0;
  if (cancelled || unpaid) return {order_id:id, date:dateTime.slice(0,10), status, excluded:cancelled ? "cancelled" : "unpaid"};
  const subtotal = amount(order.subtotal);
  const discount = amount(order.discount_amount, true);
  const tax = amount(order.tax_amount);
  const fees = amount(order.shipping_amount, true) + amount(order.pickupfee, true) + amount(order.additional_fee_total, true);
  const tip = amount(order.tips, true);
  const total = amount(order.grand_total);
  if (total <= 0 || subtotal < 0 || tax < 0 || fees < 0 || tip < 0 || Math.abs(subtotal + discount + tax + fees + tip - total) > 2) throw new Error("T&T order amounts do not reconcile; manual review is required.");
  if (!Array.isArray(order.items) || !order.items.length || order.items.length > 500) throw new Error("T&T order items are missing or invalid.");
  const ids = new Set();
  const items = order.items.map(item => {
    const itemId = text(item.item_id), title = text(item.name);
    const quantity = Number(item.qty_ordered);
    if (orderId(item.order_id) !== id || !title || title.length > 500 || !itemId || ids.has(itemId) || !Number.isFinite(quantity) || quantity <= 0 || quantity > 10000) throw new Error("T&T item schema is invalid.");
    ids.add(itemId);
    const line = amount(item.row_total);
    if (line < 0) throw new Error("T&T item amount is invalid.");
    return {item_id:itemId,title,quantity,line_total:(line/100).toFixed(2),product_url:productUrl(item.product_url)};
  });
  if (Math.abs(items.reduce((sum,item)=>sum+amount(item.line_total),0)-subtotal)>2) throw new Error("T&T item totals do not match the subtotal.");
  const window = text(order.delivery_date_time || order.pickup_date_time || order.pickup_day_time);
  const delivery = /^20\d{2}-\d{2}-\d{2} /.test(window) && window.slice(0,10)>today;
  const fulfillment = ["complete", "completed", "delivered"].includes(status) ? "delivered"
    : /shipped|in_transit|on_its_way|out_for_delivery/.test(status) || text(order.erp_shipped_at) ? "in_transit"
    : delivery ? "scheduled" : "processing";
  const refund = amount(order.total_refunded, true);
  const payment = normalizeTntPayment(order.payment);
  const notes = [window ? `配送/取货时间：${window}` : "", discount ? `订单优惠 CAD ${Math.abs(discount/100).toFixed(2)}` : "", refund>0 ? `平台退款 CAD ${(refund/100).toFixed(2)}，待银行到账核对` : ""].filter(Boolean).join("；");
  return {
    order_id:id,order_number:text(order.increment_id),date:dateTime.slice(0,10),datetime:dateTime,status,fulfillment,
    category:"groceries",discount_amount:(discount/100).toFixed(2),item_total:((subtotal+discount)/100).toFixed(2),tax:(tax/100).toFixed(2),fees:(fees/100).toFixed(2),tip:(tip/100).toFixed(2),total:(total/100).toFixed(2),currency:"CAD",
    quantity:items.reduce((sum,item)=>sum+item.quantity,0),items,notes,
    ...(payment ? {payment} : {}),
    source_url:`https://www.tntsupermarket.com/eng/sales/order/view/order_id/${id}`,
  };
}
export function mergeTntOrders(existing, incoming) {
  const orders = new Map();
  for (const order of [...existing, ...incoming]) {
    const id = orderId(order.order_id), previous = orders.get(id);
    orders.set(id, !order.excluded && !order.payment && previous?.payment ? {...order,payment:previous.payment} : order);
  }
  return [...orders.values()].sort((a,b)=>b.date.localeCompare(a.date));
}
