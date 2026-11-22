import argparse
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
import vps_login_session

try:
    import fcntl
except ImportError:  # pragma: no cover - VPS-only locking on Linux.
    fcntl = None


CONTRACT = "expense-collector-vps/v1"
REQUEST_CONTRACT = "expense-collector-bundle-request/v1"
BUNDLE_CONTRACT = "expense-collector-bundle/v1"
REQUEST_CONTRACT_V2 = "expense-collector-bundle-request/v2"
BUNDLE_CONTRACT_V2 = "expense-collector-bundle/v2"
TLS_DIAGNOSTICS_CONTRACT = "expense-collector-tls-diagnostics/v1"
PLATFORMS = (
    "walmart_ca",
    "amazon_ca",
    "uber_eats",
    "the_ordinary_ca",
    "fantuan",
)
BASE_DIR = Path("/home/ledger/apps/expense-collector")
APP_CODE_ROOT = Path(__file__).resolve().parent / "app"
APP_ROOT = BASE_DIR / "workspace"
PROFILE_ROOT = BASE_DIR / "profiles"
NODE = BASE_DIR / "runtime" / "node" / "bin" / "node"
PYTHON = Path("/usr/bin/python3")
MAX_REQUEST_BYTES = 6 * 1024 * 1024
MAX_SEED_FILE_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 25 * 1024 * 1024
COLLECTION_BUDGET_SECONDS = 540
PRIMARY_COMPONENT_TIMEOUT_SECONDS = 150
PAYMENT_COMPONENT_TIMEOUT_SECONDS = 90
STORAGE_WARNING_PERCENT = 80.0
STORAGE_CRITICAL_PERCENT = 90.0
STORAGE_CRITICAL_FREE_BYTES = 1024 * 1024 * 1024
DATE_PATTERN = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
COLLECTION_RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
COLLECTION_LOCK_PATH = BASE_DIR / "runtime" / "collection.lock"
COLLECTION_PROGRESS_PATH = BASE_DIR / "runtime" / "collection-progress.json"
EXPECTED_COMPONENTS = (
    *PLATFORMS,
    "walmart_ca_payment",
    "uber_eats_payment",
    "fantuan_payment",
)
COMPONENT_STATUSES = {
    "passed",
    "failed",
    "login_required",
    "blocked",
    "cooldown",
    "skipped",
}
SEED_PATHS = {
    "data/transactions.csv",
    "data/walmart_items.csv",
    "data/amazon_orders.json",
    "data/the_ordinary_orders.json",
    "data/payment_evidence.json",
    "data/fantuan_orders.json",
    "data/uber_eats_receipts.json",
    ".codex/walmart-product-link-attempts.json",
}
DATA_OUTPUTS = (
    "data/amazon_orders.json",
    "data/the_ordinary_orders.json",
    "data/payment_evidence.json",
    "data/fantuan_orders.json",
    "data/uber_eats_receipts.json",
)
COMPONENT_CACHE_PATHS = {
    "walmart_ca": ("data/transactions.csv", "data/walmart_items.csv"),
    "amazon_ca": ("data/amazon_orders.json", "data/payment_evidence.json"),
    "uber_eats": ("data/transactions.csv",),
    "the_ordinary_ca": (
        "data/the_ordinary_orders.json",
        "data/payment_evidence.json",
    ),
    "fantuan": ("data/fantuan_orders.json",),
    "walmart_ca_payment": ("data/payment_evidence.json",),
    "uber_eats_payment": ("data/uber_eats_receipts.json",),
    "fantuan_payment": ("data/payment_evidence.json",),
}
PAYMENT_COMPONENTS = {
    "walmart_ca": "walmart_ca_payment",
    "uber_eats": "uber_eats_payment",
    "fantuan": "fantuan_payment",
}
PLATFORM_SEED_PATHS = {
    "walmart_ca": {
        "data/transactions.csv",
        "data/walmart_items.csv",
        "data/payment_evidence.json",
        ".codex/walmart-product-link-attempts.json",
    },
    "amazon_ca": {"data/amazon_orders.json", "data/payment_evidence.json"},
    "uber_eats": {"data/transactions.csv", "data/uber_eats_receipts.json"},
    "the_ordinary_ca": {
        "data/the_ordinary_orders.json",
        "data/payment_evidence.json",
    },
    "fantuan": {
        "data/transactions.csv",
        "data/fantuan_orders.json",
        "data/payment_evidence.json",
    },
}
PLATFORM_DATA_OUTPUTS = {
    "walmart_ca": {"data/payment_evidence.json"},
    "amazon_ca": {"data/amazon_orders.json", "data/payment_evidence.json"},
    "uber_eats": {"data/uber_eats_receipts.json"},
    "the_ordinary_ca": {
        "data/the_ordinary_orders.json",
        "data/payment_evidence.json",
    },
    "fantuan": {"data/fantuan_orders.json", "data/payment_evidence.json"},
}
REQUIRED_SOURCES = (
    "scripts/collectors/runner.py",
    "scripts/collectors/collect-walmart-orders.mjs",
    "scripts/collectors/walmart-order-utils.mjs",
    "scripts/collectors/csv.mjs",
    "scripts/collectors/export-amazon-orders.mjs",
    "scripts/collectors/amazon-order-utils.mjs",
    "scripts/collectors/export-the-ordinary-orders.mjs",
    "scripts/collectors/the-ordinary-auth.mjs",
    "scripts/collectors/the-ordinary-order-utils.mjs",
    "scripts/collectors/the-ordinary-tab-selection.mjs",
    "scripts/collectors/export-fantuan-orders.mjs",
    "scripts/collectors/fantuan-order-utils.mjs",
    "scripts/collectors/fantuan-navigation.mjs",
    "scripts/collectors/export-uber-eats-receipts.mjs",
    "scripts/collectors/export-payment-evidence.mjs",
    "scripts/collectors/payment-text-utils.mjs",
    "scripts/collectors/tls_diagnostics.py",
    "scripts/browser/cdp-shop-page.mjs",
    "scripts/browser/cdp-client.mjs",
    "scripts/browser/content-ready.mjs",
    "scripts/browser/collection-policy.mjs",
    "scripts/browser/sanitize.mjs",
    "scripts/lib/atomic-file.mjs",
    "scripts/lib/file-lock.mjs",
    "scripts/importers/walmart_cdp.py",
    "scripts/importers/amazon.py",
    "scripts/importers/uber_eats.py",
    "scripts/importers/the_ordinary.py",
    "scripts/importers/fantuan.py",
    "config/collectors.json",
    "config/platforms.json",
    "config/workflow.json",
)


