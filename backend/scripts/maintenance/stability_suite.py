import argparse
import ctypes
import http.client
import json
import math
import os
import random
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "expenses.sqlite"
STATE_PATH = ROOT / ".codex" / "expense-app-state.json"
STATUS_PATH = ROOT / "exports" / "current" / "stability-status.json"
STATUS_MARKDOWN_PATH = ROOT / "exports" / "current" / "stability-status.md"
HISTORY_PATH = ROOT / ".codex" / "stability-history.json"
MAX_HISTORY = 100
REQUIRED_TABLES = {
    "cards",
    "orders",
    "order_items",
    "order_payments",
    "payment_evidence",
    "bank_accounts",
    "bank_transactions",
    "bank_reconciliations",
}
REQUIRED_VIEWS = {
    "powerbi_orders",
    "powerbi_order_items",
    "powerbi_monthly_summary",
    "powerbi_payment_review",
    "powerbi_bank_reconciliation",
}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def percentile(values, percent):
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(0, math.ceil((percent / 100) * len(ordered)) - 1)
    return ordered[index]


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_reports(payload):
    atomic_write(
        STATUS_PATH,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    checks = payload.get("checks", {})
    load = checks.get("http_load", {})
    regression = checks.get("regression", {})
    lines = [
        "# Expense Stability Status",
        "",
        f"- Status: {payload.get('status', 'unknown')}",
        f"- Completed: {payload.get('completed_at', '')}",
        f"- Duration: {payload.get('duration_seconds', 0)} seconds",
        f"- Git commit: {payload.get('git_commit', 'unknown')}",
        f"- Python tests: {regression.get('python_tests', {}).get('summary', 'unknown')}",
        f"- Browser policy tests: {regression.get('node_tests', {}).get('summary', 'unknown')}",
        f"- Ledger validation: {regression.get('ledger_validation', {}).get('status', 'unknown')}",
        f"- Read requests: {load.get('requests', 0)}",
        f"- Errors: {load.get('errors', 0)}",
        f"- Throughput: {load.get('requests_per_second', 0)} requests/second",
        f"- P95 latency: {load.get('p95_ms', 0)} ms",
        f"- Memory growth: {load.get('memory_growth_mb', 0)} MB",
        "",
        "## Failures",
        "",
    ]
    failures = payload.get("failures", [])
    lines.extend(f"- {failure}" for failure in failures)
    if not failures:
        lines.append("- None")
    lines.extend(["", "## Warnings", ""])
    warnings = payload.get("warnings", [])
    lines.extend(f"- {warning}" for warning in warnings)
    if not warnings:
        lines.append("- None")
    atomic_write(STATUS_MARKDOWN_PATH, "\n".join(lines) + "\n")

    if not payload.get("completed_at"):
        return
    history = []
    if HISTORY_PATH.exists():
        try:
            existing = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if isinstance(existing, list):
                history = existing
        except (OSError, json.JSONDecodeError):
            history = []
    history.append({
        "status": payload.get("status"),
        "completed_at": payload.get("completed_at"),
        "duration_seconds": payload.get("duration_seconds"),
        "git_commit": payload.get("git_commit"),
        "failures": payload.get("failures", []),
        "warnings": payload.get("warnings", []),
        "http_load": load,
    })
    atomic_write(
        HISTORY_PATH,
        json.dumps(history[-MAX_HISTORY:], ensure_ascii=False, indent=2) + "\n",
    )


def read_history():
    if not HISTORY_PATH.exists():
        return []
    try:
        payload = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def compare_with_history(load, history):
    prior = [
        row["http_load"]
        for row in history[-10:]
        if row.get("status") == "passed"
        and isinstance(row.get("http_load"), dict)
        and row["http_load"].get("p95_ms") is not None
        and row["http_load"].get("requests_per_second") is not None
    ]
    if len(prior) < 3:
        return {"sample_count": len(prior)}, []
    baseline_p95 = statistics.median(row["p95_ms"] for row in prior)
    baseline_rps = statistics.median(
        row["requests_per_second"]
        for row in prior
    )
    warnings = []
    if load["p95_ms"] > max(250, baseline_p95 * 2):
        warnings.append(
            f"HTTP P95 regressed from {baseline_p95:.2f} to "
            f"{load['p95_ms']:.2f} ms."
        )
    if load["requests_per_second"] < max(20, baseline_rps * 0.5):
        warnings.append(
            f"HTTP throughput regressed from {baseline_rps:.2f} to "
            f"{load['requests_per_second']:.2f} requests/second."
        )
    return {
        "sample_count": len(prior),
        "median_p95_ms": round(baseline_p95, 2),
        "median_requests_per_second": round(baseline_rps, 2),
    }, warnings


def run_command(name, command, timeout=120):
    started = time.perf_counter()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            check=False,
        )
        output = "\n".join(
            part.strip()
            for part in (result.stdout, result.stderr)
            if part and part.strip()
        )
        return {
            "name": name,
            "status": "passed" if result.returncode == 0 else "failed",
            "exit_code": result.returncode,
            "duration_seconds": round(time.perf_counter() - started, 2),
            "summary": summarize_test_output(output),
            "output_tail": output[-2500:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "status": "failed",
            "exit_code": -1,
            "duration_seconds": round(time.perf_counter() - started, 2),
            "summary": f"timed out after {timeout} seconds",
            "output_tail": str(exc)[-2500:],
        }


def summarize_test_output(output):
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped.startswith("Ran ") and " test" in stripped:
            return stripped
        if stripped.startswith("# tests "):
            return stripped.removeprefix("# ")
        if stripped.startswith("safety_backup_valid="):
            return "validation passed"
    return "completed" if output else "no output"


def git_commit():
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def read_json_url(url, timeout=5):
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        return exc.code, payload
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Local HTTP request failed: {exc}") from exc


def discover_base_url():
    candidates = []
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8-sig"))
            if state.get("url"):
                candidates.append(str(state["url"]).rstrip("/"))
        except (OSError, json.JSONDecodeError):
            pass
    candidates.extend(f"http://127.0.0.1:{port}" for port in range(8765, 8775))
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            status, payload = read_json_url(f"{candidate}/api/health", timeout=2)
            if status == 200 and payload.get("app") == "expense-ledger":
                return candidate, payload
        except RuntimeError:
            continue
    raise RuntimeError("Expense app health endpoint was not found.")


def database_checks():
    if not DB_PATH.exists():
        raise RuntimeError(f"Ledger database does not exist: {DB_PATH}")
    stat = DB_PATH.stat()
    uri = f"{DB_PATH.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        views = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'view'"
            )
        }
        metrics = {
            "orders": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
            "items": conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0],
            "payments": conn.execute(
                "SELECT COUNT(*) FROM order_payments"
            ).fetchone()[0],
            "order_duplicates": conn.execute(
                """
                SELECT COUNT(*) FROM (
                  SELECT platform, order_id
                  FROM orders
                  GROUP BY platform, order_id
                  HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0],
            "item_duplicates": conn.execute(
                """
                SELECT COUNT(*) FROM (
                  SELECT order_pk, line_key
                  FROM order_items
                  GROUP BY order_pk, line_key
                  HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0],
            "orphan_items": conn.execute(
                """
                SELECT COUNT(*)
                FROM order_items i
                LEFT JOIN orders o ON o.id = i.order_pk
                WHERE o.id IS NULL
                """
            ).fetchone()[0],
            "orphan_payments": conn.execute(
                """
                SELECT COUNT(*)
                FROM order_payments p
                LEFT JOIN orders o ON o.id = p.order_pk
                WHERE o.id IS NULL
                """
            ).fetchone()[0],
            "negative_expenses": conn.execute(
                "SELECT COUNT(*) FROM orders WHERE total < 0"
            ).fetchone()[0],
            "total": round(
                float(
                    conn.execute(
                        "SELECT COALESCE(SUM(total), 0) FROM orders"
                    ).fetchone()[0]
                ),
                2,
            ),
        }
    failures = []
    if integrity != ["ok"]:
        failures.append(f"SQLite integrity check failed: {integrity[:3]}")
    if foreign_keys:
        failures.append(f"SQLite foreign key violations: {len(foreign_keys)}")
    missing_tables = sorted(REQUIRED_TABLES - tables)
    missing_views = sorted(REQUIRED_VIEWS - views)
    if missing_tables:
        failures.append(f"Missing SQLite tables: {', '.join(missing_tables)}")
    if missing_views:
        failures.append(f"Missing SQLite views: {', '.join(missing_views)}")
    for name in (
        "order_duplicates",
        "item_duplicates",
        "orphan_items",
        "orphan_payments",
        "negative_expenses",
    ):
        if metrics[name]:
            failures.append(f"{name}={metrics[name]}")
    return {
        "status": "passed" if not failures else "failed",
        "integrity": integrity[0] if integrity else "missing",
        "foreign_key_violations": len(foreign_keys),
        "missing_tables": missing_tables,
        "missing_views": missing_views,
        "metrics": metrics,
        "file_size": stat.st_size,
        "file_mtime_ns": stat.st_mtime_ns,
        "failures": failures,
    }


