import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATABASE_RELATIVE_PATH = Path("data") / "expenses.sqlite"
BACKUP_DIR_NAME = "backups"
BACKUP_PREFIX = "expense-ledger-"
DEFAULT_MIN_FREE_MB = 256
DEFAULT_RETENTION = 24
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
BACKUP_PATTERNS = (
    "config/*.json",
    "data/*.csv",
    "data/*.json",
)


class SafetyError(RuntimeError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_id(root):
    return hashlib.sha256(str(Path(root).resolve()).lower().encode("utf-8")).hexdigest()[:12].upper()


def validate_configs(root):
    config_dir = Path(root) / "config"
    paths = sorted(config_dir.glob("*.json"))
    if not paths:
        raise SafetyError(f"No JSON configuration files were found in {config_dir}.")

    payloads = {}
    for path in paths:
        try:
            payloads[path.name] = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SafetyError(f"Invalid configuration file {path}: {exc}") from exc

    workflow = payloads.get("workflow.json")
    if not isinstance(workflow, dict):
        raise SafetyError("config/workflow.json must contain a JSON object.")
    for key in ("collection_floor", "payment_evidence_floor"):
        try:
            datetime.strptime(str(workflow[key]), "%Y-%m-%d")
        except (KeyError, TypeError, ValueError) as exc:
            raise SafetyError(f"config/workflow.json has an invalid {key}.") from exc
    for key in ("port", "overlap_days", "walmart_overlap_days"):
        value = workflow.get(key)
        if not isinstance(value, int) or value <= 0:
            raise SafetyError(f"config/workflow.json has an invalid {key}.")
    collector_limits = workflow.get("collector_max_orders", {})
    if not isinstance(collector_limits, dict) or not collector_limits:
        raise SafetyError("config/workflow.json has invalid collector_max_orders.")
    for key, value in collector_limits.items():
        if not isinstance(value, int) or not 1 <= value <= 100:
            raise SafetyError(
                f"config/workflow.json has an invalid collector_max_orders.{key}."
            )
    policy = workflow.get("collector_policy", {})
    if not isinstance(policy, dict):
        raise SafetyError("config/workflow.json has an invalid collector_policy.")
    for key in (
        "min_action_delay_ms",
        "max_action_delay_ms",
        "settle_min_ms",
        "settle_max_ms",
        "max_retries",
        "backoff_base_ms",
        "backoff_max_ms",
        "block_cooldown_minutes",
        "failure_cooldown_minutes",
        "max_consecutive_failures",
        "cdp_call_timeout_ms",
    ):
        if key in policy and (
            not isinstance(policy[key], int) or policy[key] < 0
        ):
            raise SafetyError(f"config/workflow.json has an invalid collector_policy.{key}.")
    if policy.get("min_action_delay_ms", 0) > policy.get("max_action_delay_ms", 0):
        raise SafetyError("collector_policy minimum action delay exceeds the maximum.")
    if policy.get("settle_min_ms", 0) > policy.get("settle_max_ms", 0):
        raise SafetyError("collector_policy minimum settle delay exceeds the maximum.")
    platform_policies = policy.get("platforms", {})
    if not isinstance(platform_policies, dict):
        raise SafetyError("collector_policy.platforms must be a JSON object.")
    for platform, overrides in platform_policies.items():
        if not isinstance(overrides, dict):
            raise SafetyError(f"collector_policy.platforms.{platform} must be an object.")
        for key, value in overrides.items():
            if (
                key != "platforms"
                and (not isinstance(value, int) or value < 0)
            ):
                raise SafetyError(
                    f"collector_policy.platforms.{platform}.{key} must be a non-negative integer."
                )
        minimum = overrides.get(
            "min_action_delay_ms",
            policy.get("min_action_delay_ms", 0),
        )
        maximum = overrides.get(
            "max_action_delay_ms",
            policy.get("max_action_delay_ms", 0),
        )
        if minimum > maximum:
            raise SafetyError(
                f"collector_policy.platforms.{platform} minimum delay exceeds the maximum."
            )
    return len(paths)


def validate_database(db_path, require_database=True):
    db_path = Path(db_path)
    if not db_path.exists():
        if require_database:
            raise SafetyError(f"Ledger database was not found: {db_path}")
        return {"status": "missing", "tables": 0}
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
            quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
            if quick_check != "ok":
                raise SafetyError(f"SQLite quick_check failed: {quick_check}")
            foreign_key_failures = conn.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_failures:
                raise SafetyError(
                    f"SQLite foreign_key_check found {len(foreign_key_failures)} failure(s)."
                )
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            missing = sorted(REQUIRED_TABLES - tables)
            if missing:
                raise SafetyError(
                    "SQLite ledger is missing required tables: " + ", ".join(missing)
                )
            return {"status": "ok", "tables": len(tables)}
    except sqlite3.Error as exc:
        raise SafetyError(f"Could not validate SQLite ledger: {exc}") from exc


def check_free_space(root, min_free_mb):
    free_bytes = shutil.disk_usage(Path(root)).free
    free_mb = free_bytes // (1024 * 1024)
    if free_mb < min_free_mb:
        raise SafetyError(
            f"Only {free_mb} MB is free; at least {min_free_mb} MB is required."
        )
    return free_mb


def check_writable_directories(root):
    root = Path(root)
    checked = []
    for relative in ("data", "exports", ".codex", BACKUP_DIR_NAME):
        directory = root / relative
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=directory,
                prefix=".expense-write-test-",
                delete=False,
            ) as handle:
                handle.write("ok")
                probe = Path(handle.name)
            probe.unlink()
        except OSError as exc:
            raise SafetyError(f"Directory is not writable: {directory}: {exc}") from exc
        checked.append(relative)
    return checked


