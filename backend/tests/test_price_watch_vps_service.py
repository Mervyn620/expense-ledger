import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "price_watch" / "vps_service.py"
spec = importlib.util.spec_from_file_location("price_watch_vps_service", SCRIPT)
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


def product_payload(product_id="6000207000101", name="Great Value Eggs"):
    url = f"https://www.walmart.ca/en/ip/great-value-eggs/{product_id}"
    product = {
        "product_key": f"walmart-ca:{product_id}",
        "name": name,
        "url": url,
        "first_purchased_on": "2026-07-01",
        "last_purchased_on": "2026-07-20",
        "last_purchase_price": 3.95,
        "currency": "CAD",
    }
    material = json.dumps(product, sort_keys=True).encode("utf-8")
    return {
        "contract": service.CONTRACT,
        "batch_id": __import__("hashlib").sha256(material).hexdigest(),
        "generated_at": "2026-07-24T12:00:00+00:00",
        "store_id": "3055",
        "products": [product],
    }


class FakePage:
    def __init__(
        self,
        text,
        status=200,
        url="https://www.walmart.ca/en/ip/item/1",
        anchors=None,
    ):
        self._text = text
        self.status = status
        self.url = url
        self.anchors = anchors or []

    def get_all_text(self, separator="\n", strip=True):
        return self._text

    def css(self, selector):
        if selector == 'script[type="application/ld+json"]::text':
            return FakeSelectors([])
        return FakeSelectors([FakeAnchor(value) for value in self.anchors])


class FakeSelectors:
    def __init__(self, values):
        self.values = values

    def getall(self):
        return self.values

    def __iter__(self):
        return iter(self.values)


class FakeAnchor:
    def __init__(self, text):
        self.text = text

    def get_all_text(self, separator=" ", strip=True):
        return self.text


class FakeLabel:
    def __init__(self, text):
        self.text = text

    def inner_text(self):
        return self.text


class FakeRadio:
    def __init__(self, label):
        self.label = FakeLabel(label)

    def locator(self, _selector):
        return self.label


class FakeRadios:
    def __init__(self, labels):
        self.radios = [FakeRadio(label) for label in labels]

    def count(self):
        return len(self.radios)

    def nth(self, index):
        return self.radios[index]


class FakeStorePage:
    def __init__(self, labels):
        self.radios = FakeRadios(labels)

    def locator(self, selector):
        if selector == 'input[name="pickup-store"]':
            return self.radios
        raise AssertionError(f"Unexpected selector: {selector}")


