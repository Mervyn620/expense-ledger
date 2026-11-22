#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from scrapling.fetchers import DynamicSession

import vps_service


def compact(value, limit=180):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def store_controls(page):
    rows = []
    buttons = page.locator("button")
    for index in range(min(buttons.count(), 160)):
        node = buttons.nth(index)
        aria = compact(node.get_attribute("aria-label"))
        text = compact(node.inner_text())
        joined = f"{aria} {text}".lower()
        if any(
            token in joined
            for token in (
                "store",
                "square one",
                "mississauga",
                "pickup",
                "location",
            )
        ):
            rows.append({"aria": aria, "text": text})
            if len(rows) >= 20:
                break
    return rows


def store_dialog_state(page):
    inputs = []
    fields = page.locator("input")
    for index in range(min(fields.count(), 30)):
        node = fields.nth(index)
        inputs.append(
            {
                "name": compact(node.get_attribute("name"), 80),
                "test_id": compact(
                    node.get_attribute("data-testid"),
                    80,
                ),
                "aria": compact(node.get_attribute("aria-label"), 120),
                "placeholder": compact(
                    node.get_attribute("placeholder"),
                    120,
                ),
                "type": compact(node.get_attribute("type"), 30),
            }
        )
    actions = []
    buttons = page.locator("button")
    for index in range(min(buttons.count(), 160)):
        node = buttons.nth(index)
        aria = compact(node.get_attribute("aria-label"))
        text = compact(node.inner_text())
        joined = f"{aria} {text}".lower()
        if any(
            token in joined
            for token in (
                "search",
                "apply",
                "select",
                "store",
                "square one",
                "mississauga",
                "location",
                "postal",
                "zip",
                "save",
                "close",
                "cancel",
            )
        ):
            actions.append({"aria": aria, "text": text})
            if len(actions) >= 30:
                break
    return {"inputs": inputs, "actions": actions}


def validate_url(value):
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.walmart.ca"
        or "/ip/" not in parsed.path
    ):
        raise ValueError("URL must be a Walmart Canada product page.")
    return value


def run(
    url,
    expected_store,
    store_query,
    profile,
    proxy,
    inspect_dialog=False,
):
    result = {
        "contract": "expense-price-watch-store-diagnostic/v1",
        "expected_store": expected_store,
    }

    def inspect_page(page):
        page.wait_for_timeout(1_500)
        body = re.sub(r"\s+", " ", page.locator("body").inner_text()).upper()
        result["before_match"] = expected_store.upper() in body
        result["before_controls"] = store_controls(page)
        if inspect_dialog and not result["before_match"]:
            result["overlay_count"] = page.locator(
                '[class*="OverlayScrim_scrim"]'
            ).count()
            result["current_dialog"] = store_dialog_state(page)
            if result["overlay_count"]:
                close_dialog = page.locator(
                    'button[aria-label="Close dialogue"]'
                ).last
                result["close_dialog_visible"] = (
                    close_dialog.is_visible()
                )
                if result["close_dialog_visible"]:
                    close_dialog.click()
                    page.wait_for_timeout(1_000)
                result["overlay_count_after_close"] = page.locator(
                    '[class*="OverlayScrim_scrim"]'
                ).count()
                if result["overlay_count_after_close"]:
                    return
            change_store = page.locator(
                'button[aria-label$=", change store"]'
            ).first
            result["change_store_visible"] = change_store.is_visible()
            if result["change_store_visible"]:
                change_store.click()
                page.wait_for_timeout(1_500)
                result["dialog"] = store_dialog_state(page)
            return
        try:
            vps_service.ensure_store_selection(
                page,
                expected_store,
                store_query,
            )
            result["selection_error"] = ""
        except Exception as exc:
            result["selection_error"] = type(exc).__name__
        body = re.sub(r"\s+", " ", page.locator("body").inner_text()).upper()
        result["after_match"] = expected_store.upper() in body
        result["after_controls"] = store_controls(page)

    session_options = dict(
        headless=True,
        user_data_dir=str(Path(profile).resolve()),
        locale="en-CA",
        timezone_id="America/Toronto",
        max_pages=1,
        timeout=45_000,
        wait=1_500,
        network_idle=False,
        extra_flags=["--disable-dev-shm-usage"],
    )
    if proxy:
        session_options["proxy"] = proxy
    session = DynamicSession(**session_options)
    try:
        session.start()
        response = session.fetch(
            validate_url(url),
            timeout=45_000,
            wait=1_500,
            network_idle=False,
            page_action=inspect_page,
        )
        response_text = re.sub(
            r"\s+",
            " ",
            str(response.get_all_text(separator="\n", strip=True)),
        ).upper()
        result["response_match"] = expected_store.upper() in response_text
        result["response_status"] = int(
            getattr(response, "status", 0) or 0
        )
        result["blocked"] = (
            result["response_status"] in {403, 429, 456}
            or any(
                pattern in response_text.lower()
                for pattern in vps_service.BLOCK_PATTERNS
            )
        )
        return result
    finally:
        session.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Inspect only sanitized Walmart store-selection controls."
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--expected-store", required=True)
    parser.add_argument("--store-query", required=True)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument(
        "--proxy",
        default="socks5://127.0.0.1:1080",
    )
    parser.add_argument("--direct", action="store_true")
    parser.add_argument("--inspect-dialog", action="store_true")
    args = parser.parse_args(argv)
    result = run(
        args.url,
        args.expected_store,
        args.store_query,
        args.profile,
        "" if args.direct else args.proxy,
        inspect_dialog=args.inspect_dialog,
    )
    json.dump(result, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
