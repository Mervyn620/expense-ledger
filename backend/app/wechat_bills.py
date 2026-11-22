"""Read-only comparison of a user-provided WeChat Pay detail and a Fantuan receipt."""
import re
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal


REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{3,127}")
AMOUNT = r"(?:0|[1-9]\d{0,8})(?:\.\d{1,2})?"
FIELD = re.compile(r"^(交易单号|商户单号|商家单号|当前状态|支付时间|标价)(?:\s*[:：]\s*(.*))?$")


def parse_detail(text):
    lines = [s.strip() for s in text.replace("\ufffc", "").splitlines() if s.strip()]
    fields = {}
    for i, line in enumerate(lines):
        match = FIELD.fullmatch(line)
        if match:
            name = match[1].replace("商家单号", "商户单号")
            if name in fields:
                raise ValueError("每次请粘贴一笔交易的明细。")
            fields[name] = match[2] or (lines[i + 1] if i + 1 < len(lines) else "")
    for name in ("交易单号", "商户单号"):
        if not REFERENCE.fullmatch(fields.get(name, "")) and (name == "交易单号" or fields.get(name)):
            raise ValueError(f"请从微信交易明细复制完整的{name}。")
    if fields.get("当前状态") != "支付成功":
        raise ValueError("此入口目前支持状态为「支付成功」的消费明细。退款、收入和转账需单独核对。")
    try:
        paid_at = datetime.strptime(fields.get("支付时间", ""), "%Y年%m月%d日 %H:%M:%S")
    except ValueError as exc:
        raise ValueError("明细缺少完整的微信支付时间。") from exc
    payments = []
    for i, line in enumerate(lines):
        payment = re.fullmatch(rf"[-−]\s*[¥￥]?\s*({AMOUNT})(?:元)?", line)
        if payment:
            payments.append((i, payment))
    if len(payments) != 1 or payments[0][0] == 0:
        raise ValueError("无法辨认商户与人民币实付金额，请从商户名称开始复制明细。")
    i, payment = payments[0]
    merchant = lines[i - 1]
    if len(merchant) > 120 or "://" in merchant:
        raise ValueError("无法辨认微信显示的商户名称。")
    price = None
    if "标价" in fields:
        listed = re.fullmatch(rf"({AMOUNT})\s*(加拿大元|加元|CAD|人民币|元|CNY)", fields["标价"])
        if not listed:
            raise ValueError("此版本支持人民币和加拿大元标价，请保留原币金额及单位。")
        price = {"amount": format(Decimal(listed[1]), ".2f"),
                 "currency": "CAD" if listed[2] in {"加拿大元", "加元", "CAD"} else "CNY"}
    return {"merchant": merchant, "paid_at": paid_at.isoformat(sep=" "),
            "paid": {"amount": format(Decimal(payment[1]), ".2f"), "currency": "CNY"},
            "listed": price, "transaction_id": fields["交易单号"],
            "merchant_order_id": fields.get("商户单号", "")}


def compare_fantuan(repository, bill, order_id="", transaction_id="", merchant_order_id=""):
    if bill["merchant"].casefold() not in {"fantuan delivery", "饭团外卖", "飯糰外賣"}:
        return "other_merchant", "已识别微信明细。当前平台订单核对仅支持饭团。"
    if (transaction_id or merchant_order_id) and not order_id:
        raise ValueError("填写饭团凭证上的支付单号时，请同时填写该凭证的饭团订单号。")
    if any(not REFERENCE.fullmatch(value) for value in (order_id, transaction_id, merchant_order_id) if value):
        raise ValueError("饭团凭证上的单号需完整填写。")
    with closing(repository.connect()) as conn:
        order = conn.execute(
            """SELECT id, order_id, date FROM orders
               WHERE platform='fantuan' AND order_id=?""", (order_id or bill["merchant_order_id"],)).fetchone()
        if order is None:
            latest = conn.execute("SELECT MAX(date) FROM orders WHERE platform='fantuan'").fetchone()[0]
            if latest and latest < bill["paid_at"][:10]:
                return "unmatched", f"未找到对应订单。账本最近一笔饭团订单是 {latest}，早于这笔微信付款。"
            return "unmatched", "账本中未找到单号对应的饭团订单。可以补充饭团凭证再核对。"
        payments = conn.execute(
            "SELECT amount,currency,status FROM order_payments WHERE order_pk=? AND card_id='wechat_pay'",
            (order["id"],)).fetchall()
    if ((transaction_id and transaction_id != bill["transaction_id"])
            or (merchant_order_id and merchant_order_id != bill["merchant_order_id"])):
        return "conflict", "两份凭证的同类单号不一致，需要人工核对。"
    if not (transaction_id or merchant_order_id):
        return "missing_reference", "已找到饭团订单，还缺少能在两份凭证上核对的单号。"
    amount = bill["listed"] or bill["paid"]
    if (len(payments) != 1 or payments[0]["status"] != "confirmed" or payments[0]["currency"] != amount["currency"]
            or payments[0]["amount"] is None or Decimal(str(payments[0]["amount"])) != Decimal(amount["amount"])):
        return "review", "单号相符，订单付款方式、币种或金额仍需核对。"
    if abs((date.fromisoformat(bill["paid_at"][:10]) - date.fromisoformat(order["date"])).days) > 1:
        return "review", "单号相符，两端日期相差超过一天，需核对支付时间。"
    return "consistent", "单号相符，饭团订单的微信付款方式、原币金额和日期也已核对。"
