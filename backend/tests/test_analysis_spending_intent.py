import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.analysis import DeepSeekAnalysisService
from app.analysis_answers import spending_insights
from app.ledger_questions import query_plan, question_intent
from app.repository import LedgerRepository
from test_expense_analysis import create_analysis_ledger, add_analysis_order


class SpendingIntentTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        db = create_analysis_ledger(root)
        for key, amount, platform, category, merchant in (
            ('shop-a', 60.12, 'walmart_ca', 'groceries', 'Walmart'),
            ('shop-b', 44.24, 'walmart_ca', 'groceries', 'Walmart'),
            ('shop-c', 45.96, 'walmart_ca', 'groceries', 'Walmart'),
            ('food-a', 21.11, 'fantuan', 'food_delivery', 'Noodle'),
            ('food-b', 20.82, 'fantuan', 'food_delivery', 'Chicken'),
            ('retail', 13.55, 'amazon_ca', 'retail', 'Amazon'),
        ):
            add_analysis_order(db, key, key, '1', amount, order_date='2026-09-05',
                               platform=platform, category=category, merchant=merchant)
        add_analysis_order(db, 'other-year', 'other-year', '1', 900, order_date='2025-09-05')
        self.requests = []
        self.reply = {'parts': [{'fact_id': 'scope.total'}]}

        def transport(_url, payload, _headers, _timeout):
            self.requests.append(json.loads(payload['messages'][1]['content'].split('\n', 1)[1]))
            reply = self.reply(payload) if callable(self.reply) else self.reply
            return 200, {'choices': [{'message': {'content': json.dumps(reply, ensure_ascii=False)}}]}

        self.service = DeepSeekAnalysisService(LedgerRepository(db, root / 'exports/current'),
            api_key='test-key', cache_path=root / 'cache.json', transport=transport)
        self.service.builder.today = date(2026, 9, 16)

    def test_conversational_spending_questions_never_take_total_shortcut(self):
        for question in ('我九月的钱花在哪了', '9月钱都去哪儿了', '九月钱都花哪了',
                         '本月主要花在什么上', '9月消费结构是什么',
                         '这个月支出主要集中在哪里', '九月开销怎么这么多',
                         '9月的钱花得合理吗', '九月怎么省钱', '9月消费情况',
                         'where did my money go this month', 'this month spending breakdown'):
            with self.subTest(question=question):
                self.assertIsNone(query_plan(question, self.service.builder.today))
                self.assertNotEqual(question_intent(question), 'fact')
                self.service.chat(question)
                self.assertEqual(self.requests[-1]['scope']['period'], '2026-09')

    def test_total_questions_remain_fast_factual_answers(self):
        for question in ('九月花了多少', '9月总支出', '九月Walmart花了多少钱'):
            self.assertEqual(self.service.chat(question)['meta']['source'], 'computed')
        self.assertFalse(self.requests)
        self.assertIsNone(query_plan('九月消费', self.service.builder.today))

    def test_total_only_reply_gets_useful_grounded_interpretation(self):
        result = self.service.chat('我九月的钱花在哪了')
        self.assertEqual(result['meta']['total'], 205.8)
        self.assertEqual(result['meta']['validation']['reason'], 'incomplete_interpretation')
        for value in ('Walmart', 'CAD 150.32', '73.0%', 'CAD 41.93', '最大单笔', '购买清单', '本地账本计算'):
            self.assertIn(value, result['answer'])
        self.assertNotIn('900.00', result['answer'])
        self.assertEqual({row['date'][:7] for row in result['evidence']['rows']}, {'2026-09'})

    def test_valid_analysis_preserves_prose_and_referenced_facts(self):
        self.reply = {'parts': [
            {'fact_id': 'insight.main_platform'}, {'fact_id': 'category.groceries'},
            {'text': '这意味着复查购物清单比只看总额更有帮助，可以先核对集中补货和重复购买，再决定是否调整。'},
        ]}
        result = self.service.chat('九月主要花在什么上')
        self.assertEqual(result['meta']['source'], 'deepseek')
        self.assertEqual(result['meta']['validation']['status'], 'passed')
        self.assertIn('复查购物清单', result['answer'])

    def test_category_and_platform_breakdowns_keep_all_facts_in_scope(self):
        for question, total, count in (
            ('九月外卖的钱花在哪了', 41.93, 2),
            ('九月Walmart的钱花在哪了', 150.32, 3),
        ):
            with self.subTest(question=question):
                result = self.service.chat(question)
                context = self.requests[-1]
                self.assertEqual(result['meta']['total'], total)
                self.assertEqual(len(result['evidence']['rows']), count)
                self.assertAlmostEqual(sum(row['total'] for row in context['category_totals']), total)
                self.assertAlmostEqual(sum(row['total'] for row in context['platform_totals']), total)
                self.assertNotIn('CAD 205.80', result['answer'])

    def test_unsafe_prose_still_falls_back_without_exposing_fabrication(self):
        self.reply = {'parts': [{'text': '消费浪费了 CAD 9999。'}]}
        result = self.service.chat('九月的钱花在哪了')
        self.assertEqual(result['meta']['source'], 'computed_fallback')
        self.assertNotIn('9999', result['answer'])
        self.assertIn('CAD 150.32', result['answer'])
        self.assertEqual(len(self.requests), 2)

    def test_single_repair_can_recover_a_valid_explanation(self):
        def reply(payload):
            if len(self.requests) == 1:
                return {'parts': [{'text': '九月花了 CAD 9999。'}]}
            self.assertIn('unreferenced_number', payload['messages'][-1]['content'])
            self.assertNotIn('9999', payload['messages'][-1]['content'])
            return {'parts': [{'fact_id': 'insight.main_platform'},
                              {'text': '建议先查看购物清单，区分集中补货与重复购买，再决定需要调整的项目。'}]}
        self.reply = reply
        result = self.service.chat('我九月的钱花在哪了')
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(result['meta']['source'], 'deepseek')
        self.assertEqual(result['meta']['validation']['status'], 'passed')
        self.assertNotIn('9999', result['answer'])

    def test_failed_repair_still_returns_verified_local_breakdown(self):
        def reply(_payload):
            if len(self.requests) > 1:
                raise OSError('test transport timeout')
            return {'parts': [{'fact_id': 'scope.total'}]}
        self.reply = reply
        result = self.service.chat('九月消费结构是什么')
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(result['meta']['source'], 'computed_fallback')
        self.assertIn('CAD 150.32', result['answer'])

    def test_bad_paragraph_does_not_discard_other_verified_explanation(self):
        self.reply = {'parts': [
            {'fact_id': 'insight.main_platform'},
            {'text': '不可靠数字 CAD 9999。'},
            {'text': '复查购物清单可以帮助区分集中补货和重复购买，再结合个人预算决定是否调整。'},
        ]}
        result = self.service.chat('九月的钱花在哪了')
        self.assertEqual(result['meta']['source'], 'deepseek')
        self.assertEqual(result['meta']['validation']['status'], 'filtered')
        self.assertEqual(result['meta']['validation']['omitted_paragraphs'], 1)
        self.assertIn('复查购物清单', result['answer'])
        self.assertNotIn('不可靠数字', result['answer'])
        self.assertNotIn('9999', json.dumps(result))

    def test_rank_ties_and_refund_offsets_do_not_invent_concentration(self):
        context = {'summary': {'total': 100}, 'platform_totals': [
            {'name': 'A', 'total': 50, 'share_pct': 50}, {'name': 'B', 'total': 50, 'share_pct': 50}],
            'computed_facts': {}}
        self.assertIn('并列最高', spending_insights(context)['insight.main_platform'])
        self.assertNotIn('超过', spending_insights(context)['insight.main_platform'])
        context['summary']['total'] = 0
        self.assertEqual(set(spending_insights(context)), {'insight.structure'})


if __name__ == '__main__':
    unittest.main()
