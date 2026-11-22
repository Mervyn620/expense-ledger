import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METRIC_PATTERN = re.compile(r"^([a-zA-Z0-9_]+)=(.+)$")
COLLECTOR_PLATFORMS = (
    "walmart_ca",
    "amazon_ca",
    "uber_eats",
    "the_ordinary_ca",
    "fantuan",
    "tnt_ca",
)
SELECTIVE_COLLECT_CAPABILITY = "collect_bundle_v2"


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_metrics(output):
    metrics = {}
    for line in output.splitlines():
        match = METRIC_PATTERN.match(line.strip())
        if not match:
            continue
        key, value = match.groups()
        try:
            metrics[key] = int(value)
        except ValueError:
            try:
                metrics[key] = float(value)
            except ValueError:
                metrics[key] = value
    return metrics


def atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class JobManager:
    def __init__(
        self,
        root=ROOT,
        python_executable=None,
        validation_status=None,
        timeout_seconds=900,
        collector_backend_manager=None,
    ):
        self.root = Path(root)
        self.python_executable = python_executable or sys.executable
        self.timeout_seconds = timeout_seconds
        self.collector_backend_manager = collector_backend_manager
        self.validation_status = Path(
            validation_status or (self.root / "exports" / "current" / "validation-status.json")
        )
        self._jobs = OrderedDict()
        self._active_id = None
        self._processes = {}
        self._shutting_down = False
        self._login_operation_active = False
        self._lock = threading.Lock()
        self._history_path = self.root / 'data' / 'job_history.json'
        self._history_error = False
        self._load_history()

    def _load_history(self):
        try:
            payload = json.loads(self._history_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict) or payload.get('contract') != 'expense-job-history/v1':
            return
        rows = payload.get('jobs', [])
        if not isinstance(rows, list):
            return
        interrupted = False
        for row in rows[-100:]:
            if not isinstance(row, dict) or not re.fullmatch(r'[0-9a-f]{12}', str(row.get('id', ''))):
                continue
            if row.get('status') not in {'queued', 'running', 'passed', 'partial', 'failed'}:
                continue
            row.setdefault('metrics', {})
            row.setdefault('output', '')
            row.setdefault('label', '历史任务')
            if row.get('status') in {'queued', 'running'}:
                interrupted = True
                row.update(status='failed', completed_at=now_iso(), exit_code=-1,
                           message='应用重启前任务未完成；请检查结果后重新运行。')
            self._jobs[row['id']] = row
        if interrupted:
            try:
                self._persist_history()
            except OSError:
                self._history_error = True

    def _persist_history(self):
        # Persist only summaries, not stdout, exception text, or arbitrary string metrics.
        rows = []
        for job in list(self._jobs.values())[-100:]:
            row = {key: job.get(key) for key in (
                'id', 'kind', 'label', 'status', 'started_at', 'completed_at', 'exit_code',
                'timeout_seconds')}
            row['metrics'] = {key: value for key, value in job.get('metrics', {}).items()
                              if isinstance(value, (int, float, bool))}
            row['output'] = ''
            row['message'] = '已保存运行摘要；详细输出仅保留在本次应用会话中。'
            rows.append(row)
        atomic_write_json(self._history_path, {
            'contract': 'expense-job-history/v1',
            'jobs': rows,
        })
        self._history_error = False

    def snapshot(self):
        with self._lock:
            jobs = [dict(job) for job in reversed(self._jobs.values())]
            return {
                "active_job_id": self._active_id,
                "history_warning": '运行摘要未能写入磁盘，请检查目录权限或剩余空间。' if self._history_error else None,
                "jobs": jobs[:100],
            }

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def start_validation(self):
        aggregate_validator = (
            self.root / "scripts" / "maintenance" / "validate_all.py"
        )
        validator = (
            aggregate_validator
            if aggregate_validator.exists()
            else self.root / "scripts" / "reporting" / "validate.py"
        )
        command = [
            self.python_executable,
            str(validator),
        ]
        return self._start("validate", "校验账本", command)

    def start_refresh(
        self,
        mode,
        collector_backend=None,
        collector_platforms=None,
        collector_recovery=False,
        _allow_login_operation=False,
    ):
        allowed = {"local", "stores", "scotia_login", "scotia", "all"}
        if mode not in allowed:
            raise ValueError(f"Unsupported refresh mode: {mode}")
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            raise RuntimeError("PowerShell was not found.")
        script = (
            self.root / "scripts" / "prepare-scotia-login.ps1"
            if mode == "scotia_login"
            else self.root / "scripts" / "login-and-reconcile-scotia.ps1"
            if mode == "scotia"
            else self.root / "scripts" / "weekly-expense-refresh.ps1"
        )
        command = [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ]
        resolved_backend = None
        job_timeout_seconds = None
        selected_platforms = list(collector_platforms or [])
        if len(selected_platforms) != len(set(selected_platforms)):
            raise ValueError("Collector platforms cannot contain duplicates.")
        if any(platform not in COLLECTOR_PLATFORMS for platform in selected_platforms):
            raise ValueError("Collector platforms contain an unsupported id.")
        if selected_platforms and mode not in {"stores", "all"}:
            raise ValueError("Collector platforms require a store refresh.")
        if mode in {"stores", "all"}:
            command.append("-CollectFromBrowser")
            effective_backend = collector_backend
            if selected_platforms and set(selected_platforms).issubset(
                self._local_only_platforms()
            ):
                effective_backend = "local"
            if self.collector_backend_manager is not None:
                resolution = self.collector_backend_manager.resolve(
                    effective_backend,
                    force=True,
                )
                resolved_backend = resolution["resolved_backend"]
            else:
                resolved_backend = collector_backend or "local"
            command.extend(["-CollectorBackend", resolved_backend])
            if selected_platforms:
                if resolved_backend == "vps":
                    if self.collector_backend_manager is None:
                        raise RuntimeError(
                            "无法确认 VPS 是否支持选择性重试；请刷新运行环境状态。"
                        )
                    backend_status = self.collector_backend_manager.status(
                        force=False,
                        selected_mode="vps",
                    )
                    capabilities = set(
                        backend_status.get("backends", {})
                        .get("vps", {})
                        .get("capabilities", [])
                    )
                    if SELECTIVE_COLLECT_CAPABILITY not in capabilities:
                        raise RuntimeError(
                            "VPS 尚未部署选择性重试能力（collect_bundle_v2），"
                            "请先更新 VPS 采集服务。"
                        )
                command.extend(["-CollectorPlatforms", ",".join(selected_platforms)])
                if collector_recovery:
                    command.append("-ContinueCollectorRecovery")
            elif collector_recovery:
                raise ValueError("Collector recovery requires selected platforms.")
            if resolved_backend == "vps":
                job_timeout_seconds = self._vps_refresh_timeout()
        if mode == "scotia":
            job_timeout_seconds = max(self.timeout_seconds, 1500)
        if mode == "all":
            command.append("-ReconcileScotia")
        labels = {
            "local": "重建本地报表",
            "stores": "更新商店账单",
            "scotia_login": "打开并尝试登录 Scotia",
            "scotia": "登录并自动对账 Scotia",
            "all": "更新商店与 Scotia",
        }
        if selected_platforms:
            platform_labels = {
                "walmart_ca": "Walmart",
                "amazon_ca": "Amazon",
                "uber_eats": "Uber Eats",
                "the_ordinary_ca": "The Ordinary",
                "fantuan": "饭团",
                "tnt_ca": "大统华",
            }
            labels[mode] = "采集 " + " / ".join(
                platform_labels[platform] for platform in selected_platforms
            )
        if resolved_backend:
            backend_label = "VPS" if resolved_backend == "vps" else "本地"
            labels[mode] = f"{labels[mode]} · {backend_label}"
        return self._start(
            f"refresh:{mode}",
            labels[mode],
            command,
            timeout_seconds=job_timeout_seconds,
            _allow_login_operation=_allow_login_operation,
        )

    def _local_only_platforms(self):
        config_path = getattr(
            self.collector_backend_manager,
            "config_path",
            self.root / "config" / "collector_backends.json",
        )
        try:
            payload = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return set()
        values = payload.get("local_only_platforms", [])
        if not isinstance(values, list):
            return set()
        return {
            value for value in values
            if isinstance(value, str) and value in COLLECTOR_PLATFORMS
        }

    def start_vps_login_session(self, platform):
        if platform not in COLLECTOR_PLATFORMS:
            raise ValueError("Unsupported collector platform.")
        self._begin_login_operation()
        try:
            payload = self._run_vps_login_action("Start", platform)
            tunnel = payload.get("tunnel") if isinstance(payload, dict) else None
            if not isinstance(tunnel, dict) or tunnel.get("running") is not True:
                raise RuntimeError("VPS 登录隧道未就绪。")
            return {
                "status": "ready",
                "platform": platform,
                "url": "http://127.0.0.1:6088/vnc.html?autoconnect=true&resize=scale&path=websockify",
            }
        finally:
            self._end_login_operation()

    def verify_vps_login_session(self, platform):
        if platform not in COLLECTOR_PLATFORMS:
            raise ValueError("Unsupported collector platform.")
        self._begin_login_operation()
        try:
            payload = self._run_vps_login_action("Verify", platform)
            remote = payload.get("remote") if isinstance(payload, dict) else None
            if not isinstance(remote, dict) or remote.get("status") != "authenticated":
                raise RuntimeError("VPS 页面尚未证明登录成功。")
            return {"status": "authenticated", "platform": platform}
        finally:
            self._end_login_operation()

    def verify_vps_login_and_start_refresh(self, platform):
        if platform not in COLLECTOR_PLATFORMS:
            raise ValueError("Unsupported collector platform.")
        self._begin_login_operation()
        try:
            payload = self._run_vps_login_action("Verify", platform)
            remote = payload.get("remote") if isinstance(payload, dict) else None
            if not isinstance(remote, dict) or remote.get("status") != "authenticated":
                raise RuntimeError("VPS 页面尚未证明登录成功。")
            return self.start_refresh(
                "stores",
                collector_backend="vps",
                collector_platforms=[platform],
                collector_recovery=True,
                _allow_login_operation=True,
            )
        finally:
            self._end_login_operation()

    def _begin_login_operation(self):
        with self._lock:
            if self._shutting_down:
                raise RuntimeError("The expense application is shutting down.")
            if self._login_operation_active:
                raise RuntimeError("另一个 VPS 登录操作正在进行。")
            if self._active_id:
                active = self._jobs.get(self._active_id)
                if active and active["status"] in {"queued", "running"}:
                    raise RuntimeError("采集任务运行期间不能更改 VPS 登录会话。")
            self._login_operation_active = True

    def _end_login_operation(self):
        with self._lock:
            self._login_operation_active = False

    def _run_vps_login_action(self, action, platform):
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            raise RuntimeError("PowerShell was not found.")
        script = self.root / "scripts" / "manage-vps-collector-login.ps1"
        if not script.is_file():
            raise RuntimeError("VPS 登录会话工具不存在。")
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Action",
                action,
                "-Platform",
                platform,
                "-NoOpen",
            ],
            cwd=self.root,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            if action == "Verify":
                raise RuntimeError("尚未确认登录成功；请完成登录后再验证。")
            raise RuntimeError("VPS 登录会话未能启动，请检查本地运行日志。")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("VPS 登录操作返回了无效状态。") from exc
        return payload

    def _vps_refresh_timeout(self):
        config_path = getattr(
            self.collector_backend_manager,
            "config_path",
            self.root / "config" / "collector_backends.json",
        )
        try:
            payload = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
            remote_timeout = int(
                payload.get("vps", {}).get("collection_timeout_seconds", 600)
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            remote_timeout = 600
        return max(120, min(3660, remote_timeout + 60))

    def start_price_watch(self):
        script = self.root / "scripts" / "price_watch" / "on_demand.py"
        if not script.exists():
            raise RuntimeError("Walmart price-watch runner was not found.")
        command = [
            self.python_executable,
            str(script),
            "--root",
            str(self.root),
            "--backend",
            "local",
        ]
        return self._start(
            "price_watch",
            "检查 Walmart 折扣 · 本地",
            command,
            timeout_seconds=180,
        )

    def _start(
        self,
        kind,
        label,
        command,
        timeout_seconds=None,
        _allow_login_operation=False,
    ):
        with self._lock:
            if self._shutting_down:
                raise RuntimeError("The expense application is shutting down.")
            if self._login_operation_active and not _allow_login_operation:
                raise RuntimeError("VPS 登录操作进行期间不能启动记账任务。")
            if self._active_id:
                active = self._jobs.get(self._active_id)
                if active and active["status"] in {"queued", "running"}:
                    raise RuntimeError("Another bookkeeping task is already running.")
            job_id = uuid.uuid4().hex[:12]
            job = {
                "id": job_id,
                "kind": kind,
                "label": label,
                "status": "queued",
                "started_at": None,
                "completed_at": None,
                "exit_code": None,
                "metrics": {},
                "output": "",
                "message": "",
                "timeout_seconds": timeout_seconds or self.timeout_seconds,
            }
            self._jobs[job_id] = job
            while len(self._jobs) > 100:
                self._jobs.popitem(last=False)
            self._active_id = job_id
            try:
                self._persist_history()
            except OSError as exc:
                self._active_id = None
                self._jobs.pop(job_id, None)
                self._history_error = True
                raise RuntimeError('无法保存运行摘要，任务尚未启动。') from exc

        worker = threading.Thread(
            target=self._run,
            args=(job_id, command),
            daemon=True,
            name=f"expense-job-{job_id}",
        )
        worker.start()
        return dict(job)

    def _run(self, job_id, command):
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            job["started_at"] = now_iso()
            kind = job["kind"]
            timeout_seconds = job["timeout_seconds"]
            try:
                self._persist_history()
            except OSError:
                job.update(status='failed', completed_at=now_iso(), exit_code=-1,
                           message='无法保存运行摘要，任务尚未启动。')
                self._active_id = None
                self._history_error = True
                return

        previous_validation_version = (
            self.validation_status.stat().st_mtime_ns
            if kind == "validate" and self.validation_status.exists()
            else None
        )

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                command,
                cwd=self.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=creation_flags,
            )
            with self._lock:
                self._processes[job_id] = process
            try:
                stdout, stderr = process.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                self._terminate_process_tree(process)
                # A descendant may still hold inherited pipes open. Never let
                # timeout cleanup leave the job permanently marked running.
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                raise RuntimeError(
                    f"Task exceeded the {timeout_seconds}-second safety timeout."
                ) from exc
            output = "\n".join(
                part.strip()
                for part in (stdout, stderr)
                if part and part.strip()
            )
            metrics = parse_metrics(output)
            status = "passed" if process.returncode == 0 else "failed"
            if (
                status == "passed"
                and kind.startswith("refresh:")
                and str(metrics.get("workflow_status", "")) == "partial"
            ):
                status = "partial"
            message = (
                "账本校验通过"
                if status == "passed" and kind == "validate"
                else self._scotia_login_message(metrics)
                if status == "passed" and kind == "refresh:scotia_login"
                else self._price_watch_message(metrics, status)
                if kind == "price_watch"
                else self._refresh_partial_message(metrics)
                if status == "partial"
                else "任务完成"
                if status == "passed"
                else "任务未完成，请查看结果"
            )
            completed = {
                "status": status,
                "completed_at": now_iso(),
                "exit_code": process.returncode,
                "metrics": metrics,
                "output": output[-16000:],
                "message": message,
            }
            if kind == "validate":
                persisted = self._read_fresh_validation_status(
                    previous_validation_version
                )
                persisted_is_stale_success = (
                    completed["status"] == "failed"
                    and persisted
                    and persisted.get("status") == "passed"
                )
                if persisted and not persisted_is_stale_success:
                    persisted = dict(persisted)
                    persisted["metrics"] = {
                        **completed["metrics"],
                        **persisted.get("metrics", {}),
                    }
                    completed.update({
                        "status": persisted.get("status", completed["status"]),
                        "completed_at": persisted.get(
                            "completed_at", completed["completed_at"]
                        ),
                        "exit_code": persisted.get(
                            "exit_code", completed["exit_code"]
                        ),
                        "metrics": persisted.get("metrics", completed["metrics"]),
                        "message": persisted.get("message", completed["message"]),
                    })
                else:
                    self._write_validation_status(completed)
            elif status == "failed" and kind.startswith("refresh:"):
                self._finalize_failed_refresh_state(
                    started_at=job["started_at"],
                    completed=completed,
                )
            with self._lock:
                self._jobs[job_id].update(completed)
        except Exception as exc:
            failed = {
                "status": "failed",
                "completed_at": now_iso(),
                "exit_code": -1,
                "metrics": {},
                "message": str(exc),
                "output": str(exc),
            }
            if kind == "validate":
                self._write_validation_status(failed)
            elif kind.startswith("refresh:"):
                self._finalize_failed_refresh_state(
                    started_at=job["started_at"],
                    completed=failed,
                )
            with self._lock:
                self._jobs[job_id].update(failed)
        finally:
            with self._lock:
                self._processes.pop(job_id, None)
                if self._active_id == job_id:
                    self._active_id = None
                try:
                    self._persist_history()
                except OSError:
                    self._history_error = True

    def _finalize_failed_refresh_state(self, started_at, completed):
        path = self.root / "exports" / "current" / "run-state.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        try:
            state_start = datetime.fromisoformat(str(payload.get("started_at")).replace("Z", "+00:00"))
            job_start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            # Script start follows job start. Preserve terminal evidence from
            # this run (or a newer run), but never preserve an old green result.
            current_state = state_start >= job_start
        except (TypeError, ValueError):
            current_state = False
        if current_state and payload.get("status") not in {None, "running"}:
            return
        if not current_state:
            payload = {}
        completed_at = str(completed.get("completed_at") or now_iso())
        try:
            start = datetime.fromisoformat(
                str(payload.get("started_at") or started_at).replace("Z", "+00:00")
            )
            finish = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            duration = max(0.0, (finish - start).total_seconds())
        except (TypeError, ValueError):
            duration = 0.0
        payload.update({
            "status": "failed",
            "started_at": payload.get("started_at") or started_at,
            "completed_at": completed_at,
            "duration_seconds": round(duration, 1),
            "message": str(completed.get("message") or "Refresh failed.")[:500],
        })
        atomic_write_json(path, payload)

    @staticmethod
    def _refresh_partial_message(metrics):
        if (
            str(metrics.get("collection_status", "")) == "partial"
            or str(metrics.get("collector_bundle_status", "")) == "partial"
        ):
            return "任务完成，但部分实时采集失败；已明确保留上次有效数据"
        return "任务完成并通过校验，但仍有需要查看的非采集警告"

    @staticmethod
    def _scotia_login_message(metrics):
        return {
            "logged_in": "Scotia 已登录，可以开始对账",
            "verification_required": "Scotia 等待你手动完成二次验证",
            "submitted": "Scotia 已提交登录，请检查是否需要验证",
            "manual_login_required": "请在专用浏览器中手动完成 Scotia 登录",
        }.get(
            str(metrics.get("scotia_login_status", "")),
            "Scotia 登录页面已打开",
        )

    @staticmethod
    def _price_watch_message(metrics, status):
        collector = str(metrics.get("price_watch_collector_status", ""))
        if status == "passed" and collector == "completed":
            checked = int(
                metrics.get(
                    "price_watch_checked",
                    metrics.get("price_watch_checks", 0),
                )
            )
            return (
                f"Walmart 检查完成：{checked} "
                f"个商品，{int(metrics.get('price_watch_deals', 0))} 个折扣"
            )
        return {
            "blocked": "Walmart 阻止了本次检查，请稍后再试",
            "cooldown": "Walmart 检查处于冷却期，请稍后再试",
            "login_required": "Walmart 专用浏览器需要重新登录",
            "wrong_store": "页面未确认 Square One 门店，未保存价格",
            "partial": "Walmart 只完成了部分商品检查",
            "failed": "Walmart 折扣检查失败",
        }.get(collector, "Walmart 折扣检查未完成")

    @staticmethod
    def _terminate_process_tree(process):
        if process.poll() is not None:
            return
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags,
                    check=False,
                    timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        if process.poll() is None:
            process.kill()

    def shutdown(self):
        with self._lock:
            self._shutting_down = True
            processes = list(self._processes.values())
        for process in processes:
            self._terminate_process_tree(process)

    def _read_fresh_validation_status(self, previous_version):
        if not self.validation_status.exists():
            return None
        if self.validation_status.stat().st_mtime_ns == previous_version:
            return None
        try:
            return json.loads(self.validation_status.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_validation_status(self, job):
        self.validation_status.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": job["status"],
            "completed_at": job["completed_at"],
            "exit_code": job["exit_code"],
            "metrics": job["metrics"],
            "message": job["message"],
        }
        temporary = self.validation_status.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.validation_status)