def hostile_host_status(base_url):
    parsed = urlparse(base_url)
    connection = http.client.HTTPConnection(
        parsed.hostname,
        parsed.port,
        timeout=5,
    )
    try:
        connection.putrequest("GET", "/api/health", skip_host=True)
        connection.putheader("Host", "attacker.example")
        connection.endheaders()
        return connection.getresponse().status
    finally:
        connection.close()


def functional_http_checks(base_url):
    checked = []
    failures = []

    def expect(path, expected=200):
        status, payload = read_json_url(f"{base_url}{path}")
        checked.append({"path": path, "status": status})
        if status != expected:
            failures.append(f"{path} returned {status}, expected {expected}")
        return payload

    health = expect("/api/health")
    filters = expect("/api/filters")
    expect("/api/overview?month=latest")
    orders = expect("/api/orders?month=all&limit=20")
    expect("/api/jobs")
    expect("/api/analysis/status")
    expect("/api/overview?month=invalid", 400)
    expect("/api/orders/999999999", 404)
    order_rows = orders.get("rows", []) if isinstance(orders, dict) else []
    for row in order_rows[:3]:
        expect(f"/api/orders/{row['order_pk']}")

    root_request = Request(base_url)
    with urlopen(root_request, timeout=5) as response:
        security_headers = {
            "content_security_policy": response.headers.get(
                "Content-Security-Policy",
                "",
            ),
            "frame_options": response.headers.get("X-Frame-Options", ""),
            "content_type_options": response.headers.get(
                "X-Content-Type-Options",
                "",
            ),
        }
    if "default-src 'self'" not in security_headers["content_security_policy"]:
        failures.append("Main page is missing the restrictive content security policy.")
    if security_headers["frame_options"] != "DENY":
        failures.append("Main page X-Frame-Options is not DENY.")
    if security_headers["content_type_options"] != "nosniff":
        failures.append("Main page X-Content-Type-Options is not nosniff.")
    hostile_status = hostile_host_status(base_url)
    if hostile_status != 400:
        failures.append(
            f"Host-header protection returned {hostile_status}, expected 400."
        )
    if health.get("status") != "ok":
        failures.append(f"Application health is {health.get('status')}.")
    if not filters.get("months"):
        failures.append("Ledger filters contain no months.")
    return {
        "status": "passed" if not failures else "failed",
        "checked_endpoints": checked,
        "host_header_status": hostile_status,
        "security_headers": security_headers,
        "failures": failures,
        "health": health,
        "order_ids": [row["order_pk"] for row in order_rows],
        "months": filters.get("months", []),
    }


