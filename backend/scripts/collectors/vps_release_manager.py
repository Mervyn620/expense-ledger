import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows test environment.
    fcntl = None


CONTRACT = "expense-collector-release/v1"
PLAN_CONTRACT = "expense-collector-release-plan/v1"
RESULT_CONTRACT = "expense-collector-release-result/v1"
DEFAULT_BASE_DIR = Path("/home/ledger/apps/expense-collector")
DEFAULT_NODE = DEFAULT_BASE_DIR / "runtime" / "node" / "bin" / "node"
DEFAULT_PYTHON = Path("/usr/bin/python3")
RELEASE_ID_PATTERN = re.compile(
    r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7,40}$"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
DEFAULT_RETAIN = 5
LOGIN_PORTS = (9224, 5901, 6081)
SERVICE_SOURCE_PATHS = {
    "vps_backend_service.py": "scripts/collectors/vps_backend_service.py",
    "vps_login_session.py": "scripts/collectors/vps_login_session.py",
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
ALLOWED_FILES = frozenset({
    *SERVICE_SOURCE_PATHS,
    *(f"app/{relative}" for relative in REQUIRED_SOURCES),
})
PREVIOUS_REMOVED_FROM_ALLOWLIST = frozenset({
    "app/scripts/collectors/fantuan-order-utils.mjs",
})
PRE_PAYMENT_TEXT_ALLOWED_FILES = ALLOWED_FILES - {"app/scripts/collectors/payment-text-utils.mjs"}
PRE_CONTENT_READY_ALLOWED_FILES = PRE_PAYMENT_TEXT_ALLOWED_FILES - {"app/scripts/browser/content-ready.mjs"}
PREVIOUS_ALLOWED_FILES = PRE_CONTENT_READY_ALLOWED_FILES - PREVIOUS_REMOVED_FROM_ALLOWLIST
LEGACY_REMOVED_FROM_ALLOWLIST = frozenset({
    "app/scripts/browser/sanitize.mjs",
    "app/scripts/collectors/amazon-order-utils.mjs",
    "app/scripts/collectors/csv.mjs",
    "app/scripts/collectors/walmart-order-utils.mjs",
})
LEGACY_ALLOWED_FILES = PREVIOUS_ALLOWED_FILES - LEGACY_REMOVED_FROM_ALLOWLIST
SUPPORTED_RELEASE_FILESETS = (
    ALLOWED_FILES,
    PRE_PAYMENT_TEXT_ALLOWED_FILES,
    PRE_CONTENT_READY_ALLOWED_FILES,
    PREVIOUS_ALLOWED_FILES,
    LEGACY_ALLOWED_FILES,
)
SUPPORTED_RELEASE_FILES = frozenset().union(*SUPPORTED_RELEASE_FILESETS)


class ReleaseError(RuntimeError):
    pass


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_release_id(value):
    value = str(value or "")
    if not RELEASE_ID_PATTERN.fullmatch(value):
        raise ReleaseError(
            "Release id must use YYYYMMDDTHHMMSSZ followed by a Git hash."
        )
    return value


def sha256_bytes(content):
    return hashlib.sha256(content).hexdigest()


def source_mapping(source_root):
    source_root = Path(source_root).resolve()
    mapping = {
        archive: source_root / relative
        for archive, relative in SERVICE_SOURCE_PATHS.items()
    }
    mapping.update({
        f"app/{relative}": source_root / relative
        for relative in REQUIRED_SOURCES
    })
    return mapping


def checked_source_bytes(source_root):
    source_root = Path(source_root).resolve()
    rows = {}
    for archive_path, source in source_mapping(source_root).items():
        try:
            source.resolve(strict=True).relative_to(source_root)
        except (FileNotFoundError, ValueError) as exc:
            raise ReleaseError(
                f"Release source is missing or outside the workspace: {archive_path}"
            ) from exc
        if source.is_symlink() or not source.is_file():
            raise ReleaseError(
                f"Release source must be a regular non-symlink file: {archive_path}"
            )
        size = source.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ReleaseError(f"Release source is too large: {archive_path}")
        rows[archive_path] = source.read_bytes()
    if set(rows) != set(ALLOWED_FILES):
        raise ReleaseError("The release source allowlist is inconsistent.")
    return rows


def build_manifest(source_root, release_id, source_commit=""):
    release_id = validate_release_id(release_id)
    source_commit = str(source_commit or release_id.rsplit("-", 1)[-1]).lower()
    if not re.fullmatch(r"[0-9a-f]{7,40}", source_commit):
        raise ReleaseError("Source commit must be a Git hash.")
    release_hash = release_id.rsplit("-", 1)[-1]
    if not source_commit.startswith(release_hash):
        raise ReleaseError("Release id hash must be a prefix of the source commit.")
    contents = checked_source_bytes(source_root)
    files = [
        {
            "path": path,
            "size": len(contents[path]),
            "sha256": sha256_bytes(contents[path]),
        }
        for path in sorted(contents)
    ]
    return {
        "contract": CONTRACT,
        "release_id": release_id,
        "source_commit": source_commit,
        "created_at": now_iso(),
        "files": files,
    }, contents


def zip_info(name):
    info = zipfile.ZipInfo(name)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def build_bundle(source_root, output, release_id, source_commit=""):
    manifest, contents = build_manifest(
        source_root,
        release_id,
        source_commit=source_commit,
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            archive.writestr(
                zip_info("manifest.json"),
                (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8"),
            )
            for path in sorted(contents):
                archive.writestr(zip_info(path), contents[path])
        if temporary.stat().st_size > MAX_BUNDLE_BYTES:
            raise ReleaseError("Release bundle exceeds the size limit.")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def run_git(root, arguments, *, binary=False):
    result = subprocess.run(
        ["git", "-C", str(Path(root).resolve()), *arguments],
        capture_output=True,
        text=not binary,
        encoding=None if binary else "utf-8",
        errors=None if binary else "replace",
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr if binary else str(result.stderr or result.stdout or "")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise ReleaseError(f"Git verification failed: {' '.join(detail.split())[-300:]}")
    return result.stdout


def verify_git_source(source_root, source_commit, release_id):
    source_root = Path(source_root).resolve()
    release_id = validate_release_id(release_id)
    source_commit = str(source_commit or "").lower()
    head = str(run_git(source_root, ["rev-parse", "HEAD"])).strip().lower()
    resolved = str(
        run_git(source_root, ["rev-parse", f"{source_commit}^{{commit}}"])
    ).strip().lower()
    if head != resolved or source_commit != resolved:
        raise ReleaseError("Release source commit must be the exact current HEAD.")
    if not resolved.startswith(release_id.rsplit("-", 1)[-1]):
        raise ReleaseError("Release id hash does not identify the current HEAD.")
    status = str(
        run_git(source_root, ["status", "--porcelain", "--untracked-files=all"])
    )
    if status.strip():
        raise ReleaseError("Release source worktree is not clean.")
    repository_root = Path(str(run_git(source_root, ["rev-parse", "--show-toplevel"])).strip()).resolve()
    for archive_path, source in source_mapping(source_root).items():
        relative = source.resolve(strict=True).relative_to(repository_root).as_posix()
        committed = run_git(
            source_root,
            ["show", f"{resolved}:{relative}"],
            binary=True,
        )
        if source.read_bytes() != committed:
            raise ReleaseError(
                f"Release source does not match HEAD: {archive_path}"
            )
    return resolved


def parse_manifest(payload):
    if not isinstance(payload, dict) or set(payload) != {
        "contract",
        "release_id",
        "source_commit",
        "created_at",
        "files",
    }:
        raise ReleaseError("Release manifest structure is invalid.")
    if payload.get("contract") != CONTRACT:
        raise ReleaseError("Release manifest contract is invalid.")
    validate_release_id(payload.get("release_id"))
    if not re.fullmatch(r"[0-9a-f]{7,40}", str(payload.get("source_commit") or "")):
        raise ReleaseError("Release manifest source commit is invalid.")
    if not str(payload["source_commit"]).startswith(
        str(payload["release_id"]).rsplit("-", 1)[-1]
    ):
        raise ReleaseError("Release manifest id and source commit disagree.")
    files = payload.get("files")
    if not isinstance(files, list):
        raise ReleaseError("Release manifest files are invalid.")
    rows = {}
    for row in files:
        if not isinstance(row, dict) or set(row) != {"path", "size", "sha256"}:
            raise ReleaseError("Release manifest contains an invalid file record.")
        path = str(row.get("path") or "")
        size = row.get("size")
        digest = str(row.get("sha256") or "")
        if path not in SUPPORTED_RELEASE_FILES or path in rows:
            raise ReleaseError("Release manifest contains an unexpected file path.")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or size > MAX_FILE_BYTES
            or not SHA256_PATTERN.fullmatch(digest)
        ):
            raise ReleaseError("Release manifest file metadata is invalid.")
        rows[path] = {"size": size, "sha256": digest}
    if not any(set(rows) == set(allowed) for allowed in SUPPORTED_RELEASE_FILESETS):
        raise ReleaseError("Release manifest does not contain the exact allowlist.")
    return payload, rows


def read_bundle(bundle_path):
    bundle_path = Path(bundle_path)
    if bundle_path.is_symlink() or not bundle_path.is_file():
        raise ReleaseError("Release bundle must be a regular non-symlink file.")
    if bundle_path.stat().st_size > MAX_BUNDLE_BYTES:
        raise ReleaseError("Release bundle exceeds the size limit.")
    try:
        with zipfile.ZipFile(bundle_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ReleaseError("Release bundle contains duplicate members.")
            expected = {"manifest.json", *ALLOWED_FILES}
            if set(names) != expected:
                raise ReleaseError("Release bundle contains unexpected or missing members.")
            for info in infos:
                mode = (info.external_attr >> 16) & 0o170000
                if info.is_dir() or mode == stat.S_IFLNK:
                    raise ReleaseError("Release bundle members must be regular files.")
                if info.file_size > MAX_FILE_BYTES:
                    raise ReleaseError("Release bundle member exceeds the size limit.")
            try:
                manifest_payload = json.loads(
                    archive.read("manifest.json").decode("utf-8")
                )
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
                raise ReleaseError("Release manifest is unreadable.") from exc
            manifest, expected_files = parse_manifest(manifest_payload)
            contents = {}
            for path, expected_row in expected_files.items():
                content = archive.read(path)
                if (
                    len(content) != expected_row["size"]
                    or sha256_bytes(content) != expected_row["sha256"]
                ):
                    raise ReleaseError("Release bundle checksum verification failed.")
                contents[path] = content
    except zipfile.BadZipFile as exc:
        raise ReleaseError("Release bundle is not a valid ZIP archive.") from exc
    return manifest, contents


def managed_child(root, *parts):
    root = Path(root).resolve()
    candidate = root.joinpath(*parts)
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise ReleaseError("Managed path escapes the release root.") from exc
    return candidate


def write_release_tree(destination, manifest, contents):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        for relative, content in contents.items():
            path = managed_child(destination, *Path(relative).parts)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.parent.is_symlink():
                raise ReleaseError("Release destination contains a symbolic link.")
            path.write_bytes(content)
            os.chmod(path, 0o600)
        manifest_path = destination / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(manifest_path, 0o600)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def seal_release_tree(release_dir):
    """Make an already validated release immutable to the service account."""
    release_dir = Path(release_dir)
    validate_release_tree(release_dir)
    for path in sorted(release_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_file():
            os.chmod(path, 0o400)
        elif path.is_dir():
            os.chmod(path, 0o500)
    os.chmod(release_dir, 0o500)


def validate_release_tree(release_dir):
    release_dir = Path(release_dir)
    if release_dir.is_symlink() or not release_dir.is_dir():
        raise ReleaseError("Release directory is invalid.")
    manifest_path = release_dir / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ReleaseError("Release manifest is missing.")
    try:
        manifest, expected = parse_manifest(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError("Release manifest is unreadable.") from exc
    actual_files = set()
    for path in release_dir.rglob("*"):
        if path.is_symlink():
            raise ReleaseError("Release tree cannot contain symbolic links.")
        if not path.is_file():
            continue
        relative = path.relative_to(release_dir).as_posix()
        actual_files.add(relative)
        if relative == "manifest.json":
            continue
        if relative not in expected:
            raise ReleaseError("Release tree contains an unexpected file.")
        content = path.read_bytes()
        if (
            len(content) != expected[relative]["size"]
            or sha256_bytes(content) != expected[relative]["sha256"]
        ):
            raise ReleaseError("Release tree checksum verification failed.")
    if actual_files != {"manifest.json", *expected}:
        raise ReleaseError("Release tree is incomplete.")
    return manifest


def run_checked(command, cwd, label, env=None):
    result = subprocess.run(
        [str(item) for item in command],
        cwd=cwd,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        detail = " ".join(
            (result.stderr or result.stdout or "validation failed").split()
        )[-400:]
        raise ReleaseError(f"{label} failed: {detail}")


def validation_workspace(release_dir, fallback_roots=()):
    release_dir = Path(release_dir)
    app_root = release_dir / "app"
    workspace = release_dir / ".validation-workspace"
    shutil.copytree(app_root, workspace)
    try:
        manifest = json.loads(
            (workspace / "config/collectors.json").read_text(
                encoding="utf-8-sig"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError("Collector manifest is unreadable during staging.") from exc
    entrypoints = set()
    for collector in manifest.get("collectors", []):
        if not isinstance(collector, dict):
            continue
        for phase in ("collect", "import"):
            row = collector.get(phase)
            if isinstance(row, dict):
                relative = str(row.get("entrypoint") or "")
                path = Path(relative)
                if relative and not path.is_absolute() and ".." not in path.parts:
                    entrypoints.add(path)
    for relative in entrypoints:
        target = managed_child(workspace, *relative.parts)
        if target.is_file():
            continue
        source = None
        for fallback in fallback_roots:
            candidate = managed_child(fallback, *relative.parts)
            if candidate.is_file() and not candidate.is_symlink():
                source = candidate
                break
        if source is None:
            raise ReleaseError(
                f"Collector manifest validation dependency is missing: {relative.as_posix()}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return workspace


def run_staged_validation(release_dir, python, node, fallback_roots=()):
    release_dir = Path(release_dir)
    app_root = release_dir / "app"
    python_files = [
        release_dir / "vps_backend_service.py",
        release_dir / "vps_login_session.py",
        *(release_dir / path for path in ALLOWED_FILES if path.endswith(".py") and path.startswith("app/")),
    ]
    node_files = [
        release_dir / path
        for path in sorted(ALLOWED_FILES)
        if path.endswith(".mjs")
    ]
    cache_dir = release_dir / ".validation-pycache"
    env = dict(os.environ)
    env["PYTHONPYCACHEPREFIX"] = str(cache_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        run_checked(
            [python, "-m", "py_compile", *python_files],
            release_dir,
            "Python compile validation",
            env=env,
        )
        for path in node_files:
            run_checked(
                [node, "--check", path],
                release_dir,
                f"Node syntax validation for {path.name}",
                env=env,
            )
        workspace = validation_workspace(release_dir, fallback_roots=fallback_roots)
        try:
            run_checked(
                [python, workspace / "scripts/collectors/runner.py", "validate"],
                workspace,
                "Collector manifest validation",
                env=env,
            )
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)
    validate_release_tree(release_dir)


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_login_state(base_dir):
    state_path = managed_child(base_dir, "runtime", "login-session.json")
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError("VPS login session state is unreadable.") from exc
    if not isinstance(payload, dict):
        raise ReleaseError("VPS login session state is invalid.")
    return payload


def login_session_active(base_dir):
    payload = _read_login_state(base_dir)
    return any(
        process_alive(value)
        for key, value in payload.items()
        if key.endswith("_pid")
    )


def login_status_active(base_dir, python):
    base_dir = Path(base_dir).resolve()
    candidates = [managed_child(base_dir, "vps_login_session.py")]
    current = managed_child(base_dir, "current")
    if current.is_symlink():
        candidates.append(managed_child(current, "vps_login_session.py"))
    seen = set()
    for script in candidates:
        try:
            resolved = script.resolve(strict=True)
        except FileNotFoundError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        result = subprocess.run(
            [str(python), "-B", str(resolved), "status"],
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            raise ReleaseError("A VPS login status check failed.")
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        try:
            payload = json.loads(lines[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise ReleaseError("A VPS login status check returned invalid JSON.") from exc
        if payload.get("status") not in {"stopped", "running", "failed"}:
            raise ReleaseError("A VPS login status check returned an invalid state.")
        if payload.get("status") != "stopped":
            return True
    return False


def login_ports_active(host="127.0.0.1", ports=LOGIN_PORTS):
    for port in ports:
        try:
            with socket.create_connection((host, int(port)), timeout=0.2):
                return True
        except (OSError, TimeoutError):
            continue
    return False


def managed_login_processes(base_dir, proc_root=Path("/proc")):
    if os.name == "nt" or not Path(proc_root).is_dir():
        return []
    base_text = str(Path(base_dir).resolve())
    markers = (
        f"--user-data-dir={base_text}/profiles/",
        "Xvfb\x00:91",
        "x11vnc\x00-display\x00:91",
        "websockify\x00127.0.0.1:6081",
        "--remote-debugging-port=9224",
    )
    matches = []
    owner_uid = os.getuid()
    for process_dir in Path(proc_root).iterdir():
        if not process_dir.name.isdigit():
            continue
        try:
            if process_dir.stat().st_uid != owner_uid:
                continue
            command = (process_dir / "cmdline").read_bytes().decode(
                "utf-8", errors="replace"
            )
        except OSError:
            continue
        if any(marker in command for marker in markers):
            matches.append(int(process_dir.name))
    return matches


@contextmanager
def operation_guard(base_dir, python=DEFAULT_PYTHON):
    runtime = managed_child(base_dir, "runtime")
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = runtime / "collection.lock"
    if lock_path.is_symlink():
        raise ReleaseError("Collection lock cannot be a symbolic link.")
    handle = lock_path.open("a+b")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ReleaseError("A VPS collection is active.") from exc
        if (
            login_session_active(base_dir)
            or login_status_active(base_dir, python)
            or login_ports_active()
            or managed_login_processes(base_dir)
        ):
            raise ReleaseError("A VPS login session is active.")
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def current_release_id(base_dir):
    base_dir = Path(base_dir).resolve()
    current = managed_child(base_dir, "current")
    if not current.exists() and not current.is_symlink():
        return ""
    if not current.is_symlink():
        raise ReleaseError("Current release pointer is not a symbolic link.")
    try:
        target = current.resolve(strict=True)
        relative = target.relative_to(managed_child(base_dir, "releases").resolve())
    except (FileNotFoundError, ValueError) as exc:
        raise ReleaseError("Current release pointer is invalid.") from exc
    if len(relative.parts) != 1:
        raise ReleaseError("Current release pointer does not target one release.")
    return validate_release_id(relative.name)


def pointer_release_id(base_dir, name):
    if name not in {"current", "previous"}:
        raise ReleaseError("Release pointer name is invalid.")
    base_dir = Path(base_dir).resolve()
    pointer = managed_child(base_dir, name)
    if not pointer.exists() and not pointer.is_symlink():
        return ""
    if not pointer.is_symlink():
        raise ReleaseError(f"{name.title()} release pointer is not a symbolic link.")
    try:
        target = pointer.resolve(strict=True)
        relative = target.relative_to(managed_child(base_dir, "releases").resolve())
    except (FileNotFoundError, ValueError) as exc:
        raise ReleaseError(f"{name.title()} release pointer is invalid.") from exc
    if len(relative.parts) != 1:
        raise ReleaseError(f"{name.title()} pointer does not target one release.")
    return validate_release_id(relative.name)


def atomic_pointer(base_dir, name, release_id):
    if name not in {"current", "previous"}:
        raise ReleaseError("Release pointer name is invalid.")
    release_id = validate_release_id(release_id)
    base_dir = Path(base_dir).resolve()
    target = managed_child(base_dir, "releases", release_id)
    validate_release_tree(target)
    pointer = managed_child(base_dir, name)
    if pointer.exists() and not pointer.is_symlink():
        raise ReleaseError(f"{name.title()} release pointer is not a symbolic link.")
    temporary = managed_child(base_dir, f".{name}.{os.getpid()}.tmp")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    try:
        os.symlink(os.path.relpath(target, base_dir), temporary, target_is_directory=True)
        if os.name == "nt" and pointer.is_symlink():
            pointer.unlink()
        os.replace(temporary, pointer)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_switch(base_dir, release_id):
    atomic_pointer(base_dir, "current", release_id)


def probe_current_status(base_dir, python):
    service = managed_child(base_dir, "current", "vps_backend_service.py")
    result = subprocess.run(
        [str(python), "-B", str(service), "status"],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        return False
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return False
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return False
    return payload.get("status") == "ready"


def safe_remove_release(releases_dir, path):
    releases_dir = Path(releases_dir).resolve()
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(releases_dir)
    except (FileNotFoundError, ValueError):
        return False
    if len(relative.parts) != 1 or not RELEASE_ID_PATTERN.fullmatch(relative.name):
        return False
    for child in resolved.rglob("*"):
        if child.is_dir() and not child.is_symlink():
            os.chmod(child, 0o700)
        elif child.is_file() and not child.is_symlink():
            os.chmod(child, 0o600)
    os.chmod(resolved, 0o700)
    shutil.rmtree(resolved)
    return True


def prune_releases(base_dir, current_id, previous_id="", retain=DEFAULT_RETAIN):
    releases = managed_child(base_dir, "releases")
    if not releases.is_dir():
        return []
    valid = sorted(
        (
            path
            for path in releases.iterdir()
            if path.is_dir()
            and not path.is_symlink()
            and RELEASE_ID_PATTERN.fullmatch(path.name)
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    protected = {
        identifier
        for identifier in (current_id, previous_id)
        if identifier
    }
    protected.update(path.name for path in valid[:max(0, int(retain))])
    removed = []
    for path in valid:
        if path.name not in protected and safe_remove_release(releases, path):
            removed.append(path.name)
    return removed


def legacy_release_payload(base_dir):
    base_dir = Path(base_dir).resolve()
    mapping = {
        "vps_backend_service.py": managed_child(base_dir, "vps_backend_service.py"),
        "vps_login_session.py": managed_child(base_dir, "vps_login_session.py"),
    }
    mapping.update({
        f"app/{relative}": managed_child(base_dir, "app", *Path(relative).parts)
        for relative in REQUIRED_SOURCES
    })
    contents = {}
    digest = hashlib.sha256()
    for archive_path in sorted(mapping):
        source = mapping[archive_path]
        if source.is_symlink() or not source.is_file():
            raise ReleaseError(
                f"Legacy collector source is missing or unsafe: {archive_path}"
            )
        content = source.read_bytes()
        if len(content) > MAX_FILE_BYTES:
            raise ReleaseError(f"Legacy collector source is too large: {archive_path}")
        contents[archive_path] = content
        digest.update(archive_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
    source_commit = digest.hexdigest()[:40]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    release_id = f"{timestamp}-{source_commit[:12]}"
    manifest = {
        "contract": CONTRACT,
        "release_id": release_id,
        "source_commit": source_commit,
        "created_at": now_iso(),
        "files": [
            {
                "path": path,
                "size": len(contents[path]),
                "sha256": sha256_bytes(contents[path]),
            }
            for path in sorted(contents)
        ],
    }
    parse_manifest(manifest)
    return manifest, contents


def ensure_bootstrap_release(base_dir, python, node):
    base_dir = Path(base_dir).resolve()
    existing = current_release_id(base_dir)
    if existing:
        return existing
    manifest, contents = legacy_release_payload(base_dir)
    release_id = manifest["release_id"]
    releases = managed_child(base_dir, "releases")
    staging = managed_child(base_dir, "staging")
    releases.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging.mkdir(parents=True, exist_ok=True, mode=0o700)
    release_dir = managed_child(releases, release_id)
    stage = managed_child(staging, f".{release_id}.bootstrap.{os.getpid()}")
    prior_previous = pointer_release_id(base_dir, "previous")
    reuse_existing = False
    if release_dir.exists() or release_dir.is_symlink():
        existing_manifest = validate_release_tree(release_dir)
        if existing_manifest.get("source_commit") != manifest.get("source_commit"):
            raise ReleaseError("Legacy bootstrap release id already exists.")
        reuse_existing = True
    try:
        if not reuse_existing:
            write_release_tree(stage, manifest, contents)
            run_staged_validation(
                stage,
                python,
                node,
                fallback_roots=(managed_child(base_dir, "app"),),
            )
            os.replace(stage, release_dir)
            try:
                seal_release_tree(release_dir)
            except Exception:
                safe_remove_release(releases, release_dir)
                raise
        try:
            atomic_pointer(base_dir, "current", release_id)
            atomic_pointer(base_dir, "previous", release_id)
            ready = probe_current_status(base_dir, python)
        except Exception:
            managed_child(base_dir, "current").unlink(missing_ok=True)
            if prior_previous:
                atomic_pointer(base_dir, "previous", prior_previous)
            else:
                managed_child(base_dir, "previous").unlink(missing_ok=True)
            raise
        if not ready:
            managed_child(base_dir, "current").unlink(missing_ok=True)
            if prior_previous:
                atomic_pointer(base_dir, "previous", prior_previous)
            else:
                managed_child(base_dir, "previous").unlink(missing_ok=True)
            raise ReleaseError("Legacy bootstrap release did not report ready.")
    finally:
        if stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage, ignore_errors=True)
    return release_id


def apply_release(
    base_dir,
    bundle,
    *,
    python=DEFAULT_PYTHON,
    node=DEFAULT_NODE,
    retain=DEFAULT_RETAIN,
):
    base_dir = Path(base_dir).resolve()
    if base_dir == Path(base_dir.anchor) or base_dir.is_symlink():
        raise ReleaseError("VPS collector base directory is unsafe.")
    base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest, contents = read_bundle(bundle)
    release_id = manifest["release_id"]
    releases = managed_child(base_dir, "releases")
    staging = managed_child(base_dir, "staging")
    releases.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging.mkdir(parents=True, exist_ok=True, mode=0o700)
    release_dir = managed_child(releases, release_id)
    if release_dir.exists() or release_dir.is_symlink():
        raise ReleaseError("Release id already exists and is immutable.")
    stage = managed_child(staging, f".{release_id}.{os.getpid()}")
    if stage.exists() or stage.is_symlink():
        raise ReleaseError("Release staging path already exists.")
    previous_id = ""
    with operation_guard(base_dir, python=python):
        try:
            previous_id = ensure_bootstrap_release(base_dir, python, node)
            write_release_tree(stage, manifest, contents)
            fallback_roots = [managed_child(base_dir, "app")]
            current = managed_child(base_dir, "current")
            if current.is_symlink():
                fallback_roots.insert(0, managed_child(current, "app"))
            run_staged_validation(
                stage,
                python,
                node,
                fallback_roots=fallback_roots,
            )
            os.replace(stage, release_dir)
            try:
                seal_release_tree(release_dir)
            except Exception:
                safe_remove_release(releases, release_dir)
                raise
            previous_id = current_release_id(base_dir)
            atomic_pointer(base_dir, "previous", previous_id)
            atomic_switch(base_dir, release_id)
            if not probe_current_status(base_dir, python):
                if previous_id:
                    atomic_switch(base_dir, previous_id)
                    restored = probe_current_status(base_dir, python)
                else:
                    managed_child(base_dir, "current").unlink(missing_ok=True)
                    restored = True
                if not restored:
                    raise ReleaseError(
                        "Post-switch status failed and the previous release did not recover."
                    )
                raise ReleaseError(
                    "Post-switch status was not ready; the previous release was restored."
                )
        finally:
            if stage.exists() and not stage.is_symlink():
                shutil.rmtree(stage, ignore_errors=True)
    removed = prune_releases(
        base_dir,
        release_id,
        previous_id=previous_id,
        retain=retain,
    )
    return {
        "contract": RESULT_CONTRACT,
        "action": "applied",
        "release_id": release_id,
        "previous_release_id": previous_id,
        "removed_release_ids": removed,
        "status": "ready",
    }


def rollback_release(base_dir, release_id, *, python=DEFAULT_PYTHON):
    base_dir = Path(base_dir).resolve()
    release_id = validate_release_id(release_id)
    target = managed_child(base_dir, "releases", release_id)
    validate_release_tree(target)
    with operation_guard(base_dir, python=python):
        previous_id = current_release_id(base_dir)
        if previous_id:
            atomic_pointer(base_dir, "previous", previous_id)
        atomic_switch(base_dir, release_id)
        if not probe_current_status(base_dir, python):
            if previous_id:
                atomic_switch(base_dir, previous_id)
                restored = probe_current_status(base_dir, python)
            else:
                restored = True
            if not restored:
                raise ReleaseError(
                    "Rollback target failed and the original release did not recover."
                )
            raise ReleaseError(
                "Rollback target status was not ready; the original release was restored."
            )
    return {
        "contract": RESULT_CONTRACT,
        "action": "rolled_back",
        "release_id": release_id,
        "previous_release_id": previous_id,
        "status": "ready",
    }


def print_json(payload):
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def parser():
    command_parser = argparse.ArgumentParser(
        description="Build and atomically activate VPS collector releases."
    )
    subparsers = command_parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--source-root", type=Path, required=True)
    plan_parser.add_argument("--release-id", required=True)
    plan_parser.add_argument("--source-commit", required=True)

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--source-root", type=Path, required=True)
    build_parser.add_argument("--output", type=Path, required=True)
    build_parser.add_argument("--release-id", required=True)
    build_parser.add_argument("--source-commit", required=True)
    build_parser.add_argument("--verify-git", action="store_true")

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    apply_parser.add_argument("--bundle", type=Path, required=True)
    apply_parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    apply_parser.add_argument("--node", type=Path, default=DEFAULT_NODE)
    apply_parser.add_argument("--retain", type=int, default=DEFAULT_RETAIN)

    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    rollback_parser.add_argument("--release-id", required=True)
    rollback_parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    return command_parser


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "plan":
            manifest, _ = build_manifest(
                args.source_root,
                args.release_id,
                source_commit=args.source_commit,
            )
            print_json({
                "contract": PLAN_CONTRACT,
                "action": "plan",
                "release": manifest,
                "file_count": len(manifest["files"]),
                "total_bytes": sum(row["size"] for row in manifest["files"]),
                "remote_writes": False,
            })
            return 0
        if args.command == "build":
            if args.verify_git:
                verify_git_source(
                    args.source_root,
                    args.source_commit,
                    args.release_id,
                )
            manifest = build_bundle(
                args.source_root,
                args.output,
                args.release_id,
                source_commit=args.source_commit,
            )
            if args.verify_git:
                verify_git_source(
                    args.source_root,
                    args.source_commit,
                    args.release_id,
                )
            print_json({
                "contract": RESULT_CONTRACT,
                "action": "built",
                "release_id": manifest["release_id"],
                "file_count": len(manifest["files"]),
            })
            return 0
        if args.command == "apply":
            if not 1 <= args.retain <= 20:
                raise ReleaseError("Retention must be between 1 and 20.")
            print_json(apply_release(
                args.base_dir,
                args.bundle,
                python=args.python,
                node=args.node,
                retain=args.retain,
            ))
            return 0
        print_json(rollback_release(
            args.base_dir,
            args.release_id,
            python=args.python,
        ))
        return 0
    except (OSError, ReleaseError, subprocess.SubprocessError) as exc:
        print_json({
            "contract": RESULT_CONTRACT,
            "status": "failed",
            "message": " ".join(str(exc).split())[:500],
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
