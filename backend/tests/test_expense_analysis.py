import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.analysis import AnalysisDataBuilder, AnalysisError, DeepSeekAnalysisService
from app.repository import LedgerRepository
from scripts.ledger.schema import create_schema


def create_analysis_ledger(root):
    db_path = root / "data" / "expenses.sqlite"
    db_path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(db_path)) as conn:
        create_schema(conn)
        conn.execute(
            """
            INSERT INTO cards (card_id, display_name, network, issuer, last4, kind)
            VALUES (
              'mc_1111', 'Mastercard 1111', 'Mastercard', 'Example Bank',
              '1111', 'credit_card'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO orders (
              platform, order_id, date, year, month, merchant, fulfillment,
              category, item_summary, quantity, item_total, tax, fees, tip,
              total, currency, source_url, notes, updated_at
            ) VALUES (
              'walmart_ca', 'private-order-id', '2026-06-20', 2026, '2026-06',
              'Walmart', 'delivery', 'groceries', 'Milk', '1', 10, 1.3, 2, 0,
              13.3, 'CAD', 'https://example.invalid/private', 'private note', 'now'
            )
            """
        )
        order_pk = conn.execute("SELECT id FROM orders").fetchone()[0]
        conn.execute(
            """
            INSERT INTO order_items (
              order_pk, line_key, category, product_name, quantity, price,
              delivery_status, product_link, currency, updated_at
            ) VALUES (?, 'milk', 'groceries', 'Milk 2%', '1', 10, 'delivered',
                      'https://example.invalid/product', 'CAD', 'now')
            """,
            (order_pk,),
        )
        conn.execute(
            """
            INSERT INTO order_payments (
              order_pk, payment_index, card_id, payment_method, amount,
              currency, status, source, notes, updated_at
            ) VALUES (?, 1, 'mc_1111', 'Mastercard', 13.3, 'CAD', 'confirmed',
                      'test', '', 'now')
            """,
            (order_pk,),
        )
        conn.commit()
    return db_path


