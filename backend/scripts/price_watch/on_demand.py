import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import client as price_client


ROOT = Path(__file__).resolve().parents[2]
STATUS_RELATIVE_PATH = (
    Path(".codex") / "price-watch-on-demand-status.json"
)
LOCAL_HEALTH_RELATIVE_PATH = (
    Path(".codex") / "price-watch-local-health.json"
)
LOCAL_PROGRESS_RELATIVE_PATH = (
    Path(".codex") / "price-watch-local-progress.json"
)
LOCAL_DEALS_RELATIVE_PATH = Path("data") / "walmart_deals_local.json"
LOCAL_REQUEST_CONTRACT = "expense-price-watch-local-request/v1"
BACKENDS = {"local", "vps"}


class OnDemandError(RuntimeError):
    pass


def write_status(root, status, phase, message="", **values):
    payload = {
        "contract": price_client.CONTRACT,
        "status": status,
        "phase": phase,
        "updated_at": price_client.utc_now(),
        "message": str(message)[:300],
        **values,
    }
    price_client.atomic_write_json(
        Path(root) / STATUS_RELATIVE_PATH,
        payload,
    )
    return payload


def start_home_exit_tunnel(config):
    ssh = shutil.which("ssh.exe") or shutil.which("ssh")
    if not ssh:
        raise OnDemandError("OpenSSH client was not found on this computer.")
    port = config["home_exit_remote_port"]
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
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "LogLevel=ERROR",
        "-R",
        f"127.0.0.1:{port}",
        config["ssh_host"],
    ]
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def stop_tunnel(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def wait_for_home_exit(config, tunnel, sleep=time.sleep):
    proxy = f"socks5://127.0.0.1:{config['home_exit_remote_port']}"
    deadline = time.monotonic() + config["home_exit_ready_timeout_seconds"]
    last_error = None
    while time.monotonic() < deadline:
        if tunnel.poll() is not None:
            raise OnDemandError(
                "The home-exit tunnel stopped before it became ready."
            )
        try:
            response = price_client.ssh_request_json(
                config,
                "probe",
                payload={
                    "contract": price_client.CONTRACT,
                    "proxy": proxy,
                },
                timeout=15,
            )
            if (
                response.get("status") == "ready"
                and response.get("route") == "home_exit"
            ):
                return proxy
        except price_client.PriceWatchError as exc:
            last_error = exc
        sleep(1)
    raise OnDemandError(
        "The VPS could not reach Walmart through the home-exit tunnel."
    ) from last_error


def local_browser_ready(port=9223):
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version",
            timeout=0.5,
        ):
            return True
    except (OSError, urllib.error.URLError):
        return False


def ensure_local_browser(root, port=9223, sleep=time.sleep):
    if local_browser_ready(port):
        return
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    script = Path(root) / "scripts" / "start-shop-browser.ps1"
    if not powershell or not script.is_file():
        raise OnDemandError("The local dedicated shop browser is unavailable.")
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Platform",
            "walmart_ca",
            "-Port",
            str(port),
            "-NoOpen",
        ],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise OnDemandError("The local dedicated shop browser could not start.")
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if local_browser_ready(port):
            return
        sleep(0.5)
    raise OnDemandError("The local dedicated shop browser did not become ready.")


def _parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def local_cooldown_result(root):
    health = price_client.read_json(
        Path(root) / LOCAL_HEALTH_RELATIVE_PATH,
        default={},
    ) or {}
    next_retry = _parse_time(health.get("next_retry_at"))
    if (
        health.get("strategy") == "my_items"
        and
        health.get("status") == "blocked"
        and next_retry
        and datetime.now(timezone.utc) < next_retry
    ):
        return {
            "contract": price_client.CONTRACT,
            "generated_at": price_client.utc_now(),
            "cursor": "",
            "store_id": "",
            "route": "local",
            "collector_status": "cooldown",
            "checked": 0,
            "deals": [],
            "checks": [],
            "next_retry_at": next_retry.replace(microsecond=0).isoformat(),
        }
    return None


def write_local_health(root, payload):
    completed_at = price_client.utc_now()
    status = str(payload.get("collector_status") or "failed")
    result = {
        "contract": price_client.CONTRACT,
        "status": status,
        "strategy": "my_items",
        "completed_at": completed_at,
    }
    if status == "blocked":
        result["next_retry_at"] = (
            datetime.now(timezone.utc) + timedelta(hours=6)
        ).replace(microsecond=0).isoformat()
    price_client.atomic_write_json(
        Path(root) / LOCAL_HEALTH_RELATIVE_PATH,
        result,
    )


