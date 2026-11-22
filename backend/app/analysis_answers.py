"""Render complete ledger facts; model-authored numbers never become chat facts."""
import json
import re


CATEGORY_LABELS = {
    'food_delivery': '外卖', 'dining': '餐饮', 'groceries': '杂货',
    'telecom': '电信', 'bank_fees': '银行费用', 'digital_services': '数字服务',
    'digital_entertainment': '数字娱乐', 'mixed': '混合购物',
    'general_retail': '综合零售', 'retail': '零售', 'personal_care': '个人护理',
    'retail_membership': '购物会员', 'software_subscription': '软件订阅',
    'unallocated': '未分配差额', 'uncategorized': '未分类', 'kitchenware': '餐厨用品', 'household': '家居日用',
}
PLATFORM_LABELS = {'walmart_ca': 'Walmart', 'amazon_ca': 'Amazon', 'fantuan': '饭团',
                   'uber_eats': 'Uber Eats', 'the_ordinary_ca': 'The Ordinary',
                   'scotia': 'Scotiabank', 'scotiabank': 'Scotiabank', 'bank': '其他银行'}


def group_label(section, name):
    labels = CATEGORY_LABELS if section == 'category' else PLATFORM_LABELS if section == 'platform' else {}
    return labels.get(name, name)


def fact_catalog(context):
    """Each ID identifies a whole labelled statement, never an interchangeable number."""
    period = context['scope']['period']
    summary = context['summary']
    facts = {}

    def total_line(label, row):
        return f"{label}：已记录 {row['orders']} 笔，净支出 CAD {row['total']:,.2f}。"

    facts['scope.total'] = total_line(f'{period} 全部所选记录', summary)
    for row in context['monthly_totals']:
        facts[f"month.{row['month']}.total"] = total_line(f"{row['month']} 全部所选记录", row)
    for section, label in (('category', '类别'), ('platform', '平台'),
                           ('merchant', '商户'), ('payment', '付款方式')):
        for row in context.get(f'{section}_totals', []):
            name = group_label(section, row['name'])
            facts[f"{section}.{row['name']}"] = (
                total_line(f'{period} {label}「{name}」', row)
                + f"占所选记录净支出的 {row['share_pct']:.1f}%。")
    for row in context.get('monthly_category_totals', []):
        name = CATEGORY_LABELS.get(row['category'], row['category'])
        facts[f"month.{row['month']}.category.{row['category']}"] = total_line(
            f"{row['month']} 类别「{name}」", row)
    for row in context.get('orders', []):
        facts[f"order.{row['order_pk']}"] = (
            f"{row['date']}，{group_label('platform', row['platform'])} / {row['merchant']}："
            f"订单净支出 CAD {row['total']:,.2f}；付款方式：{row['payment']['label']}。"
            + (f"订单摘要：{row['item_summary']}。" if row.get('item_summary') else ''))
    comparison = context.get('computed_facts', {}).get('period_comparison', {})
    if comparison.get('projected_month_total') is not None:
        facts['forecast.month_total'] = (
            f"若后续维持截至 {comparison['as_of_date']} 的自然日支出节奏，"
            f"{comparison['latest_month']} 整月线性外推约为 CAD {comparison['projected_month_total']:,.2f}。"
            '这是低置信度条件预测，会受账本同步完整性和后续大额消费影响。')
    if comparison.get('previous_same_day_total') is not None:
        facts['comparison.same_day'] = (
            f"同日口径，截至各月第 {comparison['comparison_day']} 日："
            f"{comparison['previous_month']} 为 CAD {comparison['previous_same_day_total']:,.2f}；"
            f"{comparison['latest_month']} 为 CAD {comparison['latest_same_day_total']:,.2f}。"
            '仅比较已记录消费，不代表采集完整。')
    facts.update(spending_insights(context))
    return facts


def spending_insights(context):
    """Ground interpretation in full-scope aggregates, never truncated order samples."""
    facts = {}
    total = context['summary']['total']
    if total <= 0:
        facts['insight.structure'] = '所选记录的净支出不为正，可能受退款抵扣影响，不适合用普通消费占比解读。'
        return facts
    for section, label in (('platform', '平台'), ('category', '类别')):
        rows = sorted(context.get(f'{section}_totals', []), key=lambda row: row['total'], reverse=True)
        if not rows or rows[0]['total'] <= 0:
            continue
        top = rows[0]
        name = group_label(section, top['name'])
        tied = sum(abs(row['total'] - top['total']) < .005 for row in rows) > 1
        rank = '并列最高' if tied else '最高'
        facts[f'insight.main_{section}'] = (
            f"按{label}看，「{name}」的已记录净支出{rank}，为 CAD {top['total']:,.2f}，"
            f"占所选范围净支出的 {top['share_pct']:.1f}%。"
            + ('这部分超过所选净支出的一半。' if 50 < top['share_pct'] <= 100 else ''))
    largest = context.get('computed_facts', {}).get('largest_order')
    if largest and largest['total'] > 0:
        facts['insight.largest_order'] = (
            f"所选范围最大单笔净支出是 {largest['date']} 在「{largest['merchant']}」的 CAD {largest['total']:,.2f}，"
            f"占所选范围净支出的 {largest['total'] / total * 100:.1f}%。")
    return facts