class ServiceError(RuntimeError):
    pass


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def command_version(executable):
    if not Path(executable).is_file():
        return ""
    result = subprocess.run(
        [str(executable), "--version"],
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    return (result.stdout or result.stderr).strip()[:40]


def marker_valid(platform):
    marker = PROFILE_ROOT / platform / ".authenticated.json"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("contract") == "expense-collector-auth-marker/v1"
        and payload.get("platform") == platform
        and payload.get("route") == "home_exit"
    )


def _atomic_directory_link(destination, source):
    destination = Path(destination)
    source = Path(source).resolve(strict=True)
    if not source.is_dir():
        raise ServiceError("Collector release source is not a directory.")
    if destination.is_symlink():
        try:
            if destination.resolve(strict=True) == source:
                return
        except FileNotFoundError:
            pass
    elif destination.exists():
        raise ServiceError("Collector workspace code path is not a symbolic link.")
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    try:
        os.symlink(source, temporary, target_is_directory=True)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_app_workspace():
    expected_workspace = BASE_DIR / "workspace"
    if APP_ROOT != expected_workspace:
        # Unit tests may supply an isolated complete application root.
        return
    if APP_ROOT.is_symlink():
        raise ServiceError("Collector workspace cannot be a symbolic link.")
    APP_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(APP_ROOT, 0o700)
    for name in ("scripts", "config"):
        _atomic_directory_link(APP_ROOT / name, APP_CODE_ROOT / name)
    for name in ("data", "raw", ".codex"):
        path = APP_ROOT / name
        if path.is_symlink():
            raise ServiceError("Collector mutable workspace cannot be a symbolic link.")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)


