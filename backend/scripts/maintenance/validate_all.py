import argparse
import os
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SAFETY = ROOT / "scripts" / "maintenance" / "safety.py"
LEDGER_VALIDATOR = ROOT / "scripts" / "reporting" / "validate.py"
COLLECTOR_RUNNER = ROOT / "scripts" / "collectors" / "runner.py"
BACKUP_DIR = ROOT / "backups"
STATUS_PATH = ROOT / "exports" / "current" / "validation-status.json"
METRIC_PATTERN = re.compile(r"^([a-zA-Z0-9_]+)=(.+)$")


def run(command):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return result.returncode, "\n".join(
        part for part in (result.stdout, result.stderr) if part
    )


def parse_metrics(output):
    metrics = {}
    for line in output.splitlines():
        match = METRIC_PATTERN.match(line.strip())
        if not match:
            continue
        key, value = match.groups()
        try:
            metrics[key] = int(value)
        except ValueError:
            try:
                metrics[key] = float(value)
            except ValueError:
                metrics[key] = value
    return metrics


def read_status():
    if not STATUS_PATH.exists():
        return {}
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_status(status, exit_code, metrics, failure=""):
    payload = read_status()
    payload.update({
        "status": status,
        "completed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "exit_code": exit_code,
        "metrics": {
            **payload.get("metrics", {}),
            **metrics,
        },
        "message": (
            "账本及运行安全校验通过"
            if status == "passed"
            else "账本或运行安全校验失败"
        ),
    })
    failures = [] if status == "passed" else list(payload.get("failures", []))
    if failure:
        failures.append(failure)
    payload["failures"] = failures
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS_PATH.with_name(f".{STATUS_PATH.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, STATUS_PATH)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate ledger and runtime safety.")
    parser.add_argument(
        "--skip-ledger",
        action="store_true",
        help="Reuse the immediately preceding successful ledger validation.",
    )
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if args.skip_ledger and read_status().get("status") != "passed":
        write_status(
            "failed",
            1,
            {},
            failure="Cannot skip ledger validation without a current passing result.",
        )
        return 1

    commands = [
        [sys.executable, str(SAFETY), "preflight", "--no-backup"],
        [sys.executable, str(COLLECTOR_RUNNER), "validate"],
    ]
    if not args.skip_ledger:
        commands.append([sys.executable, str(LEDGER_VALIDATOR)])
    if list(BACKUP_DIR.glob("expense-ledger-*.zip")):
        commands.append([sys.executable, str(SAFETY), "verify"])

    metrics = {}
    for command in commands:
        exit_code, output = run(command)
        metrics.update(parse_metrics(output))
        if exit_code != 0:
            write_status(
                "failed",
                exit_code,
                metrics,
                failure=f"Command failed: {Path(command[1]).name}",
            )
            return exit_code
    write_status("passed", 0, metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
