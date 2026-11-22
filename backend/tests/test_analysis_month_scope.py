import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.analysis import AnalysisError, DeepSeekAnalysisService
from app.ledger_questions import question_scope, query_plan
from app.repository import LedgerRepository
from test_expense_analysis import create_analysis_ledger, add_analysis_order


class AnalysisMonthScopeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db_path = create_analysis_ledger(root)
        for key, amount, when, category, platform in (
            ('old-august', 900, '2025-08-10', 'food_delivery', 'fantuan'),
            ('july', 51, '2026-07-10', 'food_delivery', 'fantuan'),
            ('august-food', 60, '2026-08-10', 'food_delivery', 'fantuan'),
            ('august-retail', 40, '2026-08-11', 'retail', 'amazon_ca'),
            ('september', 12, '2026-09-05', 'retail', 'amazon_ca'),
        ):
            add_analysis_order(self.db_path, key, key, '1', amount,
                               order_date=when, category=category, platform=platform)
        self.contexts = []
        self.reply = '已按所选月份整理账本记录。'

        def transport(_url, payload, _headers, _timeout):
            self.contexts.append(json.loads(payload['messages'][1]['content'].split('\n', 1)[1]))
            return 200, {'choices': [{'message': {'content': self.reply}}]}

        self.service = DeepSeekAnalysisService(
            LedgerRepository(self.db_path, root / 'exports' / 'current'),
            api_key='test-key', cache_path=root / 'cache.json', transport=transport)
        self.service.builder.today = date(2026, 9, 9)

    def test_month_analysis_preserves_intent_and_filters_every_fact(self):
        for question in ('8月花销分析', '八月花销分析', '2026年8月花销分析',
                         '2026-08花销分析', '今年8月花销分析', '上个月花销分析'):
            with self.subTest(question=question):
                result = self.service.chat(question)
                self.assertEqual(result['meta']['source'], 'deepseek')
                self.assertEqual(result['meta']['period'], '2026-08')
                self.assertEqual(result['meta']['total'], 100)
                context = self.contexts[-1]
                self.assertEqual({row['month'] for row in context['orders']}, {'2026-08'})
                self.assertEqual({row['month'] for row in context['monthly_totals']}, {'2026-08'})
                self.assertEqual({row['month'] for row in context['monthly_category_totals']}, {'2026-08'})
                self.assertEqual(sum(row['total'] for row in context['category_totals']), 100)
                self.assertEqual({row['date'][:7] for row in result['evidence']['rows']}, {'2026-08'})

    def test_bare_month_totals_and_food_do_not_sum_full_ledger(self):
        for question, total, count in (
            ('8月总花销是多少', 100, 2), ('八月外卖花了多少', 60, 1),
            ('2025年8月总花销是多少', 900, 1), ('去年八月外卖花了多少', 900, 1),
        ):
            with self.subTest(question=question):
                result = self.service.chat(question)
                self.assertEqual(result['meta']['total'], total)
                self.assertEqual(result['meta']['orders'], count)
                self.assertEqual(result['meta']['source'], 'computed')
        self.assertFalse(self.contexts)

    def test_category_share_is_available_to_model_and_passes_validation(self):
        self.reply = json.dumps({'parts': [{'fact_id': 'category.food_delivery'}]})
        result = self.service.chat('分析2026年8月的各分类占比')
        self.assertIn('CAD 60.00', result['answer'])
        self.assertIn('60.0%', result['answer'])
        self.assertEqual(result['meta']['validation']['status'], 'passed')
        row = next(row for row in self.contexts[-1]['category_totals'] if row['name'] == 'food_delivery')
        self.assertEqual(row['share_pct'], 60)

    def test_platform_analysis_preserves_platform_filter(self):
        result = self.service.chat('8月饭团花销分析')
        self.assertEqual(result['meta']['total'], 60)
        self.assertEqual({row['platform'] for row in self.contexts[-1]['orders']}, {'fantuan'})

    def test_comparisons_are_not_replaced_with_combined_total(self):
        for question in ('今年8月对比7月', '8月总花销比7月如何', '8月和7月的外卖对比'):
            with self.subTest(question=question):
                result = self.service.chat(question)
                self.assertEqual(result['meta']['source'], 'deepseek')
                self.assertEqual({row['month'] for row in self.contexts[-1]['monthly_totals']},
                                 {'2026-07', '2026-08'})

    def test_no_records_never_substitutes_another_month(self):
        for question in ('10月花销分析', '2024年8月花销分析'):
            with self.subTest(question=question), self.assertRaises(AnalysisError) as error:
                self.service.chat(question)
            self.assertEqual(error.exception.status_code, 404)
        self.assertFalse(self.contexts)

    def test_wrong_out_of_scope_amount_still_blocked(self):
        self.reply = '8月总花销为 CAD 900.00。'
        result = self.service.chat('8月花销分析')
        self.assertEqual(result['meta']['source'], 'computed_fallback')
        self.assertNotIn('900.00', result['answer'])

    def test_year_and_no_date_scopes_still_work(self):
        for question, total in (('去年花销分析', 900), ('今年花销分析', 176.3),
                                ('花销分析', 1076.3)):
            with self.subTest(question=question):
                self.assertAlmostEqual(self.service.chat(question)['meta']['total'], total)

    def test_calendar_boundaries_and_ranges(self):
        for question, today, months in (
            ('上个月分析', date(2026, 1, 2), ['2025-12']),
            ('八月分析', date(2027, 1, 2), ['2027-08']),
            ('去年十二月对比今年一月', date(2026, 9, 9), ['2025-12', '2026-01']),
            ('2025年8月对比7月', date(2026, 9, 9), ['2025-08', '2025-07']),
            ('去年的八月分析', date(2026, 9, 9), ['2025-08']),
            ('2025年12月至2026年2月', date(2026, 9, 9), ['2025-12', '2026-01', '2026-02']),
            ('7月到9月花销分析', date(2026, 9, 9), ['2026-07', '2026-08', '2026-09']),
        ):
            with self.subTest(question=question):
                self.assertEqual(question_scope(question, today)['months'], months)

    def test_invalid_month_returns_actionable_error(self):
        for question in ('13月花销分析', '2026-00花销分析', '9月至7月花销分析'):
            with self.subTest(question=question), self.assertRaises(AnalysisError) as error:
                self.service.chat(question)
            self.assertEqual(error.exception.status_code, 400)
        self.assertIsNone(query_plan('2026-08-01饭团多少钱', date(2026, 9, 9)))


if __name__ == '__main__':
    unittest.main()