def backup_candidates(root):
    root = Path(root)
    seen = set()
    for pattern in BACKUP_PATTERNS:
        for path in sorted(root.glob(pattern)):
            if path.is_file() and path.resolve() not in seen:
                seen.add(path.resolve())
                yield path


def backup_database(source, destination):
    with closing(sqlite3.connect(source, timeout=5)) as source_conn:
        with closing(sqlite3.connect(destination)) as destination_conn:
            source_conn.backup(destination_conn)
    validate_database(destination)


def create_snapshot(root=ROOT, label="checkpoint", retention=DEFAULT_RETENTION):
    root = Path(root).resolve()
    db_path = root / DATABASE_RELATIVE_PATH
    validate_database(db_path)
    backup_dir = root / BACKUP_DIR_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_label = "".join(character for character in label.lower() if character.isalnum() or character in "-_")
    safe_label = safe_label.strip("-_") or "checkpoint"
    destination = backup_dir / f"{BACKUP_PREFIX}{timestamp}-{safe_label}.zip"
    temporary_archive = destination.with_suffix(".zip.tmp")

    with tempfile.TemporaryDirectory(prefix="expense-backup-", dir=backup_dir) as temp:
        temp_dir = Path(temp)
        db_copy = temp_dir / DATABASE_RELATIVE_PATH
        db_copy.parent.mkdir(parents=True, exist_ok=True)
        backup_database(db_path, db_copy)

        files = [(db_copy, DATABASE_RELATIVE_PATH)]
        files.extend((path, path.relative_to(root)) for path in backup_candidates(root))
        manifest_files = {}
        for source, relative in files:
            manifest_files[relative.as_posix()] = {
                "sha256": sha256_file(source),
                "size": source.stat().st_size,
            }

        manifest = {
            "format_version": 1,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "workspace_id": workspace_id(root),
            "label": safe_label,
            "database_integrity": "ok",
            "files": manifest_files,
        }
        try:
            with zipfile.ZipFile(
                temporary_archive,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                for source, relative in files:
                    archive.write(source, relative.as_posix())
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                )
            verify_snapshot(temporary_archive, expected_workspace_id=workspace_id(root))
            os.replace(temporary_archive, destination)
        finally:
            temporary_archive.unlink(missing_ok=True)

    prune_snapshots(backup_dir, retention)
    return destination


