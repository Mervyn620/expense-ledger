import argparse
import json
import os
import socket
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import ProxyHandler, build_opener


CONTRACT = "expense-collector-tls-diagnostics/v1"
TARGETS = (
    ("walmart_ca", "www.walmart.ca"),
    ("amazon_ca", "www.amazon.ca"),
    ("the_ordinary_ca", "theordinary.com"),
    ("uber_eats", "www.ubereats.com"),
    ("fantuan", "www.fantuanorder.com"),
)
TRANSIENT_CATEGORIES = {
    "connection_reset",
    "dns_failed",
    "network_unavailable",
    "timeout",
}


class SocksError(OSError):
    pass


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _recv_exact(connection, length):
    output = bytearray()
    while len(output) < length:
        chunk = connection.recv(length - len(output))
        if not chunk:
            raise SocksError("SOCKS proxy closed the connection.")
        output.extend(chunk)
    return bytes(output)


def connect_socks5(proxy_host, proxy_port, destination, port, timeout):
    connection = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        connection.settimeout(timeout)
        connection.sendall(b"\x05\x01\x00")
        if _recv_exact(connection, 2) != b"\x05\x00":
            raise SocksError("SOCKS proxy rejected no-authentication mode.")
        encoded = destination.encode("idna")
        if not encoded or len(encoded) > 255:
            raise SocksError("SOCKS destination is invalid.")
        connection.sendall(
            b"\x05\x01\x00\x03"
            + bytes([len(encoded)])
            + encoded
            + int(port).to_bytes(2, "big")
        )
        response = _recv_exact(connection, 4)
        if response[0] != 5 or response[1] != 0:
            raise SocksError("SOCKS proxy could not reach the destination.")
        address_type = response[3]
        if address_type == 1:
            _recv_exact(connection, 4)
        elif address_type == 3:
            _recv_exact(connection, _recv_exact(connection, 1)[0])
        elif address_type == 4:
            _recv_exact(connection, 16)
        else:
            raise SocksError("SOCKS proxy returned an invalid address type.")
        _recv_exact(connection, 2)
        return connection
    except Exception:
        connection.close()
        raise


