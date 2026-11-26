"""
Production AI Agent — Part 6 Final Project.

Chạy:   uvicorn app.main:app --workers 2
Chấm:   python check_production_ready.py

Endpoints
  GET  /health  liveness  — luôn 200 khi process còn chạy (không phụ thuộc Redis)
  GET  /ready   readiness — 200 khi Redis kết nối được, 503 khi không (hoặc đang tắt)
  POST /ask     {"question": "..."} + header X-API-Key
                → 401 sai/thiếu key · 429 vượt rate limit · 402 hết budget · 503 Redis lỗi
"""
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from app import cost_guard
from app.auth import verify_api_key
from app.config import settings
from app.logging_config import log_event
from app.rate_limiter import check_rate_limit
from app.redis_client import close_redis, redis_healthy
from utils.mock_llm import ask as llm_ask

START_TIME = time.time()
_state = {"ready": False}
QUIET_PATHS = {"/health", "/ready"}   # probe gọi liên tục → chỉ log ở mức DEBUG


# ─────────────────────────────────────────────────────────
# Lifespan — startup & graceful shutdown (tiêu chí 11)
# ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Không bắt buộc có Redis để khởi động: container vẫn lên, /ready báo 503
    log_event("startup", environment=settings.environment, version=settings.app_version,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              daily_budget_usd=settings.daily_budget_usd,
              monthly_budget_usd=settings.monthly_budget_usd,
              api_key_configured=bool(settings.agent_api_key),
              redis_ok=redis_healthy())
    if not settings.agent_api_key:
        log_event("config_warning", logging.WARNING,
                  detail="AGENT_API_KEY chưa đặt — /ask sẽ từ chối mọi request (401)")
    _state["ready"] = True
    yield
    # SIGTERM: uvicorn đã ngừng nhận kết nối mới và chờ các request đang chạy xong
    # rồi mới chạy tới đây → chỉ còn việc dọn dẹp. Không tự đăng ký signal handler,
    # để không ghi đè handler của uvicorn.
    _state["ready"] = False
    log_event("shutdown", uptime_seconds=round(time.time() - START_TIME, 1))
    close_redis()


app = FastAPI(title="Production AI Agent", version=settings.app_version, lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs",
              redoc_url=None)


# ─────────────────────────────────────────────────────────
# Middleware — access log JSON + request id + chặn lộ stack trace
# ─────────────────────────────────────────────────────────
@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # Lỗi không lường trước: log đầy đủ (1 dòng JSON), client chỉ thấy thông báo chung
        log_event("unhandled_error", logging.ERROR, exc_info=True,
                  request_id=request_id, method=request.method, path=request.url.path)
        response = JSONResponse(status_code=500,
                                content={"error": "internal_error", "request_id": request_id})
    response.headers["X-Request-ID"] = request_id
    level = logging.DEBUG if request.url.path in QUIET_PATHS else logging.INFO
    log_event("request", level, request_id=request_id, method=request.method,
              path=request.url.path, status=response.status_code,
              ms=round((time.perf_counter() - started) * 1000, 1))
    return response


# ─────────────────────────────────────────────────────────
# Health / readiness (tiêu chí 4, 5)
# ─────────────────────────────────────────────────────────
@app.get("/health")
def health():
    """Liveness: chỉ báo process còn sống. Không kiểm tra Redis — Redis chết thì
    restart agent cũng không sửa được, nên không được làm fail liveness."""
    return {"status": "ok", "version": settings.app_version,
            "environment": settings.environment,
            "uptime_seconds": round(time.time() - START_TIME, 1)}


@app.get("/ready")
def ready():
    """Readiness: có nên nhận traffic không — cần Redis (rate limit + cost guard)."""
    redis_ok = redis_healthy()
    if not (_state["ready"] and redis_ok):
        return JSONResponse(status_code=503, content={
            "ready": False, "redis": "ok" if redis_ok else "unreachable",
            "accepting": _state["ready"]})
    return {"ready": True, "redis": "ok"}


# ─────────────────────────────────────────────────────────
# Agent endpoint (tiêu chí 6, 7, 8)
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)


@app.post("/ask")
def ask(body: AskRequest, response: Response, api_key: str = Depends(verify_api_key)):
    # `def` (không phải `async def`): FastAPI chạy trong threadpool, nên lời gọi
    # LLM / Redis blocking không chặn event loop (/health vẫn trả lời ngay).
    try:
        rate = check_rate_limit(api_key)                                    # 429
        reserved = cost_guard.check_budget(cost_guard.estimate_cost(body.question))  # 402
    except RedisError:
        log_event("dependency_error", logging.ERROR, dependency="redis", path="/ask")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable",
                            headers={"Retry-After": "5"})

    try:
        answer = llm_ask(body.question)
    except Exception:
        cost_guard.release(reserved)
        log_event("llm_error", logging.ERROR, exc_info=True)
        raise HTTPException(status_code=502, detail="LLM backend error")

    input_tokens = cost_guard.count_tokens(body.question)
    output_tokens = cost_guard.count_tokens(answer)
    cost = cost_guard.record_cost(input_tokens, output_tokens, reserved)
    log_event("llm_call", question_chars=len(body.question), input_tokens=input_tokens,
              output_tokens=output_tokens, cost_usd=round(cost, 8))

    response.headers["X-RateLimit-Limit"] = str(rate["limit"])
    response.headers["X-RateLimit-Remaining"] = str(rate["remaining"])
    return {
        "question": body.question,
        "answer": answer,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8),
                  "requests_remaining_this_minute": rate["remaining"]},
    }