def run_local_collector(root, config, check_limit, port=9223):
    cooldown = local_cooldown_result(root)
    if cooldown:
        cooldown["store_id"] = config["store_id"]
        return cooldown
    ensure_local_browser(root, port=port)
    node = shutil.which("node")
    script = Path(root) / "scripts" / "price_watch" / "local_collector.mjs"
    if not node or not script.is_file():
        raise OnDemandError("The local price-watch collector is unavailable.")
    request = {
        "contract": LOCAL_REQUEST_CONTRACT,
        "port": port,
        "store_id": config["store_id"],
        "store_name": config["store_name"],
        "settle_ms": 8000,
        "max_scrolls": 12,
    }
    result = subprocess.run(
        [node, str(script)],
        input=json.dumps(request, ensure_ascii=False),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=config["collection_timeout_seconds"],
        check=False,
    )
    if result.returncode != 0:
        raise OnDemandError("The local Walmart price collector failed.")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise OnDemandError("The local price collector returned no result.")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise OnDemandError(
            "The local price collector returned invalid JSON."
        ) from exc
    if (
        payload.get("contract") != price_client.CONTRACT
        or payload.get("store_id") != config["store_id"]
        or payload.get("route") != "local"
    ):
        raise OnDemandError("The local price collector contract is invalid.")
    write_local_health(root, payload)
    return payload


def emit_metrics(status, backend, collected=None, received=None):
    collected = collected or {}
    received = received or {}
    values = {
        "price_watch_status": status,
        "price_watch_backend": backend,
        "price_watch_route": collected.get(
            "route",
            "local" if backend == "local" else "home_exit",
        ),
        "price_watch_checked": int(collected.get("checked", 0) or 0),
        "price_watch_deals": int(received.get("deals", 0) or 0),
        "price_watch_checks": int(received.get("checks", 0) or 0),
        "price_watch_collector_status": (
            collected.get("status")
            if collected.get("status") == "queued"
            else received.get(
                "collector_status",
                collected.get(
                    "collector_status",
                    collected.get("status", status),
                ),
            )
        ),
    }
    for key, value in values.items():
        print(f"{key}={value}")


def _result_message(backend, collector_status, received):
    if collector_status == "queued" and backend == "vps":
        return (
            "VPS My Items 检查已排队；完成后同步即可显示结果"
        )
    if collector_status == "completed":
        backend_label = "本地" if backend == "local" else "VPS"
        checked = int(received.get("checked", received.get("checks", 0)) or 0)
        return (
            f"{backend_label}检查完成：{checked} 个商品，"
            f"{received['deals']} 个折扣"
        )
    generic_failure = (
        "本地商品检查失败"
        if backend == "local"
        else "VPS 商品检查失败"
    )
    return {
        "home_exit_offline": "家庭网络出口已断开，未使用 VPS 直连",
        "blocked": "Walmart 阻止了本次检查，请稍后再试",
        "cooldown": "Walmart 检查处于冷却期，请稍后再试",
        "login_required": "Walmart 专用浏览器需要重新登录",
        "wrong_store": "页面未确认 Square One 门店，未保存价格",
        "partial": "只完成了部分商品检查",
        "failed": generic_failure,
    }.get(collector_status, "Walmart 商品检查未完成")


