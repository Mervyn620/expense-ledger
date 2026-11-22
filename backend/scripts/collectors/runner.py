import argparse
import json
import os
import re
import shutil
import string
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "config" / "collectors.json"
WORKSPACE_ENV = "EXPENSE_COLLECTOR_WORKSPACE_ROOT"
CONTRACT = "expense-collector/v1"
ALLOWED_RUNTIMES = {"node", "python"}
ALLOWED_ROLES = {"orders"}
ALLOWED_CAPABILITIES = {
    "orders",
    "items",
    "product_links",
    "payment_evidence",
    "payment_evidence_external",
    "sanitized_cache",
}
ALLOWED_PLACEHOLDERS = {"port", "min_date", "max_orders", "root"}
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ManifestError(ValueError):
    pass


def workspace_root(code_root=ROOT, environ=None):
    code_root = Path(code_root).resolve(strict=True)
    value = str((environ or os.environ).get(WORKSPACE_ENV) or "").strip()
    if not value:
        return code_root
    candidate = Path(value)
    if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_dir():
        raise ManifestError(
            f"{WORKSPACE_ENV} must be an existing absolute non-symlink directory."
        )
    current = candidate
    while current != current.parent:
        if current.is_symlink():
            raise ManifestError(f"{WORKSPACE_ENV} cannot traverse symbolic links.")
        current = current.parent
    resolved = candidate.resolve(strict=True)
    try:
        release_root = code_root.parent
        releases_root = release_root.parent
        base_root = releases_root.parent
        if (
            releases_root.name == "releases"
            and resolved != (base_root / "workspace").resolve(strict=True)
        ):
            raise ManifestError(
                f"{WORKSPACE_ENV} must select the stable collector workspace."
            )
    except IndexError as exc:  # pragma: no cover - defensive path boundary.
        raise ManifestError("Collector code root is invalid.") from exc
    return resolved


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_manifest(path=DEFAULT_MANIFEST):
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ManifestError(f"Collector manifest was not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"Collector manifest is invalid JSON: {exc}") from exc
    return payload


def relative_workspace_path(root, value, field):
    path = Path(str(value or ""))
    if not value or path.is_absolute() or ".." in path.parts:
        raise ManifestError(f"{field} must be a workspace-relative path: {value!r}")
    resolved = (Path(root) / path).resolve()
    try:
        resolved.relative_to(Path(root).resolve())
    except ValueError as exc:
        raise ManifestError(f"{field} resolves outside the workspace: {value!r}") from exc
    return resolved


def placeholders(arguments):
    found = set()
    formatter = string.Formatter()
    for argument in arguments:
        try:
            for _, field_name, _, _ in formatter.parse(argument):
                if field_name:
                    found.add(field_name)
        except ValueError as exc:
            raise ManifestError(f"Invalid argument template {argument!r}: {exc}") from exc
    return found


def validate_phase(root, collector_id, phase_name, phase):
    if not isinstance(phase, dict):
        raise ManifestError(f"{collector_id}.{phase_name} must be an object.")
    unexpected = set(phase) - {"runtime", "entrypoint", "arguments"}
    if unexpected:
        raise ManifestError(
            f"{collector_id}.{phase_name} has unsupported fields: {sorted(unexpected)}"
        )
    runtime = phase.get("runtime")
    if runtime not in ALLOWED_RUNTIMES:
        raise ManifestError(f"{collector_id}.{phase_name}.runtime is invalid: {runtime!r}")
    entrypoint = relative_workspace_path(
        root,
        phase.get("entrypoint"),
        f"{collector_id}.{phase_name}.entrypoint",
    )
    expected_suffix = ".mjs" if runtime == "node" else ".py"
    if entrypoint.suffix.lower() != expected_suffix:
        raise ManifestError(
            f"{collector_id}.{phase_name}.entrypoint must end with {expected_suffix}."
        )
    if not entrypoint.is_file():
        raise ManifestError(
            f"{collector_id}.{phase_name}.entrypoint does not exist: {entrypoint}"
        )
    arguments = phase.get("arguments")
    if not isinstance(arguments, list) or any(not isinstance(item, str) for item in arguments):
        raise ManifestError(f"{collector_id}.{phase_name}.arguments must be strings.")
    unknown = placeholders(arguments) - ALLOWED_PLACEHOLDERS
    if unknown:
        raise ManifestError(
            f"{collector_id}.{phase_name} has unknown placeholders: {sorted(unknown)}"
        )


def validate_manifest(payload, root=ROOT, workspace=None):
    root = Path(root)
    workspace = Path(workspace or root)
    if not isinstance(payload, dict):
        raise ManifestError("Collector manifest must be a JSON object.")
    unexpected = set(payload) - {
        "$schema",
        "manifest_version",
        "component_contract",
        "collectors",
    }
    if unexpected:
        raise ManifestError(f"Collector manifest has unsupported fields: {sorted(unexpected)}")
    if payload.get("manifest_version") != 1:
        raise ManifestError("Collector manifest_version must be 1.")
    if payload.get("component_contract") != CONTRACT:
        raise ManifestError(f"Collector component_contract must be {CONTRACT!r}.")
    collectors = payload.get("collectors")
    if not isinstance(collectors, list) or not collectors:
        raise ManifestError("Collector manifest must define at least one collector.")

    try:
        platform_config = json.loads(
            (root / "config" / "platforms.json").read_text(encoding="utf-8-sig")
        )
        workflow_config = json.loads(
            (root / "config" / "workflow.json").read_text(encoding="utf-8-sig")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Could not read collector dependency configuration: {exc}") from exc

    seen_ids = set()
    for index, collector in enumerate(collectors):
        if not isinstance(collector, dict):
            raise ManifestError(f"collectors[{index}] must be an object.")
        allowed_fields = {
            "id",
            "label",
            "enabled",
            "role",
            "ledger_platform",
            "tab_platform",
            "capabilities",
            "window",
            "collect",
            "import",
            "outputs",
        }
        unexpected = set(collector) - allowed_fields
        if unexpected:
            raise ManifestError(
                f"collectors[{index}] has unsupported fields: {sorted(unexpected)}"
            )
        collector_id = collector.get("id")
        if not isinstance(collector_id, str) or not ID_PATTERN.fullmatch(collector_id):
            raise ManifestError(f"collectors[{index}].id is invalid: {collector_id!r}")
        if collector_id in seen_ids:
            raise ManifestError(f"Duplicate collector id: {collector_id}")
        seen_ids.add(collector_id)
        if not str(collector.get("label") or "").strip():
            raise ManifestError(f"{collector_id}.label is required.")
        if not isinstance(collector.get("enabled"), bool):
            raise ManifestError(f"{collector_id}.enabled must be boolean.")
        if collector.get("role") not in ALLOWED_ROLES:
            raise ManifestError(f"{collector_id}.role is invalid.")
        for field in ("ledger_platform", "tab_platform"):
            value = collector.get(field)
            if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
                raise ManifestError(f"{collector_id}.{field} is invalid: {value!r}")
        if collector["tab_platform"] not in platform_config:
            raise ManifestError(
                f"{collector_id}.tab_platform is not defined in config/platforms.json."
            )

        capabilities = collector.get("capabilities")
        if (
            not isinstance(capabilities, list)
            or not capabilities
            or len(capabilities) != len(set(capabilities))
            or set(capabilities) - ALLOWED_CAPABILITIES
        ):
            raise ManifestError(f"{collector_id}.capabilities is invalid.")
        if "orders" not in capabilities or "items" not in capabilities:
            raise ManifestError(f"{collector_id} must provide orders and items.")

        window = collector.get("window")
        if not isinstance(window, dict):
            raise ManifestError(f"{collector_id}.window must be an object.")
        unexpected_window = set(window) - {
            "overlap_days_setting",
            "max_orders_setting",
        }
        if unexpected_window:
            raise ManifestError(
                f"{collector_id}.window has unsupported fields: {sorted(unexpected_window)}"
            )
        if not str(window.get("overlap_days_setting") or "").strip():
            raise ManifestError(f"{collector_id}.window.overlap_days_setting is required.")
        overlap_setting = window["overlap_days_setting"]
        if not isinstance(workflow_config.get(overlap_setting), int):
            raise ManifestError(
                f"{collector_id}.window references unknown workflow setting: {overlap_setting}"
            )

        validate_phase(root, collector_id, "collect", collector.get("collect"))
        validate_phase(root, collector_id, "import", collector.get("import"))
        collect_placeholders = placeholders(collector["collect"]["arguments"])
        if "max_orders" in collect_placeholders and not window.get("max_orders_setting"):
            raise ManifestError(
                f"{collector_id} uses max_orders but has no max_orders_setting."
            )
        max_orders_setting = window.get("max_orders_setting")
        if max_orders_setting and not isinstance(
            workflow_config.get("collector_max_orders", {}).get(max_orders_setting),
            int,
        ):
            raise ManifestError(
                f"{collector_id}.window references unknown max-orders setting: "
                f"{max_orders_setting}"
            )

        outputs = collector.get("outputs")
        if not isinstance(outputs, dict):
            raise ManifestError(f"{collector_id}.outputs must be an object.")
        unexpected_outputs = set(outputs) - {
            "transactions",
            "item_staging",
            "sanitized_cache",
        }
        if unexpected_outputs:
            raise ManifestError(
                f"{collector_id}.outputs has unsupported fields: {sorted(unexpected_outputs)}"
            )
        for field in ("transactions", "item_staging"):
            relative_workspace_path(
                workspace,
                outputs.get(field),
                f"{collector_id}.outputs.{field}",
            )
        caches = outputs.get("sanitized_cache", [])
        if not isinstance(caches, list) or any(not isinstance(item, str) for item in caches):
            raise ManifestError(f"{collector_id}.outputs.sanitized_cache must be strings.")
        for cache in caches:
            if Path(cache).is_absolute() or ".." in Path(cache).parts:
                raise ManifestError(
                    f"{collector_id}.outputs.sanitized_cache is unsafe: {cache!r}"
                )

    return collectors


def load_validated_manifest(path=DEFAULT_MANIFEST, root=ROOT, workspace=None):
    payload = read_manifest(path)
    validate_manifest(payload, root, workspace=workspace)
    return payload


def find_collector(payload, collector_id):
    for collector in payload.get("collectors", []):
        if collector.get("id") == collector_id:
            return collector
    raise ManifestError(f"Unknown collector id: {collector_id}")


def render_arguments(arguments, context):
    required = placeholders(arguments)
    missing = sorted(
        key for key in required
        if context.get(key) is None or str(context.get(key)) == ""
    )
    if missing:
        raise ManifestError(f"Missing collector context values: {missing}")
    return [argument.format_map(context) for argument in arguments]


def runtime_executable(runtime, node=None):
    if runtime == "python":
        return sys.executable
    candidate = node or shutil.which("node")
    if not candidate:
        raise ManifestError("Node.js was not found for the collector runtime.")
    return str(candidate)


def build_command(collector, phase_name, context, root=ROOT, node=None):
    phase = collector[phase_name]
    executable = runtime_executable(phase["runtime"], node=node)
    entrypoint = relative_workspace_path(
        root,
        phase["entrypoint"],
        f"{collector['id']}.{phase_name}.entrypoint",
    )
    arguments = render_arguments(phase["arguments"], context)
    return [executable, str(entrypoint), *arguments]


def sanitize_output(value):
    text = str(value or "")
    text = re.sub(r"https?://\S+", "[url]", text)
    text = re.sub(r"\b\d{3}-\d{7}-\d{7}\b", "[order-id]", text)
    text = re.sub(r"\bCA\d{8,12}\b", "[order-id]", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{12,20}\b", "[order-id]", text)
    return text


def clean_message(stdout, stderr):
    sanitized_stdout = sanitize_output(stdout).strip()
    if sanitized_stdout:
        try:
            payload = json.loads(sanitized_stdout)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            return json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )[:240]

    lines = [
        line.strip()
        for line in sanitize_output(f"{stdout or ''}\n{stderr or ''}").splitlines()
        if line.strip()
    ]
    meaningful = [
        line for line in lines
        if line not in {"{", "}", "[", "]", "},", "],"}
    ]
    message = meaningful[-1] if meaningful else (lines[-1] if lines else "")
    return message[:240]


def collector_failure_status(root, collector_id):
    path = Path(root) / ".codex" / "collector-health.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return "failed"
    row = (payload.get("platforms") or {}).get(collector_id) or {}
    status = str(row.get("status") or "")
    if status in {"login_required", "blocked", "cooldown"}:
        return status
    return "failed"


def run_component(
    collector,
    phase_name,
    context,
    root=ROOT,
    node=None,
    status_dir=None,
    workspace=None,
):
    root = Path(root).resolve()
    workspace = Path(workspace or root).resolve()
    command = build_command(collector, phase_name, context, root=root, node=node)
    status_dir = Path(status_dir or workspace / ".codex" / "collector-runs")
    status_path = status_dir / f"{collector['id']}-{phase_name}.json"
    started_at = now_iso()
    started = datetime.now(timezone.utc)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env[WORKSPACE_ENV] = str(workspace)
    result = subprocess.run(
        command,
        cwd=workspace,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    sanitized_stdout = sanitize_output(result.stdout).strip()
    sanitized_stderr = sanitize_output(result.stderr).strip()
    if result.returncode == 0:
        if sanitized_stdout:
            print(sanitized_stdout)
        if sanitized_stderr:
            print(sanitized_stderr)
    else:
        message = clean_message(result.stdout, result.stderr)
        print(message or f"{collector['id']} {phase_name} failed.")
    completed = datetime.now(timezone.utc)
    status = (
        "passed"
        if result.returncode == 0
        else collector_failure_status(workspace, collector["id"])
    )
    atomic_write_json(status_path, {
        "contract": CONTRACT,
        "collector_id": collector["id"],
        "ledger_platform": collector["ledger_platform"],
        "phase": phase_name,
        "status": status,
        "exit_code": result.returncode,
        "started_at": started_at,
        "completed_at": completed.replace(microsecond=0).isoformat(),
        "duration_seconds": round((completed - started).total_seconds(), 3),
        "context": {
            "min_date": context.get("min_date", ""),
            "max_orders": context.get("max_orders", ""),
        },
        "message": clean_message(result.stdout, result.stderr),
    })
    return result.returncode


def parser():
    command_parser = argparse.ArgumentParser(
        description="Validate and run manifest-defined expense collectors."
    )
    command_parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Collector manifest path.",
    )
    subparsers = command_parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--json", action="store_true")

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--role", choices=sorted(ALLOWED_ROLES))
    list_parser.add_argument("--enabled", action="store_true")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--id", required=True)
    run_parser.add_argument("--phase", choices=["collect", "import"], required=True)
    run_parser.add_argument("--port", type=int, default=9223)
    run_parser.add_argument("--min-date", default="")
    run_parser.add_argument("--max-orders", type=int)
    run_parser.add_argument("--node")
    run_parser.add_argument("--dry-run", action="store_true")

    many_parser = subparsers.add_parser(
        "run-many",
        help="Collect several stores at once; each store keeps its own browser window.",
    )
    many_parser.add_argument("--phase", choices=["collect"], default="collect")
    many_parser.add_argument("--port", type=int, default=9223)
    many_parser.add_argument("--node")
    many_parser.add_argument("--max-workers", type=int, default=3)
    many_parser.add_argument("--stagger-seconds", type=float, default=1.5)
    many_parser.add_argument(
        "--job", action="append", required=True,
        help="ID=MIN_DATE[=MAX_ORDERS], repeated once per collector.",
    )
    return command_parser


def parse_job(value):
    parts = str(value).split("=")
    if len(parts) not in (2, 3) or not parts[0]:
        raise ManifestError(f"--job must be ID=MIN_DATE[=MAX_ORDERS]: {value}")
    collector_id, min_date = parts[0], parts[1]
    if min_date and not DATE_PATTERN.fullmatch(min_date):
        raise ManifestError(f"--job min date must use YYYY-MM-DD: {value}")
    max_orders = parts[2] if len(parts) == 3 and parts[2] else None
    if max_orders is not None and not max_orders.isdigit():
        raise ManifestError(f"--job max orders must be a number: {value}")
    return collector_id, min_date, max_orders


def run_many(args, manifest_path, collectors):
    """Run one `run --phase collect` subprocess per store, a few at a time.

    Each store is collected exactly as in a sequential run; only the waiting overlaps.
    Output is printed per store in the requested order, followed by one
    ``collector_parallel_result=ID:EXIT_CODE`` line each, so the caller can report
    failures per store. Imports are not run here and stay sequential.
    """
    jobs = [parse_job(value) for value in args.job]
    ids = [job[0] for job in jobs]
    if len(ids) != len(set(ids)):
        raise ManifestError("Each collector can appear only once in run-many.")
    for collector_id in ids:
        collector = find_collector({"collectors": collectors}, collector_id)
        if not collector["enabled"]:
            raise ManifestError(f"Collector is disabled: {collector_id}")
    workers = max(1, min(args.max_workers, len(jobs), 6))
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    def run_one(index, job):
        collector_id, min_date, max_orders = job
        time.sleep(index * max(0.0, args.stagger_seconds))   # avoid opening every store at the same instant
        command = [sys.executable, str(Path(__file__).resolve()), "--manifest", str(manifest_path),
                   "run", "--id", collector_id, "--phase", args.phase, "--port", str(args.port),
                   "--min-date", min_date]
        if max_orders is not None:
            command += ["--max-orders", max_orders]
        if args.node:
            command += ["--node", args.node]
        started = time.monotonic()
        result = subprocess.run(command, text=True, capture_output=True, encoding="utf-8",
                                errors="replace", env=env, check=False)
        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        return collector_id, result.returncode, output, round(time.monotonic() - started, 1)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Only the first wave is staggered; later stores start as soon as a slot frees up.
        futures = [pool.submit(run_one, index if index < workers else 0, job)
                   for index, job in enumerate(jobs)]
        results = [future.result() for future in futures]
    for collector_id, code, output, seconds in results:
        print(f"== Collect {collector_id} orders (parallel, {seconds}s) ==")
        if output:
            print(output)
    for collector_id, code, _, _ in results:
        print(f"collector_parallel_result={collector_id}:{code}")
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        workspace = workspace_root(ROOT)
        payload = load_validated_manifest(args.manifest, ROOT, workspace=workspace)
        collectors = payload["collectors"]
        if args.command == "validate":
            result = {
                "status": "passed",
                "contract": payload["component_contract"],
                "collectors": len(collectors),
                "enabled": sum(bool(item["enabled"]) for item in collectors),
            }
            if args.json:
                print(json.dumps(result))
            else:
                for key, value in result.items():
                    print(f"collector_manifest_{key}={value}")
            return 0

        if args.command == "list":
            rows = [
                collector for collector in collectors
                if (not args.role or collector["role"] == args.role)
                and (not args.enabled or collector["enabled"])
            ]
            print(json.dumps(rows, ensure_ascii=False))
            return 0

        if args.command == "run-many":
            return run_many(args, args.manifest, collectors)

        collector = find_collector(payload, args.id)
        if not collector["enabled"]:
            raise ManifestError(f"Collector is disabled: {args.id}")
        if args.min_date and not DATE_PATTERN.fullmatch(args.min_date):
            raise ManifestError("--min-date must use YYYY-MM-DD.")
        context = {
            "port": args.port,
            "min_date": args.min_date,
            "max_orders": args.max_orders,
            "root": str(workspace),
        }
        command = build_command(
            collector,
            args.phase,
            context,
            root=ROOT,
            node=args.node,
        )
        if args.dry_run:
            print(json.dumps({
                "collector_id": collector["id"],
                "phase": args.phase,
                "runtime": collector[args.phase]["runtime"],
                "entrypoint": collector[args.phase]["entrypoint"],
                "arguments": command[2:],
            }, ensure_ascii=False))
            return 0
        return run_component(
            collector,
            args.phase,
            context,
            root=ROOT,
            node=args.node,
            workspace=workspace,
        )
    except ManifestError as exc:
        print(f"Collector manifest error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
