"""Bounded local collection status for the native maintenance page."""
import json
import math
from datetime import datetime, timedelta, timezone

PLATFORMS = {"walmart_ca", "uber_eats", "fantuan", "amazon_ca", "the_ordinary_ca", "tnt_ca"}
STATES = {"running", "passed", "partial", "failed", "login_required", "blocked", "cooldown", "skipped"}

def read_object(path):
    try:
        if path.stat().st_size > 128 * 1024:
            return {}
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def seconds(value):
    return round(float(value), 1) if isinstance(value, (float,int)) and not isinstance(value,bool) and math.isfinite(value) and 0 <= value <= 7200 else None

def timestamp(value):
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def state_name(value):
    return value if isinstance(value, str) and value in STATES else "unknown"


def local_components(root, state):
    started, finished = timestamp(state.get("started_at")), timestamp(state.get("completed_at"))
    if finished is None and state.get("status") == "running":
        finished = datetime.now(timezone.utc)
    requested = state.get("collector_platforms", [])
    if not started or not finished or not isinstance(requested, list):
        return []
    result = []
    for platform in sorted(PLATFORMS.intersection(x for x in requested if isinstance(x, str))):
        row = read_object(root / ".codex/collector-runs" / f"{platform}-collect.json")
        row_start, row_end = timestamp(row.get("started_at")), timestamp(row.get("completed_at"))
        context = row.get("context", {})
        # Local runner timestamps have second precision. Reject old and VPS records.
        if not row_start or not row_end or row_end < row_start or row_start < started - timedelta(seconds=1) or row_end > finished + timedelta(seconds=1):
            continue
        if not isinstance(context, dict) or context.get("backend") == "vps" or row.get("phase") != "collect" or row.get("collector_id") != platform:
            continue
        result.append({"id": platform, "status": state_name(row.get("status")), "duration_seconds": seconds(row.get("duration_seconds")), "cached": row.get("data_freshness") == "cache_preserved"})
    return result


# A refresh that only imports or reconciles does not collect; it still rewrites
# run-state.json. The maintenance page then falls back to the newest local
# collection session instead of showing no platform results at all.
SESSION_WINDOW = timedelta(minutes=30)
STATE_RANK = {"passed": 0, "skipped": 0, "cooldown": 1, "partial": 1, "running": 1, "unknown": 1,
              "login_required": 2, "blocked": 2, "failed": 2}


def latest_local_session(root):
    rows = []
    for platform in sorted(PLATFORMS):
        row = read_object(root / ".codex/collector-runs" / f"{platform}-collect.json")
        start, end = timestamp(row.get("started_at")), timestamp(row.get("completed_at"))
        context = row.get("context", {})
        if not start or not end or end < start or row.get("phase") != "collect" or row.get("collector_id") != platform:
            continue
        if isinstance(context, dict) and context.get("backend") == "vps":
            continue
        rows.append((platform, row, start, end))
    if not rows:
        return None
    latest = max(end for _, _, _, end in rows)
    session = [item for item in rows if item[3] >= latest - SESSION_WINDOW]
    first = min(start for _, _, start, _ in session)
    states = [state_name(row.get("status")) for _, row, _, _ in session]
    worst = max(states, key=lambda value: STATE_RANK.get(value, 1))
    status = "passed" if STATE_RANK.get(worst, 1) == 0 else "partial" if any(STATE_RANK.get(x, 1) == 0 for x in states) else worst
    return {"status": status, "backend": "本地", "duration_seconds": seconds((latest - first).total_seconds()),
            "components": [{"id": platform, "status": state_name(row.get("status")), "duration_seconds": seconds(row.get("duration_seconds")),
                            "cached": row.get("data_freshness") == "cache_preserved"} for platform, row, _, _ in session],
            "timings": [], "started_at": first.isoformat(), "source": "latest_collection"}


def collection_summary(root):
    state = read_object(root / "exports/current/run-state.json")
    if not state:
        return latest_local_session(root)
    if state.get("collector_backend_resolved") not in ("local", "vps") and state.get("status") != "running":
        fallback = latest_local_session(root)
        if fallback:
            return fallback
    result = {"status": state_name(state.get("status")),
              "backend": "VPS" if state.get("collector_backend_resolved") == "vps" else "本地",
              "duration_seconds": seconds(state.get("duration_seconds")), "components": [], "timings": [],
              "started_at": timestamp(state.get("started_at")).isoformat() if timestamp(state.get("started_at")) else None}
    if state.get("collector_backend_resolved") == "local":
        result["components"] = local_components(root, state)
        return result
    bundle = read_object(root / ".codex/collector-runs/vps-bundle.json")
    if state.get("collector_backend_resolved") != "vps" or not state.get("collection_run_id") or state.get("collection_run_id") != bundle.get("collection_run_id"):
        return result
    requested = bundle.get("attempt_requested_platforms", [])
    if not isinstance(requested,list):
        return result
    rows = bundle.get("components", [])
    for row in rows[:10] if isinstance(rows,list) else []:
        if not isinstance(row,dict):
            continue
        key = row.get("id", "")
        if not isinstance(key,str):
            continue
        platform = key.removesuffix("_payment")
        if platform not in PLATFORMS or platform not in requested:
            continue
        result["components"].append({"id":key, "status":state_name(row.get("status")), "duration_seconds":seconds(row.get("duration_seconds")), "cached":row.get("data_freshness") == "cache_preserved"})
    timings = bundle.get("timings",{})
    for group, key, label in (("client","connection_seconds","连接与准备"),("remote","session_start_seconds","浏览器启动与切换"),("remote","components_seconds","订单与付款采集"),("remote","final_cleanup_seconds","浏览器退出"),("remote","bundle_seconds","生成采集包"),("client","validation_apply_seconds","接收校验与保存")):
        values = timings.get(group,{}) if isinstance(timings,dict) else {}
        value = seconds(values.get(key)) if isinstance(values,dict) else None
        if value is not None:
            result["timings"].append({"label":label,"seconds":value})
    return result