class PriceWatchVpsServiceTest(unittest.TestCase):
    def test_ingest_is_cumulative_and_batch_replay_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "watch.sqlite3"
            first_payload = product_payload()
            first = service.ingest_watchlist(database, first_payload)
            replay = service.ingest_watchlist(database, first_payload)
            second_payload = product_payload("6000207000102", "Great Value Milk")
            second = service.ingest_watchlist(database, second_payload)

            with closing(sqlite3.connect(database)) as conn:
                count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]

            self.assertEqual(first["created"], 1)
            self.assertTrue(replay["replayed"])
            self.assertEqual(second["created"], 1)
            self.assertEqual(count, 2)

    def test_extract_price_requires_store_and_ignores_recommendation_prices(self):
        page = FakePage(
            """
            Great Value Household Gloves
            Current price is
            $4.97
            Free pickup at MISSISSAUGA (SQUARE ONE), ON
            Similar items
            Rollback
            Current price is $1.00
            Was price $5.00
            """
        )

        facts = service.extract_price_facts(
            page, "MISSISSAUGA (SQUARE ONE), ON"
        )

        self.assertEqual(facts["current_price"], 4.97)
        self.assertIsNone(facts["regular_price"])
        self.assertFalse(facts["on_sale"])

    def test_extract_price_stops_on_block_and_wrong_store(self):
        with self.assertRaises(service.CollectionBlocked):
            service.extract_price_facts(
                FakePage("Verify Your Identity — real shoppers, not robots"),
                "MISSISSAUGA (SQUARE ONE), ON",
            )
        with self.assertRaises(service.WrongStore):
            service.extract_price_facts(
                FakePage("Current price is $4.97 Pickup at TORONTO, ON"),
                "MISSISSAUGA (SQUARE ONE), ON",
            )

    def test_extract_price_parses_cents_and_requires_regular_price(self):
        cents = service.extract_price_facts(
            FakePage(
                "Current price is 94¢ Rollback "
                "Pickup at MISSISSAUGA (SQUARE ONE), ON"
            ),
            "MISSISSAUGA (SQUARE ONE), ON",
        )
        deal = service.extract_price_facts(
            FakePage(
                "Now $3 97 Was $5.47 "
                "Pickup at MISSISSAUGA (SQUARE ONE), ON"
            ),
            "MISSISSAUGA (SQUARE ONE), ON",
        )

        self.assertEqual(cents["current_price"], 0.94)
        self.assertFalse(cents["on_sale"])
        self.assertEqual(deal["current_price"], 3.97)
        self.assertEqual(deal["regular_price"], 5.47)
        self.assertTrue(deal["on_sale"])

    def test_extract_my_items_uses_one_page_and_only_was_rows(self):
        facts = service.extract_my_items_facts(
            FakePage(
                "MISSISSAUGA (SQUARE ONE), ON My Items",
                url=service.MY_ITEMS_URL,
                anchors=[
                    "Great Value Eggs $3.97",
                    "Grace 100% Pure Coconut Water, 1L Bottle "
                    "$3.27 Was $4.98 33¢/100ml",
                    "Smucker's Apricot Spread 500ml "
                    "$5.77 Was $6.47 $1.15/100ml",
                ],
            ),
            "MISSISSAUGA (SQUARE ONE), ON",
        )

        self.assertEqual(facts["item_count"], 3)
        self.assertEqual(len(facts["deals"]), 2)
        self.assertEqual(
            facts["deals"][0]["source_name"],
            "Grace 100% Pure Coconut Water, 1L Bottle",
        )

    def test_my_items_success_replaces_deals_and_returns_names_only(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "watch.sqlite3"
            service.ingest_watchlist(database, product_payload())
            page = FakePage(
                "MISSISSAUGA (SQUARE ONE), ON My Items",
                url=service.MY_ITEMS_URL,
                anchors=[
                    "Great Value Eggs $3.25 Was $4.25",
                    "Great Value Milk $5.00",
                ],
            )

            result = service.run_my_items_collection(
                database,
                "3055",
                "MISSISSAUGA (SQUARE ONE), ON",
                Path(temp) / "profile",
                route="home_exit",
                proxy="socks5://127.0.0.1:1080",
                fetch_page=lambda *args, **kwargs: page,
                proxy_probe=lambda _proxy: None,
            )
            returned = service.get_deals(database, "3055")

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["checked"], 2)
            self.assertEqual(result["deals"], 1)
            self.assertEqual(
                returned["deals"][0]["source_name"],
                "Great Value Eggs",
            )
            self.assertEqual(returned["route"], "home_exit")
            self.assertEqual(returned["collector_status"], "completed")
            self.assertNotIn("url", returned["deals"][0])

    def test_store_selector_matches_radio_label_not_a_button(self):
        page = FakeStorePage(
            [
                "Heartland, Mississauga, ON",
                "Mississauga (square One), On\n100 City Centre Drive",
            ]
        )

        selected = service.find_matching_store_radio(
            page,
            "MISSISSAUGA (SQUARE ONE), ON",
        )

        self.assertIs(selected, page.radios.radios[1])

    def test_collection_persists_valid_check_and_returns_it(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "watch.sqlite3"
            service.ingest_watchlist(database, product_payload())

            result = service.run_collection(
                database,
                "3055",
                "MISSISSAUGA (SQUARE ONE), ON",
                "A1A 1A1",
                Path(temp) / "profile",
                delay_seconds=0,
                fetch_page=lambda *args, **kwargs: FakePage(
                    """
                    Current price is $3.25
                    Was price $4.25
                    Pickup at MISSISSAUGA (SQUARE ONE), ON
                    """
                ),
            )
            with closing(sqlite3.connect(database)) as conn:
                persisted = conn.execute(
                    """
                    SELECT current_price, on_sale
                    FROM price_checks
                    WHERE store_id = '3055'
                    """
                ).fetchone()

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["checked"], 1)
            self.assertEqual(persisted[0], 3.25)
            self.assertEqual(persisted[1], 1)

    def test_home_exit_never_falls_back_when_tunnel_is_offline(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "watch.sqlite3"
            service.ingest_watchlist(database, product_payload())
            fetched = []

            def offline(_proxy):
                raise service.HomeExitOffline("home tunnel unavailable")

            result = service.run_collection(
                database,
                "3055",
                "MISSISSAUGA (SQUARE ONE), ON",
                "A1A 1A1",
                Path(temp) / "profile",
                route="home_exit",
                proxy="socks5://127.0.0.1:1080",
                delay_seconds=0,
                fetch_page=lambda *args, **kwargs: fetched.append(args),
                proxy_probe=offline,
            )

            self.assertEqual(result["status"], "home_exit_offline")
            self.assertEqual(result["checked"], 0)
            self.assertEqual(fetched, [])

    def test_blocked_cycle_resumes_only_remaining_products(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "watch.sqlite3"
            service.ingest_watchlist(database, product_payload())
            service.ingest_watchlist(
                database,
                product_payload("6000207000102", "Great Value Milk"),
            )
            calls = []

            def block_second(url, **_kwargs):
                calls.append(url)
                if len(calls) == 2:
                    return FakePage("Verify Your Identity")
                return FakePage(
                    "Current price is $3.25 Was $4.25 "
                    "Pickup at MISSISSAUGA (SQUARE ONE), ON"
                )

            first = service.run_collection(
                database,
                "3055",
                "MISSISSAUGA (SQUARE ONE), ON",
                "A1A 1A1",
                Path(temp) / "profile",
                delay_seconds=0,
                fetch_page=block_second,
            )
            calls.clear()
            second = service.run_collection(
                database,
                "3055",
                "MISSISSAUGA (SQUARE ONE), ON",
                "A1A 1A1",
                Path(temp) / "profile",
                delay_seconds=0,
                force=True,
                fetch_page=lambda url, **_kwargs: (
                    calls.append(url)
                    or FakePage(
                        "Current price is $3.25 Was $4.25 "
                        "Pickup at MISSISSAUGA (SQUARE ONE), ON"
                    )
                ),
            )

            self.assertEqual(first["status"], "blocked")
            self.assertEqual(first["checked"], 1)
            self.assertEqual(first["remaining"], 1)
            self.assertTrue(second["resumed"])
            self.assertEqual(second["status"], "completed")
            self.assertEqual(second["checked"], 1)
            self.assertEqual(second["remaining"], 0)
            self.assertEqual(len(calls), 1)

    def test_manual_request_accepts_only_loopback_home_proxy(self):
        payload = {
            "contract": service.CONTRACT,
            "store_id": "3055",
            "store_name": "MISSISSAUGA (SQUARE ONE), ON",
            "store_query": "A1A 1A1",
            "proxy": "socks5://example.com:1080",
            "limit": 20,
            "delay_seconds": 8,
        }

        with self.assertRaises(service.ServiceError):
            service.run_request(Path("unused.sqlite3"), payload)


if __name__ == "__main__":
    unittest.main()