def process_memory_bytes(pid):
    if os.name == "nt":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        open_process = ctypes.windll.kernel32.OpenProcess
        open_process.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        open_process.restype = ctypes.c_void_p
        close_handle = ctypes.windll.kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
        get_memory.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulong,
        ]
        get_memory.restype = ctypes.c_int

        process = open_process(0x1000, False, int(pid))
        if not process:
            return None
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        try:
            success = get_memory(
                process,
                ctypes.byref(counters),
                counters.cb,
            )
            return int(counters.WorkingSetSize) if success else None
        finally:
            close_handle(process)
    status_path = Path(f"/proc/{pid}/status")
    if status_path.exists():
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    return None


def run_http_load(base_url, paths, duration_seconds, workers):
    deadline = time.monotonic() + duration_seconds

    def worker(worker_id):
        generator = random.Random(worker_id + 20260628)
        latencies = []
        errors = []
        requests = 0
        while time.monotonic() < deadline:
            path = generator.choice(paths)
            started = time.perf_counter()
            try:
                status, _payload = read_json_url(
                    f"{base_url}{path}",
                    timeout=5,
                )
                if status != 200:
                    errors.append(f"{path}: HTTP {status}")
            except Exception as exc:
                errors.append(f"{path}: {type(exc).__name__}")
            latencies.append((time.perf_counter() - started) * 1000)
            requests += 1
        return requests, latencies, errors

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(worker, range(workers)))
    elapsed = time.perf_counter() - started
    request_count = sum(result[0] for result in results)
    latencies = [
        latency
        for result in results
        for latency in result[1]
    ]
    errors = [
        error
        for result in results
        for error in result[2]
    ]
    return {
        "requests": request_count,
        "errors": len(errors),
        "error_samples": errors[:10],
        "duration_seconds": round(elapsed, 2),
        "requests_per_second": round(request_count / max(elapsed, 0.001), 2),
        "average_ms": round(sum(latencies) / max(len(latencies), 1), 2),
        "p95_ms": round(percentile(latencies, 95), 2),
        "max_ms": round(max(latencies, default=0), 2),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run non-destructive expense app stability checks."
    )
    parser.add_argument("--base-url", default="")
    parser.add_argument("--duration-seconds", type=int, default=20)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)
    args.duration_seconds = max(5, min(args.duration_seconds, 120))
    args.workers = max(1, min(args.workers, 20))

    started_at = utc_now()
    started = time.perf_counter()
    failures = []
    warnings = []
    checks = {}
    payload = {
        "status": "running",
        "started_at": started_at,
        "completed_at": None,
        "duration_seconds": 0,
        "git_commit": git_commit(),
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
    }
    write_reports(payload)
    try:
        base_url, initial_health = (
            (args.base_url.rstrip("/"), read_json_url(
                f"{args.base_url.rstrip('/')}/api/health"
            )[1])
            if args.base_url
            else discover_base_url()
        )
        checks["app"] = {
            "status": "passed" if initial_health.get("status") == "ok" else "failed",
            "base_url": base_url,
            "health": initial_health,
        }
        if checks["app"]["status"] != "passed":
            failures.append(f"Application health is {initial_health.get('status')}.")

        database = database_checks()
        checks["database"] = database
        failures.extend(database["failures"])

        node = shutil.which("node")
        node_tests = sorted(str(path) for path in (ROOT / "tests_js").glob("*.test.mjs"))
        regression = {
            "python_tests": run_command(
                "python tests",
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-p",
                    "test_*.py",
                ],
            ),
            "node_tests": (
                run_command("node tests", [node, "--test", *node_tests])
                if node and node_tests
                else {
                    "name": "node tests",
                    "status": "failed",
                    "exit_code": -1,
                    "duration_seconds": 0,
                    "summary": "Node.js or tests were not found",
                    "output_tail": "",
                }
            ),
            "ledger_validation": run_command(
                "ledger validation",
                [
                    sys.executable,
                    str(ROOT / "scripts" / "maintenance" / "validate_all.py"),
                ],
            ),
        }
        checks["regression"] = regression
        for stage in regression.values():
            if stage["status"] != "passed":
                failures.append(f"{stage['name']} failed: {stage['summary']}")

        functional = functional_http_checks(base_url)
        checks["http_functional"] = {
            key: value
            for key, value in functional.items()
            if key not in {"order_ids", "months"}
        }
        failures.extend(functional["failures"])

        paths = [
            "/api/health",
            "/api/filters",
            "/api/jobs",
            "/api/analysis/status",
            "/api/overview?month=latest",
            "/api/orders?month=all&limit=20",
        ]
        paths.extend(
            f"/api/overview?month={month}"
            for month in functional["months"][:4]
        )
        paths.extend(
            f"/api/orders/{order_pk}"
            for order_pk in functional["order_ids"][:10]
        )
        pid = initial_health.get("pid")
        memory_before = process_memory_bytes(pid) if pid else None
        load = run_http_load(
            base_url,
            paths,
            args.duration_seconds,
            args.workers,
        )
        memory_after = process_memory_bytes(pid) if pid else None
        if memory_before is not None and memory_after is not None:
            load["memory_before_mb"] = round(memory_before / 1048576, 2)
            load["memory_after_mb"] = round(memory_after / 1048576, 2)
            load["memory_growth_mb"] = round(
                (memory_after - memory_before) / 1048576,
                2,
            )
        else:
            load["memory_before_mb"] = None
            load["memory_after_mb"] = None
            load["memory_growth_mb"] = None
            warnings.append("Could not measure application process memory.")

        load_failures = []
        if load["errors"]:
            load_failures.append(f"HTTP load errors={load['errors']}")
        if load["requests_per_second"] < 2:
            load_failures.append(
                f"HTTP throughput too low: {load['requests_per_second']} requests/second"
            )
        if load["p95_ms"] > 1500:
            load_failures.append(f"HTTP P95 latency too high: {load['p95_ms']} ms")
        if load["max_ms"] > 5000:
            load_failures.append(f"HTTP maximum latency too high: {load['max_ms']} ms")
        if (
            load["memory_growth_mb"] is not None
            and load["memory_growth_mb"] > 64
        ):
            load_failures.append(
                f"Application memory grew by {load['memory_growth_mb']} MB"
            )
        if load["p95_ms"] > 500 and load["p95_ms"] <= 1500:
            warnings.append(f"HTTP P95 latency is elevated: {load['p95_ms']} ms")
        load["baseline"], history_warnings = compare_with_history(
            load,
            read_history(),
        )
        warnings.extend(history_warnings)

        final_status, final_health = read_json_url(f"{base_url}/api/health")
        if final_status != 200 or final_health.get("status") != "ok":
            load_failures.append("Application health failed after HTTP load.")
        final_db_stat = DB_PATH.stat()
        db_unchanged = (
            final_db_stat.st_size == database["file_size"]
            and final_db_stat.st_mtime_ns == database["file_mtime_ns"]
        )
        load["database_unchanged"] = db_unchanged
        if not db_unchanged:
            load_failures.append("Read-only stability test changed the SQLite file.")
        load["status"] = "passed" if not load_failures else "failed"
        load["failures"] = load_failures
        checks["http_load"] = load
        failures.extend(load_failures)
    except Exception as exc:
        failures.append(f"Stability suite error: {type(exc).__name__}: {exc}")

    payload["status"] = "passed" if not failures else "failed"
    payload["completed_at"] = utc_now()
    payload["duration_seconds"] = round(time.perf_counter() - started, 2)
    write_reports(payload)
    print(f"stability_status={payload['status']}")
    print(f"stability_failures={len(failures)}")
    print(f"stability_warnings={len(warnings)}")
    if "http_load" in checks:
        print(f"stability_requests={checks['http_load'].get('requests', 0)}")
        print(f"stability_p95_ms={checks['http_load'].get('p95_ms', 0)}")
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
