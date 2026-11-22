"""Small explicit query plans for factual questions. Never executes model-generated SQL."""
import re
from contextlib import closing

PLATFORMS = {'饭团': 'fantuan', 'fantuan': 'fantuan', 'uber': 'uber_eats',
             'amazon': 'amazon_ca', '沃尔玛': 'walmart_ca', 'walmart': 'walmart_ca',
             'ordinary': 'the_ordinary_ca'}


CHINESE_MONTHS = {name: index for index, name in enumerate(
    ('一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二'), 1)}
MONTH_REFERENCE = re.compile(
    r'(?<![\d一二三四五六七八九十])'
    r'(?:(?P<year>20\d{2})\s*年\s*的?\s*|(?P<relative_year>今年|本年|去年|前年)\s*的?\s*)?'
    r'(?P<month>\d{1,2}|十一|十二|[一二三四五六七八九十])\s*月'
    r'|(?<!\d)(?P<iso_year>20\d{2})-(?P<iso_month>\d{1,2})(?!\d)'
    r'|(?P<relative>上个月|上月|本月|这个月|这月|當月|当月|last month|this month|current month)',
    re.I,
)


def question_intent(question):
    """Keep conversational interpretation out of the narrow SQL total shortcut."""
    text = str(question or '').casefold()
    if re.search(r'花(?:费|销)?(?:了|掉|出去)?(?:在|到)?(?:哪|什么|啥)|钱.{0,8}去(?:了)?(?:哪|哪里|哪儿|什么地方)'
                 r'|用(?:在|到)(?:哪|什么)|开销去向|支出去向|消费结构|支出结构|开销结构'
                 r'|主要.{0,8}(?:花|支出|消费|开销)|(?:支出|消费|开销).{0,8}集中'
                 r'|开销大头|消费大头|支出大头|消费分布|花销分布|支出分布|哪(?:些|个)?方面'
                 r'|where.{0,30}(?:money|spend)|spending breakdown', text):
        return 'spending_breakdown'
    if any(token in text for token in (
        '分析', '复盘', '解读', '总结', '看看', '情况', '怎么样', '如何',
        '为什么', '怎么', '为何', '原因', '合理', '正常', '值得', '建议',
        '省钱', '省点', '可以省', '能省', '节省', '节约', '控制', '改善', '优化', '太多',
        'analysis', 'analyze', 'why', 'advice', 'save', 'reduce', 'review',
    )):
        return 'interpretation'
    return 'fact'


def question_scope(question, today):
    """Resolve dates once for both factual SQL and AI context. Bare months use this year."""
    text = question.casefold()
    months = []
    inherited_year = today.year
    matches = list(MONTH_REFERENCE.finditer(text))
    for match in matches:
        relative = match['relative']
        if relative:
            offset = -1 if relative in ('上个月', '上月', 'last month') else 0
            index = today.year * 12 + today.month - 1 + offset
            year, month = index // 12, index % 12 + 1
        else:
            year = match['year'] or match['iso_year']
            if year:
                inherited_year = int(year)
            elif match['relative_year']:
                inherited_year = today.year - {'去年': 1, '前年': 2}.get(match['relative_year'], 0)
            year = inherited_year
            value = match['month'] or match['iso_month']
            month = CHINESE_MONTHS[value] if value in CHINESE_MONTHS else int(value)
            if not 1 <= month <= 12:
                raise ValueError('月份须在 1 至 12 月之间。')
        months.append(f'{year:04d}-{month:02d}')
    # A range includes intervening months; a comparison includes only named months.
    if len(months) == 2 and re.fullmatch(r'\s*(?:到|至|[-~～—])\s*', text[matches[0].end():matches[1].start()]):
        start, end = (int(value[:4]) * 12 + int(value[5:]) - 1 for value in months)
        if end < start:
            raise ValueError('月份范围的结束时间早于开始时间，请写明起止年月。')
        months = [f'{index // 12:04d}-{index % 12 + 1:02d}' for index in range(start, end + 1)]
    months = list(dict.fromkeys(months))
    if months:
        return {'mode': 'month' if len(months) == 1 else 'trend', 'months': months}
    year_match = re.search(r'(?<!\d)(20\d{2})\s*年|今年|本年|去年|前年|current year|this year', text)
    if year_match:
        year = int(year_match[1]) if year_match[1] else today.year - {'去年': 1, '前年': 2}.get(year_match[0], 0)
        return {'mode': 'year', 'year': year}
    return {'mode': 'trend'}


