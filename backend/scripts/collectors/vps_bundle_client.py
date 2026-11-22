import argparse
import base64
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REQUEST_CONTRACT = "expense-collector-bundle-request/v1"
BUNDLE_CONTRACT = "expense-collector-bundle/v1"
REQUEST_CONTRACT_V2 = "expense-collector-bundle-request/v2"
BUNDLE_CONTRACT_V2 = "expense-collector-bundle/v2"
TLS_DIAGNOSTICS_CONTRACT = "expense-collector-tls-diagnostics/v1"
TLS_CONSISTENCY_CONTRACT = "expense-collector-tls-consistency/v1"
COLLECTOR_CONTRACT = "expense-collector/v1"
PLATFORMS = (
    "walmart_ca",
    "amazon_ca",
    "uber_eats",
    "the_ordinary_ca",
    "fantuan",
)
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
DATA_FRESHNESS_VALUES = {"live", "cache_preserved", "none"}
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
SEED_PATHS = (
    "data/transactions.csv",
    "data/walmart_items.csv",
    "data/amazon_orders.json",
    "data/the_ordinary_orders.json",
    "data/payment_evidence.json",
    "data/fantuan_orders.json",
    "data/uber_eats_receipts.json",
    ".codex/walmart-product-link-attempts.json",
)
DATA_OUTPUTS = {
    "data/amazon_orders.json",
    "data/the_ordinary_orders.json",
    "data/payment_evidence.json",
    "data/fantuan_orders.json",
    "data/uber_eats_receipts.json",
}
RAW_OUTPUT_PATTERN = re.compile(
    r"^raw/[A-Za-z0-9_.-]+_(walmart_ca|uber_eats)_[A-Za-z0-9_.-]+\.json$"
)
DATE_PATTERN = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
COLLECTION_RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
MAX_BUNDLE_BYTES = 25 * 1024 * 1024
RECOVERY_BACKLOG_CONTRACT = "expense-collector-recovery-backlog/v1"


class BundleClientError(RuntimeError):
    pass


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sanitize_component_message(value):
    message = str(value or "")
    message = re.sub(r"https?://\S+", "[url]", message, flags=re.I)
    message = re.sub(
        r"(?<!\w)(?:/[A-Za-z0-9_.-]+){2,}",
        "[remote-path]",
        message,
    )
    message = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[host]", message)
    return re.sub(r"\s+", " ", message).strip()[-300:]


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path, payload):
    atomic_write(
        path,
        (
            json.dumps(payload, ensure_ascii=False, indent=2)
            + "\n"
        ).encode("utf-8"),
    )


