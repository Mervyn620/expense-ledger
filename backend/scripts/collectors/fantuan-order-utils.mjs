function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function money(value) {
  if (typeof value === "number") return value;
  const match = String(value || "").match(/-?\$?\s*([0-9]+(?:\.[0-9]{1,2})?)/);
  return match ? Number(match[1]) : 0;
}

function moneyString(value) {
  return money(value).toFixed(2);
}

export function fantuanCardSignature(value) {
  return [
    clean(value?.datetime || value?.dateTime),
    moneyString(value?.total),
  ].join("|");
}

export function refreshFantuanCachedStatuses(existingOrders, liveCards) {
  const liveStatuses = new Map();
  for (const card of liveCards || []) {
    const status = clean(card?.status);
    if (status) liveStatuses.set(fantuanCardSignature(card), status);
  }

  return (existingOrders || []).map((order) => {
    const status = liveStatuses.get(fantuanCardSignature(order));
    return status && status !== clean(order?.status)
      ? { ...order, status }
      : order;
  });
}

export function parseFantuanCardText(text, kind = "history") {
  const lines = String(text || "").split(/\r?\n/).map(clean).filter(Boolean);
  const totalLine = lines.find(line => /\$\s*[0-9]+(?:\.[0-9]{2})?/.test(line)) || "";
  const dateLine = lines.find(line => /20\d{2}-\d{2}-\d{2}\s+\d{1,2}:\d{2}/.test(line)) || "";
  const statusLine = lines.find((line,index) => index > 0 && /完成|取消|退款|进行|等待|接单|备餐|制作|配送|送餐|预约|支付|已/.test(line)) || "";
  const totalIndex = totalLine ? lines.indexOf(totalLine) : lines.length;
  const statusIndex = statusLine ? lines.indexOf(statusLine) : 1;
  return {kind,merchant:lines[0] || "",status:statusLine,
    pickupNumber:lines.find(line=>/取餐号/.test(line))?.match(/#[0-9]+/)?.[0] || "",
    itemSummary:lines.slice(Math.max(0,statusIndex+1),totalIndex).join("; "),
    total:moneyString(totalLine),dateTime:dateLine,date:dateLine.slice(0,10),text:lines.join("\n")};
}

export function fantuanOrderMetadata(data, card = {}) {
  const created = (data?.orderInfo?.contentList || []).find(entry =>
    entry?.type === "CREATED_AT" || /下单时间|order(?:ed)? (?:at|time)|placed/i.test(clean(entry?.label)));
  const dateTime = clean(created?.name) || clean(card.dateTime);
  const match = dateTime.match(/^(20\d{2}-\d{2}-\d{2})[ T](\d{1,2}:\d{2})/);
  const orderId = clean(data?.sn);
  if (!orderId || !match) throw new Error("Fantuan order detail is missing its stable order ID or purchase time");
  return {orderId,date:match[1],dateTime:`${match[1]} ${match[2]}`,
    status:clean(data?.orderStatusInfo?.statusString) || clean(card.status) || (card.kind === "active" ? "进行中" : clean(data?.status))};
}

export function isFantuanActiveStatus(value) {
  return !/已完成|已送达|已取消|取消成功|订单取消|退款成功|completed|delivered|cancelled|canceled|refunded/i.test(clean(value));
}

export function fantuanCardsNeedingDetails(previous, cards) {
  const cached = new Map((previous || []).map(order=>[fantuanCardSignature(order),order]));
  return (cards || []).filter(card=>{
    const known=cached.get(fantuanCardSignature(card));
    return card.kind === "active" || !known || isFantuanActiveStatus(known.status);
  });
}
