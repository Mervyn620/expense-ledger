import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from app.settings import ROOT


CONFIG_CONTRACT = "expense-collector-backends/v1"
STATE_CONTRACT = "expense-collector-backend-state/v1"
REMOTE_CONTRACT = "expense-collector-vps/v1"
MODES = {"auto", "local", "vps"}
REQUIRED_COLLECT_CAPABILITY = "collect_bundle_v1"
REQUIRED_CAPABILITIES = {REQUIRED_COLLECT_CAPABILITY}
HOST_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
REMOTE_STATUSES = {
    "ready",
    "setup_required",
    "login_required",
    "blocked",
    "unavailable",
    "failed",
}
COLLECTION_STATUSES = {"idle", "running", "passed", "partial", "failed"}
STORAGE_STATUSES = {"ok", "warning", "critical", "unavailable"}
COLLECTION_STAGES = {
    "idle",
    "preparing",
    "collecting",
    "stopping_browser",
    "building_bundle",
    "completed",
    "failed",
}
COLLECTION_PLATFORMS = {
    "",
    "walmart_ca",
    "amazon_ca",
    "uber_eats",
    "the_ordinary_ca",
    "fantuan",
    "tnt_ca",
}
KNOWN_COLLECTOR_PLATFORMS = COLLECTION_PLATFORMS - {""}


