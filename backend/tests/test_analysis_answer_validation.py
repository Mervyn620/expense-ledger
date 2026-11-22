import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.analysis import DeepSeekAnalysisService
from app.repository import LedgerRepository
from test_expense_analysis import create_analysis_ledger, add_analysis_order


class AnalysisAnswerValidationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        db = create_analysis_ledger(root)
        for key, amount, when, category in (
            ('july', 51, '2026-07-10', 'food_delivery'),
            ('august-food', 60, '2026-08-10', 'food_delivery'),
            ('august-retail', 40, '2026-08-11', 'retail'),
        ):
            add_analysis_order(db, key, key, '1', amount, order_date=when, category=category)
        self.content = ''
        self.payload = None

        def transport(_url, payload, _headers, _timeout):
            self.payload = payload
            return 200, {'choices': [{'message': {'content': self.content}}]}

        self.service = DeepSeekAnalysisService(
            LedgerRepository(db, root / 'exports' / 'current'),
            api_key='test-key', cache_path=root / 'cache.json', transport=transport)
        self.service.builder.today = date(2026, 9, 10)

    def answer(self, parts, question='8月花销分析'):
        self.content = json.dumps({'parts': parts}, ensure_ascii=False)
        return self.service.chat(question)

    def test_complete_fact_references_keep_month_category_and_amount_bound(self):
        result = self.answer([
            {'fact_id': 'month.2026-07.total'},
            {'fact_id': 'month.2026-08.total'},
            {'fact_id': 'category.food_delivery'},
        ], '7月和8月花销分析')
        self.assertIn('2026-07 全部所选记录：已记录 1 笔，净支出 CAD 51.00', result['answer'])
        self.assertIn('2026-08 全部所选记录：已记录 2 笔，净支出 CAD 100.00', result['answer'])
        self.assertIn('类别「外卖」', result['answer'])
        self.assertEqual(result['meta']['validation']['status'], 'passed')
        self.assertEqual(self.payload['response_format'], {'type': 'json_object'})
        self.assertIn('answer_facts', self.payload['messages'][1]['content'])

    def test_cautious_prose_is_accepted_with_correct_facts(self):
        for text in ('不能仅凭这些数据认定消费浪费。', '现有记录不足以进行预测。',
                     '无法判断个人偏好。', '不应将这些消费认定为非必需。'):
            with self.subTest(text=text):
                result = self.answer([{'fact_id': 'scope.total'}, {'text': text}])
                self.assertEqual(result['meta']['source'], 'deepseek')
                self.assertIn(text, result['answer'])
                self.assertIn('CAD 100.00', result['answer'])

    def test_assertions_and_mixed_negation_stay_blocked(self):
        for text in ('这些消费都是浪费。', '现有记录不足以进行预测，但预计支出会下降。',
                     '不能说没有浪费。', '不能断定浪费；但你的消费存在浪费。',
                     '无法判断偏好。不过你的偏好很明显。'):
            with self.subTest(text=text):
                result = self.answer([{'text': text}])
                self.assertEqual(result['meta']['validation']['reason'], 'unsupported_claim')
                self.assertNotIn(text, result['answer'])

    def test_same_ledger_number_cannot_be_relabelled_as_another_month(self):
        result = self.answer([{'text': '8月净支出 CAD 51.00。'}], '7月与8月花销分析')
        self.assertEqual(result['meta']['source'], 'computed_fallback')
        self.assertEqual(result['meta']['validation']['reason'], 'unreferenced_number')
        self.assertNotIn('8月净支出 CAD 51.00', result['answer'])
        self.assertIn('2026-08 全部所选记录：已记录 2 笔，净支出 CAD 100.00', result['answer'])

    def test_model_cannot_override_fact_labels(self):
        result = self.answer([{'fact_id': 'scope.total', 'text': '外卖'}])
        self.assertEqual(result['meta']['validation']['reason'], 'invalid_format')

    def test_unknown_or_wrong_scope_fact_is_not_rendered(self):
        for fact_id in ('month.2026-07.total', 'unknown', ['scope.total'], None):
            with self.subTest(fact_id=fact_id):
                result = self.answer([{'fact_id': fact_id}])
                self.assertEqual(result['meta']['validation']['reason'], 'unknown_fact')

    def test_unreferenced_numbers_and_percentages_use_fallback(self):
        for text in ('占比为 40%。', '占比为百分之四十。', '外卖六十加币。',
                     '今年八月净支出一百元。', '预算 $500。', '一共两笔。'):
            with self.subTest(text=text):
                result = self.answer([{'text': text}])
                self.assertEqual(result['meta']['validation']['reason'], 'unreferenced_number')
                self.assertNotIn(text, result['answer'])

    def test_malformed_and_empty_content_returns_useful_marked_summary(self):
        for content in (None, '', {}, '{broken', '{"parts":[]}', '{"parts":[null]}',
                        '{"parts":[{"text":""}]}', '```json\n{broken\n```'):
            with self.subTest(content=content):
                self.content = content
                result = self.service.chat('8月花销分析')
                self.assertEqual(result['meta']['source'], 'computed_fallback')
                self.assertIn('本地计算', result['answer'])
                self.assertIn('CAD 100.00', result['answer'])
                self.assertIn('60.0%', result['answer'])
                self.assertEqual(result['meta']['validation']['fact_ids'], [])
                self.assertEqual(len(result['evidence']['rows']), 2)

    def test_rejected_output_is_not_exposed_in_metadata_or_evidence(self):
        marker = 'untrusted-response-marker'
        result = self.answer([{'text': marker + ' CAD 9999'}])
        self.assertNotIn(marker, json.dumps(result))

    def test_fallback_does_not_present_scope_total_as_a_category_answer(self):
        result = self.answer([{'text': '外卖金额 CAD 999。'}], '8月外卖花销分析')
        self.assertIn('不代替对原问题的完整解读', result['answer'])
        self.assertIn('全部所选记录', result['answer'])
        self.assertEqual(len(result['evidence']['rows']), 2)

    def test_api_returns_200_for_content_validation_fallback(self):
        from app import main
        self.content = '错误金额 CAD 999。'
        with patch.object(main, 'analysis_service', self.service):
            client = TestClient(main.app, base_url='http://127.0.0.1')
            response = client.post('/api/analysis/chat', json={'question': '8月花销分析'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['meta']['source'], 'computed_fallback')

    def test_report_limitations_do_not_reject_negated_judgments(self):
        context = self.service.builder.build('trend')
        self.assertTrue(self.service._claim_semantics_supported('无法判断个人偏好。', 'limitations', context))
        self.assertFalse(self.service._claim_semantics_supported('这些支出都是浪费。', 'suggestions', context))


if __name__ == '__main__':
    unittest.main()