def verify_snapshot(path, expected_workspace_id=None):
    path = Path(path)
    try:
        with zipfile.ZipFile(path, mode="r") as archive:
            corrupt = archive.testzip()
            if corrupt:
                raise SafetyError(f"Backup contains a corrupt file: {corrupt}")
            try:
                manifest = json.loads(archive.read("manifest.json"))
            except (KeyError, json.JSONDecodeError) as exc:
                raise SafetyError("Backup manifest is missing or invalid.") from exc
            if expected_workspace_id and manifest.get("workspace_id") != expected_workspace_id:
                raise SafetyError("Backup belongs to a different expense workspace.")
            for name, expected in manifest.get("files", {}).items():
                try:
                    content = archive.read(name)
                except KeyError as exc:
                    raise SafetyError(f"Backup is missing {name}.") from exc
                digest = hashlib.sha256(content).hexdigest()
                if digest != expected.get("sha256") or len(content) != expected.get("size"):
                    raise SafetyError(f"Backup checksum failed for {name}.")
            if DATABASE_RELATIVE_PATH.as_posix() not in manifest.get("files", {}):
                raise SafetyError("Backup does not contain the SQLite ledger.")
            return manifest
    except (OSError, zipfile.BadZipFile) as exc:
        raise SafetyError(f"Could not verify backup {path}: {exc}") from exc


def atomic_replace_bytes(destination, content):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.restore.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def restore_snapshot(
    root,
    snapshot,
    retention=DEFAULT_RETENTION,
    allow_workspace_mismatch=False,
):
    root = Path(root).resolve()
    snapshot = Path(snapshot).resolve()
    manifest = verify_snapshot(
        snapshot,
        expected_workspace_id=(
            None if allow_workspace_mismatch else workspace_id(root)
        ),
    )
    files = manifest.get("files", {})
    allowed_names = []
    for name in files:
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not relative.parts
            or relative.parts[0] not in {"config", "data"}
        ):
            raise SafetyError(f"Backup contains an unsafe path: {name}")
        allowed_names.append(name)

    rollback = None
    (root / BACKUP_DIR_NAME).mkdir(parents=True, exist_ok=True)
    try:
        validate_database(root / DATABASE_RELATIVE_PATH)
        rollback = create_snapshot(root, "before-restore", retention)
    except SafetyError:
        pass

    originals = {}
    changed = []
    with zipfile.ZipFile(snapshot, mode="r") as archive:
        with tempfile.TemporaryDirectory(
            prefix="expense-restore-",
            dir=root / BACKUP_DIR_NAME,
        ) as temp:
            stage = Path(temp)
            for name in allowed_names:
                staged = stage / name
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(archive.read(name))
            validate_configs(stage)
            validate_database(stage / DATABASE_RELATIVE_PATH)

            archived_names = set(allowed_names)
            managed_existing = {
                path.relative_to(root).as_posix()
                for path in backup_candidates(root)
            }
            managed_existing.add(DATABASE_RELATIVE_PATH.as_posix())
            stale_names = sorted(managed_existing - archived_names)
            managed_names = sorted(archived_names | set(stale_names))
            for name in managed_names:
                destination = root / name
                originals[name] = (
                    destination.read_bytes() if destination.exists() else None
                )

            try:
                for name in stale_names:
                    destination = root / name
                    destination.unlink(missing_ok=True)
                    changed.append(name)
                for name in allowed_names:
                    destination = root / name
                    atomic_replace_bytes(destination, (stage / name).read_bytes())
                    changed.append(name)
                validate_configs(root)
                validate_database(root / DATABASE_RELATIVE_PATH)
            except Exception as exc:
                for name in reversed(changed):
                    destination = root / name
                    previous = originals[name]
                    if previous is None:
                        destination.unlink(missing_ok=True)
                    else:
                        atomic_replace_bytes(destination, previous)
                raise SafetyError(
                    f"Backup restore failed and replaced files were rolled back: {exc}"
                ) from exc
    return rollback


