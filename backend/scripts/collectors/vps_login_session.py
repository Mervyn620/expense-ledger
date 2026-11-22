import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True

try:
    import fcntl
except ImportError:  # pragma: no cover - VPS-only locking on Linux.
    fcntl = None


CONTRACT = "expense-collector-login-session/v1"
BASE_DIR = Path("/home/ledger/apps/expense-collector")
PROFILE_ROOT = BASE_DIR / "profiles"
RUNTIME_DIR = BASE_DIR / "runtime"
LOG_DIR = RUNTIME_DIR / "logs"
STATE_PATH = RUNTIME_DIR / "login-session.json"
COLLECTION_LOCK_PATH = RUNTIME_DIR / "collection.lock"
DISPLAY = ":91"
CDP_PORT = 9224
VNC_PORT = 5901
WEB_PORT = 6081
HOME_PROXY_HOST = "127.0.0.1"
HOME_PROXY_PORT = 1080
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3
PLATFORMS = {
    "walmart_ca": {
        "label": "Walmart Canada",
        "url": "https://www.walmart.ca/orders",
        "hosts": {"www.walmart.ca", "walmart.ca"},
        "paths": ("/orders",),
    },
    "amazon_ca": {
        "label": "Amazon Canada",
        "url": "https://www.amazon.ca/gp/css/order-history",
        "hosts": {"www.amazon.ca", "amazon.ca"},
        "paths": ("/gp/css/order-history", "/your-orders"),
    },
    "the_ordinary_ca": {
        "label": "The Ordinary Canada",
        "url": "https://theordinary.com/en-ca/orders",
        "hosts": {"theordinary.com", "www.theordinary.com"},
        "paths": (
            "/en-ca/orders",
            "/on/demandware.store/Sites-deciem-Site/en_CA/Account-ProfileSettings",
        ),
    },
    "uber_eats": {
        "label": "Uber Eats",
        "url": "https://www.ubereats.com/orders",
        "hosts": {"www.ubereats.com", "ubereats.com"},
        "paths": ("/orders",),
    },
    "fantuan": {
        "label": "Fantuan",
        "url": "https://www.fantuanorder.com/zh-CN",
        "hosts": {"www.fantuanorder.com", "fantuanorder.com"},
        "paths": ("/zh-CN",),
    },
}
FIXED_LOG_NAMES = frozenset({
    "xvfb.log",
    "x11vnc.log",
    "websockify.log",
    *(f"{platform}-chromium.log" for platform in PLATFORMS),
})
LOGIN_MARKERS = (
    "login",
    "log-in",
    "signin",
    "sign-in",
    "sign in",
    "登录",
    "驗證",
    "验证",
)


class SessionError(RuntimeError):
    pass


def collection_is_active():
    if os.environ.get("EXPENSE_COLLECTOR_COLLECTION_ACTIVE") == "1":
        return False
    if fcntl is None:
        return False
    COLLECTION_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = COLLECTION_LOCK_PATH.open("a+b")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


def require_collection_idle():
    if collection_is_active():
        raise SessionError(
            "A VPS collection is active; manual login-session changes are temporarily unavailable."
        )


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_state():
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # kill(pid, 0) also succeeds for zombies. They cannot own live listeners,
    # and waiting for them here adds five seconds per exited session process.
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0]
        if state in {"Z", "X"}:
            return False
    except (OSError, IndexError):
        pass
    return True


def stop_process_group(pid):
    if not process_alive(pid):
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        return
    deadline = time.monotonic() + 5
    while process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if process_alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


def stop_session():
    require_collection_idle()
    state = read_state()
    for key in ("websockify_pid", "vnc_pid", "browser_pid", "xvfb_pid"):
        stop_process_group(state.get(key))
    STATE_PATH.unlink(missing_ok=True)
    return {
        "contract": CONTRACT,
        "status": "stopped",
        "message": "VPS登录会话已停止",
    }


def find_browser():
    managed = BASE_DIR / "runtime" / "chrome" / "chrome-linux64" / "chrome"
    if managed.is_file() and os.access(managed, os.X_OK):
        return str(managed)
    for name in ("google-chrome", "chromium", "chromium-browser"):
        executable = shutil.which(name)
        if executable:
            return executable
    cache = Path.home() / ".cache" / "ms-playwright"
    matches = sorted(cache.glob("chromium-*/chrome-linux*/chrome"), reverse=True)
    if not matches:
        raise SessionError("No Chromium runtime was found.")
    return str(matches[0])


