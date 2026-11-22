import json
import re
from datetime import datetime
from pathlib import Path


PLATFORM_COMPONENTS = {
    "walmart_ca": ("walmart_ca", "walmart_ca_payment"),
    "amazon_ca": ("amazon_ca",),
    "uber_eats": ("uber_eats", "uber_eats_payment"),
    "the_ordinary_ca": ("the_ordinary_ca",),
    "fantuan": ("fantuan", "fantuan_payment"),
    "tnt_ca": ("tnt_ca",),
}
PLATFORM_LABELS = {
    "walmart_ca": "Walmart",
    "amazon_ca": "Amazon",
    "uber_eats": "Uber Eats",
    "the_ordinary_ca": "The Ordinary",
    "fantuan": "Fantuan",
    "tnt_ca": "大统华",
}
KNOWN_STATUSES = {
    "passed",
    "failed",
    "login_required",
    "blocked",
    "cooldown",
    "skipped",
}
PRIORITY = {
    "login_required": 5,
    "blocked": 4,
    "cooldown": 3,
    "failed": 2,
    "skipped": 1,
    "passed": 0,
}
SUPPORTED_CONTRACTS = {
    "expense-collector-bundle-status/v1",
    "expense-collector-bundle-status/v2",
}
RECOVERY_BACKLOG_CONTRACT = "expense-collector-recovery-backlog/v1"
DATA_FRESHNESS = {"live", "cache_preserved", "none"}
RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _read_json(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _time(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _expected_components(platforms):
    return (
        *(PLATFORM_COMPONENTS[platform][0] for platform in platforms),
        *(
            component
            for platform in platforms
            for component in PLATFORM_COMPONENTS[platform][1:]
        ),
    )


def _validated_bundle(payload):
    if payload.get("contract") not in SUPPORTED_CONTRACTS:
        return None
    requested = payload.get("requested_platforms")
    if requested is None and payload.get("contract").endswith("/v1"):
        requested = list(PLATFORM_COMPONENTS)
    if (
        not isinstance(requested, list)
        or not requested
        or len(requested) != len(set(requested))
        or any(platform not in PLATFORM_COMPONENTS for platform in requested)
    ):
        return None
    normalized_requested = [
        platform for platform in PLATFORM_COMPONENTS if platform in requested
    ]
    if requested != normalized_requested:
        return None
    requested = normalized_requested
    expected = _expected_components(requested)
    if (
        payload.get("contract").endswith("/v2")
        and payload.get("expected_components") != list(expected)
    ):
        return None
    raw_components = payload.get("components")
    if not isinstance(raw_components, list) or len(raw_components) != len(expected):
        return None
    components = {}
    for raw in raw_components:
        if not isinstance(raw, dict):
            return None
        component = str(raw.get("id") or "")
        status = str(raw.get("status") or "")
        freshness = str(raw.get("data_freshness") or "none")
        if (
            component not in expected
            or component in components
            or status not in KNOWN_STATUSES
            or freshness not in DATA_FRESHNESS
            or (status == "passed" and freshness != "live")
            or (status != "passed" and freshness == "live")
        ):
            return None
        components[component] = {
            "status": status,
            "message": str(raw.get("message") or "")[:300],
            "data_freshness": freshness,
        }
    if set(components) != set(expected):
        return None
    all_passed = all(row["status"] == "passed" for row in components.values())
    if (payload.get("status") == "passed") != all_passed:
        return None
    if payload.get("status") not in {"partial", "passed"}:
        return None
    return requested, components


def _validated_backlog(payload):
    if payload.get("contract") != RECOVERY_BACKLOG_CONTRACT:
        return None
    if _time(payload.get("generated_at")) is None:
        return None
    raw_platforms = payload.get("platforms")
    if not isinstance(raw_platforms, list) or len(raw_platforms) > len(PLATFORM_COMPONENTS):
        return None
    requested = []
    components = {}
    for raw in raw_platforms:
        if not isinstance(raw, dict):
            return None
        platform = str(raw.get("id") or "")
        if platform not in PLATFORM_COMPONENTS or platform in requested:
            return None
        if RUN_ID_PATTERN.fullmatch(str(raw.get("collection_run_id") or "")) is None:
            return None
        if _time(raw.get("generated_at")) is None:
            return None
        expected = PLATFORM_COMPONENTS[platform]
        raw_components = raw.get("components")
        if not isinstance(raw_components, list) or len(raw_components) != len(expected):
            return None
        platform_components = {}
        for component in raw_components:
            if not isinstance(component, dict):
                return None
            component_id = str(component.get("id") or "")
            status = str(component.get("status") or "")
            freshness = str(component.get("data_freshness") or "none")
            if (
                component_id not in expected
                or component_id in platform_components
                or status not in KNOWN_STATUSES
                or freshness not in DATA_FRESHNESS
                or (status == "passed" and freshness != "live")
                or (status != "passed" and freshness == "live")
            ):
                return None
            platform_components[component_id] = {
                "status": status,
                "message": str(component.get("message") or "")[:300],
                "data_freshness": freshness,
            }
        if set(platform_components) != set(expected):
            return None
        if all(row["status"] == "passed" for row in platform_components.values()):
            return None
        requested.append(platform)
        components.update(platform_components)
    if requested != [platform for platform in PLATFORM_COMPONENTS if platform in requested]:
        return None
    expected_status = "partial" if requested else "passed"
    if payload.get("status") != expected_status:
        return None
    return requested, components


def collector_recovery_status(root):
    root = Path(root)
    bundle = _read_json(root / ".codex" / "collector-runs" / "vps-bundle.json")
    backlog = _read_json(root / ".codex" / "collector-runs" / "vps-recovery.json")
    run_state = _read_json(root / "exports" / "current" / "run-state.json")
    weekly = _read_json(root / "exports" / "current" / "weekly-status.json")
    normalized = _validated_bundle(bundle)
    backlog_normalized = _validated_backlog(backlog)
    generated = _time(bundle.get("generated_at"))
    completed = _time(run_state.get("completed_at"))
    bundle_run_id = str(bundle.get("collection_run_id") or "")
    state_run_id = str(run_state.get("collection_run_id") or "")
    weekly_run_id = str(weekly.get("collection_run_id") or "")
    same_run = (
        generated is not None
        and completed is not None
        and abs((completed - generated).total_seconds()) <= 900
        and RUN_ID_PATTERN.fullmatch(bundle_run_id) is not None
        and bundle_run_id == state_run_id == weekly_run_id
        and run_state.get("collect_from_browser") is True
        and weekly.get("collect_from_browser") is True
        and run_state.get("collector_backend_resolved") == "vps"
        and run_state.get("status") in {"partial", "passed"}
    )
    using_backlog = backlog_normalized is not None and bool(backlog_normalized[0])
    if not using_backlog and (not same_run or normalized is None):
        return {
            "status": "unavailable",
            "generated_at": "",
            "platforms": [],
            "message": "最近一次运行没有可用于恢复的 VPS 采集结果。",
        }

    if using_backlog:
        requested, components = backlog_normalized
        status_source = backlog
        validation = "passed"
    else:
        requested, components = normalized
        status_source = bundle
        validation = str(weekly.get("validation") or "unknown")[:40]
    backend_config = _read_json(root / "config" / "collector_backends.json")
    configured_local_only = backend_config.get("local_only_platforms", [])
    local_only = {
        platform for platform in configured_local_only
        if platform in PLATFORM_COMPONENTS
    } if isinstance(configured_local_only, list) else set()
    requested = [platform for platform in requested if platform not in local_only]
    if not requested:
        return {
            "status": "unavailable",
            "generated_at": "",
            "platforms": [],
            "message": "最近一次结果只包含当前固定在本地采集的平台。",
        }
    # A disabled VPS cannot be logged into or retried, so its old failures are not actionable.
    vps_config = backend_config.get("vps")
    if isinstance(vps_config, dict) and vps_config.get("enabled") is False:
        return {
            "status": "unavailable",
            "generated_at": "",
            "platforms": [],
            "message": "VPS 采集已停用；当前在本地采集，没有需要恢复的 VPS 平台。",
        }
    # A later successful collection of the same platform (for example a local run)
    # supersedes an older VPS failure.
    failed_at = {platform: _time(status_source.get("generated_at")) for platform in requested}
    if using_backlog:
        for raw in backlog.get("platforms", []):
            if isinstance(raw, dict) and raw.get("id") in failed_at:
                failed_at[raw["id"]] = _time(raw.get("generated_at"))
    superseded = set()
    for platform in requested:
        latest = _read_json(root / ".codex" / "collector-runs" / f"{platform}-collect.json")
        completed_at = _time(latest.get("completed_at"))
        if (latest.get("status") == "passed" and completed_at is not None
                and failed_at[platform] is not None and completed_at > failed_at[platform]):
            superseded.add(platform)
    requested = [platform for platform in requested if platform not in superseded]
    if not requested:
        return {
            "status": "unavailable",
            "generated_at": "",
            "platforms": [],
            "message": "之前失败的平台之后都已采集成功，没有需要恢复的平台。",
        }

    rows = []
    for platform in requested:
        required = PLATFORM_COMPONENTS[platform]
        values = [
            components.get(component, {
                "status": "failed",
                "message": "本次组件结果缺失。",
                "data_freshness": "none",
            })
            for component in required
        ]
        primary = components.get(platform, values[0])
        payment_login_required = any(
            component != platform
            and components.get(component, {}).get("status") == "login_required"
            for component in required
        )
        if primary["status"] == "login_required":
            state = "login_required"
        elif payment_login_required:
            state = "blocked"
        else:
            state = max(values, key=lambda row: PRIORITY[row["status"]])["status"]
        if state == "skipped":
            state = "failed"
        messages = []
        for row in values:
            message = row["message"].strip()
            if row["status"] != "passed" and message and message not in messages:
                messages.append(message)
        rows.append({
            "id": platform,
            "label": PLATFORM_LABELS[platform],
            "status": state,
            "retryable": state == "failed",
            "login_required": state == "login_required",
            "manual_diagnosis": payment_login_required,
            "using_cached_data": any(
                row["data_freshness"] == "cache_preserved" for row in values
            ),
            "message": "；".join(messages)[:500],
        })

    failed = [row for row in rows if row["status"] != "passed"]
    return {
        "status": "partial" if failed else "passed",
        "generated_at": str(status_source.get("generated_at") or "")[:80],
        "validation": validation,
        "platforms": rows,
        "message": (
            f"{len(failed)} 个平台需要处理。"
            if failed else "最近一次 VPS 采集全部完成。"
        ),
    }
