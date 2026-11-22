import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.desktop_host import DesktopLifecycle, configure, bind_listener

class Jobs:
    _login_operation_active = False
    busy = False
    stopped = False
    def snapshot(self):
        return {"active_job_id": "abc" if self.busy else None,
                "jobs": [{"id": "abc", "label": "test", "status": "running"}] if self.busy else []}
    def shutdown(self): self.stopped = True

class DesktopHostTests(unittest.TestCase):
    def setUp(self):
        self.jobs = Jobs()
        self.state = DesktopLifecycle("x" * 64, "test-instance", self.jobs)
        self.exited = False
        self.app = FastAPI()
        configure(self.app, self.state, self.exit)
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8785")
        self.headers = {"X-Desktop-Token": "x" * 64}
    def exit(self): self.exited = True
    def test_control_requires_session_token(self):
        self.assertEqual(self.client.get("/api/desktop/status").status_code, 403)
        self.assertEqual(self.client.post("/api/desktop/shutdown").status_code, 403)
        self.assertFalse(self.exited)
    def test_status_exposes_completion_kind_and_timestamps_without_logs(self):
        self.jobs.snapshot = lambda: {"active_job_id": None, "jobs": [{
            "id": "done", "kind": "refresh:stores", "label": "fixture", "status": "partial",
            "started_at": "2026-09-29T12:00:00Z", "completed_at": "2026-09-29T12:00:03Z",
            "stdout": "private fixture log"}]}
        job = self.state.status()["jobs"][0]
        self.assertEqual(job["kind"], "refresh:stores")
        self.assertEqual(job["completed_at"], "2026-09-29T12:00:03Z")
        self.assertNotIn("stdout", job)

    def test_idle_shutdown(self):
        result = self.client.post("/api/desktop/shutdown", headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertTrue(self.exited)
    def test_busy_shutdown_refuses(self):
        self.jobs.busy = True
        result = self.client.post("/api/desktop/shutdown", headers=self.headers)
        self.assertEqual(result.status_code, 409)
        self.assertFalse(self.exited)
        self.assertFalse(self.jobs.stopped)
    def test_explicit_force_stops_jobs(self):
        self.jobs.busy = True
        self.assertEqual(self.client.post("/api/desktop/shutdown?force=true", headers=self.headers).status_code, 200)
        self.assertTrue(self.exited)
        self.assertTrue(self.jobs.stopped)
    def test_cross_origin_localhost_port_rejected(self):
        headers = dict(self.headers, Origin="http://127.0.0.1:8765")
        self.assertEqual(self.client.post("/api/desktop/shutdown", headers=headers).status_code, 403)
        self.assertFalse(self.exited)
    def test_same_origin_allowed(self):
        headers = dict(self.headers, Origin="http://127.0.0.1:8785")
        self.assertEqual(self.client.post("/api/desktop/shutdown", headers=headers).status_code, 200)
    def test_closing_rejects_new_business_requests(self):
        @self.app.post("/api/test-write")
        def write(): raise AssertionError("must never write")
        self.state.stopping = True
        self.assertEqual(self.client.post("/api/test-write").status_code, 503)
    def test_analysis_inflight_blocks_shutdown(self):
        entered, release = threading.Event(), threading.Event()
        @self.app.post("/api/test-analysis")
        async def analysis():
            entered.set()
            await asyncio.to_thread(release.wait, 5)
            return {"status": "ok"}
        with ThreadPoolExecutor() as pool:
            future = pool.submit(self.client.post, "/api/test-analysis")
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.client.post("/api/desktop/shutdown", headers=self.headers).status_code, 409)
            finally: release.set()
            self.assertEqual(future.result().status_code, 200)
        self.assertEqual(self.state.active_requests, 0)
    def test_login_operation_counts_as_busy(self):
        self.jobs._login_operation_active = True
        self.assertTrue(self.state.status()["busy"])
    def test_port_conflict_uses_next_listener(self):
        first = bind_listener(28885)
        second = bind_listener(first.getsockname()[1])
        try:
            self.assertNotEqual(first.getsockname()[1], second.getsockname()[1])
        finally: first.close(); second.close()

if __name__ == "__main__": unittest.main()