def query_plan(question, today, *, scope=None):
    text = question.casefold()
    if question_intent(question) != 'fact':
        return None
    platforms = list(dict.fromkeys(value for key, value in PLATFORMS.items() if key in text))
    try:
        scope = scope or question_scope(question, today)
    except ValueError:
        return None
    months = scope.get('months', [])
    if len(months) != 1:
        return None
    month = months[0]
    if (re.search(r'20\d{2}-\d{1,2}-\d{1,2}', text)
            or re.search(r'\d+\s*(?:日|号|加币|cad|\$)', text)
            or (any(word in text for word in ('本月', '这个月', 'this month'))
                and any(word in text for word in ('上月', '上个月', 'last month')))):
        return None
    # These questions need interpretation beyond this narrow, reviewable plan.
    if any(word in text for word in ('分析', 'analysis', 'analyze', '为什么', '建议', '预测', '平均', '每天', '预算', '次数',
                                      '商品', '物品', 'why', 'average', 'forecast',
                                      '退款', '退货', '取消', 'refund', 'cancel', '除了', '不算',
                                      '最少', '最低', '小费', '税', '手续费', '昨天', '今天',
                                      '每日', '每周', '星期', '超过', '大于', '小于', '至少',
                                      '送到', '发货', '分类', '类别', '商户', '餐厅', '占比', '比例')):
        return None
    # Other category filters are handled by the analysis facts, not an unfiltered total.
    if any(word in text for word in ('餐饮', '电信', '银行费用', '数字服务', '数字娱乐', '杂货')):
        return None
    if not platforms and not re.search(r'[\d一二三四五六七八九十]\s*月|20\d{2}-\d', text) and not any(word in text for word in ('上个月', '上月', 'last month')):
        return None
    compare = len(platforms) == 2 and any(word in text for word in ('比', '差', '对比', 'compare'))
    operation = 'compare' if compare else 'largest' if any(word in text for word in ('最大', '最贵', '最多', 'largest')) else 'list' if any(word in text for word in ('列出', '明细', '哪些', '订单', 'list')) else None
    if operation is None:
        # A month alone is not evidence that the user asked for a sum.
        if not any(word in text for word in ('多少', '总额', '合计', '总花销', '总花费', '总支出', 'how much', 'total spend')):
            return None
        operation = 'total'
    if len(platforms) > 1 and not compare:
        return None
    category = 'food_delivery' if '外卖' in text else None
    return {'month': month, 'platforms': platforms, 'category': category,
            'operation': operation, 'accounting_basis': 'net_purchase_month'}


def answer_plan(repository, plan):
    where, params = ['month=?', "currency='CAD'"], [plan['month']]
    if plan['platforms']:
        where.append('platform IN (' + ','.join('?' for _ in plan['platforms']) + ')')
        params.extend(plan['platforms'])
    source = 'expense_orders'
    if plan['category']:
        source = '''(SELECT o.id,o.date,o.month,o.currency,o.platform,o.merchant,
                       o.gross_total,o.refund_total,a.cents/100.0 AS total,a.category
                     FROM expense_orders o JOIN order_category_amounts a ON a.order_pk=o.id)'''
        where.append('category=?')
        params.append(plan['category'])
    clause = ' AND '.join(where)
    with closing(repository.connect()) as conn:
        aggregate = conn.execute(f'SELECT COUNT(*) AS n, ROUND(COALESCE(SUM(total),0),2) AS total FROM {source} WHERE {clause}', params).fetchone()
        rows = [dict(row) for row in conn.execute(f'''
            SELECT id AS order_pk,date,platform,merchant,total,gross_total,refund_total
            FROM {source} WHERE {clause} ORDER BY total DESC,id DESC LIMIT 200''', params)]
        groups = dict(conn.execute(f'SELECT platform,ROUND(SUM(total),2) FROM {source} WHERE {clause} GROUP BY platform', params).fetchall())
    labels = {'walmart_ca': 'Walmart', 'amazon_ca': 'Amazon', 'fantuan': '饭团',
              'uber_eats': 'Uber Eats', 'the_ordinary_ca': 'The Ordinary'}
    title = plan['month'] + (' / ' + '、'.join(labels.get(p, p) for p in plan['platforms']) if plan['platforms'] else '') + (' 外卖' if plan['category'] else '')
    if plan['operation'] == 'compare':
        left, right = plan['platforms']
        a, b = groups.get(left, 0), groups.get(right, 0)
        answer = f'{title}：{labels[left]} CAD {a:.2f}，{labels[right]} CAD {b:.2f}；前者减后者为 CAD {a-b:.2f}。'
    elif plan['operation'] == 'largest' and rows:
        item = rows[0]
        answer = f"{title} 范围内最大一笔是 {item['date']} 的 {item['merchant']}，CAD {item['total']:.2f}。"
        rows = rows[:1]
    else:
        answer = f"{title} 范围内已记录 {aggregate['n']} 笔，净支出 CAD {aggregate['total']:.2f}。"
    answer += ' 按购买月份统计，已减去手动关联的银行到账退款；未检查的平台不代表零消费。'
    return {'answer': answer, 'query_plan': plan,
            'evidence': {'rows': rows, 'total_count': aggregate['n'],
                         'truncated': aggregate['n'] > 200,
                         'basis': '银行到账退款回溯至原购买月份；商品明细仍为原购买价格'},
            'meta': {'source': 'computed', 'mode': 'chat', 'period': plan['month'],
                     'orders': aggregate['n'], 'total': aggregate['total'], 'currency': 'CAD'}}
