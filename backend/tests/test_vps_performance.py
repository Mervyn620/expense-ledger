import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from test_vps_collector_bundle import LOGIN, SERVICE, CLIENT


class SessionPerformanceTests(unittest.TestCase):
    def test_zombie_pid_is_not_considered_running(self):
        for state in ("Z", "X"):
            with self.subTest(state=state), patch.object(LOGIN.os, "kill"), patch.object(Path, "read_text", return_value=f"42 (worker ) spaced) {state} 1 2"):
                self.assertFalse(LOGIN.process_alive(42))

    def test_live_process_and_proc_unavailable_keep_existing_liveness_check(self):
        with patch.object(LOGIN.os, "kill"), patch.object(Path, "read_text", return_value="42 (worker) S 1 2"):
            self.assertTrue(LOGIN.process_alive(42))
        with patch.object(LOGIN.os, "kill"), patch.object(Path, "read_text", side_effect=FileNotFoundError):
            self.assertTrue(LOGIN.process_alive(42))
        with patch.object(LOGIN.os, "kill", side_effect=ProcessLookupError):
            self.assertFalse(LOGIN.process_alive(42))

    def test_exited_child_does_not_exhaust_shutdown_grace_period(self):
        with patch.object(LOGIN, "process_alive", side_effect=[True,False,False]), patch.object(LOGIN.os, "killpg", create=True) as kill, patch.object(LOGIN.time, "sleep") as sleep:
            LOGIN.stop_process_group(42)
        sleep.assert_not_called()
        kill.assert_called_once_with(42, LOGIN.signal.SIGTERM)

    def test_collection_session_excludes_vnc_but_manual_login_keeps_it(self):
        for interactive, expected in ((False,2),(True,4)):
            with self.subTest(interactive=interactive), ExitStack() as stack:
                for name in ("require_collection_idle","stop_session","probe_home_proxy","wait_for"):
                    stack.enter_context(patch.object(LOGIN,name))
                stack.enter_context(patch.object(LOGIN,"prepare_profile",return_value=Path("fixture-profile")))
                stack.enter_context(patch.object(LOGIN,"find_browser",return_value="fixture-browser"))
                stack.enter_context(patch.object(LOGIN,"process_alive",return_value=True))
                stack.enter_context(patch.object(LOGIN,"session_status",return_value={"status":"running"}))
                stack.enter_context(patch.object(LOGIN.time,"sleep"))
                launch=stack.enter_context(patch.object(LOGIN,"launch",side_effect=[SimpleNamespace(pid=i) for i in range(100,104)]))
                write=stack.enter_context(patch.object(LOGIN,"atomic_write_json"))
                self.assertEqual(LOGIN.start_session("walmart_ca",interactive=interactive)["status"],"running")
                self.assertEqual(launch.call_count,expected)
                state=write.call_args.args[1]
                self.assertEqual("vnc_pid" in state,interactive)
                self.assertEqual("websockify_pid" in state,interactive)

    def test_failed_session_start_still_cleans_owned_processes(self):
        with ExitStack() as stack:
            for name in ("require_collection_idle","stop_session","probe_home_proxy"):
                stack.enter_context(patch.object(LOGIN,name))
            stack.enter_context(patch.object(LOGIN,"prepare_profile",return_value=Path("fixture-profile")))
            stack.enter_context(patch.object(LOGIN,"find_browser",return_value="fixture-browser"))
            stack.enter_context(patch.object(LOGIN,"launch",return_value=SimpleNamespace(pid=123)))
            stack.enter_context(patch.object(LOGIN,"wait_for",side_effect=LOGIN.SessionError("fixture timeout")))
            stack.enter_context(patch.object(Path,"unlink"))
            stop=stack.enter_context(patch.object(LOGIN,"stop_process_group"))
            with self.assertRaises(LOGIN.SessionError): LOGIN.start_session("walmart_ca",interactive=False)
            stop.assert_called_once_with(123)

    def test_timing_fields_are_allowlisted_finite_and_backward_compatible(self):
        self.assertEqual(CLIENT.timing_summary({}),{"remote":{},"client":{}})
        result=CLIENT.timing_summary({"timings":{"total_seconds":12.34567,"session_start_seconds":float("nan"),"bundle_seconds":-1,"secret":"not retained"},"client_timings":{"total_seconds":14,"connection_seconds":True,"remote_roundtrip_seconds":float("inf")}})
        self.assertEqual(result,{"remote":{"total_seconds":12.346},"client":{"total_seconds":14.0}})