def read_json_object(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _validated_recovery_backlog(path):
    payload = read_json_object(path)
    if payload.get("contract") != RECOVERY_BACKLOG_CONTRACT:
        return {}
    raw_platforms = payload.get("platforms")
    if not isinstance(raw_platforms, list) or len(raw_platforms) > len(PLATFORMS):
        return {}
    validated = {}
    for raw in raw_platforms:
        if not isinstance(raw, dict):
            return {}
        platform = str(raw.get("id") or "")
        if platform not in PLATFORMS or platform in validated:
            return {}
        run_id = str(raw.get("collection_run_id") or "")
        if COLLECTION_RUN_ID_PATTERN.fullmatch(run_id) is None:
            return {}
        expected = expected_components_for((platform,))
        raw_components = raw.get("components")
        if not isinstance(raw_components, list) or len(raw_components) != len(expected):
            return {}
        by_id = {}
        for component in raw_components:
            if not isinstance(component, dict):
                return {}
            component_id = str(component.get("id") or "")
            status = str(component.get("status") or "")
            freshness = str(component.get("data_freshness") or "")
            exit_code = component.get("exit_code", 1)
            duration = component.get("duration_seconds", 0)
            if (
                component_id not in expected
                or component_id in by_id
                or status not in COMPONENT_STATUSES
                or freshness not in DATA_FRESHNESS_VALUES
                or isinstance(exit_code, bool)
                or not isinstance(exit_code, int)
                or isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or duration < 0
                or (status == "passed" and freshness != "live")
                or (status != "passed" and freshness == "live")
            ):
                return {}
            by_id[component_id] = {
                "id": component_id,
                "status": status,
                "exit_code": exit_code,
                "duration_seconds": float(duration),
                "data_freshness": freshness,
                "message": sanitize_component_message(component.get("message")),
            }
        if set(by_id) != set(expected):
            return {}
        ordered = [by_id[component] for component in expected]
        if all(component["status"] == "passed" for component in ordered):
            return {}
        validated[platform] = {
            "id": platform,
            "collection_run_id": run_id,
            "generated_at": str(raw.get("generated_at") or "")[:80],
            "components": ordered,
        }
    return validated


def update_recovery_backlog(status_dir, response, requested, current_rows):
    path = Path(status_dir) / "vps-recovery.json"
    unresolved = _validated_recovery_backlog(path)
    run_id = str(response.get("collection_run_id") or "")[:32]
    generated_at = str(response.get("generated_at") or now_iso())[:80]
    for platform in requested:
        expected = expected_components_for((platform,))
        rows = [current_rows[component] for component in expected]
        if all(row.get("status") == "passed" for row in rows):
            unresolved.pop(platform, None)
            continue
        unresolved[platform] = {
            "id": platform,
            "collection_run_id": run_id,
            "generated_at": generated_at,
            "components": rows,
        }
    ordered = [unresolved[platform] for platform in PLATFORMS if platform in unresolved]
    atomic_write_json(path, {
        "contract": RECOVERY_BACKLOG_CONTRACT,
        "status": "partial" if ordered else "passed",
        "generated_at": generated_at,
        "platforms": ordered,
    })


def browser_major(value):
    match = re.search(
        r"(?:Chrome|Chromium)(?: for Testing)?(?:/|\s+)(\d+)",
        str(value or ""),
        re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


def validate_tls_diagnostics(payload, route):
    if (
        not isinstance(payload, dict)
        or payload.get("contract") != TLS_DIAGNOSTICS_CONTRACT
    ):
        raise BundleClientError("TLS diagnostics contract is invalid.")
    if (
        payload.get("route") != route
        or payload.get("status") not in {"passed", "partial"}
    ):
        raise BundleClientError("TLS diagnostics route or status is invalid.")
    targets = payload.get("targets")
    if not isinstance(targets, list) or len(targets) != len(PLATFORMS):
        raise BundleClientError("TLS diagnostics target set is invalid.")
    by_platform = {}
    for row in targets:
        if not isinstance(row, dict) or row.get("platform") not in PLATFORMS:
            raise BundleClientError("TLS diagnostics contains an invalid platform.")
        if row.get("platform") in by_platform:
            raise BundleClientError("TLS diagnostics contains duplicate platforms.")
        if row.get("status") not in {"passed", "failed"}:
            raise BundleClientError("TLS diagnostics contains an invalid target status.")
        by_platform[row["platform"]] = row
    if set(by_platform) != set(PLATFORMS):
        raise BundleClientError("TLS diagnostics does not cover all platforms.")
    return payload


def run_local_tls_diagnostics(root):
    script = Path(root) / "scripts" / "collectors" / "tls_diagnostics.py"
    try:
        workflow = json.loads(
            (Path(root) / "config" / "workflow.json").read_text(
                encoding="utf-8-sig"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleClientError("Workflow configuration is invalid.") from exc
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--route",
            "local",
            "--cdp-port",
            str(int(workflow.get("port", 9223))),
            "--output",
            "-",
        ],
        cwd=root,
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
        raise BundleClientError(
            "Local TLS diagnostics returned invalid JSON."
        ) from exc
    return validate_tls_diagnostics(payload, "local")


def compare_tls_diagnostics(local, remote):
    local_rows = {row["platform"]: row for row in local["targets"]}
    remote_rows = {row["platform"]: row for row in remote["targets"]}
    comparisons = []
    issues = []
    for platform in PLATFORMS:
        local_row = local_rows[platform]
        remote_row = remote_rows[platform]
        issue = ""
        if local_row["status"] != "passed":
            issue = f"local_{local_row.get('category', 'failed')}"
        elif remote_row["status"] != "passed":
            issue = f"home_exit_{remote_row.get('category', 'failed')}"
        elif local_row.get("tls_version") != remote_row.get("tls_version"):
            issue = "tls_version_mismatch"
        elif local_row.get("alpn") != remote_row.get("alpn"):
            issue = "alpn_mismatch"
        if issue:
            issues.append(f"{platform}:{issue}")
        comparisons.append({
            "platform": platform,
            "status": "passed" if not issue else "needs_review",
            "issue": issue,
            "local_tls": local_row.get("tls_version", ""),
            "home_exit_tls": remote_row.get("tls_version", ""),
            "local_alpn": local_row.get("alpn", ""),
            "home_exit_alpn": remote_row.get("alpn", ""),
        })
    local_browser = local.get("runtime", {}).get("browser", "")
    remote_browser = remote.get("runtime", {}).get("browser", "")
    local_major = browser_major(local_browser)
    remote_major = browser_major(remote_browser)
    browser_gap = (
        abs(local_major - remote_major)
        if local_major is not None and remote_major is not None
        else None
    )
    if browser_gap is not None and browser_gap > 1:
        issues.append("browser_major_version_gap")
    try:
        local_time = datetime.fromisoformat(
            str(local.get("generated_at") or "").replace("Z", "+00:00")
        )
        remote_time = datetime.fromisoformat(
            str(remote.get("generated_at") or "").replace("Z", "+00:00")
        )
        clock_skew_seconds = round(abs((local_time - remote_time).total_seconds()))
    except ValueError:
        clock_skew_seconds = None
    if clock_skew_seconds is not None and clock_skew_seconds > 120:
        issues.append("clock_skew")
    return {
        "contract": TLS_CONSISTENCY_CONTRACT,
        "status": "passed" if not issues else "needs_review",
        "generated_at": now_iso(),
        "routes": {"local": local, "home_exit": remote},
        "browser_consistency": {
            "local_major": local_major,
            "home_exit_major": remote_major,
            "major_gap": browser_gap,
            "status": (
                "passed"
                if browser_gap is None or browser_gap <= 1
                else "needs_review"
            ),
        },
        "clock_consistency": {
            "skew_seconds": clock_skew_seconds,
            "status": (
                "passed"
                if clock_skew_seconds is None or clock_skew_seconds <= 120
                else "needs_review"
            ),
        },
        "comparisons": comparisons,
        "issues": issues,
    }


def load_backend_config(root):
    path = Path(root) / "config" / "collector_backends.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleClientError(f"Collector backend configuration is invalid: {exc}") from exc
    remote = payload.get("vps") if isinstance(payload, dict) else None
    if not isinstance(remote, dict):
        raise BundleClientError("VPS collector backend configuration is missing.")
    host = str(remote.get("ssh_host") or "")
    python = str(remote.get("remote_python") or "")
    service = str(remote.get("remote_service") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", host):
        raise BundleClientError("VPS ssh_host is invalid.")
    for value, field in ((python, "remote_python"), (service, "remote_service")):
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", value) or ".." in Path(value).parts:
            raise BundleClientError(f"VPS {field} is invalid.")
    return {
        "ssh_host": host,
        "remote_python": python,
        "remote_service": service,
        "connect_timeout_seconds": int(remote.get("timeout_seconds", 6)),
        "collection_timeout_seconds": int(
            remote.get("collection_timeout_seconds", 1200)
        ),
        "home_exit_remote_port": int(remote.get("home_exit_remote_port", 1080)),
    }


def parse_key_values(values, allowed, value_parser, label):
    result = {}
    for raw in values:
        if "=" not in raw:
            raise BundleClientError(f"{label} must use platform=value.")
        key, value = raw.split("=", 1)
        if key not in allowed or key in result:
            raise BundleClientError(f"{label} contains an invalid platform.")
        result[key] = value_parser(value)
    if set(result) != set(allowed):
        raise BundleClientError(f"{label} must cover every configured platform.")
    return result


def parse_requested_platforms(values):
    requested = list(values or [])
    if not requested:
        return PLATFORMS, False
    if len(requested) != len(set(requested)) or set(requested) - set(PLATFORMS):
        raise BundleClientError("platform contains an invalid or duplicate value.")
    return tuple(platform for platform in PLATFORMS if platform in requested), True


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


def parse_date(value):
    if not DATE_PATTERN.fullmatch(value):
        raise BundleClientError("Collection windows must be ISO dates.")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise BundleClientError("Collection windows must be valid dates.") from exc
    return value


def parse_limit(value):
    try:
        result = int(value)
    except ValueError as exc:
        raise BundleClientError("Collector limits must be integers.") from exc
    if not 1 <= result <= 100:
        raise BundleClientError("Collector limits must be from 1 to 100.")
    return result


def build_seed(root, requested_platforms=None):
    allowed_paths = seed_paths_for(requested_platforms or PLATFORMS)
    seed = {}
    total = 0
    for relative in SEED_PATHS:
        if relative not in allowed_paths:
            continue
        path = Path(root) / relative
        if not path.is_file():
            continue
        content = path.read_bytes()
        total += len(content)
        if total > 6 * 1024 * 1024:
            raise BundleClientError("Sanitized collection seed exceeds the size limit.")
        seed[relative] = base64.b64encode(content).decode("ascii")
    return seed


def ssh_command(config, action):
    ssh = shutil.which("ssh.exe") or shutil.which("ssh")
    if not ssh:
        raise BundleClientError("OpenSSH client was not found.")
    return [
        ssh,
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={config['connect_timeout_seconds']}",
        config["ssh_host"],
        config["remote_python"],
        config["remote_service"],
        action,
    ]


def request_remote(config, action, payload=None, timeout=30):
    result = subprocess.run(
        ssh_command(config, action),
        input=(
            json.dumps(payload, ensure_ascii=False)
            if payload is not None
            else None
        ),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        detail = (result.stderr or "VPS returned no status.").strip()
        raise BundleClientError(detail[-240:])
    try:
        response = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise BundleClientError("VPS returned invalid JSON.") from exc
    if result.returncode != 0 or response.get("status") == "failed":
        raise BundleClientError(
            str(response.get("message") or "VPS collection failed.")[:300]
        )
    return response


def stop_login_tunnel(root):
    state = Path(root) / ".codex" / "vps-collector-login-tunnel.json"
    if not state.exists() or os.name != "nt":
        return
    powershell = shutil.which("powershell.exe")
    script = Path(root) / "scripts" / "manage-vps-collector-login.ps1"
    if not powershell or not script.is_file():
        raise BundleClientError("The active VPS login tunnel could not be closed safely.")
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Action",
            "Stop",
        ],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise BundleClientError("The active VPS login tunnel could not be closed.")


def start_home_tunnel(config):
    ssh = shutil.which("ssh.exe") or shutil.which("ssh")
    if not ssh:
        raise BundleClientError("OpenSSH client was not found.")
    command = [
        ssh,
        "-N",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
        "-R",
        f"127.0.0.1:{config['home_exit_remote_port']}",
        config["ssh_host"],
    ]
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def wait_for_home_exit(config, tunnel):
    deadline = time.monotonic() + 20
    last_error = None
    while time.monotonic() < deadline:
        if tunnel.poll() is not None:
            raise BundleClientError("The home-exit SSH tunnel stopped during startup.")
        try:
            response = request_remote(config, "probe", timeout=12)
            if (
                response.get("status") == "ready"
                and response.get("route") == "home_exit"
            ):
                return
        except BundleClientError as exc:
            last_error = exc
        time.sleep(1)
    raise BundleClientError(
        f"The home-exit route did not become ready: {last_error or 'timeout'}"
    )


def validate_member_name(name):
    if name in DATA_OUTPUTS or RAW_OUTPUT_PATTERN.fullmatch(name):
        return
    raise BundleClientError(f"VPS bundle contains an unsupported member: {name}")


def validate_member_scope(name, requested_platforms):
    if name in data_outputs_for(requested_platforms):
        return
    raw_match = RAW_OUTPUT_PATTERN.fullmatch(name)
    if raw_match and raw_match.group(1) in requested_platforms:
        return
    raise BundleClientError(
        f"VPS bundle contains an unrequested platform member: {name}"
    )


def decode_bundle(response):
    encoded = response.get("bundle_base64")
    expected_hash = str(response.get("bundle_sha256") or "")
    if not isinstance(encoded, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise BundleClientError("VPS bundle metadata is invalid.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise BundleClientError("VPS bundle encoding is invalid.") from exc
    if not content or len(content) > MAX_BUNDLE_BYTES:
        raise BundleClientError("VPS bundle size is invalid.")
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise BundleClientError("VPS bundle checksum does not match.")
    return content


def read_bundle_members(content, requested_platforms=None, allow_empty=False):
    members = {}
    try:
        archive = zipfile.ZipFile(io.BytesIO(content), "r")
    except zipfile.BadZipFile as exc:
        raise BundleClientError("VPS bundle is not a valid ZIP archive.") from exc
    with archive:
        infos = archive.infolist()
        if (not infos and not allow_empty) or len(infos) > 200:
            raise BundleClientError("VPS bundle member count is invalid.")
        total = 0
        for info in infos:
            if info.is_dir():
                continue
            validate_member_name(info.filename)
            if requested_platforms is not None:
                validate_member_scope(info.filename, requested_platforms)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise BundleClientError("VPS bundle cannot contain symbolic links.")
            total += info.file_size
            if total > MAX_BUNDLE_BYTES:
                raise BundleClientError("VPS bundle expands beyond the size limit.")
            content = archive.read(info)
            try:
                json.loads(content.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BundleClientError(
                    f"VPS bundle member is invalid JSON: {info.filename}"
                ) from exc
            members[info.filename] = content
    return members


def apply_members(root, members):
    previous = {}
    written = []
    try:
        for relative, content in members.items():
            path = (Path(root) / relative).resolve()
            if Path(root).resolve() not in path.parents:
                raise BundleClientError("VPS bundle member escaped the workspace.")
            previous[path] = path.read_bytes() if path.exists() else None
            atomic_write(path, content)
            written.append(path)
    except Exception:
        for path in reversed(written):
            old = previous[path]
            if old is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, old)
        raise


def _legacy_data_freshness(component, status_value, seed_paths):
    if status_value == "passed":
        return "live"
    return (
        "cache_preserved"
        if any(
            relative in seed_paths
            for relative in COMPONENT_CACHE_PATHS.get(component, ())
        )
        else "none"
    )


def validate_component_results(
    response,
    seed_paths,
    expected_components=None,
):
    expected = tuple(expected_components or EXPECTED_COMPONENTS)
    raw_components = response.get("components")
    if not isinstance(raw_components, list):
        raise BundleClientError("VPS component results must be a list.")
    raw_components = list(raw_components)
    if (
        response.get("contract") == BUNDLE_CONTRACT
        and expected == EXPECTED_COMPONENTS
        and len(raw_components) < len(expected)
    ):
        identifiers = [
            str(row.get("id") or "")
            for row in raw_components
            if isinstance(row, dict)
        ]
        for platform, payment_component in PAYMENT_COMPONENTS.items():
            if payment_component in identifiers or identifiers.count(platform) != 1:
                continue
            primary = next(
                row for row in raw_components
                if isinstance(row, dict) and row.get("id") == platform
            )
            if str(primary.get("status") or "") == "passed":
                continue
            raw_components.append({
                "id": payment_component,
                "status": "skipped",
                "exit_code": 125,
                "started_at": str(primary.get("started_at") or ""),
                "duration_seconds": 0,
                "message": (
                    "Legacy VPS response omitted payment collection because "
                    "its primary component did not pass."
                ),
            })
            identifiers.append(payment_component)
    if len(raw_components) != len(expected):
        raise BundleClientError(
            "VPS component results must match the expected component set."
        )

    by_id = {}
    for raw in raw_components:
        if not isinstance(raw, dict):
            raise BundleClientError("A VPS component result is invalid.")
        component = str(raw.get("id") or "")
        if component not in expected:
            raise BundleClientError("A VPS component result has an unknown id.")
        if component in by_id:
            raise BundleClientError("A VPS component result is duplicated.")
        status_value = str(raw.get("status") or "")
        if status_value not in COMPONENT_STATUSES:
            raise BundleClientError("A VPS component result has an invalid status.")
        exit_code = raw.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise BundleClientError("A VPS component result has an invalid exit code.")
        duration = raw.get("duration_seconds", 0)
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or duration < 0
        ):
            raise BundleClientError("A VPS component result has an invalid duration.")
        freshness = raw.get("data_freshness")
        if freshness is None:
            freshness = _legacy_data_freshness(
                component,
                status_value,
                seed_paths,
            )
        freshness = str(freshness)
        if freshness not in DATA_FRESHNESS_VALUES:
            raise BundleClientError("A VPS component result has invalid freshness.")
        if status_value == "passed" and freshness != "live":
            raise BundleClientError("A passed VPS component must contain live data.")
        if status_value != "passed" and freshness == "live":
            raise BundleClientError("A failed VPS component cannot claim live data.")
        by_id[component] = {
            "id": component,
            "status": status_value,
            "exit_code": exit_code,
            "started_at": str(raw.get("started_at") or "")[:80],
            "duration_seconds": float(duration),
            "message": sanitize_component_message(raw.get("message")),
            "data_freshness": freshness,
        }

    if set(by_id) != set(expected):
        raise BundleClientError("VPS component results do not match the contract.")
    rows = [by_id[component] for component in expected]
    all_passed = all(row["status"] == "passed" for row in rows)
    bundle_status = response.get("status")
    if (bundle_status == "passed") != all_passed:
        raise BundleClientError("VPS bundle and component statuses disagree.")
    return rows


def sanitized_timing_values(value, allowed):
    if not isinstance(value, dict):
        return {}
    return {key: round(float(seconds), 3) for key, seconds in value.items()
            if key in allowed and isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
            and math.isfinite(seconds) and 0 <= seconds <= 7200}


def timing_summary(response):
    return {
        "remote": sanitized_timing_values(response.get("timings"), {
            "total_seconds", "session_start_seconds", "final_cleanup_seconds", "bundle_seconds", "components_seconds"}),
        "client": sanitized_timing_values(response.get("client_timings"), {
            "total_seconds", "connection_seconds", "remote_roundtrip_seconds", "validation_apply_seconds"}),
    }


def write_component_statuses(
    root,
    response,
    components,
    windows,
    limits,
    requested_platforms=None,
    expected_components=None,
    continue_recovery=False,
):
    requested = tuple(requested_platforms or PLATFORMS)
    expected = tuple(expected_components or EXPECTED_COMPONENTS)
    by_id = {
        row.get("id"): row
        for row in components
        if isinstance(row, dict)
    }
    status_dir = Path(root) / ".codex" / "collector-runs"
    for platform in requested:
        row = by_id.get(platform, {})
        atomic_write_json(status_dir / f"{platform}-collect.json", {
            "contract": COLLECTOR_CONTRACT,
            "collector_id": platform,
            "ledger_platform": platform,
            "phase": "collect",
            "status": row.get("status", "failed"),
            "exit_code": int(row.get("exit_code", 1)),
            "started_at": row.get("started_at", ""),
            "completed_at": response.get("generated_at", now_iso()),
            "duration_seconds": row.get("duration_seconds", 0),
            "data_freshness": row.get("data_freshness", "none"),
            "context": {
                "min_date": windows[platform],
                "max_orders": limits[platform],
                "backend": "vps",
                "data_freshness": row.get("data_freshness", "none"),
            },
            "message": str(row.get("message") or "No VPS component result.")[:300],
        })
    generated_at = str(response.get("generated_at") or now_iso())[:80]
    current_rows = {
        row["id"]: {
            "id": row["id"],
            "status": row["status"],
            "exit_code": row["exit_code"],
            "duration_seconds": row["duration_seconds"],
            "data_freshness": row["data_freshness"],
            "message": row["message"],
        }
        for row in components
    }
    summary_path = status_dir / "vps-bundle.json"
    previous = read_json_object(summary_path)
    aggregate_requested = requested
    aggregate_rows = dict(current_rows)
    recovery_started_at = generated_at
    if (
        continue_recovery
        and requested != PLATFORMS
        and previous.get("status") == "partial"
    ):
        old_requested = previous.get("requested_platforms")
        old_expected = previous.get("expected_components")
        old_components = previous.get("components")
        normalized_old_requested = (
            tuple(platform for platform in PLATFORMS if platform in old_requested)
            if isinstance(old_requested, list)
            else ()
        )
        if (
            previous.get("contract") == "expense-collector-bundle-status/v2"
            and COLLECTION_RUN_ID_PATTERN.fullmatch(
                str(previous.get("collection_run_id") or "")
            )
            and normalized_old_requested
            and len(old_requested) == len(set(old_requested))
            and len(normalized_old_requested) == len(old_requested)
            and old_expected == list(expected_components_for(normalized_old_requested))
            and isinstance(old_components, list)
            and len(old_components) == len(old_expected)
        ):
            old_by_id = {
                row.get("id"): row
                for row in old_components
                if isinstance(row, dict) and row.get("id") in old_expected
            }
            old_rows_valid = all(
                row.get("status") in COMPONENT_STATUSES
                and row.get("data_freshness") in DATA_FRESHNESS_VALUES
                and not (
                    row.get("status") == "passed"
                    and row.get("data_freshness") != "live"
                )
                and not (
                    row.get("status") != "passed"
                    and row.get("data_freshness") == "live"
                )
                for row in old_by_id.values()
            )
            if set(old_by_id) == set(old_expected) and old_rows_valid:
                aggregate_requested = tuple(
                    platform
                    for platform in PLATFORMS
                    if platform in set(normalized_old_requested) | set(requested)
                )
                aggregate_rows = dict(old_by_id)
                aggregate_rows.update(current_rows)
                recovery_started_at = str(
                    previous.get("recovery_started_at")
                    or previous.get("generated_at")
                    or generated_at
                )[:80]
    aggregate_expected = expected_components_for(aggregate_requested)
    aggregate_components = [
        aggregate_rows[component] for component in aggregate_expected
    ]
    aggregate_status = (
        "passed"
        if all(row.get("status") == "passed" for row in aggregate_components)
        else "partial"
    )
    attempt_started_at = min(
        (
            str(row.get("started_at") or "")
            for row in components
            if str(row.get("started_at") or "")
        ),
        default=generated_at,
    )[:80]
    atomic_write_json(summary_path, {
        "contract": "expense-collector-bundle-status/v2",
        "status": aggregate_status,
        "generated_at": generated_at,
        "collection_run_id": str(response.get("collection_run_id") or "")[:32],
        "recovery_started_at": recovery_started_at,
        "attempt_started_at": attempt_started_at,
        "attempt_generated_at": generated_at,
        "attempt_requested_platforms": list(requested),
        "requested_platforms": list(aggregate_requested),
        "expected_components": list(aggregate_expected),
        "components": aggregate_components,
        "timings": timing_summary(response),
    })
    update_recovery_backlog(status_dir, response, requested, current_rows)


def diagnose(args):
    root = Path(args.root).resolve()
    config = load_backend_config(root)
    local = run_local_tls_diagnostics(root)
    stop_login_tunnel(root)
    tunnel = start_home_tunnel(config)
    try:
        wait_for_home_exit(config, tunnel)
        remote = request_remote(config, "diagnose", timeout=75)
    finally:
        stop_process(tunnel)
    validate_tls_diagnostics(remote, "home_exit")
    payload = compare_tls_diagnostics(local, remote)
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    try:
        output.resolve().relative_to(root)
    except ValueError as exc:
        raise BundleClientError(
            "TLS diagnostics output must stay inside the workspace."
        ) from exc
    atomic_write_json(output, payload)
    print(f"tls_consistency_status={payload['status']}")
    print(
        "tls_consistency_targets="
        f"{sum(row['status'] == 'passed' for row in payload['comparisons'])}/"
        f"{len(payload['comparisons'])}"
    )
    print(
        "tls_browser_major_gap="
        f"{payload['browser_consistency']['major_gap']}"
    )
    if payload["issues"]:
        print(f"tls_consistency_issues={','.join(payload['issues'])}")
    return 0 if payload["status"] == "passed" else 1


def collect(args):
    client_started = time.monotonic()
    client_timings = {}
    root = Path(args.root).resolve()
    config = load_backend_config(root)
    if not args.payment_min_date:
        raise BundleClientError("collect requires --payment-min-date.")
    requested_platforms, selective = parse_requested_platforms(
        getattr(args, "platform", [])
    )
    continue_recovery = bool(getattr(args, "continue_recovery", False))
    if continue_recovery and not selective:
        raise BundleClientError(
            "Continuing collector recovery requires selected platforms."
        )
    collection_run_id = str(getattr(args, "collection_run_id", "") or "")
    if selective and not COLLECTION_RUN_ID_PATTERN.fullmatch(collection_run_id):
        raise BundleClientError(
            "Collection requires a 32-character lowercase collection run id."
        )
    if (
        not selective
        and collection_run_id
        and not COLLECTION_RUN_ID_PATTERN.fullmatch(collection_run_id)
    ):
        raise BundleClientError("Collection run id is invalid.")
    expected_components = expected_components_for(requested_platforms)
    windows = parse_key_values(
        args.window,
        requested_platforms,
        parse_date,
        "window",
    )
    limits = parse_key_values(
        args.max_order,
        requested_platforms,
        parse_limit,
        "max-order",
    )
    payment_min_date = parse_date(args.payment_min_date)
    payment_max_orders = parse_limit(str(args.payment_max_orders))
    seed_files = build_seed(root, requested_platforms)
    request = {
        "contract": REQUEST_CONTRACT_V2 if selective else REQUEST_CONTRACT,
        "windows": windows,
        "max_orders": limits,
        "payment_min_date": payment_min_date,
        "payment_max_orders": payment_max_orders,
        "seed_files": seed_files,
    }
    if selective:
        request["platforms"] = list(requested_platforms)
        request["collection_run_id"] = collection_run_id
    connection_started = time.monotonic()
    stop_login_tunnel(root)
    tunnel = start_home_tunnel(config)
    try:
        wait_for_home_exit(config, tunnel)
        client_timings["connection_seconds"] = time.monotonic() - connection_started
        remote_started = time.monotonic()
        response = request_remote(
            config,
            "collect",
            payload=request,
            timeout=config["collection_timeout_seconds"],
        )
    finally:
        stop_process(tunnel)
    client_timings["remote_roundtrip_seconds"] = time.monotonic() - remote_started
    validation_started = time.monotonic()
    expected_contract = BUNDLE_CONTRACT_V2 if selective else BUNDLE_CONTRACT
    if response.get("contract") != expected_contract:
        raise BundleClientError("VPS collection bundle contract does not match.")
    bundle_status = response.get("status")
    if bundle_status not in {"passed", "partial"}:
        raise BundleClientError("VPS collection bundle status is invalid.")
    if selective:
        if response.get("requested_platforms") != list(requested_platforms):
            raise BundleClientError("VPS response requested platforms do not match.")
        if response.get("expected_components") != list(expected_components):
            raise BundleClientError("VPS response expected components do not match.")
        if response.get("collection_run_id") != collection_run_id:
            raise BundleClientError("VPS response collection run id does not match.")
    else:
        response = {**response, "collection_run_id": collection_run_id}
    components = validate_component_results(
        response,
        set(seed_files),
        expected_components,
    )
    members = read_bundle_members(
        decode_bundle(response),
        requested_platforms if selective else None,
        allow_empty=selective,
    )
    apply_members(root, members)
    client_timings["validation_apply_seconds"] = time.monotonic() - validation_started
    client_timings["total_seconds"] = time.monotonic() - client_started
    response["client_timings"] = client_timings
    write_component_statuses(
        root,
        response,
        components,
        windows,
        limits,
        requested_platforms,
        expected_components,
        continue_recovery,
    )
    failed_components = [
        row["id"] for row in components if row["status"] != "passed"
    ]
    cached_components = [
        row["id"]
        for row in components
        if row["data_freshness"] == "cache_preserved"
    ]
    print(f"collector_bundle_status={bundle_status}")
    print(
        "collector_bundle_failed_components="
        + (",".join(failed_components) if failed_components else "none")
    )
    print(
        "collector_bundle_cached_components="
        + (",".join(cached_components) if cached_components else "none")
    )
    print(f"collector_bundle_files={len(members)}")
    print(f"collector_bundle_bytes={int(response.get('bundle_bytes', 0))}")
    for row in components:
        print(f"collector_component_{row['id']}={row['status']}")
    return 0


def parser():
    command_parser = argparse.ArgumentParser(
        description="Operate the sanitized multi-platform VPS collector."
    )
    command_parser.add_argument("command", choices=["collect", "diagnose"])
    command_parser.add_argument("--root", type=Path, default=ROOT)
    command_parser.add_argument(
        "--output",
        default=".codex/tls-consistency.json",
        help="Workspace-relative diagnostics output path.",
    )
    command_parser.add_argument(
        "--platform",
        action="append",
        default=[],
        help="Repeat to selectively retry one or more configured platforms.",
    )
    command_parser.add_argument("--collection-run-id", default="")
    command_parser.add_argument(
        "--continue-recovery",
        action="store_true",
        help="Merge this attempt only into the previous validated recovery set.",
    )
    command_parser.add_argument(
        "--window",
        action="append",
        default=[],
        help="Repeat platform=YYYY-MM-DD for every configured platform.",
    )
    command_parser.add_argument(
        "--max-order",
        action="append",
        default=[],
        help="Repeat platform=limit for every configured platform.",
    )
    command_parser.add_argument("--payment-min-date")
    command_parser.add_argument("--payment-max-orders", type=int, default=20)
    return command_parser


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "collect":
            return collect(args)
        if args.command == "diagnose":
            return diagnose(args)
    except (BundleClientError, OSError, subprocess.SubprocessError) as exc:
        print(f"collector_bundle_status=failed")
        print(f"collector_bundle_error={str(exc)[:300]}")
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
