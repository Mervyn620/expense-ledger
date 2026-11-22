import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LEDGER_DIR = ROOT / "scripts" / "ledger"
if str(LEDGER_DIR) not in sys.path:
    sys.path.insert(0, str(LEDGER_DIR))

from schema import create_schema


REQUIRED_CONFIGS = (
    "collector-manifest.schema.json",
    "collectors.json",
    "merchant_rules.json",
    "payment_cards.json",
    "platforms.json",
    "workflow.json",
)
RUNTIME_DIRECTORIES = (
    "data",
    "exports/current",
    "exports/periods",
    "exports/reconciliation",
    "exports/summaries",
    "raw",
    "downloads",
    "backups",
    ".codex",
)


class InitializationError(RuntimeError):
    pass


# Machine-local configuration that is not part of the source tree. A new workspace gets
# a local-only default; an existing file is never replaced.
DEFAULT_LOCAL_CONFIGS = {
    "collector_backends.json": {
        "contract": "expense-collector-backends/v1",
        "default_mode": "local",
        "health_cache_seconds": 15,
        "local_only_platforms": ["the_ordinary_ca", "tnt_ca"],
        "local": {"label": "本地", "type": "local", "enabled": True},
        "vps": {
            "label": "VPS",
            "type": "ssh",
            "enabled": False,
            "ssh_host": "ledger_vps",
            "remote_python": "/usr/bin/python3",
            "remote_service": "/home/ledger/apps/expense-collector/current/vps_backend_service.py",
            "timeout_seconds": 6,
            "home_exit_remote_port": 1080,
            "collection_timeout_seconds": 600,
        },
    },
}


def initialize_workspace(root=ROOT):
    root = Path(root).resolve()
    missing_configs = [
        name for name in REQUIRED_CONFIGS
        if not (root / "config" / name).is_file()
    ]
    if missing_configs:
        raise InitializationError(
            "Required configuration files are missing: "
            + ", ".join(missing_configs)
        )

    for relative in RUNTIME_DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)

    for name, payload in DEFAULT_LOCAL_CONFIGS.items():
        path = root / "config" / name
        if not path.exists():
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    db_path = root / "data" / "expenses.sqlite"
    database_created = not db_path.exists()
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA foreign_keys = ON")
        create_schema(conn)
        quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise InitializationError(f"SQLite quick_check failed: {quick_check}")
        conn.commit()

    status = {
        "status": "initialized",
        "initialized_at": datetime.now(timezone.utc).replace(
            microsecond=0
        ).isoformat(),
        "database": str(db_path.relative_to(root).as_posix()),
        "database_created": database_created,
        "existing_data_preserved": not database_created,
        "browser_profile_migrated": False,
        "secrets_migrated": False,
    }
    status_path = root / ".codex" / "initialization-status.json"
    temporary = status_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(status_path)
    return status


def build_parser():
    parser = argparse.ArgumentParser(
        description="Safely initialize an Expense Ledger workspace."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        status = initialize_workspace(args.root)
    except (InitializationError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"initialization_error={exc}")
        return 1
    for key, value in status.items():
        print(f"initialization_{key}={str(value).lower() if isinstance(value, bool) else value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
