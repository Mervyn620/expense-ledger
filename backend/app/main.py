import asyncio
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.analysis import AnalysisError, DeepSeekAnalysisService, VALID_MODES
from app.chatgpt_export import clean_temporary_exports, export_periods, prepare_export
from app.collector_backends import (
    CollectorBackendError,
    CollectorBackendManager,
    MODES as COLLECTOR_BACKEND_MODES,
)
from app.collector_recovery import collector_recovery_status
from app.jobs import JobManager
from app.manual_wechat import DuplicateRequestConflict, ManualWechatStore
from app.wechat_bills import compare_fantuan, parse_detail
from app.repository import CashNoteSupersededError, LedgerRepository
from app.ledger_review import confirm_match, set_product_rule
from app.settings import APP_ID, APP_VERSION, ROOT, STATIC_DIR
from scripts.importers import bank_statement
from app.setup_state import SetupError, SetupStore


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
WORKSPACE_ID = hashlib.sha256(
    str(ROOT).lower().encode("utf-8")
).hexdigest()[:12].upper()

repository = LedgerRepository()
manual_wechat = ManualWechatStore(repository.db_path)
setup_store = SetupStore(ROOT)
collector_backends = CollectorBackendManager()
jobs = JobManager(collector_backend_manager=collector_backends)
analysis_service = DeepSeekAnalysisService(repository)


def _overview_source_signature():
    paths = [repository.db_path, repository.db_path.with_name(repository.db_path.name + "-wal"),
             repository.db_path.parent.parent / "config" / "workflow.json"]
    return (datetime.now().astimezone().date(), *(
        (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None
        for path in paths
    ))


async def _maintain_chatgpt_overview():
    previous = None
    while True:
        current = _overview_source_signature()
        if current != previous:
            try:
                await asyncio.to_thread(prepare_export, repository, "trend")
                previous = current
            except AnalysisError as exc:
                if exc.status_code == 404:
                    previous = current
                else:
                    logging.getLogger(__name__).warning("ChatGPT overview refresh failed: %s", exc)
            except Exception:
                logging.getLogger(__name__).exception("ChatGPT overview refresh failed")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_app):
    if not repository.db_path.exists():
        # A brand-new workspace (first launch before any import) gets an empty ledger, so the
        # app opens and the welcome guide can run. An existing ledger is never touched here.
        from scripts.maintenance.initialize_workspace import InitializationError, initialize_workspace
        try:
            initialize_workspace(ROOT)
        except (InitializationError, OSError):
            logging.getLogger(__name__).exception("New workspace could not be initialized")
    repository.ensure_schema()
    try:
        clean_temporary_exports(repository)
    except OSError:
        logging.getLogger(__name__).exception("ChatGPT temporary export cleanup failed")
    overview_task = asyncio.create_task(_maintain_chatgpt_overview())
    try:
        yield
    finally:
        overview_task.cancel()
        with suppress(asyncio.CancelledError):
            await overview_task
        jobs.shutdown()


app = FastAPI(
    title="Expense Ledger",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


class RefreshRequest(BaseModel):
    mode: Literal["local", "stores", "scotia_login", "scotia", "all"] = "stores"
    platforms: list[Literal[
        "walmart_ca",
        "amazon_ca",
        "uber_eats",
        "the_ordinary_ca",
        "fantuan",
        "tnt_ca",
    ]] = Field(default_factory=list, max_length=6)


class RetryCollectorsRequest(BaseModel):
    platform: Literal[
        "tnt_ca",
        "walmart_ca",
        "amazon_ca",
        "uber_eats",
        "the_ordinary_ca",
        "fantuan",
    ] | None = None


class VpsLoginSessionRequest(BaseModel):
    platform: Literal[
        "walmart_ca",
        "amazon_ca",
        "uber_eats",
        "the_ordinary_ca",
        "fantuan",
    ]


class CollectorBackendRequest(BaseModel):
    mode: Literal["auto", "local", "vps"]


class AnalysisRequest(BaseModel):
    mode: str


class AnalysisChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1000)


class AnalysisChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1200)
    history: list[AnalysisChatMessage] = Field(default_factory=list, max_length=10)


class ChatGptExportRequest(BaseModel):
    mode: Literal["month", "year", "trend", "question"]
    question: str = Field(default="", max_length=1200)


class CashWithdrawalNoteRequest(BaseModel):
    note: str = Field(default="", max_length=300)


class ManualWechatDraft(BaseModel):
    paid_on: date
    merchant: str = Field(min_length=1, max_length=120)
    amount: Decimal = Field(gt=0, le=Decimal("99999999.99"))
    currency: Literal["CNY", "CAD"]
    note: str = Field(default="", max_length=300)


class ManualWechatCreate(ManualWechatDraft):
    request_id: UUID


class CollectionSettings(BaseModel):
    parallel_collection: bool


class SetupChoice(BaseModel):
    bank_mode: Literal["scotia", "statements", "later"]
    platforms: list[str] = Field(default_factory=list, max_length=12)
    collection_floor: str | None = Field(default=None, max_length=10)


class BankStatementImport(BaseModel):
    account_id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    filename: str = Field(default="", max_length=260)
    content: str = Field(min_length=1, max_length=6_000_000)
    institution: str = Field(default="", max_length=80)
    statement_type: Literal["", "bank_account", "credit_card"] = ""
    date_format: str | None = Field(default=None, max_length=32)
    purchases_positive: bool = False
    columns: list[str] | None = Field(default=None, max_length=12)
    dry_run: bool = False


class WechatBillPreview(BaseModel):
    detail: str = Field(min_length=1, max_length=12000)
    fantuan_order_id: str = Field(default="", max_length=128)
    receipt_transaction_id: str = Field(default="", max_length=128)
    receipt_merchant_order_id: str = Field(default="", max_length=128)


class RefundLinkRequest(BaseModel):
    bank_transaction_pk: int = Field(gt=0)
    confirmed: Literal[True]
    note: str = Field(default='', max_length=300)


class MatchConfirmation(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=300)
    platform: str = Field(min_length=1, max_length=80)
    order_id: str = Field(min_length=1, max_length=300)
    confirmed: Literal[True]


@app.post('/api/reconciliation/confirm')
def confirm_bank_match(payload: MatchConfirmation):
    if jobs.snapshot()['active_job_id']:
        raise HTTPException(status_code=409, detail='请等待当前任务完成后确认对账。')
    confirm_match(repository, payload.transaction_id, payload.platform, payload.order_id)
    return {'saved': True}


class ProductCategoryRequest(BaseModel):
    product_name: str = Field(min_length=1, max_length=2000)
    category: str | None = Field(default=None, min_length=1, max_length=60, pattern=r'^[\w -]+$')


@app.put('/api/orders/{order_pk}/items/{item_pk}/category')
def remember_product_category(order_pk: int, item_pk: int, payload: ProductCategoryRequest):
    category = payload.category.strip() if payload.category is not None else None
    if category in {'', 'mixed', 'unallocated'}:
        raise ValueError('请选择具体商品类别。')
    set_product_rule(repository, order_pk, item_pk, payload.product_name, category)
    return {'saved': True}


@app.get('/api/order-changes')
def order_changes(limit: int = 50):
    return {'rows': repository.recent_changes(limit)}