class AnalysisDataBuilderTest(unittest.TestCase):
    def test_context_contains_items_and_payment_but_not_source_identifiers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LedgerRepository(
                db_path=create_analysis_ledger(root),
                current_exports=root / "exports" / "current",
            )

            payload = AnalysisDataBuilder(repository).build("month")
            serialized = json.dumps(payload, ensure_ascii=False)
            order = payload["orders"][0]

            self.assertEqual(payload["schema_version"], "1.5")
            self.assertTrue(payload["generated_at"])
            self.assertFalse(payload["data_quality"]["has_incomplete_items"])
            self.assertEqual(
                payload["data_quality"]["item_coverage"]["complete_orders"],
                1,
            )
            self.assertTrue(
                payload["analysis_rules"]["computed_facts_are_authoritative"]
            )
            self.assertEqual(
                payload["analysis_rules"]["authoritative_spend_field"],
                "orders.total",
            )
            self.assertEqual(order["items"][0]["name"], "Milk 2%")
            self.assertEqual(order["record_kind"], "platform_order")
            self.assertEqual(order["items"][0]["item_kind"], "product")
            self.assertEqual(order["items"][0]["item_data_status"], "complete")
            self.assertTrue(order["items"][0]["price_known"])
            self.assertTrue(order["items"][0]["contributes_to_item_rankings"])
            self.assertEqual(order["payment"]["label"], "Mastercard 1111")
            self.assertNotIn("last4", order["payment"])
            self.assertEqual(
                payload["computed_facts"]["top_items_by_order_count"][0]["name"],
                "Milk 2%",
            )
            self.assertEqual(
                payload["computed_facts"]["top_items_by_quantity"][0][
                    "total_quantity"
                ],
                1,
            )
            self.assertEqual(
                payload["computed_facts"]["spend_period"][
                    "calendar_day_average"
                ],
                13.3,
            )
            self.assertEqual(
                payload["computed_facts"]["largest_order"]["merchant"],
                "Walmart",
            )
            self.assertEqual(payload["merchant_totals"][0]["name"], "Walmart")
            self.assertEqual(payload["merchant_totals"][0]["share_pct"], 100.0)
            self.assertNotIn("private-order-id", serialized)
            self.assertNotIn("example.invalid", serialized)
            self.assertNotIn("private note", serialized)

    def test_bank_placeholder_is_retained_but_not_ranked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = create_analysis_ledger(root)
            add_analysis_order(
                db_path,
                "bank-placeholder",
                "Bank-recorded purchase",
                "",
                49.99,
                platform="scotiabank",
                merchant="Example Merchant",
            )
            repository = LedgerRepository(
                db_path=db_path,
                current_exports=root / "exports" / "current",
            )

            payload = AnalysisDataBuilder(repository).build("trend")
            bank_order = next(
                order for order in payload["orders"]
                if order["merchant"] == "Example Merchant"
            )
            ranked_names = {
                item["name"]
                for item in payload["computed_facts"]["top_items_by_order_count"]
            }

            self.assertEqual(bank_order["record_kind"], "bank_recorded_order")
            self.assertTrue(bank_order["contributes_to_total_spend"])
            self.assertFalse(bank_order["contributes_to_item_rankings"])
            self.assertEqual(bank_order["items"][0]["item_kind"], "bank_placeholder")
            self.assertEqual(
                bank_order["items"][0]["item_data_status"],
                "placeholder",
            )
            self.assertNotIn("Bank-recorded purchase", ranked_names)
            self.assertEqual(payload["data_quality"]["generic_item_orders"], 1)
            self.assertTrue(payload["data_quality"]["has_incomplete_items"])

    def test_unknown_quantity_is_not_inferred_or_quantity_ranked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = create_analysis_ledger(root)
            add_analysis_order(
                db_path,
                "unknown-quantity",
                "Mystery Bulk Item",
                "unknown",
                99.0,
            )
            repository = LedgerRepository(
                db_path=db_path,
                current_exports=root / "exports" / "current",
            )

            payload = AnalysisDataBuilder(repository).build("trend")
            quantity_names = {
                item["name"]
                for item in payload["computed_facts"]["top_items_by_quantity"]
            }
            count_names = {
                item["name"]
                for item in payload["computed_facts"]["top_items_by_order_count"]
            }

            self.assertIn("Mystery Bulk Item", count_names)
            self.assertNotIn("Mystery Bulk Item", quantity_names)
            self.assertEqual(payload["data_quality"]["unknown_quantity_rows"], 1)
            self.assertEqual(
                payload["data_quality"]["item_coverage"]["partial_orders"],
                1,
            )

    def test_missing_price_stays_unknown_and_is_not_spend_ranked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = create_analysis_ledger(root)
            add_analysis_order(
                db_path,
                "missing-price",
                "Price Unknown Item",
                "2",
                None,
                order_total=20.0,
            )
            repository = LedgerRepository(
                db_path=db_path,
                current_exports=root / "exports" / "current",
            )

            payload = AnalysisDataBuilder(repository).build("trend")
            unknown_order = next(
                order for order in payload["orders"]
                if order["item_summary"] == "Price Unknown Item"
            )
            spend_names = {
                item["name"]
                for item in payload["computed_facts"]["top_items_by_spend"]
            }

            self.assertIsNone(unknown_order["items"][0]["price"])
            self.assertFalse(unknown_order["items"][0]["price_known"])
            self.assertEqual(payload["data_quality"]["unknown_price_rows"], 1)
            self.assertNotIn("Price Unknown Item", spend_names)

    def test_partial_month_uses_complete_month_average_and_same_day_comparison(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = create_analysis_ledger(root)
            with closing(sqlite3.connect(db_path)) as conn:
                from app.data_status import LABELS
                conn.executemany('INSERT INTO collection_observations VALUES (?,?,?,?,?,?,?,?)',
                    [('test-check', platform, 'local', 'passed', '2026-06-22T12:00:00+00:00',
                      '2026-05-01', 'bounded', '') for platform in LABELS])
                conn.commit()
            add_analysis_order(
                db_path,
                "may-complete",
                "May Item",
                "1",
                100.0,
                order_date="2026-05-15",
            )
            repository = LedgerRepository(
                db_path=db_path,
                current_exports=root / "exports" / "current",
            )

            payload = AnalysisDataBuilder(
                repository,
                today=date(2026, 6, 22),
            ).build("trend")
            comparison = payload["computed_facts"]["period_comparison"]

            self.assertEqual(comparison["complete_month_count"], 1)
            self.assertEqual(comparison["complete_month_average"], 100.0)
            self.assertFalse(comparison["latest_month_complete"])
            self.assertEqual(comparison["comparison_day"], 20)
            self.assertEqual(comparison["previous_same_day_total"], 100.0)
            self.assertEqual(comparison["latest_same_day_total"], 13.3)
            self.assertEqual(comparison["projected_month_total"], 19.95)
            self.assertIn(
                "同日口径",
                DeepSeekAnalysisService._deterministic_trends(payload)[-1],
            )
            self.assertIn(
                "低置信度条件预测",
                DeepSeekAnalysisService._deterministic_forecast(payload),
            )

    def test_each_mode_uses_expected_period(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LedgerRepository(
                db_path=create_analysis_ledger(root),
                current_exports=root / "exports" / "current",
            )
            builder = AnalysisDataBuilder(repository)

            self.assertEqual(builder.build("month")["scope"]["period"], "2026-06")
            self.assertEqual(builder.build("year")["scope"]["period"], "2026")
            self.assertEqual(
                builder.build("trend")["scope"]["period"],
                "2026-06 至 2026-06",
            )


def add_analysis_order(
    db_path,
    order_id,
    product_name,
    quantity,
    price,
    platform="amazon_ca",
    merchant="Amazon",
    category="retail",
    order_date="2026-06-21",
    order_total=None,
):
    parsed_date = date.fromisoformat(order_date)
    resolved_total = price if order_total is None else order_total
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO orders (
              platform, order_id, date, year, month, merchant, fulfillment,
              category, item_summary, quantity, item_total, tax, fees, tip,
              total, currency, source_url, notes, updated_at
            ) VALUES (
              ?, ?, ?, ?, ?,
              ?, 'delivery', ?, ?, ?, ?, 0, 0, 0,
              ?, 'CAD', '', '', 'now'
            )
            """,
            (
                platform,
                order_id,
                order_date,
                parsed_date.year,
                parsed_date.strftime("%Y-%m"),
                merchant,
                category,
                product_name,
                quantity,
                resolved_total,
                resolved_total,
            ),
        )
        order_pk = conn.execute(
            "SELECT id FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO order_items (
              order_pk, line_key, category, product_name, quantity, price,
              delivery_status, product_link, currency, updated_at
            ) VALUES (?, 'line', ?, ?, ?, ?, 'delivered', '', 'CAD', 'now')
            """,
            (order_pk, category, product_name, quantity, price),
        )
        conn.commit()


class DeepSeekAnalysisServiceTest(unittest.TestCase):
    def test_requires_api_key_without_calling_upstream(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LedgerRepository(
                db_path=create_analysis_ledger(root),
                current_exports=root / "exports" / "current",
            )
            service = DeepSeekAnalysisService(
                repository,
                api_key="",
                cache_path=root / "cache.json",
            )

            with self.assertRaises(AnalysisError) as raised:
                service.analyze("month")

            self.assertEqual(raised.exception.status_code, 503)

    def test_calls_json_api_and_reuses_content_addressed_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LedgerRepository(
                db_path=create_analysis_ledger(root),
                current_exports=root / "exports" / "current",
            )
            calls = []

            def transport(url, payload, headers, timeout):
                calls.append((url, payload, headers, timeout))
                content = json.dumps(
                    {
                        "title": "六月分析",
                        "summary": "食品支出为主要部分。",
                        "highlights": ["牛奶是本月明细。"],
                        "trends": ["当前只有一个月份。"],
                        "suggestions": ["购买前比较单位价格。"],
                    },
                    ensure_ascii=False,
                )
                return 200, {
                    "choices": [{"message": {"content": content}}],
                    "usage": {"total_tokens": 321},
                }

            service = DeepSeekAnalysisService(
                repository,
                api_key="test-key",
                model="deepseek-v4-flash",
                cache_path=root / "cache.json",
                transport=transport,
            )

            first = service.analyze("month")
            second = service.analyze("month")

            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1]["response_format"]["type"], "json_object")
            self.assertEqual(calls[0][1]["thinking"]["type"], "disabled")
            self.assertEqual(calls[0][1]["temperature"], 0)
            prompt = calls[0][1]["messages"][1]["content"]
            self.assertIn("Milk 2%", prompt)
            self.assertIn("Mastercard 1111", prompt)
            self.assertIn("period_comparison", prompt)
            self.assertNotIn("private-order-id", prompt)
            self.assertNotIn("test-key", json.dumps(calls[0][1]))
            self.assertEqual(first["meta"]["usage"]["total_tokens"], 321)
            self.assertEqual(first["meta"]["filtered_claims"], 0)
            self.assertEqual(first["result"]["summary"], (
                "2026-06 截至 2026-06-20 共记录 1 笔支出，合计 CAD 13.30。"
            ))

    def test_unsupported_generated_amount_is_hidden(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LedgerRepository(
                db_path=create_analysis_ledger(root),
                current_exports=root / "exports" / "current",
            )

            def transport(*_args):
                content = json.dumps(
                    {
                        "title": "模型标题",
                        "summary": "模型把金额算错成 CAD 40.13。",
                        "highlights": [
                            "无法核验的组合金额为 CAD 40.13。",
                            "账本总额为 CAD 13.30。",
                        ],
                        "trends": [],
                        "forecast": [],
                        "suggestions": [],
                        "limitations": [],
                    },
                    ensure_ascii=False,
                )
                return 200, {"choices": [{"message": {"content": content}}]}

            service = DeepSeekAnalysisService(
                repository,
                api_key="test-key",
                cache_path=root / "cache.json",
                transport=transport,
            )
            result = service.analyze("month")

            self.assertEqual(
                result["result"]["highlights"],
                ["账本总额为 CAD 13.30。"],
            )
            self.assertEqual(result["meta"]["filtered_claims"], 1)
            self.assertNotIn("40.13", json.dumps(result["result"], ensure_ascii=False))

    def test_uber_fee_breakdown_is_marked_not_captured(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = create_analysis_ledger(root)
            add_analysis_order(
                db_path, "june-uber", "Meal A", "1", 20.0,
                platform="uber_eats", merchant="Restaurant A",
                category="food_delivery",
            )
            repository = LedgerRepository(
                db_path=db_path,
                current_exports=root / "exports" / "current",
            )

            payload = AnalysisDataBuilder(repository).build("month")
            by_platform = {order["platform"]: order for order in payload["orders"]}

            self.assertEqual(by_platform["uber_eats"]["fee_breakdown"], "not_captured")
            self.assertEqual(by_platform["walmart_ca"]["fee_breakdown"], "captured")
            self.assertEqual(payload["data_quality"]["fee_breakdown_not_captured_orders"], 1)
            self.assertIn("fee_breakdown_rule", payload["analysis_rules"])

    def test_monthly_category_claim_cannot_relabel_platform_total(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = create_analysis_ledger(root)
            add_analysis_order(
                db_path,
                "may-uber",
                "Meal A",
                "1",
                20.0,
                platform="uber_eats",
                merchant="Restaurant A",
                category="food_delivery",
                order_date="2026-05-10",
            )
            add_analysis_order(
                db_path,
                "may-fantuan",
                "Meal B",
                "1",
                30.0,
                platform="fantuan",
                merchant="Restaurant B",
                category="food_delivery",
                order_date="2026-05-12",
            )
            repository = LedgerRepository(
                db_path=db_path,
                current_exports=root / "exports" / "current",
            )
            service = DeepSeekAnalysisService(
                repository,
                api_key="",
                cache_path=root / "cache.json",
            )
            context = service.builder.build("trend")
            generated = {
                "title": "test",
                "summary": "test",
                "highlights": [],
                "trends": [
                    "5月外卖支出 CAD 20.00。",
                    "5月外卖支出 CAD 50.00。",
                ],
                "forecast": [],
                "suggestions": [],
                "limitations": [],
            }

            result, filtered = service._finalize_result("trend", context, generated)

            self.assertIn("5月外卖支出 CAD 50.00。", result["trends"])
            self.assertNotIn("5月外卖支出 CAD 20.00。", result["trends"])
            self.assertEqual(filtered, 1)

    def test_generated_timestamp_does_not_invalidate_analysis_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LedgerRepository(
                db_path=create_analysis_ledger(root),
                current_exports=root / "exports" / "current",
            )
            service = DeepSeekAnalysisService(
                repository,
                api_key="test-key",
                cache_path=root / "cache.json",
            )
            context = service.builder.build("month")
            later_context = {
                **context,
                "generated_at": "2099-01-01T00:00:00+00:00",
            }

            self.assertEqual(
                service._cache_key("month", context),
                service._cache_key("month", later_context),
            )

    def test_maps_upstream_auth_error_without_exposing_response(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LedgerRepository(
                db_path=create_analysis_ledger(root),
                current_exports=root / "exports" / "current",
            )
            service = DeepSeekAnalysisService(
                repository,
                api_key="bad-key",
                cache_path=root / "cache.json",
                transport=lambda *_args: (401, {"error": {"message": "secret"}}),
            )

            with self.assertRaises(AnalysisError) as raised:
                service.analyze("month")

            self.assertIn("密钥无效", raised.exception.detail)
            self.assertNotIn("secret", raised.exception.detail)


    def test_chat_uses_full_ledger_context_and_page_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LedgerRepository(
                db_path=create_analysis_ledger(root),
                current_exports=root / "exports" / "current",
            )
            calls = []

            def transport(url, payload, headers, timeout):
                calls.append((url, payload, headers, timeout))
                return 200, {
                    "choices": [{"message": {"content": json.dumps({'parts': [{'fact_id': 'order.1'}]})}}],
                    "usage": {"total_tokens": 222},
                }

            service = DeepSeekAnalysisService(
                repository,
                api_key="test-key",
                cache_path=root / "cache.json",
                transport=transport,
            )

            first = service.chat(
                "我买了什么牛奶？",
                history=[
                    {"role": "user", "content": "先记住我关心牛奶"},
                    {"role": "assistant", "content": "我会沿用这次页面会话。"},
                ],
            )

            self.assertFalse(first["cached"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(first["meta"]["mode"], "chat")
            self.assertEqual(first["meta"]["history_messages"], 2)
            serialized_messages = json.dumps(
                calls[0][1]["messages"],
                ensure_ascii=False,
            )
            self.assertIn("我买了什么牛奶？", serialized_messages)
            self.assertIn("先记住我关心牛奶", serialized_messages)
            self.assertIn("Milk 2%", serialized_messages)
            self.assertIn("Mastercard 1111", serialized_messages)
            self.assertIn("computed_facts", serialized_messages)
            self.assertNotIn("private-order-id", serialized_messages)
            self.assertNotIn("example.invalid", serialized_messages)
            self.assertNotIn("test-key", json.dumps(calls[0][1]))
            self.assertEqual(first["meta"]["usage"]["total_tokens"], 222)

    def test_chat_blocks_unsupported_amount(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LedgerRepository(
                db_path=create_analysis_ledger(root),
                current_exports=root / "exports" / "current",
            )
            service = DeepSeekAnalysisService(
                repository,
                api_key="test-key",
                cache_path=root / "cache.json",
                transport=lambda *_args: (
                    200,
                    {"choices": [{"message": {"content": "牛奶合计 CAD 40.13。"}}]},
                ),
            )

            result = service.chat("牛奶多少钱？")
            self.assertEqual(result['meta']['source'], 'computed_fallback')
            self.assertNotIn('40.13', result['answer'])
            self.assertIn('13.30', result['answer'])

    def test_item_superlative_question_uses_computed_facts_without_upstream(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = create_analysis_ledger(root)
            add_analysis_order(db_path, "order-milk-2", "Milk 2%", "1", 5.25)
            add_analysis_order(db_path, "order-eggs", "Eggs", "12", 7.5)
            add_analysis_order(
                db_path,
                "bank-restaurant-1",
                "Restaurant/coffee purchase",
                "99",
                95.21,
                platform="scotiabank",
                merchant="Scotiabank",
            )
            repository = LedgerRepository(
                db_path=db_path,
                current_exports=root / "exports" / "current",
            )

            def transport(*_args):
                raise AssertionError("computed item facts should avoid API call")

            service = DeepSeekAnalysisService(
                repository,
                api_key="",
                cache_path=root / "cache.json",
                transport=transport,
            )
            result = service.chat("我买的次数最多的物品和数量最多的物品是什么？")

            self.assertIn("Milk 2%", result["answer"])
            self.assertIn("Eggs", result["answer"])
            self.assertIn("购买次数", result["answer"])
            self.assertIn("购买数量", result["answer"])
            self.assertNotIn("Restaurant/coffee purchase", result["answer"])
            self.assertEqual(result["meta"]["source"], "computed")
            self.assertEqual(result["meta"]["usage"], {})

    def test_group_and_total_questions_use_computed_facts_without_upstream(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LedgerRepository(
                db_path=create_analysis_ledger(root),
                current_exports=root / "exports" / "current",
            )

            def transport(*_args):
                raise AssertionError("aggregate facts should avoid API call")

            service = DeepSeekAnalysisService(
                repository,
                api_key="",
                cache_path=root / "cache.json",
                transport=transport,
            )

            merchant = service.chat("哪个商户总花销最高？")
            total = service.chat("我的总支出是多少？")

            self.assertIn("Walmart", merchant["answer"])
            self.assertIn("CAD 13.30", merchant["answer"])
            self.assertIn("CAD 13.30", total["answer"])
            self.assertEqual(merchant["meta"]["source"], "computed")

    def test_current_month_largest_order_uses_month_scope_without_upstream(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = create_analysis_ledger(root)
            add_analysis_order(
                db_path,
                "older-large-order",
                "Older purchase",
                "1",
                100.0,
                merchant="Older Merchant",
                order_date="2026-07-10",
            )
            add_analysis_order(
                db_path,
                "current-largest-order",
                "Current purchase",
                "1",
                50.0,
                merchant="Current Merchant",
                order_date="2026-08-19",
            )
            repository = LedgerRepository(
                db_path=db_path,
                current_exports=root / "exports" / "current",
            )

            def transport(*_args):
                raise AssertionError("largest-order fact should avoid API call")

            service = DeepSeekAnalysisService(
                repository,
                api_key="",
                cache_path=root / "cache.json",
                transport=transport,
            )
            service.builder.today = date(2026, 8, 20)
            result = service.chat("本月花销最大的是什么？")

            self.assertIn("Current Merchant", result["answer"])
            self.assertIn("CAD 50.00", result["answer"])
            self.assertNotIn("Older Merchant", result["answer"])
            self.assertEqual(result["meta"]["period"], "2026-08")
            self.assertEqual(result["meta"]["ledger_scope"], "month")
            self.assertEqual(result["meta"]["source"], "computed")

    def test_current_month_delivery_questions_use_scoped_computed_facts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = create_analysis_ledger(root)
            add_analysis_order(
                db_path,
                "delivery-one",
                "Noodles",
                "1",
                20.0,
                platform="fantuan",
                merchant="Noodle Shop",
                category="food_delivery",
                order_date="2026-08-18",
            )
            add_analysis_order(
                db_path,
                "delivery-two",
                "Chicken",
                "1",
                30.0,
                platform="fantuan",
                merchant="Chicken Shop",
                category="food_delivery",
                order_date="2026-08-19",
            )
            add_analysis_order(
                db_path,
                "older-delivery",
                "Old meal",
                "1",
                80.0,
                platform="fantuan",
                merchant="Old Shop",
                category="food_delivery",
                order_date="2026-07-19",
            )
            repository = LedgerRepository(
                db_path=db_path,
                current_exports=root / "exports" / "current",
            )

            def transport(*_args):
                raise AssertionError("delivery facts should avoid API call")

            service = DeepSeekAnalysisService(
                repository,
                api_key="",
                cache_path=root / "cache.json",
                transport=transport,
            )
            service.builder.today = date(2026, 8, 20)

            spend = service.chat("本月外卖花销是多少？")
            orders = service.chat("本月的外卖")

            self.assertIn("外卖共 2 笔", spend["answer"])
            self.assertIn("CAD 50.00", spend["answer"])
            self.assertIn("Noodle Shop", orders["answer"])
            self.assertIn("Chicken Shop", orders["answer"])
            self.assertNotIn("Old Shop", orders["answer"])
            self.assertEqual(spend["meta"]["source"], "computed")
            self.assertEqual(orders["meta"]["ledger_scope"], "month")

    def test_daily_average_question_uses_actual_ledger_dates_without_upstream(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = create_analysis_ledger(root)
            add_analysis_order(
                db_path,
                "order-average",
                "Coffee",
                "1",
                30.0,
                order_date="2026-06-22",
            )
            repository = LedgerRepository(
                db_path=db_path,
                current_exports=root / "exports" / "current",
            )

            def transport(*_args):
                raise AssertionError("daily average should avoid API call")

            service = DeepSeekAnalysisService(
                repository,
                api_key="",
                cache_path=root / "cache.json",
                transport=transport,
            )
            result = service.chat("平均每天，我的花销是多少？")

            self.assertIn("2026-06-20 至 2026-06-22", result["answer"])
            self.assertIn("3 个自然日", result["answer"])
            self.assertIn("CAD 43.30", result["answer"])
            self.assertIn("CAD 14.43", result["answer"])
            self.assertIn("2 天", result["answer"])
            self.assertIn("CAD 21.65", result["answer"])
            self.assertNotIn("92", result["answer"])
            self.assertEqual(result["meta"]["source"], "computed")


class AnalysisApiTest(unittest.TestCase):
    def test_analysis_page_status_and_run_are_separate_routes(self):
        from app import main

        class FakeAnalysisService:
            @staticmethod
            def status():
                return {
                    "configured": True,
                    "model": "test-model",
                    "periods": {"latest_month": "2026-06"},
                }

            @staticmethod
            def analyze(mode):
                return {
                    "result": {"title": mode, "summary": "ok"},
                    "meta": {"mode": mode},
                    "cached": False,
                }

            @staticmethod
            def chat(question, history=None):
                return {
                    "answer": f"answer: {question}",
                    "meta": {"mode": "chat", "history_messages": len(history or [])},
                    "cached": False,
                }

        # The status route also lists export periods from the ledger; stub it so the
        # test uses no real data and passes on machines without backend/data.
        with patch.object(main, "analysis_service", FakeAnalysisService()), \
                patch.object(main, "export_periods", lambda _repo: {}):
            client = TestClient(main.app, base_url="http://127.0.0.1")
            page = client.get("/analysis")
            status = client.get("/api/analysis/status")
            result = client.post("/api/analysis/run", json={"mode": "month"})
            chat = client.post(
                "/api/analysis/chat",
                json={
                    "question": "总花销多少？",
                    "history": [{"role": "user", "content": "先看六月"}],
                },
            )
            invalid = client.post("/api/analysis/run", json={"mode": "custom"})

        self.assertEqual(page.status_code, 200)
        self.assertIn("账本分析", page.text)
        self.assertTrue(status.json()["configured"])
        self.assertEqual(result.json()["meta"]["mode"], "month")
        self.assertEqual(chat.json()["answer"], "answer: 总花销多少？")
        self.assertEqual(chat.json()["meta"]["history_messages"], 1)
        self.assertEqual(invalid.status_code, 400)


class AnalysisStaticAssetsTest(unittest.TestCase):
    def test_chat_history_is_page_scoped_and_not_persisted(self):
        static = Path(__file__).resolve().parents[1] / "app" / "static"
        javascript = (static / "analysis.js").read_text(encoding="utf-8")
        css = (static / "analysis.css").read_text(encoding="utf-8")

        self.assertIn("const chatHistory = [];", javascript)
        self.assertIn("history: chatHistory.slice", javascript)
        self.assertIn("rememberChatTurn", javascript)
        self.assertIn("function renderMarkdown", javascript)
        self.assertIn('document.createElement("table")', javascript)
        self.assertIn("appendInlineMarkdown", javascript)
        self.assertIn('appendList(content, "轻量预测", result.forecast)', javascript)
        self.assertIn('appendList(content, "数据限制", result.limitations)', javascript)
        self.assertIn("meta.as_of_date", javascript)
        self.assertIn(".markdown-table-wrap", css)
        self.assertNotIn("innerHTML", javascript)
        # Only the provider choice persists; the financial conversation remains page-scoped.
        storage_lines = [line.strip() for line in javascript.splitlines() if "localStorage" in line]
        self.assertEqual(len(storage_lines), 2)
        self.assertTrue(all('"expense-analysis-provider"' in line for line in storage_lines))
        self.assertFalse(any("chatHistory" in line for line in storage_lines))
        self.assertNotIn("sessionStorage", javascript)


if __name__ == "__main__":
    unittest.main()
