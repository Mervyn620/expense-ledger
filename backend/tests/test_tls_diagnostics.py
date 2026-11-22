import importlib.util
import json
import socket
import ssl
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


TLS = load_module(
    "expense_tls_diagnostics_test",
    ROOT / "scripts" / "collectors" / "tls_diagnostics.py",
)
CLIENT = load_module(
    "expense_vps_bundle_client_tls_test",
    ROOT / "scripts" / "collectors" / "vps_bundle_client.py",
)


def passed_row(platform, host, tls="TLSv1.3", alpn="h2"):
    return {
        "platform": platform,
        "host": host,
        "status": "passed",
        "category": "ok",
        "tls_version": tls,
        "alpn": alpn,
        "cipher": "TLS_AES_256_GCM_SHA384",
        "cipher_bits": 256,
        "certificate_days_remaining": 90,
        "attempts": 1,
        "elapsed_ms": 50,
    }


def diagnostic(route, browser="Chrome/150.0.0.0"):
    return {
        "contract": TLS.CONTRACT,
        "status": "passed",
        "route": route,
        "generated_at": "2026-07-31T12:00:00+00:00",
        "runtime": {"browser": browser},
        "targets": [passed_row(platform, host) for platform, host in TLS.TARGETS],
    }


class TlsDiagnosticsTest(unittest.TestCase):
    def test_error_categories_separate_tls_from_transient_transport(self):
        cert_error = ssl.SSLCertVerificationError(1, "certificate verify failed")
        self.assertEqual(TLS.classify_error(cert_error), "certificate_error")
        self.assertEqual(TLS.classify_error(ssl.SSLError("handshake")), "tls_handshake_failed")
        self.assertEqual(TLS.classify_error(socket.timeout()), "timeout")
        self.assertEqual(TLS.classify_error(TLS.SocksError()), "proxy_unavailable")
        self.assertNotIn("certificate_error", TLS.TRANSIENT_CATEGORIES)

    def test_collection_uses_fixed_allowlisted_targets(self):
        calls = []

        def probe(platform, host, **options):
            calls.append((platform, host, options["timeout"]))
            return passed_row(platform, host)

        payload = TLS.collect_diagnostics(
            route="local",
            timeout=4,
            probe=probe,
        )
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(
            [(platform, host) for platform, host, _timeout in calls],
            list(TLS.TARGETS),
        )
        self.assertTrue(all(timeout == 4 for _platform, _host, timeout in calls))

    def test_consistency_requires_matching_tls_and_alpn(self):
        local = diagnostic("local")
        remote = diagnostic("home_exit", browser="Chromium 150.0.0.0")
        payload = CLIENT.compare_tls_diagnostics(local, remote)
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["browser_consistency"]["major_gap"], 0)

        remote["targets"][0]["alpn"] = "http/1.1"
        payload = CLIENT.compare_tls_diagnostics(local, remote)
        self.assertEqual(payload["status"], "needs_review")
        self.assertIn("walmart_ca:alpn_mismatch", payload["issues"])

    def test_atomic_output_stays_valid_json(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "diagnostics.json"
            payload = diagnostic("local")
            TLS.atomic_write(
                output,
                (json.dumps(payload) + "\n").encode("utf-8"),
            )
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))["contract"],
                TLS.CONTRACT,
            )


if __name__ == "__main__":
    unittest.main()