class CollectorBackendError(RuntimeError):
    pass


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise CollectorBackendError(f"Backend configuration was not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectorBackendError(f"Backend configuration is invalid: {exc}") from exc


def _validate_remote_path(value, field):
    path = PurePosixPath(str(value or ""))
    if not path.is_absolute() or ".." in path.parts:
        raise CollectorBackendError(f"{field} must be an absolute remote path.")
    return str(path)


def sanitize_status_message(value):
    message = str(value or "")
    message = re.sub(r"(?<!\w)(?:/[A-Za-z0-9_.-]+){2,}", "[remote-path]", message)
    message = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[host]", message)
    return message.strip()[:240]


def load_config(path):
    payload = _read_json(path)
    if not isinstance(payload, dict) or payload.get("contract") != CONFIG_CONTRACT:
        raise CollectorBackendError(
            f"Backend configuration contract must be {CONFIG_CONTRACT}."
        )
    if payload.get("default_mode") not in MODES:
        raise CollectorBackendError("Backend default_mode is invalid.")
    cache_seconds = payload.get("health_cache_seconds")
    if (
        isinstance(cache_seconds, bool)
        or not isinstance(cache_seconds, int)
        or not 0 <= cache_seconds <= 300
    ):
        raise CollectorBackendError("health_cache_seconds must be from 0 to 300.")
    local_only = payload.get("local_only_platforms", [])
    if (
        not isinstance(local_only, list)
        or any(not isinstance(item, str) for item in local_only)
        or len(local_only) != len(set(local_only))
        or set(local_only) - KNOWN_COLLECTOR_PLATFORMS
    ):
        raise CollectorBackendError("local_only_platforms is invalid.")
    payload["local_only_platforms"] = list(local_only)

    for backend_id, expected_type in (("local", "local"), ("vps", "ssh")):
        backend = payload.get(backend_id)
        if not isinstance(backend, dict):
            raise CollectorBackendError(f"Backend {backend_id} must be an object.")
        if backend.get("type") != expected_type:
            raise CollectorBackendError(f"Backend {backend_id} type is invalid.")
        if not isinstance(backend.get("enabled"), bool):
            raise CollectorBackendError(f"Backend {backend_id} enabled must be boolean.")
        if not str(backend.get("label") or "").strip():
            raise CollectorBackendError(f"Backend {backend_id} label is required.")

    remote = payload["vps"]
    if not HOST_PATTERN.fullmatch(str(remote.get("ssh_host") or "")):
        raise CollectorBackendError("VPS ssh_host is invalid.")
    remote["remote_python"] = _validate_remote_path(
        remote.get("remote_python"),
        "remote_python",
    )
    remote["remote_service"] = _validate_remote_path(
        remote.get("remote_service"),
        "remote_service",
    )
    timeout = remote.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 30:
        raise CollectorBackendError("VPS timeout_seconds must be from 1 to 30.")
    home_exit_port = remote.get("home_exit_remote_port")
    if (
        isinstance(home_exit_port, bool)
        or not isinstance(home_exit_port, int)
        or not 1024 <= home_exit_port <= 65535
    ):
        raise CollectorBackendError(
            "VPS home_exit_remote_port must be from 1024 to 65535."
        )
    collection_timeout = remote.get("collection_timeout_seconds")
    if (
        isinstance(collection_timeout, bool)
        or not isinstance(collection_timeout, int)
        or not 60 <= collection_timeout <= 3600
    ):
        raise CollectorBackendError(
            "VPS collection_timeout_seconds must be from 60 to 3600."
        )
    return payload


class CollectorBackendManager:
    def __init__(
        self,
        root=ROOT,
        config_path=None,
        state_path=None,
        local_probe=None,
        remote_probe=None,
        monotonic=None,
    ):
        self.root = Path(root).resolve()
        self.config_path = Path(
            config_path or self.root / "config" / "collector_backends.json"
        )
        self.state_path = Path(
            state_path or self.root / ".codex" / "collector-backend-state.json"
        )
        self._local_probe = local_probe or self._default_local_probe
        self._remote_probe = remote_probe or self._default_remote_probe
        self._monotonic = monotonic or time.monotonic
        self._cache = None
        self._cache_at = 0.0
        self._lock = threading.Lock()

    def selected_mode(self):
        config = load_config(self.config_path)
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return config["default_mode"]
        if (
            isinstance(payload, dict)
            and payload.get("contract") == STATE_CONTRACT
            and payload.get("selected_mode") in MODES
        ):
            return payload["selected_mode"]
        return config["default_mode"]

    def set_mode(self, mode):
        if mode not in MODES:
            raise CollectorBackendError(f"Unknown collector backend mode: {mode}")
        status = self.status(force=True, selected_mode=mode)
        if mode != "auto" and status["resolved_backend"] != mode:
            detail = status["backends"][mode]["message"]
            raise CollectorBackendError(detail or f"{mode} backend is not ready.")
        atomic_write_json(self.state_path, {
            "contract": STATE_CONTRACT,
            "selected_mode": mode,
            "updated_at": now_iso(),
        })
        return self.status(force=False, selected_mode=mode)

    def resolve(self, mode=None, force=True):
        selected = mode or self.selected_mode()
        if selected not in MODES:
            raise CollectorBackendError(f"Unknown collector backend mode: {selected}")
        status = self.status(force=force, selected_mode=selected)
        resolved = status["resolved_backend"]
        if not resolved:
            if selected == "auto":
                raise CollectorBackendError("No collector backend is ready.")
            detail = status["backends"][selected]["message"]
            raise CollectorBackendError(detail or f"{selected} backend is not ready.")
        return {
            "contract": CONFIG_CONTRACT,
            "requested_mode": selected,
            "resolved_backend": resolved,
            "local_only_platforms": status["local_only_platforms"],
            "status": "ready",
            "message": status["message"],
        }

    def status(self, force=False, selected_mode=None):
        config = load_config(self.config_path)
        selected = selected_mode or self.selected_mode()
        with self._lock:
            cache_valid = (
                not force
                and self._cache is not None
                and self._monotonic() - self._cache_at
                <= config["health_cache_seconds"]
            )
            if cache_valid:
                health = self._cache
            else:
                health = {
                    "local": self._safe_probe(
                        "local",
                        config["local"],
                        self._local_probe,
                    ),
                    "vps": self._safe_probe(
                        "vps",
                        config["vps"],
                        self._remote_probe,
                    ),
                }
                self._cache = health
                self._cache_at = self._monotonic()
        return self._build_status(config, selected, health)

    def _safe_probe(self, backend_id, config, probe):
        if not config["enabled"]:
            return {
                "status": "unavailable",
                "message": f"{config['label']}运行环境已停用",
                "capabilities": [],
            }
        try:
            payload = probe(config)
        except Exception as exc:
            return {
                "status": "unavailable",
                "message": (
                    f"{config['label']}运行环境不可用："
                    f"{sanitize_status_message(exc)}"
                ),
                "capabilities": [],
            }
        if not isinstance(payload, dict):
            return {
                "status": "failed",
                "message": f"{config['label']}运行环境返回了无效状态",
                "capabilities": [],
            }
        status = str(payload.get("status") or "failed")
        if status not in REMOTE_STATUSES:
            status = "failed"
        capabilities = payload.get("capabilities")
        if not isinstance(capabilities, list):
            capabilities = []
        collection = payload.get("collection")
        safe_collection = None
        if isinstance(collection, dict):
            collection_status = str(collection.get("status") or "idle")
            collection_stage = str(collection.get("stage") or "idle")
            collection_platform = str(collection.get("platform") or "")
            completed = collection.get("components_completed", 0)
            total = collection.get("components_total", 8)
            if (
                collection_status in COLLECTION_STATUSES
                and collection_stage in COLLECTION_STAGES
                and collection_platform in COLLECTION_PLATFORMS
                and isinstance(completed, int)
                and not isinstance(completed, bool)
                and 0 <= completed <= 8
                and isinstance(total, int)
                and not isinstance(total, bool)
                and 1 <= total <= 8
                and completed <= total
            ):
                safe_collection = {
                    "status": collection_status,
                    "stage": collection_stage,
                    "platform": collection_platform,
                    "components_completed": completed,
                    "components_total": total,
                }
        storage = payload.get("storage")
        safe_storage = None
        if isinstance(storage, dict):
            storage_status = str(storage.get("status") or "")
            if storage_status == "unavailable":
                safe_storage = {"status": storage_status}
            else:
                used_percent = storage.get("used_percent")
                free_bytes = storage.get("free_bytes")
                if (
                    storage_status in STORAGE_STATUSES
                    and isinstance(used_percent, (int, float))
                    and not isinstance(used_percent, bool)
                    and 0 <= used_percent <= 100
                    and isinstance(free_bytes, int)
                    and not isinstance(free_bytes, bool)
                    and 0 <= free_bytes <= 2**63 - 1
                ):
                    safe_storage = {
                        "status": storage_status,
                        "used_percent": round(float(used_percent), 1),
                        "free_bytes": free_bytes,
                    }
        return {
            "status": status,
            "message": sanitize_status_message(payload.get("message")),
            "capabilities": [
                str(item) for item in capabilities
                if isinstance(item, str)
            ][:10],
            "browser_status": str(payload.get("browser_status") or ""),
            "collection": safe_collection,
            "storage": safe_storage,
        }

    @staticmethod
    def _is_ready(payload):
        return (
            payload.get("status") == "ready"
            and REQUIRED_CAPABILITIES.issubset(
                set(payload.get("capabilities", []))
            )
        )

    def _build_status(self, config, selected, health):
        local_ready = self._is_ready(health["local"])
        vps_ready = self._is_ready(health["vps"])
        if selected == "auto":
            resolved = "vps" if vps_ready else "local" if local_ready else None
        elif selected == "local":
            resolved = "local" if local_ready else None
        else:
            resolved = "vps" if vps_ready else None

        if selected == "auto" and resolved == "local" and not vps_ready:
            message = "VPS尚未就绪，当前自动使用本地运行环境"
        elif resolved == "vps" and config["local_only_platforms"]:
            message = "订单采集将使用VPS运行环境；指定平台仍使用本地专用浏览器"
        elif resolved:
            message = (
                f"订单采集将使用"
                f"{config[resolved]['label']}运行环境"
            )
        else:
            message = "所选运行环境当前不可用"

        return {
            "contract": CONFIG_CONTRACT,
            "selected_mode": selected,
            "resolved_backend": resolved,
            "message": message,
            "updated_at": now_iso(),
            "local_only_platforms": list(config["local_only_platforms"]),
            "backends": {
                backend_id: {
                    "label": config[backend_id]["label"],
                    "status": health[backend_id]["status"],
                    "message": health[backend_id]["message"],
                    "capabilities": health[backend_id]["capabilities"],
                    "browser_status": health[backend_id].get("browser_status", ""),
                    "collection": health[backend_id].get("collection"),
                    "storage": health[backend_id].get("storage"),
                    "selectable": self._is_ready(health[backend_id]),
                }
                for backend_id in ("local", "vps")
            },
        }

    def _default_local_probe(self, _config):
        required = [
            self.root / "config" / "collectors.json",
            self.root / "scripts" / "collectors" / "runner.py",
            self.root / "scripts" / "start-shop-browser.ps1",
        ]
        missing = [path.name for path in required if not path.is_file()]
        node = shutil.which("node")
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if missing or not node or not powershell:
            details = []
            if missing:
                details.append(f"缺少 {', '.join(missing)}")
            if not node:
                details.append("缺少 Node.js")
            if not powershell:
                details.append("缺少 PowerShell")
            return {
                "status": "setup_required",
                "message": "；".join(details),
                "capabilities": [],
                "browser_status": "unknown",
            }

        browser_status = "stopped"
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:9223/json/version",
                timeout=0.4,
            ):
                browser_status = "running"
        except (OSError, urllib.error.URLError):
            pass
        return {
            "status": "ready",
            "message": (
                "本地订单采集浏览器正在运行"
                if browser_status == "running"
                else "本地订单采集环境可用；任务开始时会启动专用浏览器"
            ),
            "capabilities": sorted(REQUIRED_CAPABILITIES),
            "browser_status": browser_status,
        }

    @staticmethod
    def _ssh_status(config, python_key, service_key, expected_contract):
        ssh = shutil.which("ssh")
        if not ssh:
            raise CollectorBackendError("OpenSSH client was not found.")
        command = [
            ssh,
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={config['timeout_seconds']}",
            config["ssh_host"],
            config[python_key],
            config[service_key],
            "status",
        ]
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=config["timeout_seconds"] + 2,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "SSH status failed").strip()
            raise CollectorBackendError(message[-180:])
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            raise CollectorBackendError("VPS returned an empty status.")
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise CollectorBackendError("VPS returned invalid JSON.") from exc
        if payload.get("contract") != expected_contract:
            raise CollectorBackendError("VPS service contract does not match.")
        return payload

    @staticmethod
    def _default_remote_probe(config):
        return CollectorBackendManager._ssh_status(
            config,
            "remote_python",
            "remote_service",
            REMOTE_CONTRACT,
        )