def classify_error(error, proxied=False):
    if isinstance(error, ssl.SSLCertVerificationError):
        return "certificate_error"
    if isinstance(error, ssl.SSLError):
        return "tls_handshake_failed"
    if isinstance(error, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(error, socket.gaierror):
        return "dns_failed"
    if isinstance(error, (ConnectionResetError, BrokenPipeError)):
        return "connection_reset"
    if isinstance(error, SocksError):
        return "proxy_unavailable"
    if isinstance(error, ConnectionRefusedError):
        return "proxy_unavailable" if proxied else "network_unavailable"
    return "network_unavailable"


def _open_transport(host, port, timeout, proxy_host=None, proxy_port=None):
    if proxy_host and proxy_port:
        return connect_socks5(proxy_host, proxy_port, host, port, timeout)
    return socket.create_connection((host, port), timeout=timeout)


def probe_target(
    platform,
    host,
    *,
    timeout=6,
    proxy_host=None,
    proxy_port=None,
    attempts=2,
    sleep=time.sleep,
):
    started = time.monotonic()
    proxied = bool(proxy_host and proxy_port)
    last_category = "network_unavailable"
    used_attempts = 0
    for attempt in range(1, max(1, attempts) + 1):
        used_attempts = attempt
        transport = None
        try:
            transport = _open_transport(
                host,
                443,
                timeout,
                proxy_host=proxy_host,
                proxy_port=proxy_port,
            )
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.set_alpn_protocols(["h2", "http/1.1"])
            with context.wrap_socket(transport, server_hostname=host) as secured:
                transport = None
                cipher = secured.cipher() or ("", "", 0)
                certificate = secured.getpeercert() or {}
                expires_at = certificate.get("notAfter")
                days_to_expiry = None
                if expires_at:
                    days_to_expiry = max(
                        0,
                        int((ssl.cert_time_to_seconds(expires_at) - time.time()) // 86400),
                    )
                return {
                    "platform": platform,
                    "host": host,
                    "status": "passed",
                    "category": "ok",
                    "tls_version": secured.version() or "",
                    "alpn": secured.selected_alpn_protocol() or "",
                    "cipher": str(cipher[0] or "")[:80],
                    "cipher_bits": int(cipher[2] or 0),
                    "certificate_days_remaining": days_to_expiry,
                    "attempts": used_attempts,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                }
        except Exception as error:
            last_category = classify_error(error, proxied=proxied)
            if last_category not in TRANSIENT_CATEGORIES or attempt >= attempts:
                break
            sleep(0.25 * attempt)
        finally:
            if transport is not None:
                transport.close()
    return {
        "platform": platform,
        "host": host,
        "status": "failed",
        "category": last_category,
        "tls_version": "",
        "alpn": "",
        "cipher": "",
        "cipher_bits": 0,
        "certificate_days_remaining": None,
        "attempts": used_attempts,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


def cdp_runtime(port, timeout=2):
    if not port:
        return {"status": "not_requested", "browser": "", "protocol_version": ""}
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{int(port)}/json/version", timeout=timeout) as response:
            content = response.read(65537)
        if len(content) > 65536:
            raise ValueError("CDP version response is too large.")
        payload = json.loads(content.decode("utf-8"))
        return {
            "status": "available",
            "browser": str(payload.get("Browser") or "")[:80],
            "protocol_version": str(payload.get("Protocol-Version") or "")[:20],
        }
    except Exception:
        return {"status": "unavailable", "browser": "", "protocol_version": ""}


def collect_diagnostics(
    *,
    route,
    timeout=6,
    proxy_host=None,
    proxy_port=None,
    browser_version="",
    cdp_port=None,
    probe=probe_target,
):
    targets = [
        probe(
            platform,
            host,
            timeout=timeout,
            proxy_host=proxy_host,
            proxy_port=proxy_port,
        )
        for platform, host in TARGETS
    ]
    cdp = cdp_runtime(cdp_port)
    browser = cdp.get("browser") or str(browser_version or "")[:80]
    passed = all(row.get("status") == "passed" for row in targets)
    return {
        "contract": CONTRACT,
        "status": "passed" if passed else "partial",
        "route": route,
        "generated_at": now_iso(),
        "runtime": {
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "openssl": str(ssl.OPENSSL_VERSION)[:100],
            "browser": browser,
            "cdp_status": cdp.get("status", "not_requested"),
            "cdp_protocol_version": cdp.get("protocol_version", ""),
        },
        "targets": targets,
    }


def parser():
    command_parser = argparse.ArgumentParser(
        description="Run sanitized TLS path diagnostics for expense collectors."
    )
    command_parser.add_argument(
        "--route", choices=["local", "home_exit"], required=True
    )
    command_parser.add_argument("--proxy-host", default="")
    command_parser.add_argument("--proxy-port", type=int)
    command_parser.add_argument("--timeout", type=int, default=6)
    command_parser.add_argument("--browser-version", default="")
    command_parser.add_argument("--cdp-port", type=int)
    command_parser.add_argument("--output", default="-")
    return command_parser


def main(argv=None):
    args = parser().parse_args(argv)
    if bool(args.proxy_host) != bool(args.proxy_port):
        raise SystemExit("--proxy-host and --proxy-port must be provided together.")
    if args.route == "home_exit" and not args.proxy_host:
        raise SystemExit("home_exit diagnostics require a SOCKS proxy.")
    if not 1 <= args.timeout <= 30:
        raise SystemExit("--timeout must be from 1 to 30 seconds.")
    payload = collect_diagnostics(
        route=args.route,
        timeout=args.timeout,
        proxy_host=args.proxy_host or None,
        proxy_port=args.proxy_port,
        browser_version=args.browser_version,
        cdp_port=args.cdp_port,
    )
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if args.output == "-":
        sys.stdout.buffer.write(encoded)
    else:
        atomic_write(args.output, encoded)
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