def _run_vps(root, config, check_limit, probe_only):
    if probe_only:
        remote = price_client.ssh_request_json(config, "status", timeout=30)
        if remote.get("status") != "ok":
            raise OnDemandError("VPS price-watch service is not ready.")
        return None, write_status(
            root,
            "passed",
            "probe_complete",
            "VPS 折扣检查服务已就绪",
            backend="vps",
            route="home_exit",
        )
    write_status(
        root,
        "running",
        "syncing",
        "正在向 VPS 同步新增商品",
        backend="vps",
        route="home_exit",
    )
    price_client.sync_products(root)
    tunnel = start_home_exit_tunnel(config)
    write_status(
        root,
        "running",
        "preparing_home_exit",
        "正在建立临时家庭网络出口",
        backend="vps",
        route="home_exit",
    )
    try:
        proxy = wait_for_home_exit(config, tunnel)
        write_status(
            root,
            "running",
            "collecting",
            "VPS 正通过家庭网络读取 Walmart My Items 页面",
            backend="vps",
            route="home_exit",
            collector_status="running",
        )
        run_summary = price_client.ssh_request_json(
            config,
            "run",
            payload={
                "contract": price_client.CONTRACT,
                "store_id": config["store_id"],
                "store_name": config["store_name"],
                "proxy": proxy,
            },
            timeout=config["collection_timeout_seconds"],
        )
        collected = price_client.ssh_request_json(
            config,
            "deals",
            timeout=30,
        )
        collector_status = str(collected.get("collector_status", ""))
        if collector_status != str(run_summary.get("status", "")):
            raise OnDemandError(
                "VPS returned a result that does not match the completed run."
            )
        write_status(
            root,
            "running",
            "receiving",
            "正在保存 VPS 本次检查结果",
            backend="vps",
            route="home_exit",
            collector_status=collector_status,
        )
        received = price_client.persist_deals_payload(
            root,
            collected,
            merge_existing=collector_status != "completed",
        )
        return collected, received
    finally:
        stop_tunnel(tunnel)


def _run_local(root, config, check_limit, probe_only):
    write_status(
        root,
        "running",
        "preparing_local",
        "正在准备本地专用浏览器",
        backend="local",
        route="local",
    )
    if probe_only:
        ensure_local_browser(root)
        return None, write_status(
            root,
            "passed",
            "probe_complete",
            "本地专用浏览器已就绪",
            backend="local",
            route="local",
        )
    write_status(
        root,
        "running",
        "collecting",
        "本地专用浏览器正在检查 Walmart",
        backend="local",
        route="local",
    )
    collected = run_local_collector(root, config, check_limit)
    write_status(
        root,
        "running",
        "receiving",
        "正在验证本地价格结果",
        backend="local",
        route="local",
        collector_status=str(collected.get("collector_status", "")),
    )
    received = price_client.persist_deals_payload(
        root,
        collected,
        merge_existing=(
            str(collected.get("collector_status")) != "completed"
        ),
        output_relative_path=LOCAL_DEALS_RELATIVE_PATH,
        update_receive_state=False,
    )
    return collected, received


def run(root=ROOT, probe_only=False, limit=None, backend="local"):
    root = Path(root).resolve()
    config = price_client.load_config(root)
    if not config["enabled"]:
        raise OnDemandError("Price watch is not enabled.")
    if backend not in BACKENDS:
        raise OnDemandError("Price-watch backend must be local or vps.")
    if backend == "vps" and config["transport"] != "ssh":
        raise OnDemandError("SSH price watch is not enabled.")
    if limit is not None:
        if isinstance(limit, bool) or not 1 <= int(limit) <= 5000:
            raise OnDemandError("Diagnostic limit must be from 1 to 5000.")
        check_limit = int(limit)
    else:
        check_limit = config["check_limit"]
    collected = None
    received = None
    try:
        if backend == "local":
            collected, received = _run_local(
                root,
                config,
                check_limit,
                probe_only,
            )
        else:
            collected, received = _run_vps(
                root,
                config,
                check_limit,
                probe_only,
            )
        if probe_only:
            emit_metrics("ready", backend)
            return received
        collector_status = str(
            collected.get(
                "collector_status",
                collected.get("status", "failed"),
            )
        )
        success = collector_status in {"completed", "queued"}
        message = _result_message(backend, collector_status, received)
        result = write_status(
            root,
            "passed" if success else "failed",
            "complete",
            message,
            backend=backend,
            route=collected.get(
                "route",
                "local" if backend == "local" else "direct",
            ),
            collector_status=collector_status,
            checked=int(collected.get("checked", 0) or 0),
            deals=int(received.get("deals", 0) or 0),
            checks=int(received.get("checks", 0) or 0),
        )
        emit_metrics(result["status"], backend, collected, received)
        return result
    except (
        OnDemandError,
        price_client.PriceWatchError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        write_status(
            root,
            "failed",
            "failed",
            str(exc),
            backend=backend,
            route="local" if backend == "local" else "home_exit",
        )
        emit_metrics("failed", backend, collected, received)
        raise OnDemandError(str(exc)) from exc


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run local Walmart price watch in the dedicated shop browser."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--backend",
        choices=["local"],
        default="local",
    )
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--limit", type=int)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        result = run(
            args.root,
            probe_only=args.probe_only,
            limit=args.limit,
            backend=args.backend,
        )
    except OnDemandError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.probe_only:
        print(result["message"])
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