def prepare_profile(platform):
    profile = PROFILE_ROOT / platform
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(PROFILE_ROOT, 0o700)
    os.chmod(profile, 0o700)
    default_dir = profile / "Default"
    default_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The VPS collector runs one platform at a time. Chromium's unclean-exit
    # session restore can otherwise reopen several stale order-history tabs and
    # make a collector attach to the wrong document. Remove only tab/session
    # restore metadata; authentication and site storage remain untouched.
    for path in (
        default_dir / "Current Session",
        default_dir / "Current Tabs",
        default_dir / "Last Session",
        default_dir / "Last Tabs",
    ):
        if path.is_file():
            path.unlink(missing_ok=True)
    sessions_dir = default_dir / "Sessions"
    if sessions_dir.is_dir():
        for pattern in ("Session_*", "Tabs_*"):
            for path in sessions_dir.glob(pattern):
                if path.is_file():
                    path.unlink(missing_ok=True)
    preferences_path = default_dir / "Preferences"
    try:
        preferences = json.loads(preferences_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        preferences = {}
    if not isinstance(preferences, dict):
        preferences = {}
    preferences["credentials_enable_service"] = False
    profile_preferences = preferences.setdefault("profile", {})
    if not isinstance(profile_preferences, dict):
        profile_preferences = {}
        preferences["profile"] = profile_preferences
    profile_preferences["password_manager_enabled"] = False
    profile_preferences["exit_type"] = "Normal"
    profile_preferences["exited_cleanly"] = True
    atomic_write_json(preferences_path, preferences)
    return profile


def rotate_log(name):
    if name not in FIXED_LOG_NAMES:
        raise SessionError("The requested VPS login log name is not allowed.")
    if LOG_DIR.is_symlink():
        raise SessionError("The VPS login log directory cannot be a symbolic link.")
    LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolved_log_dir = LOG_DIR.resolve()
    path = LOG_DIR / name
    if path.parent.resolve() != resolved_log_dir:
        raise SessionError("The requested VPS login log path escapes its directory.")
    candidates = [
        path,
        *(path.with_name(f"{path.name}.{index}") for index in range(1, LOG_BACKUP_COUNT + 1)),
    ]
    for candidate in candidates:
        if candidate.is_symlink():
            raise SessionError("VPS login logs cannot be symbolic links.")
        if candidate.exists() and not candidate.is_file():
            raise SessionError("A VPS login log path is not a regular file.")
    if not path.exists() or path.stat().st_size < LOG_MAX_BYTES:
        return path
    oldest = candidates[-1]
    oldest.unlink(missing_ok=True)
    for index in range(LOG_BACKUP_COUNT - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        destination = path.with_name(f"{path.name}.{index + 1}")
        if source.exists():
            os.replace(source, destination)
    os.replace(path, path.with_name(f"{path.name}.1"))
    return path


def open_log(name):
    path = rotate_log(name)
    handle = path.open("ab", buffering=0)
    try:
        os.chmod(path, 0o600)
    except Exception:
        handle.close()
        raise
    return handle


def launch(command, log_name, env=None):
    log = open_log(log_name)
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    finally:
        log.close()


def wait_for(predicate, description, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise SessionError(f"Timed out waiting for {description}.")


def cdp_targets():
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{CDP_PORT}/json/list",
            timeout=2,
        ) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def probe_home_proxy(destination, port=443, timeout=8):
    try:
        with socket.create_connection(
            (HOME_PROXY_HOST, HOME_PROXY_PORT),
            timeout=timeout,
        ) as connection:
            connection.settimeout(timeout)
            connection.sendall(b"\x05\x01\x00")
            if connection.recv(2) != b"\x05\x00":
                raise SessionError("The home-exit SOCKS tunnel rejected access.")
            host = destination.encode("idna")
            request = (
                b"\x05\x01\x00\x03"
                + bytes([len(host)])
                + host
                + int(port).to_bytes(2, "big")
            )
            connection.sendall(request)
            response = connection.recv(4)
            if len(response) != 4 or response[0] != 5 or response[1] != 0:
                raise SessionError(
                    "The home-exit SOCKS tunnel could not reach the platform."
                )
    except SessionError:
        raise
    except (OSError, TimeoutError) as exc:
        raise SessionError(
            "The home-exit SOCKS tunnel is unavailable; direct VPS login is prohibited."
        ) from exc


def safe_page_state(platform):
    target = PLATFORMS[platform]
    rows = []
    for page in cdp_targets():
        if not isinstance(page, dict) or page.get("type") != "page":
            continue
        parts = urlsplit(str(page.get("url") or ""))
        title = str(page.get("title") or "")
        rows.append({
            "host": parts.hostname or "",
            "path": parts.path[:120],
            "login_signal": any(
                marker in f"{parts.hostname or ''} {parts.path} {title}".lower()
                for marker in LOGIN_MARKERS
            ),
        })
    matching = [
        row for row in rows
        if row["host"] in target["hosts"]
        and any(
            row["path"].lower().startswith(path.lower())
            for path in target["paths"]
        )
    ]
    return {
        "pages": rows[:8],
        "candidate_authenticated": any(
            not row["login_signal"] for row in matching
        ),
    }


def start_session(platform, *, interactive=True):
    require_collection_idle()
    stop_session()
    target = PLATFORMS[platform]
    probe_home_proxy(sorted(target["hosts"])[0])
    profile = prepare_profile(platform)
    browser = find_browser()
    environment = os.environ.copy()
    environment["DISPLAY"] = DISPLAY

    xvfb = launch(
        [
            shutil.which("Xvfb") or "/usr/bin/Xvfb",
            DISPLAY,
            "-screen",
            "0",
            "1365x768x24",
            "-nolisten",
            "tcp",
            "-ac",
        ],
        "xvfb.log",
    )
    chromium = None
    vnc = None
    websockify = None
    try:
        wait_for(lambda: Path("/tmp/.X11-unix/X91").exists(), "virtual display")
        chromium = launch(
            [
                browser,
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-dev-shm-usage",
                "--disable-session-crashed-bubble",
                "--disable-save-password-bubble",
                "--disable-features=PasswordManagerOnboarding,SigninInterceptBubble",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                f"--proxy-server=socks5://{HOME_PROXY_HOST}:{HOME_PROXY_PORT}",
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={CDP_PORT}",
                "--window-size=1365,768",
                "--start-maximized",
                target["url"],
            ],
            f"{platform}-chromium.log",
            env=environment,
        )
        wait_for(lambda: bool(cdp_targets()), "Chromium debugging endpoint")
        if interactive:
            vnc = launch(
                [
                    shutil.which("x11vnc") or "/usr/bin/x11vnc",
                    "-display",
                    DISPLAY,
                    "-localhost",
                    "-rfbport",
                    str(VNC_PORT),
                    "-forever",
                    "-shared",
                    "-nopw",
                    "-noxdamage",
                ],
                "x11vnc.log",
                env=environment,
            )
            websockify = launch(
                [
                    shutil.which("websockify") or "/usr/bin/websockify",
                    "--web=/usr/share/novnc",
                    f"127.0.0.1:{WEB_PORT}",
                    f"127.0.0.1:{VNC_PORT}",
                ],
                "websockify.log",
                env=environment,
            )
        state = {
            "contract": CONTRACT,
            "platform": platform,
            "label": target["label"],
            "started_at": now_iso(),
            "xvfb_pid": xvfb.pid,
            "browser_pid": chromium.pid,
            "interactive": interactive,
        }
        if interactive:
            state.update(vnc_pid=vnc.pid, websockify_pid=websockify.pid)
        atomic_write_json(STATE_PATH, state)
        time.sleep(0.5)
        if not all(
            process_alive(state[key])
            for key in state if key.endswith("_pid")
        ):
            raise SessionError("A login-session component exited during startup.")
        return session_status()
    except Exception:
        for process in (websockify, vnc, chromium, xvfb):
            if process is not None:
                stop_process_group(process.pid)
        STATE_PATH.unlink(missing_ok=True)
        raise


def session_status():
    state = read_state()
    platform = state.get("platform")
    if platform not in PLATFORMS:
        return {
            "contract": CONTRACT,
            "status": "stopped",
            "message": "当前没有VPS登录会话",
        }
    components = {
        name.removesuffix("_pid"): process_alive(pid)
        for name, pid in state.items()
        if name.endswith("_pid")
    }
    running = bool(components) and all(components.values())
    page_state = safe_page_state(platform) if running else {
        "pages": [],
        "candidate_authenticated": False,
    }
    return {
        "contract": CONTRACT,
        "status": "running" if running else "failed",
        "message": (
            f"{PLATFORMS[platform]['label']} VPS登录会话正在运行"
            if running
            else "VPS登录会话组件异常"
        ),
        "platform": platform,
        "started_at": state.get("started_at", ""),
        "components": components,
        "page_state": page_state,
        "route": "home_exit",
        "web_port": WEB_PORT,
    }


def verify_session(platform=None):
    state = read_state()
    active_platform = state.get("platform")
    if active_platform not in PLATFORMS:
        raise SessionError("No running login session can be verified.")
    if platform and platform != active_platform:
        raise SessionError("The active login session is for a different platform.")
    current = session_status()
    if current["status"] != "running":
        raise SessionError("The login session is not healthy.")
    if not current["page_state"]["candidate_authenticated"]:
        raise SessionError("The current page does not prove an authenticated session.")
    marker = {
        "contract": "expense-collector-auth-marker/v1",
        "platform": active_platform,
        "verified_at": now_iso(),
        "route": "home_exit",
    }
    atomic_write_json(
        PROFILE_ROOT / active_platform / ".authenticated.json",
        marker,
    )
    return {
        "contract": CONTRACT,
        "status": "authenticated",
        "message": f"{PLATFORMS[active_platform]['label']} VPS会话已验证",
        "platform": active_platform,
        "verified_at": marker["verified_at"],
        "route": "home_exit",
    }


def parser():
    command_parser = argparse.ArgumentParser(
        description="Manage a loopback-only VPS browser login session."
    )
    command_parser.add_argument(
        "command",
        choices=["start", "status", "stop", "verify"],
    )
    command_parser.add_argument("--platform", choices=sorted(PLATFORMS))
    return command_parser


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "start":
            if not args.platform:
                raise SessionError("--platform is required for start.")
            payload = start_session(args.platform)
        elif args.command == "stop":
            payload = stop_session()
        elif args.command == "verify":
            payload = verify_session(args.platform)
        else:
            payload = session_status()
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0 if payload["status"] != "failed" else 1
    except (OSError, SessionError, subprocess.SubprocessError) as exc:
        print(json.dumps({
            "contract": CONTRACT,
            "status": "failed",
            "message": str(exc),
        }, ensure_ascii=False, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