def prune_snapshots(backup_dir, retention):
    if retention <= 0:
        raise SafetyError("Backup retention must be greater than zero.")
    snapshots = sorted(
        Path(backup_dir).glob(f"{BACKUP_PREFIX}*.zip"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for path in snapshots[retention:]:
        path.unlink()


def latest_snapshot(root=ROOT, expected_workspace_id=None):
    snapshots = sorted(
        (Path(root) / BACKUP_DIR_NAME).glob(f"{BACKUP_PREFIX}*.zip"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not snapshots:
        raise SafetyError("No expense ledger backup was found.")
    if expected_workspace_id is None:
        return snapshots[0]
    for snapshot in snapshots:
        try:
            verify_snapshot(
                snapshot,
                expected_workspace_id=expected_workspace_id,
            )
            return snapshot
        except SafetyError as exc:
            if str(exc) != "Backup belongs to a different expense workspace.":
                raise
    raise SafetyError("No backup belongs to the current expense workspace.")


def preflight(root=ROOT, min_free_mb=DEFAULT_MIN_FREE_MB, create_backup=True, retention=DEFAULT_RETENTION):
    root = Path(root).resolve()
    free_mb = check_free_space(root, min_free_mb)
    writable = check_writable_directories(root)
    config_count = validate_configs(root)
    database = validate_database(root / DATABASE_RELATIVE_PATH, require_database=False)
    snapshot = None
    if create_backup and database["status"] == "ok":
        snapshot = create_snapshot(root, "pre-refresh", retention)
    return {
        "free_mb": free_mb,
        "writable_directories": len(writable),
        "config_files": config_count,
        "database_status": database["status"],
        "backup": str(snapshot) if snapshot else "",
    }


def build_parser():
    parser = argparse.ArgumentParser(description="Expense ledger safety checks and backups.")
    parser.add_argument("--root", type=Path, default=ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--min-free-mb", type=int, default=DEFAULT_MIN_FREE_MB)
    preflight_parser.add_argument("--retention", type=int, default=DEFAULT_RETENTION)
    preflight_parser.add_argument("--no-backup", action="store_true")

    checkpoint_parser = subparsers.add_parser("checkpoint")
    checkpoint_parser.add_argument("--label", default="checkpoint")
    checkpoint_parser.add_argument("--retention", type=int, default=DEFAULT_RETENTION)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--path", type=Path)
    verify_parser.add_argument("--allow-workspace-move", action="store_true")

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--path", type=Path, required=True)
    restore_parser.add_argument("--confirm", required=True)
    restore_parser.add_argument("--retention", type=int, default=DEFAULT_RETENTION)
    restore_parser.add_argument("--allow-workspace-move", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preflight":
            result = preflight(
                args.root,
                min_free_mb=args.min_free_mb,
                create_backup=not args.no_backup,
                retention=args.retention,
            )
            for key, value in result.items():
                print(f"safety_{key}={value}")
        elif args.command == "checkpoint":
            snapshot = create_snapshot(args.root, args.label, args.retention)
            print(f"safety_backup={snapshot}")
        elif args.command == "verify":
            expected_workspace = (
                None if args.allow_workspace_move else workspace_id(args.root)
            )
            snapshot = args.path or latest_snapshot(
                args.root,
                expected_workspace_id=expected_workspace,
            )
            manifest = verify_snapshot(
                snapshot,
                expected_workspace_id=expected_workspace,
            )
            print(f"safety_backup={snapshot}")
            print(f"safety_backup_files={len(manifest['files'])}")
            print("safety_backup_valid=1")
        else:
            expected_confirmation = (
                "MIGRATE" if args.allow_workspace_move else "RESTORE"
            )
            if args.confirm != expected_confirmation:
                raise SafetyError(
                    f"Restore requires --confirm {expected_confirmation}."
                )
            rollback = restore_snapshot(
                args.root,
                args.path,
                retention=args.retention,
                allow_workspace_mismatch=args.allow_workspace_move,
            )
            print(f"safety_restored_backup={args.path}")
            print(f"safety_rollback_backup={rollback or ''}")
            print("safety_restore_valid=1")
    except SafetyError as exc:
        print(f"safety_error={exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
