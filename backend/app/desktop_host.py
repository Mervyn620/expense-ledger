"""Owned desktop backend. No changes to the original project's entry point."""
import argparse
import asyncio
import hmac
import json
import os
from pathlib import Path
import socket

import uvicorn
from fastapi import Request
from fastapi.responses import JSONResponse

from app.main import app, jobs
from app.settings import ROOT
from app.desktop_collection import collection_summary

class DesktopLifecycle:
    def __init__(self, token, instance, manager):
        if len(token) < 32:
            raise ValueError("Desktop session token is missing.")
        self.token, self.instance, self.manager = token, instance, manager
        self.active_requests = 0
        self.stopping = False

    def authorized(self, supplied):
        return hmac.compare_digest(supplied or "", self.token)

    def status(self):
        snapshot = self.manager.snapshot()
        active_id = snapshot.get("active_job_id")
        active = next((j for j in snapshot["jobs"] if j["id"] == active_id), None)
        return {
            "instance": self.instance, "pid": os.getpid(), "workspace": str(ROOT),
            "busy": bool(active_id or self.active_requests or
                         getattr(self.manager, "_login_operation_active", False)),
            "active_requests": self.active_requests,
            "active_job": {k: active.get(k) for k in ("id", "kind", "label", "status", "started_at")} if active else None,
            "jobs": [{k: j.get(k) for k in ("id", "kind", "label", "status", "started_at", "completed_at")} for j in snapshot["jobs"][:10]],
            "stopping": self.stopping,
            "collection": collection_summary(ROOT),
        }

def configure(application, lifecycle, request_exit):
    @application.middleware("http")
    async def desktop_boundary(request: Request, call_next):
        path = request.url.path
        control = path.startswith("/api/desktop/")
        if control and not lifecycle.authorized(request.headers.get("X-Desktop-Token")):
            return JSONResponse({"detail": "Desktop session required."}, status_code=403)
        write = request.method in {"POST", "PATCH", "PUT", "DELETE"}
        origin = request.headers.get("origin")
        if write and origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Same-origin request required."}, status_code=403)
        if lifecycle.stopping and not control:
            return JSONResponse({"detail": "Application is closing."}, status_code=503)
        tracked = write and not control
        if tracked:
            lifecycle.active_requests += 1
        try:
            return await call_next(request)
        finally:
            if tracked:
                lifecycle.active_requests -= 1

    @application.get("/api/desktop/status")
    async def desktop_status():
        return lifecycle.status()

    @application.post("/api/desktop/shutdown")
    async def desktop_shutdown(force: bool = False):
        if lifecycle.status()["busy"] and not force:
            return JSONResponse({"detail": "Work is still running."}, status_code=409)
        lifecycle.stopping = True
        if force:
            await asyncio.to_thread(lifecycle.manager.shutdown)
        request_exit()
        return {"status": "stopping"}

def bind_listener(preferred):
    for port in range(preferred, preferred + 20):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", port))
            listener.listen(128)
            return listener
        except OSError:
            listener.close()
    raise RuntimeError("No free desktop service port. Close old desktop instances and retry.")

async def run(args):
    token = os.environ.pop("EXPENSE_DESKTOP_TOKEN", "")
    instance = os.environ.pop("EXPENSE_DESKTOP_INSTANCE", "")
    lifecycle = DesktopLifecycle(token, instance, jobs)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1",
        access_log=False, log_level="warning", timeout_graceful_shutdown=12))
    configure(app, lifecycle, lambda: setattr(server, "should_exit", True))
    listener = bind_listener(args.port)
    ready = Path(args.ready)
    try:
        task = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started and not task.done():
            await asyncio.sleep(.05)
        if not server.started:
            await task
            raise RuntimeError("Backend did not become ready.")
        payload = {"url": f"http://127.0.0.1:{listener.getsockname()[1]}",
                   "pid": os.getpid(), "instance": instance}
        ready.parent.mkdir(parents=True, exist_ok=True)
        temp = ready.with_suffix(".tmp")
        temp.write_text(json.dumps(payload), encoding="utf-8")
        temp.replace(ready)
        await task
    finally:
        listener.close()
        ready.unlink(missing_ok=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready", required=True)
    parser.add_argument("--port", type=int, default=8785)
    asyncio.run(run(parser.parse_args()))
