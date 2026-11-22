import argparse
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import vps_service


APP_HOME = Path(__file__).resolve().parent
STATE_DIR = APP_HOME / "state"
STATUS_PATH = STATE_DIR / "schedule-status.json"
TRIGGER_PATH = STATE_DIR / "manual-trigger.json"
LOCK_PATH = STATE_DIR / "schedule.lock"
TORONTO = ZoneInfo("America/Toronto")


class ScheduleError(RuntimeError):
    pass


def toronto_now():
    return datetime.now(TORONTO)


@contextmanager
def exclusive_lock(path=LOCK_PATH):
    try:
        import fcntl
    except ImportError as exc:
        raise ScheduleError("The VPS scheduler requires a POSIX file lock.") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def base_status():
    return {
        "contract": vps_service.CONTRACT,
        "timezone": "America/Toronto",
        "mode": "manual_only",
        "strategy": "my_items",
    }


def write_status(**values):
    payload = vps_service.read_json(STATUS_PATH, default={}) or {}
    if not isinstance(payload, dict):
        payload = {}
    payload.update(base_status())
    payload.update(values)
    vps_service.atomic_write_json(STATUS_PATH, payload)
    return payload


def initialize_status():
    existing = vps_service.read_json(STATUS_PATH, default={}) or {}
    if not isinstance(existing, dict):
        existing = {}
    if existing.get("strategy") != "my_items":
        existing = {
            "status": "idle",
            "message": "Manual My Items collection is ready.",
        }
    for stale_key in (
        "cadence_days",
        "scheduled_local_time",
        "next_due_at",
        "last_attempt_local_date",
    ):
        existing.pop(stale_key, None)
    existing.update(base_status())
    existing.setdefault("status", "idle")
    vps_service.atomic_write_json(STATUS_PATH, existing)
    return existing


def defer_after_block():
    remaining = 0
    try:
        with vps_service.connect_database(
            vps_service.DEFAULT_DATABASE
        ) as connection:
            progress = connection.execute(
                """
                SELECT store_id, cycle_started_at
                FROM collection_progress
                WHERE route = 'direct'
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
            if progress:
                remaining = vps_service.remaining_cycle_count(
                    connection,
                    progress["store_id"],
                    progress["cycle_started_at"],
                )
    except sqlite3.Error:
        remaining = 0
    return write_status(
        status="attention",
        last_attempt_at=datetime.now(timezone.utc).replace(
            microsecond=0
        ).isoformat(),
        last_result_status="blocked",
        checked=0,
        remaining=remaining,
        deals=0,
        message="Walmart verification detected; the next attempt was deferred.",
    )


def load_trigger():
    payload = vps_service.read_json(TRIGGER_PATH, default=None)
    if not isinstance(payload, dict):
        return None
    if payload.get("contract") != vps_service.CONTRACT:
        raise ScheduleError("The manual trigger contract is invalid.")
    return payload


def validate_settings(payload):
    store_id = vps_service.validate_text(
        payload.get("store_id"), "store_id", 64, required=True
    )
    if not vps_service.STORE_ID_PATTERN.fullmatch(store_id):
        raise ScheduleError("The scheduled store_id is invalid.")
    delay_seconds = payload.get("delay_seconds", 75)
    if (
        isinstance(delay_seconds, bool)
        or not isinstance(delay_seconds, int)
        or not 30 <= delay_seconds <= 120
    ):
        raise ScheduleError("The scheduled delay must be from 30 to 120 seconds.")
    return {
        "store_id": store_id,
        "store_name": vps_service.validate_text(
            payload.get("store_name"), "store_name", 200, required=True
        ),
        "store_query": vps_service.validate_text(
            payload.get("store_query"), "store_query", 100, required=True
        ),
        "delay_seconds": delay_seconds,
    }


def run_collection(settings, source):
    now = toronto_now()
    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    write_status(
        status="running",
        source=source,
        last_attempt_at=started_at,
    )
    result = vps_service.run_my_items_collection(
        vps_service.DEFAULT_DATABASE,
        settings["store_id"],
        settings["store_name"],
        vps_service.DEFAULT_AUTH_PROFILE,
        route="direct",
        cooldown_seconds=21600,
    )
    snapshot = vps_service.get_deals(
        vps_service.DEFAULT_DATABASE,
        settings["store_id"],
    )
    return write_status(
        status="completed" if result["status"] == "completed" else "attention",
        last_result_status=result["status"],
        checked=int(result.get("checked", 0) or 0),
        remaining=0,
        deals=len(snapshot.get("deals", [])),
        completed_at=result.get("completed_at", vps_service.utc_now()),
    )


def process(mode, settings=None):
    with exclusive_lock() as acquired:
        if not acquired:
            return vps_service.read_json(STATUS_PATH, default=base_status())
        now = toronto_now()
        if mode == "scheduled":
            return write_status(
                status="disabled",
                message="Periodic price checks are disabled; use a manual request.",
            )
        elif mode == "trigger":
            trigger = load_trigger()
            if trigger is None:
                return vps_service.read_json(STATUS_PATH, default=base_status())
            settings = validate_settings(trigger)
            source = "manual"
            TRIGGER_PATH.unlink(missing_ok=True)
        else:
            raise ScheduleError("Unknown scheduler mode.")
        return run_collection(validate_settings(settings), source)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Process manual VPS Walmart price-watch requests."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    scheduled = subparsers.add_parser("scheduled")
    scheduled.add_argument("--store-id", required=True)
    scheduled.add_argument("--store-name", required=True)
    scheduled.add_argument("--store-query", required=True)
    scheduled.add_argument("--delay-seconds", type=int, default=75)
    subparsers.add_parser("trigger")
    subparsers.add_parser("initialize")
    subparsers.add_parser("defer-blocked")
    subparsers.add_parser("status")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.command == "status":
            result = vps_service.read_json(STATUS_PATH, default=base_status())
        elif args.command == "initialize":
            result = initialize_status()
        elif args.command == "defer-blocked":
            result = defer_after_block()
        elif args.command == "trigger":
            result = process("trigger")
        else:
            result = process(
                "scheduled",
                {
                    "store_id": args.store_id,
                    "store_name": args.store_name,
                    "store_query": args.store_query,
                    "delay_seconds": args.delay_seconds,
                },
            )
    except (ScheduleError, vps_service.ServiceError) as exc:
        result = write_status(
            status="failed",
            last_result_status="failed",
            message=str(exc)[:300],
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