def source_ready():
    try:
        ensure_app_workspace()
    except (OSError, ServiceError):
        return False
    return all((APP_ROOT / path).is_file() for path in REQUIRED_SOURCES)


def storage_status():
    try:
        usage = shutil.disk_usage(BASE_DIR)
    except OSError:
        return {"status": "unavailable"}
    total = max(0, int(usage.total))
    free = max(0, int(usage.free))
    used_percent = ((total - free) / total * 100.0) if total else 100.0
    if used_percent >= STORAGE_CRITICAL_PERCENT or free < STORAGE_CRITICAL_FREE_BYTES:
        status_value = "critical"
    elif used_percent >= STORAGE_WARNING_PERCENT:
        status_value = "warning"
    else:
        status_value = "ok"
    return {
        "status": status_value,
        "used_percent": round(min(100.0, max(0.0, used_percent)), 1),
        "free_bytes": free,
    }


def read_collection_progress():
    try:
        payload = json.loads(
            COLLECTION_PROGRESS_PATH.read_text(encoding="utf-8-sig")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {
            "status": "idle",
            "stage": "idle",
            "platform": "",
            "components_completed": 0,
            "components_total": len(EXPECTED_COMPONENTS),
        }
    if not isinstance(payload, dict):
        return {
            "status": "idle",
            "stage": "idle",
            "platform": "",
            "components_completed": 0,
            "components_total": len(EXPECTED_COMPONENTS),
        }
    return {
        "status": str(payload.get("status") or "idle"),
        "stage": str(payload.get("stage") or ""),
        "platform": str(payload.get("platform") or ""),
        "started_at": str(payload.get("started_at") or ""),
        "updated_at": str(payload.get("updated_at") or ""),
        "completed_at": str(payload.get("completed_at") or ""),
        "components_completed": int(payload.get("components_completed") or 0),
        "components_total": int(
            payload.get("components_total") or len(EXPECTED_COMPONENTS)
        ),
    }


def status():
    browser = vps_login_session.find_browser()
    browser_version = command_version(browser)
    profiles = {
        platform: {
            "profile_present": (PROFILE_ROOT / platform).is_dir(),
            "configured": marker_valid(platform),
        }
        for platform in PLATFORMS
    }
    runtime = {
        "node": NODE.is_file(),
        "node_version": command_version(NODE),
        "browser": bool(browser),
        "browser_version": browser_version,
        "xvfb": bool(shutil.which("Xvfb")),
        "login_ui": bool(shutil.which("x11vnc") and shutil.which("websockify")),
        "collector_source": source_ready(),
    }
    ready = all(runtime.values()) and all(
        row["configured"] for row in profiles.values()
    )
    return {
        "contract": CONTRACT,
        "status": "ready" if ready else "setup_required",
        "message": (
            "VPS五平台采集运行时与独立登录会话均已就绪"
            if ready
            else "VPS仍缺少采集运行时、部署代码或已验证登录会话"
        ),
        "capabilities": [
            "status",
            "collect_bundle_v1",
            "collect_bundle_v2",
            "tls_diagnostics_v1",
        ] if ready else ["status"],
        "browser_status": "profiles_ready" if ready else "profiles_missing",
        "runtime": runtime,
        "profiles": profiles,
        "collection": read_collection_progress(),
        "storage": storage_status(),
    }


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_collection_progress(
    status_value,
    stage,
    *,
    platform="",
    started_at="",
    components=None,
    components_total=len(EXPECTED_COMPONENTS),
    message="",
):
    rows = list(components or [])
    payload = {
        "contract": "expense-collector-progress/v1",
        "status": status_value,
        "stage": stage,
        "platform": platform,
        "started_at": started_at,
        "updated_at": now_iso(),
        "completed_at": now_iso()
        if status_value in {"passed", "partial", "failed"}
        else "",
        "components_completed": len(rows),
        "components_total": max(0, min(len(EXPECTED_COMPONENTS), int(components_total))),
        "components": [
            {
                "id": str(row.get("id") or ""),
                "status": str(row.get("status") or "failed"),
                "duration_seconds": float(row.get("duration_seconds") or 0),
            }
            for row in rows
            if isinstance(row, dict)
        ],
        "message": clean_message(message),
    }
    atomic_write(
        COLLECTION_PROGRESS_PATH,
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


@contextmanager
def exclusive_collection_lock():
    COLLECTION_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = COLLECTION_LOCK_PATH.open("a+b")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ServiceError("Another VPS collection is already running.") from exc
        previous = os.environ.get("EXPENSE_COLLECTOR_COLLECTION_ACTIVE")
        os.environ["EXPENSE_COLLECTOR_COLLECTION_ACTIVE"] = "1"
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("EXPENSE_COLLECTOR_COLLECTION_ACTIVE", None)
            else:
                os.environ["EXPENSE_COLLECTOR_COLLECTION_ACTIVE"] = previous
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def bounded_component_timeout(deadline, cap):
    remaining = int(deadline - time.monotonic())
    return max(1, min(int(cap), remaining))


def normalize_requested_platforms(values):
    if not isinstance(values, list) or not values:
        raise ServiceError("platforms must be a non-empty list.")
    if any(not isinstance(value, str) for value in values):
        raise ServiceError("platforms contains an invalid platform.")
    if len(values) != len(set(values)) or set(values) - set(PLATFORMS):
        raise ServiceError("platforms contains an invalid platform.")
    return tuple(platform for platform in PLATFORMS if platform in values)


def expected_components_for(platforms):
    requested = tuple(platforms)
    return (
        *requested,
        *(
            PAYMENT_COMPONENTS[platform]
            for platform in requested
            if platform in PAYMENT_COMPONENTS
        ),
    )


def seed_paths_for(platforms):
    return set().union(*(PLATFORM_SEED_PATHS[platform] for platform in platforms))


def data_outputs_for(platforms):
    return set().union(*(PLATFORM_DATA_OUTPUTS[platform] for platform in platforms))


def bundle_all_passed(components, expected_components=None):
    expected = tuple(expected_components or EXPECTED_COMPONENTS)
    if not isinstance(components, (list, tuple)):
        return False
    identifiers = [
        str(row.get("id") or "")
        for row in components
        if isinstance(row, dict)
    ]
    if (
        len(identifiers) != len(expected)
        or len(set(identifiers)) != len(expected)
        or set(identifiers) != set(expected)
    ):
        return False
    by_id = {
        str(row.get("id") or ""): str(row.get("status") or "failed")
        for row in components
        if isinstance(row, dict)
    }
    return all(
        by_id[component] == "passed" for component in expected
    )


def validate_date(value, field):
    value = str(value or "")
    if not DATE_PATTERN.fullmatch(value):
        raise ServiceError(f"{field} must be an ISO date.")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ServiceError(f"{field} must be a valid date.") from exc
    return value


def validate_request(payload):
    if not isinstance(payload, dict) or payload.get("contract") not in {
        REQUEST_CONTRACT,
        REQUEST_CONTRACT_V2,
    }:
        raise ServiceError("Unsupported collection bundle request.")
    request_contract = payload["contract"]
    is_v2 = request_contract == REQUEST_CONTRACT_V2
    unexpected = set(payload) - {
        "contract",
        "windows",
        "max_orders",
        "payment_min_date",
        "payment_max_orders",
        "seed_files",
        "platforms",
        "collection_run_id",
    }
    if unexpected or (
        not is_v2
        and ({"platforms", "collection_run_id"} & set(payload))
    ):
        raise ServiceError("Collection bundle request has unsupported fields.")
    requested_platforms = (
        normalize_requested_platforms(payload.get("platforms"))
        if is_v2
        else PLATFORMS
    )
    windows = payload.get("windows")
    if not isinstance(windows, dict) or set(windows) != set(requested_platforms):
        raise ServiceError("Collection windows must exactly match requested platforms.")
    windows = {
        platform: validate_date(windows[platform], f"windows.{platform}")
        for platform in requested_platforms
    }
    max_orders = payload.get("max_orders")
    if not isinstance(max_orders, dict):
        raise ServiceError("max_orders must be an object.")
    if is_v2 and set(max_orders) != set(requested_platforms):
        raise ServiceError("max_orders must exactly match requested platforms.")
    if not is_v2 and set(max_orders) - set(PLATFORMS):
        raise ServiceError("max_orders contains an invalid platform.")
    normalized_limits = {}
    for platform in requested_platforms:
        value = max_orders.get(platform, 12)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            raise ServiceError(f"max_orders.{platform} must be from 1 to 100.")
        normalized_limits[platform] = value
    payment_max = payload.get("payment_max_orders", 20)
    if (
        isinstance(payment_max, bool)
        or not isinstance(payment_max, int)
        or not 1 <= payment_max <= 100
    ):
        raise ServiceError("payment_max_orders must be from 1 to 100.")
    seed_files = payload.get("seed_files", {})
    allowed_seed_paths = seed_paths_for(requested_platforms)
    if not isinstance(seed_files, dict) or set(seed_files) - allowed_seed_paths:
        raise ServiceError("seed_files contains an unsupported path.")
    collection_run_id = str(payload.get("collection_run_id") or "")
    if is_v2 and not COLLECTION_RUN_ID_PATTERN.fullmatch(collection_run_id):
        raise ServiceError("collection_run_id must be a 32-character lowercase hex id.")
    return {
        "request_contract": request_contract,
        "response_contract": BUNDLE_CONTRACT_V2 if is_v2 else BUNDLE_CONTRACT,
        "requested_platforms": requested_platforms,
        "expected_components": expected_components_for(requested_platforms),
        "windows": windows,
        "max_orders": normalized_limits,
        "payment_min_date": validate_date(
            payload.get("payment_min_date"),
            "payment_min_date",
        ),
        "payment_max_orders": payment_max,
        "seed_files": seed_files,
        "collection_run_id": collection_run_id,
    }


def _decode_seed_files(seed_files):
    decoded = {}
    for relative, encoded in seed_files.items():
        if not isinstance(encoded, str):
            raise ServiceError("A seed file is not base64 text.")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ServiceError("A seed file is not valid base64.") from exc
        if len(content) > MAX_SEED_FILE_BYTES:
            raise ServiceError("A seed file exceeds the size limit.")
        if relative.endswith(".json"):
            try:
                json.loads(content.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ServiceError("A JSON seed file is invalid.") from exc
        elif relative.endswith(".csv"):
            first_line = content.decode("utf-8-sig", errors="strict").splitlines()[0]
            if "platform" not in first_line or "order_id" not in first_line:
                raise ServiceError("A CSV seed file has an invalid header.")
        decoded[relative] = content
    return decoded


def install_seed_files(seed_files, requested_platforms=None):
    requested = tuple(requested_platforms or PLATFORMS)
    decoded = _decode_seed_files(seed_files)
    allowed_seed_paths = seed_paths_for(requested)
    if set(decoded) - allowed_seed_paths:
        raise ServiceError("seed_files contains an unsupported path.")
    managed_paths = allowed_seed_paths | data_outputs_for(requested)
    for relative in managed_paths - set(decoded):
        path = APP_ROOT / relative
        if path.is_file():
            path.unlink()
    for relative, content in decoded.items():
        atomic_write(APP_ROOT / relative, content)


def component_data_freshness(component, status_value, seed_files):
    if status_value == "passed":
        return "live"
    cached_paths = COMPONENT_CACHE_PATHS.get(component, ())
    return (
        "cache_preserved"
        if any(relative in seed_files for relative in cached_paths)
        else "none"
    )


def finalize_component_records(
    components,
    seed_files,
    expected_components=None,
):
    expected = tuple(expected_components or EXPECTED_COMPONENTS)
    by_id = {}
    for raw in components:
        if not isinstance(raw, dict):
            raise ServiceError("A VPS component result is invalid.")
        component = str(raw.get("id") or "")
        if component not in expected:
            raise ServiceError("A VPS component result has an unknown id.")
        if component in by_id:
            raise ServiceError("A VPS component result is duplicated.")
        by_id[component] = dict(raw)

    rows = []
    for component in expected:
        row = by_id.get(component)
        if row is None:
            is_payment = component in PAYMENT_COMPONENTS.values()
            row = {
                "id": component,
                "status": "skipped" if is_payment else "failed",
                "exit_code": 125,
                "started_at": now_iso(),
                "duration_seconds": 0,
                "message": (
                    "Payment component was skipped because its primary component failed."
                    if is_payment
                    else "Component did not run because a prerequisite failed."
                ),
            }
        status_value = str(row.get("status") or "failed")
        if status_value not in COMPONENT_STATUSES:
            raise ServiceError("A VPS component result has an invalid status.")
        row["data_freshness"] = component_data_freshness(
            component,
            status_value,
            seed_files,
        )
        rows.append(row)
    return rows


def clean_message(value):
    message = str(value or "")
    message = re.sub(r"https?://\S+", "[url]", message, flags=re.I)
    message = re.sub(
        r"(?<!\w)(?:/[A-Za-z0-9_.-]+){2,}",
        "[remote-path]",
        message,
    )
    message = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[host]", message)
    message = re.sub(r"\s+", " ", message).strip()
    return message[-300:]


def run_command(component, command, timeout=210):
    started_at = now_iso()
    started = time.monotonic()
    environment = os.environ.copy()
    environment["PATH"] = f"{NODE.parent}:/usr/bin:/bin"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["EXPENSE_COLLECTOR_WORKSPACE_ROOT"] = str(APP_ROOT)
    try:
        result = subprocess.run(
            [str(item) for item in command],
            cwd=APP_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        output = (result.stdout or result.stderr or "").strip()
        component_status = "passed" if result.returncode == 0 else "failed"
        if result.returncode != 0 and component in PLATFORMS:
            status_path = (
                APP_ROOT / ".codex" / "collector-runs" / f"{component}-collect.json"
            )
            try:
                status_payload = json.loads(status_path.read_text(encoding="utf-8-sig"))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                status_payload = {}
            reported = str(status_payload.get("status") or "")
            if reported in {"failed", "login_required", "blocked", "cooldown"}:
                component_status = reported
        return {
            "id": component,
            "status": component_status,
            "exit_code": result.returncode,
            "started_at": started_at,
            "duration_seconds": round(time.monotonic() - started, 3),
            "message": clean_message(output),
        }
    except subprocess.TimeoutExpired:
        return {
            "id": component,
            "status": "failed",
            "exit_code": 124,
            "started_at": started_at,
            "duration_seconds": round(time.monotonic() - started, 3),
            "message": "Collector exceeded its bounded timeout.",
        }


def runner_command(platform, window, maximum):
    command = [
        PYTHON,
        APP_ROOT / "scripts" / "collectors" / "runner.py",
        "run",
        "--id",
        platform,
        "--phase",
        "collect",
        "--port",
        str(vps_login_session.CDP_PORT),
        "--min-date",
        window,
    ]
    if platform != "uber_eats":
        command.extend(["--max-orders", str(maximum)])
    return command


def payment_command(platform, request):
    return [
        NODE,
        APP_ROOT / "scripts" / "collectors" / "export-payment-evidence.mjs",
        "--port",
        str(vps_login_session.CDP_PORT),
        "--min-date",
        request["payment_min_date"],
        "--platforms",
        platform,
        "--max-orders",
        str(request["payment_max_orders"]),
    ]


def uber_receipt_command(request):
    return [
        NODE,
        APP_ROOT / "scripts" / "collectors" / "export-uber-eats-receipts.mjs",
        "--port",
        str(vps_login_session.CDP_PORT),
        "--min-date",
        request["payment_min_date"],
        "--max-receipts",
        str(request["payment_max_orders"]),
    ]


def clear_raw_outputs(requested_platforms=None):
    requested = set(requested_platforms or PLATFORMS)
    raw = APP_ROOT / "raw"
    raw.mkdir(parents=True, exist_ok=True, mode=0o700)
    for platform in ("walmart_ca", "uber_eats"):
        if platform not in requested:
            continue
        for path in raw.glob(f"*_{platform}_*.json"):
            if path.is_file() and path.parent.resolve() == raw.resolve():
                path.unlink()


def tls_diagnostics():
    browser = vps_login_session.find_browser()
    command = [
        PYTHON,
        APP_ROOT / "scripts" / "collectors" / "tls_diagnostics.py",
        "--route",
        "home_exit",
        "--proxy-host",
        vps_login_session.HOME_PROXY_HOST,
        "--proxy-port",
        str(vps_login_session.HOME_PROXY_PORT),
        "--browser-version",
        command_version(browser),
        "--output",
        "-",
    ]
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [str(value) for value in command],
        cwd=APP_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ServiceError("TLS diagnostics returned invalid JSON.") from exc
    if payload.get("contract") != TLS_DIAGNOSTICS_CONTRACT:
        raise ServiceError("TLS diagnostics returned an invalid contract.")
    if payload.get("route") != "home_exit":
        raise ServiceError("TLS diagnostics returned an invalid route.")
    if payload.get("status") not in {"passed", "partial"}:
        raise ServiceError("TLS diagnostics returned an invalid status.")
    return payload


def bundle_members(requested_platforms=None):
    requested = tuple(requested_platforms or PLATFORMS)
    members = []
    for relative in DATA_OUTPUTS:
        if relative not in data_outputs_for(requested):
            continue
        path = APP_ROOT / relative
        if path.is_file():
            members.append((relative, path))
    raw = APP_ROOT / "raw"
    for platform in ("walmart_ca", "uber_eats"):
        if platform not in requested:
            continue
        for path in sorted(raw.glob(f"*_{platform}_*.json")):
            members.append((f"raw/{path.name}", path))
    return members


def build_bundle(requested_platforms=None):
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        for relative, path in bundle_members(requested_platforms):
            content = path.read_bytes()
            if len(content) > MAX_BUNDLE_BYTES:
                raise ServiceError("A bundle member exceeds the size limit.")
            if relative.endswith(".json"):
                try:
                    json.loads(content.decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ServiceError("A bundle JSON member is invalid.") from exc
            archive.writestr(relative, content)
    content = output.getvalue()
    if not content or len(content) > MAX_BUNDLE_BYTES:
        raise ServiceError("The sanitized collection bundle has an invalid size.")
    return content


def budget_failure(component):
    return {
        "id": component,
        "status": "failed",
        "exit_code": 124,
        "started_at": now_iso(),
        "duration_seconds": 0,
        "message": "VPS collection budget was exhausted before this component started.",
    }


def _collect_bundle_locked(payload, started_at, components, request=None):
    elapsed_start = time.monotonic()
    timings = {"session_start_seconds": 0.0}
    if status()["status"] != "ready":
        raise ServiceError("The VPS collector backend is not ready.")
    request = request or validate_request(payload)
    deadline = time.monotonic() + COLLECTION_BUDGET_SECONDS
    vps_login_session.probe_home_proxy("www.walmart.ca")
    requested_platforms = request["requested_platforms"]
    expected_components = request["expected_components"]
    install_seed_files(request["seed_files"], requested_platforms)
    clear_raw_outputs(requested_platforms)
    try:
        for index, platform in enumerate(requested_platforms):
            if deadline - time.monotonic() < 15:
                components.extend(
                    budget_failure(remaining)
                    for remaining in requested_platforms[index:]
                )
                break
            write_collection_progress(
                "running",
                "collecting",
                platform=platform,
                started_at=started_at,
                components=components,
                components_total=len(expected_components),
            )
            try:
                session_start = time.monotonic()
                try:
                    vps_login_session.start_session(platform, interactive=False)
                finally:
                    timings["session_start_seconds"] += time.monotonic() - session_start
            except (OSError, vps_login_session.SessionError) as exc:
                components.append({
                    "id": platform,
                    "status": "failed",
                    "exit_code": 1,
                    "started_at": now_iso(),
                    "duration_seconds": 0,
                    "message": clean_message(exc),
                })
                continue
            primary = run_command(
                platform,
                runner_command(
                    platform,
                    request["windows"][platform],
                    request["max_orders"][platform],
                ),
                timeout=bounded_component_timeout(
                    deadline,
                    PRIMARY_COMPONENT_TIMEOUT_SECONDS,
                ),
            )
            components.append(primary)
            if primary["status"] != "passed":
                continue
            if platform == "uber_eats":
                components.append(run_command(
                    "uber_eats_payment",
                    uber_receipt_command(request),
                    timeout=bounded_component_timeout(
                        deadline,
                        PAYMENT_COMPONENT_TIMEOUT_SECONDS,
                    ),
                ))
            elif platform in {"walmart_ca", "fantuan"}:
                components.append(run_command(
                    f"{platform}_payment",
                    payment_command(platform, request),
                    timeout=bounded_component_timeout(
                        deadline,
                        PAYMENT_COMPONENT_TIMEOUT_SECONDS,
                    ),
                ))
    finally:
        write_collection_progress(
            "running",
            "stopping_browser",
            started_at=started_at,
            components=components,
            components_total=len(expected_components),
        )
        cleanup_start = time.monotonic()
        try:
            vps_login_session.stop_session()
        finally:
            timings["final_cleanup_seconds"] = time.monotonic() - cleanup_start
    write_collection_progress(
        "running",
        "building_bundle",
        started_at=started_at,
        components=components,
        components_total=len(expected_components),
    )
    components[:] = finalize_component_records(
        components,
        request["seed_files"],
        expected_components,
    )
    bundle_start = time.monotonic()
    bundle = build_bundle(requested_platforms)
    timings["bundle_seconds"] = time.monotonic() - bundle_start
    timings["components_seconds"] = sum(float(row.get("duration_seconds") or 0) for row in components)
    timings["total_seconds"] = time.monotonic() - elapsed_start
    timings = {key: round(value, 3) for key, value in timings.items()}
    all_passed = bundle_all_passed(components, expected_components)
    bundle_hash = hashlib.sha256(bundle).hexdigest()
    response = {
        "contract": request["response_contract"],
        "status": "passed" if all_passed else "partial",
        "message": (
            "VPS五平台脱敏采集包已生成"
            if all_passed
            else "VPS采集包已生成，但至少一个组件需要处理"
        ),
        "generated_at": now_iso(),
        "route": "home_exit",
        "timings": timings,
        "components": components,
        "bundle_sha256": bundle_hash,
        "bundle_bytes": len(bundle),
        "bundle_base64": base64.b64encode(bundle).decode("ascii"),
    }
    if request["request_contract"] == REQUEST_CONTRACT_V2:
        response["requested_platforms"] = list(requested_platforms)
        response["expected_components"] = list(expected_components)
        response["collection_run_id"] = request["collection_run_id"]
    return response


def collect_bundle(payload):
    started_at = now_iso()
    components = []
    with exclusive_collection_lock():
        components_total = len(EXPECTED_COMPONENTS)
        try:
            request = validate_request(payload)
            components_total = len(request["expected_components"])
            write_collection_progress(
                "running",
                "preparing",
                started_at=started_at,
                components=components,
                components_total=components_total,
            )
            response = _collect_bundle_locked(
                payload,
                started_at,
                components,
                request=request,
            )
        except Exception as exc:
            write_collection_progress(
                "failed",
                "failed",
                started_at=started_at,
                components=components,
                components_total=components_total,
                message=exc,
            )
            raise
        write_collection_progress(
            response["status"],
            "completed",
            started_at=started_at,
            components=components,
            components_total=len(
                response.get("expected_components", EXPECTED_COMPONENTS)
            ),
            message=response["message"],
        )
        return response


def read_request():
    content = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(content) > MAX_REQUEST_BYTES:
        raise ServiceError("The collection request exceeds the size limit.")
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceError("The collection request is not valid JSON.") from exc


def parser():
    command_parser = argparse.ArgumentParser(
        description="Report readiness or collect a sanitized VPS bundle."
    )
    command_parser.add_argument(
        "command",
        choices=["status", "probe", "diagnose", "collect"],
    )
    return command_parser


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "status":
            payload = status()
        elif args.command == "probe":
            vps_login_session.probe_home_proxy("www.walmart.ca")
            payload = {
                "contract": CONTRACT,
                "status": "ready",
                "route": "home_exit",
                "message": "家庭网络出口已就绪",
            }
        elif args.command == "diagnose":
            payload = tls_diagnostics()
        else:
            payload = collect_bundle(read_request())
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0 if payload.get("status") in {"ready", "passed", "partial"} else 1
    except (
        OSError,
        ServiceError,
        subprocess.SubprocessError,
        vps_login_session.SessionError,
    ) as exc:
        print(json.dumps({
            "contract": CONTRACT,
            "status": "failed",
            "message": clean_message(exc),
        }, ensure_ascii=False, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