def breakdown_is_complete(content, fact_ids):
    """A total or a list of facts alone does not answer a request for interpretation."""
    try:
        parts = json.loads(content)['parts']
    except (ValueError, TypeError, KeyError):
        return False
    prose = [part['text'].strip() for part in parts if isinstance(part, dict) and isinstance(part.get('text'), str)]
    has_breakdown = any(key.startswith(('platform.', 'category.', 'merchant.', 'insight.main_')) for key in fact_ids)
    return has_breakdown and any(len(text) >= 12 for text in prose)


def verified_breakdown_parts(content, facts, prose_is_supported):
    """Drop whole invalid prose paragraphs, never keep or silently rewrite their numbers.

    Fact references and document structure remain strict. A retained answer still
    needs both a breakdown fact and explanation; otherwise use the local fallback.
    """
    try:
        document = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(document, dict) or set(document) != {'parts'}:
        return None
    parts = document['parts']
    if not isinstance(parts, list) or not 1 <= len(parts) <= 20:
        return None
    kept, removed = [], 0
    for part in parts:
        _, reason, _ = render_answer(json.dumps({'parts': [part]}, ensure_ascii=False), facts, prose_is_supported)
        if reason in {'unreferenced_number', 'unsupported_claim'}:
            removed += 1
        elif reason:
            return None
        else:
            kept.append(part)
    cleaned = json.dumps({'parts': kept}, ensure_ascii=False)
    answer, reason, ids = render_answer(cleaned, facts, prose_is_supported)
    if removed and not reason and breakdown_is_complete(cleaned, ids):
        return answer, ids, removed
    return None


def spending_breakdown_answer(context, facts):
    """Useful, explicitly local interpretation if the model omits the actual answer."""
    main = facts.get('insight.main_platform') or facts.get('insight.main_category') or facts.get('insight.structure')
    lines = ['**钱主要花在哪里**', main or facts['scope.total']]
    if main != facts.get('insight.main_category') and 'insight.main_category' in facts:
        lines.append(facts['insight.main_category'])
    lines.extend(['**具体去向**', facts['scope.total']])
    rows = sorted(context.get('platform_totals', []), key=lambda row: row['total'], reverse=True)
    lines.extend(facts[f"platform.{row['name']}"] for row in rows[:3])
    if 'insight.largest_order' in facts:
        lines.append(facts['insight.largest_order'])
    lines.append('**怎么看、怎么调整**')
    if context['summary']['total'] > 0 and rows and rows[0]['total'] > 0:
        lines.append(f"如果你想压缩开销，可以先复查「{group_label('platform', rows[0]['name'])}」的购买清单与较大订单，区分集中补货和重复购买，再决定调整什么。")
    lines.append('平台说明钱付给了谁，订单分类和商品明细才帮助判断买了什么；综合零售不能直接当作食品或生活必需品。是否值得保留，还要结合你的实际需要和预算。')
    lines.append('以上为本地账本计算的消费去向解读；AI 未提供足够完整的可核查解读。金额是购买月份净支出，商品原价不等于退款后净额；未记录不代表没有消费，未结束月份不与完整月份直接比较。')
    return '\n\n'.join(lines)


def render_answer(content, facts, prose_is_supported):
    """Return (answer, reason, referenced IDs). Rejected prose is never returned."""
    if not isinstance(content, str) or not content.strip():
        return None, 'empty_response', []
    if len(content) > 16000:
        return None, 'invalid_format', []
    try:
        document = json.loads(content)
    except (ValueError, TypeError):
        # Plain qualitative replies remain usable; raw quantities require fact IDs.
        document = {'parts': [{'text': content}]} if not content.lstrip().startswith(('{', '[', '```')) else None
    if not isinstance(document, dict) or set(document) != {'parts'}:
        return None, 'invalid_format', []
    parts = document['parts']
    if not isinstance(parts, list) or not 1 <= len(parts) <= 20:
        return None, 'invalid_format', []
    rendered, ids = [], []
    for part in parts:
        if not isinstance(part, dict):
            return None, 'invalid_format', []
        if set(part) == {'fact_id'}:
            key = part['fact_id']
            if not isinstance(key, str) or key not in facts:
                return None, 'unknown_fact', []
            rendered.append(facts[key])
            ids.append(key)
        elif set(part) == {'text'} and isinstance(part['text'], str) and part['text'].strip():
            prose = part['text'].strip()
            # Dates, counts, prices and percentages belong in complete backend facts.
            if re.search(r'\d|[%％$]|百分之|[零〇一二两三四五六七八九十百千万亿半]+\s*(?:元|块|刀|加币|美元|笔|次|件|成|倍|月|年)'
                         r'|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand)\s+(?:dollars?|percent|orders?)\b', prose, re.I):
                return None, 'unreferenced_number', []
            if not prose_is_supported(prose):
                return None, 'unsupported_claim', []
            rendered.append(prose)
        else:
            return None, 'invalid_format', []
    return '\n\n'.join(rendered), None, ids


def fallback_answer(context, facts):
    lines = [
        'AI 解读未通过账本校验，以下是本地计算的所选范围摘要；这份摘要不代替对原问题的完整解读。',
        facts['scope.total'],
    ]
    if len(context['monthly_totals']) > 1:
        lines.extend(facts[f"month.{row['month']}.total"] for row in context['monthly_totals'])
    lines.extend(facts[f"category.{row['name']}"] for row in context['category_totals'][:5])
    lines.append('金额按购买月份统计，已减去手动关联的银行到账退款；未记录不代表没有消费，商品明细仍为原购买价格。')
    return '\n\n'.join(lines)
