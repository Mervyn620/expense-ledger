"""First-run setup: which bank source, which shopping platforms, and the ledger start date.

The desktop shows its welcome guide only when setup was never completed and the
workspace has no ledger data yet, so existing users never see it after an
upgrade. Choices are stored in ``config/setup.json``; the start date is written
to ``config/workflow.json`` (``collection_floor`` / ``payment_evidence_floor``),
which the refresh workflow already reads.
"""
import json
import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path


CONTRACT = "expense-setup/v1"
BANK_MODES = ("scotia", "statements", "later")
PLATFORMS = ("walmart_ca", "uber_eats", "fantuan", "amazon_ca", "the_ordinary_ca", "tnt_ca")
EARLIEST_FLOOR = date(2000, 1, 1)


class SetupError(ValueError):
    pass


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SetupStore:
    def __init__(self, root):
        self.root = Path(root)
        self.setup_path = self.root / "config" / "setup.json"
        self.workflow_path = self.root / "config" / "workflow.json"
        # Per-machine choices that must not change the repository defaults in workflow.json.
        self.local_path = self.root / "config" / "collection.local.json"

    def _read(self, path):
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def has_existing_ledger(self):
        data = self.root / "data"
        if (data / "scotia_transactions.json").exists() or any((data / "bank_statements").glob("*.json")):
            return True
        database = data / "expenses.sqlite"
        if not database.exists():
            return False
        try:
            with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                return bool(conn.execute("SELECT EXISTS(SELECT 1 FROM orders)").fetchone()[0])
        except sqlite3.Error:
            return True   # an unreadable ledger is still someone's ledger; never offer a fresh start

    def status(self):
        setup = self._read(self.setup_path)
        existing = self.has_existing_ledger()
        completed = bool(setup.get("completed_at"))
        workflow = self._read(self.workflow_path)
        return {
            "completed": completed,
            "has_existing_ledger": existing,
            "needs_onboarding": not completed and not existing,
            # Existing workspaces predate the choice and collect from Scotia.
            "bank_mode": setup.get("bank_mode") or ("scotia" if existing else "later"),
            "platforms": [p for p in setup.get("platforms", []) if p in PLATFORMS],
            "collection_floor": workflow.get("collection_floor", ""),
            "suggested_floor": self.suggested_floor().isoformat(),
            "available_platforms": list(PLATFORMS),
        }

    @staticmethod
    def suggested_floor(today=None):
        """First day of the month three months ago."""
        today = today or date.today()
        months = today.year * 12 + today.month - 1 - 3
        return date(months // 12, months % 12 + 1, 1)

    def collection_settings(self):
        parallel = dict(self._read(self.workflow_path).get("parallel_collection") or {})
        local = self._read(self.local_path).get("parallel_collection")
        if isinstance(local, dict):
            parallel.update(local)
        return {
            "parallel_collection": parallel.get("enabled") is True,
            "max_workers": parallel.get("max_workers", 3) if isinstance(parallel.get("max_workers"), int) else 3,
        }

    def set_parallel_collection(self, enabled):
        local = self._read(self.local_path)
        local["parallel_collection"] = {**(local.get("parallel_collection") or {}), "enabled": bool(enabled)}
        _write_json(self.local_path, local)
        return self.collection_settings()

    def save(self, bank_mode, platforms=None, collection_floor=None, today=None):
        if bank_mode not in BANK_MODES:
            raise SetupError("银行选项无效。")
        platforms = list(dict.fromkeys(platforms or []))
        unknown = [p for p in platforms if p not in PLATFORMS]
        if unknown:
            raise SetupError(f"不支持的平台：{', '.join(unknown)}")
        floor = None
        if collection_floor:
            try:
                floor = date.fromisoformat(str(collection_floor))
            except ValueError:
                raise SetupError("起始日期格式应为 YYYY-MM-DD。") from None
            if not EARLIEST_FLOOR <= floor <= (today or date.today()):
                raise SetupError("起始日期不能晚于今天，也不能早于 2000 年。")
        if floor:
            workflow = self._read(self.workflow_path)
            if not workflow:
                raise SetupError("找不到 config/workflow.json，无法保存起始日期。")
            workflow["collection_floor"] = workflow["payment_evidence_floor"] = floor.isoformat()
            _write_json(self.workflow_path, workflow)
        _write_json(self.setup_path, {
            "contract": CONTRACT,
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "bank_mode": bank_mode,
            "platforms": platforms,
        })
        return self.status()