@app.post('/api/orders/{order_pk}/refunds')
def link_order_refund(order_pk: int, payload: RefundLinkRequest):
    if jobs.snapshot()['active_job_id']:
        raise HTTPException(status_code=409, detail='请等待当前任务完成后关联退款。')
    try:
        return repository.link_refund(order_pk, payload.bank_transaction_pk, payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete('/api/orders/{order_pk}/refunds/{refund_id}')
def unlink_order_refund(order_pk: int, refund_id: int):
    if jobs.snapshot()['active_job_id']:
        raise HTTPException(status_code=409, detail='请等待当前任务完成后更改退款。')
    return repository.unlink_refund(order_pk, refund_id)


def _price_watch_snapshot_path():
    return ROOT / "data" / "walmart_deals_local.json"


def _read_price_watch_snapshot():
    try:
        payload = json.loads(
            _price_watch_snapshot_path().read_text(encoding="utf-8-sig")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_price_watch_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def public_price_watch_status():
    status_path = ROOT / ".codex" / "price-watch-on-demand-status.json"
    source_payload = _read_price_watch_snapshot()
    status = {
        "status": "idle",
        "phase": "idle",
        "message": "尚未运行本机检查",
        "updated_at": "",
        "backend": "local",
        "route": "local",
        "collector_status": "",
        "checked": 0,
        "checks": 0,
        "deals": 0,
        "result_source": "local",
        "synced": False,
    }
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        payload = {}
    if isinstance(payload, dict) and payload.get("backend") == "local":
        for key in (
            "status",
            "phase",
            "message",
            "updated_at",
            "backend",
            "route",
            "collector_status",
            "checked",
            "checks",
            "deals",
        ):
            value = payload.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                status[key] = value
    if isinstance(source_payload, dict):
        deals = source_payload.get("deals")
        checks = source_payload.get("checks")
        checked = source_payload.get(
            "last_valid_checked",
            source_payload.get("checked"),
        )
        if isinstance(deals, list):
            status["deals"] = len(deals)
        if isinstance(checks, list):
            status["checks"] = len(checks)
        if (
            isinstance(checked, int)
            and not isinstance(checked, bool)
            and checked >= 0
        ):
            status["checked"] = checked
        collector = source_payload.get("collector")
        source_received_at = _parse_price_watch_time(
            source_payload.get("received_at")
        )
        status_updated_at = _parse_price_watch_time(status["updated_at"])
        snapshot_is_newer = (
            source_received_at is not None
            and (
                status_updated_at is None
                or source_received_at > status_updated_at
            )
        )
        if snapshot_is_newer:
            status.update(
                status="idle",
                phase="idle",
                message="尚未运行本机检查",
                updated_at=str(source_payload.get("received_at", "")),
            )
        if (
            isinstance(collector, dict)
            and (status["status"] == "idle" or snapshot_is_newer)
        ):
            collector_status = str(collector.get("status", ""))
            if collector_status:
                status["collector_status"] = collector_status
            if collector_status == "completed":
                status.update(
                    status="passed",
                    phase="complete",
                    message="本机最近一次检查完成",
                )
            elif collector_status == "running":
                status.update(
                    status="running",
                    phase="collecting",
                    message="本机正在读取 My Items 页面",
                )
            elif collector_status in {
                "blocked",
                "cooldown",
                "failed",
                "login_required",
                "wrong_store",
            }:
                status.update(status="failed", phase="failed")
                status["message"] = {
                    "blocked": "Walmart 阻止了最近一次检查，已保留先前有效结果",
                    "cooldown": "Walmart 检查处于冷却期，已保留先前有效结果",
                    "failed": "最近一次 Walmart 检查未完成",
                    "login_required": "Walmart 专用浏览器需要重新登录",
                    "wrong_store": "页面未确认 Square One 门店，未保存本次价格",
                }[collector_status]
        received_at = source_payload.get("received_at")
        if isinstance(received_at, str) and not status["updated_at"]:
            status["updated_at"] = received_at
    if (
        status["status"] == "passed"
        and status["phase"] == "complete"
        and status["collector_status"] == "completed"
    ):
        status["message"] = (
            f"本机检查完成：{status['checked']} 个商品，"
            f"{status['deals']} 个折扣"
        )
    return status


def public_price_watch_deals():
    payload = _read_price_watch_snapshot()
    rows = []
    for raw in payload.get("deals", []) if isinstance(payload, dict) else []:
        if not isinstance(raw, dict):
            continue
        regular = raw.get("regular_price")
        current = raw.get("current_price")
        url = str(raw.get("url", ""))
        parsed = urlparse(url)
        if (
            isinstance(regular, bool)
            or isinstance(current, bool)
            or not isinstance(regular, (int, float))
            or not isinstance(current, (int, float))
            or current >= regular
            or parsed.scheme != "https"
            or (parsed.hostname or "").lower() != "www.walmart.ca"
            or "/ip/" not in parsed.path.lower()
        ):
            continue
        rows.append(
            {
                "product_key": str(raw.get("product_key", ""))[:80],
                "name": str(raw.get("name", ""))[:300],
                "url": url,
                "regular_price": round(float(regular), 2),
                "current_price": round(float(current), 2),
                "discount_percent": round(
                    (float(regular) - float(current)) / float(regular) * 100,
                    1,
                ),
                "checked_at": str(raw.get("checked_at", ""))[:40],
            }
        )
    rows.sort(key=lambda row: (-row["discount_percent"], row["name"]))
    return {
        "source": "local",
        "result_source": "local",
        "synced": False,
        "generated_at": (
            str(
                payload.get(
                    "last_valid_at",
                    payload.get("received_at", ""),
                )
            )
            if isinstance(payload, dict)
            else ""
        ),
        "deals": rows,
    }


@app.middleware("http")
async def local_only_actions(request: Request, call_next):
    if request.url.hostname not in LOOPBACK_HOSTS:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid localhost host header."},
        )
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin and urlparse(origin).hostname not in LOOPBACK_HOSTS:
            return JSONResponse(
                status_code=403,
                content={"detail": "Local application requests only."},
            )
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif (
        request.url.path in {"/", "/analysis", "/price-watch"}
        or request.url.path.startswith("/static/")
    ):
        response.headers["Cache-Control"] = "no-cache"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; connect-src 'self'; img-src 'self'; "
        "style-src 'self'; script-src 'self'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(FileNotFoundError)
async def database_missing_handler(_request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def invalid_value_handler(_request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/api/health")
def health():
    return {
        "app": APP_ID,
        "version": APP_VERSION,
        "workspace": str(ROOT),
        "workspace_id": WORKSPACE_ID,
        "pid": os.getpid(),
        "status": "ok" if repository.db_path.exists() else "missing_database",
        "database": repository.db_path.name,
    }


@app.get("/api/overview")
def overview(month: str = "latest"):
    return repository.overview(month)


@app.get("/api/filters")
def filters():
    return repository.filters()


@app.get("/api/reconciliation/review")
def reconciliation_review():
    return {"rows": repository.reconciliation_review()}


@app.get("/api/cash-withdrawals")
def cash_withdrawals(
    month: str = "all",
    limit: int = Query(default=200, ge=1, le=500),
):
    return repository.cash_withdrawals(month=month, limit=limit)


@app.post("/api/wechat-bills/preview")
def preview_wechat_bill(payload: WechatBillPreview):
    try:
        bill = parse_detail(payload.detail)
        status, reason = compare_fantuan(repository, bill, payload.fantuan_order_id.strip(),
                                         payload.receipt_transaction_id.strip(), payload.receipt_merchant_order_id.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"bill": bill, "match_status": status, "reason": reason}


@app.get("/api/manual-wechat-payments")
def manual_wechat_payments(month: str = "all", limit: int = Query(default=200, ge=1, le=500)):
    try:
        return manual_wechat.list(month, limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/setup")
def setup_status():
    """First-run state; the desktop shows its welcome guide when needs_onboarding is true."""
    return setup_store.status()


@app.post("/api/setup")
def save_setup(payload: SetupChoice):
    try:
        return setup_store.save(payload.bank_mode, payload.platforms, payload.collection_floor)
    except SetupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/collection-settings")
def get_collection_settings():
    return setup_store.collection_settings()


@app.post("/api/collection-settings")
def save_collection_settings(payload: CollectionSettings):
    """Parallel collection: several stores at once, each in its own browser window."""
    if jobs.snapshot()["active_job_id"]:
        raise HTTPException(status_code=409, detail="有任务正在运行，请完成后再修改采集方式。")
    try:
        return setup_store.set_parallel_collection(payload.parallel_collection)
    except SetupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/bank-statements")
def list_bank_statements():
    """Accounts whose OFX/CSV statements were imported, for the settings and import pages."""
    accounts = []
    directory = bank_statement.statements_dir(ROOT)
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else ():
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        dates = [row.get("date", "") for row in payload.get("transactions", [])]
        accounts.append({key: payload.get(key, "") for key in
                         ("account_id", "institution", "statement_type", "last4", "generated_at")}
                        | {"rows": len(dates), "date_range": [min(dates), max(dates)] if dates else []})
    return {"accounts": accounts}


@app.post("/api/bank-statements/import")
def import_bank_statement(payload: BankStatementImport):
    """Store a bank's OFX/CSV export; the next refresh reconciles it."""
    try:
        return bank_statement.import_statement(
            payload.content, account_id=payload.account_id, filename=payload.filename,
            institution=payload.institution, statement_type=payload.statement_type,
            columns=payload.columns, date_format=payload.date_format,
            purchases_positive=payload.purchases_positive, root=ROOT, dry_run=payload.dry_run)
    except bank_statement.StatementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/manual-wechat-payments", status_code=201)
def create_manual_wechat_payment(payload: ManualWechatCreate):
    try:
        return manual_wechat.create(payload.request_id, payload)
    except DuplicateRequestConflict:
        raise HTTPException(status_code=409, detail="请求编号已用于另一笔付款，请刷新列表核实。") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/manual-wechat-payments/{payment_id}")
def update_manual_wechat_payment(payment_id: int, payload: ManualWechatDraft):
    if payment_id <= 0:
        raise HTTPException(status_code=404, detail="手填付款不存在。")
    try:
        row = manual_wechat.update(payment_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="手填付款不存在。")
    return row


@app.delete("/api/manual-wechat-payments/{payment_id}")
def delete_manual_wechat_payment(payment_id: int):
    if payment_id <= 0 or not manual_wechat.delete(payment_id):
        raise HTTPException(status_code=404, detail="手填付款不存在。")
    return {"deleted": True}


@app.patch("/api/cash-withdrawals/{bank_transaction_pk}")
def update_cash_withdrawal_note(
    bank_transaction_pk: int,
    payload: CashWithdrawalNoteRequest,
):
    try:
        result = repository.update_cash_withdrawal_note(
            bank_transaction_pk,
            payload.note,
        )
    except CashNoteSupersededError:
        raise HTTPException(
            status_code=409,
            detail="A newer cash-note edit superseded this request.",
        ) from None
    if result is None:
        raise HTTPException(status_code=404, detail="Cash withdrawal not found.")
    return result


@app.get("/api/orders")
def order_list(
    month: str = "all",
    platform: str = "",
    category: str = "",
    card_id: str = "",
    q: str = "",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return repository.orders(
        month=month,
        platform=platform,
        category=category,
        card_id=card_id,
        query=q,
        limit=limit,
        offset=offset,
    )


@app.get("/api/orders/{order_pk}")
def order_detail(order_pk: int):
    detail = repository.order_detail(order_pk)
    if detail is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    return detail


@app.get("/api/jobs")
def job_list():
    return jobs.snapshot()


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@app.get("/api/collector-backends")
def collector_backend_status(refresh: bool = False):
    return collector_backends.status(force=refresh)


@app.patch("/api/collector-backends")
def select_collector_backend(payload: CollectorBackendRequest):
    if payload.mode not in COLLECTOR_BACKEND_MODES:
        raise HTTPException(status_code=400, detail="Unknown collector backend mode.")
    if jobs.snapshot()["active_job_id"]:
        raise HTTPException(
            status_code=409,
            detail="抓取任务运行期间不能切换后端。",
        )
    try:
        return collector_backends.set_mode(payload.mode)
    except CollectorBackendError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/collector-recovery")
def collector_recovery():
    return collector_recovery_status(ROOT)


@app.get("/api/price-watch/status")
def price_watch_status():
    return public_price_watch_status()


@app.get("/api/price-watch/deals")
def price_watch_deals():
    return public_price_watch_deals()


@app.get("/api/analysis/status")
def analysis_status():
    return {**analysis_service.status(), "chatgpt_periods": export_periods(repository)}


@app.post("/api/analysis/run")
async def run_analysis(payload: AnalysisRequest):
    if payload.mode not in VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail="Analysis mode must be month, year, or trend.",
        )
    try:
        return await asyncio.to_thread(analysis_service.analyze, payload.mode)
    except AnalysisError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc


@app.post("/api/analysis/chat")
async def chat_analysis(payload: AnalysisChatRequest):
    try:
        return await asyncio.to_thread(
            analysis_service.chat,
            payload.question,
            payload.history,
        )
    except AnalysisError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc


@app.post("/api/analysis/chatgpt/prepare")
async def prepare_chatgpt_export(payload: ChatGptExportRequest):
    try:
        return await asyncio.to_thread(
            prepare_export, repository, payload.mode, payload.question,
        )
    except AnalysisError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs/validate", status_code=202)
def validate_ledger():
    try:
        return jobs.start_validation()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/jobs/refresh", status_code=202)
def refresh_ledger(payload: RefreshRequest):
    try:
        return jobs.start_refresh(
            payload.mode,
            collector_platforms=payload.platforms,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/jobs/retry-collectors", status_code=202)
def retry_collectors(payload: RetryCollectorsRequest):
    recovery = collector_recovery_status(ROOT)
    rows = recovery.get("platforms") or []
    selected = [
        row["id"] for row in rows
        if row.get("retryable")
        and (payload.platform is None or row.get("id") == payload.platform)
    ]
    if not selected:
        matching = next(
            (row for row in rows if row.get("id") == payload.platform),
            None,
        )
        if matching and matching.get("login_required"):
            raise HTTPException(
                status_code=409,
                detail="请先打开该平台的 VPS 登录会话，完成登录后再更新。",
            )
        raise HTTPException(status_code=409, detail="没有可安全重试的失败平台。")
    try:
        return jobs.start_refresh(
            "stores",
            collector_backend="vps",
            collector_platforms=selected,
            collector_recovery=True,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/vps-login-session")
def start_vps_login_session(payload: VpsLoginSessionRequest):
    recovery = collector_recovery_status(ROOT)
    matching = next(
        (
            row for row in recovery.get("platforms") or []
            if row.get("id") == payload.platform
        ),
        None,
    )
    if not matching or not matching.get("login_required"):
        raise HTTPException(
            status_code=409,
            detail="最近一次 VPS 采集未要求该平台重新登录。",
        )
    try:
        return jobs.start_vps_login_session(payload.platform)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/vps-login-session/verify", status_code=202)
def verify_vps_login_session(payload: VpsLoginSessionRequest):
    recovery = collector_recovery_status(ROOT)
    matching = next(
        (
            row for row in recovery.get("platforms") or []
            if row.get("id") == payload.platform
        ),
        None,
    )
    if not matching or not matching.get("login_required"):
        raise HTTPException(
            status_code=409,
            detail="最近一次 VPS 采集未要求该平台重新登录。",
        )
    try:
        return jobs.verify_vps_login_and_start_refresh(payload.platform)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/jobs/price-watch", status_code=202)
def run_price_watch():
    try:
        return jobs.start_price_watch()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/analysis", include_in_schema=False)
def analysis_page():
    return FileResponse(STATIC_DIR / "analysis.html")


@app.get("/price-watch", include_in_schema=False)
def price_watch_page():
    return FileResponse(STATIC_DIR / "price-watch.html")
