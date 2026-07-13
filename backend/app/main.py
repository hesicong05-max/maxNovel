"""FastAPI main application."""

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.api import auth, chapters, community, export, outline, projects, settings, worldview
from app.config import settings as app_settings
from app.core.logging_config import setup_logging
from app.core.rate_limiter import limiter
from app.core.settings_store import load_settings
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting %s ...", app_settings.APP_NAME)

    # Initialize Sentry error monitoring (no-op if DSN not set)
    from app.core.sentry import init_sentry
    init_sentry()

    await init_db()
    logger.info("Database initialized")
    yield
    logger.info("Shutting down %s", app_settings.APP_NAME)


app = FastAPI(
    title=app_settings.APP_NAME,
    debug=app_settings.DEBUG,
    lifespan=lifespan,
)

# Rate limiter state
app.state.limiter = limiter


# Rate limit exceeded handler
from slowapi.errors import RateLimitExceeded  # noqa: E402
from slowapi import _rate_limit_exceeded_handler  # noqa: E402

app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---- Middleware ----

app.add_middleware(
    CORSMiddleware,
    allow_origins=app_settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["*"],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Restrict API endpoints — no iframe embedding
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log each request with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next: Callable):
        request_id = str(uuid.uuid4())[:8]
        start = time.perf_counter()

        # Attach request_id for logging correlation
        request.state.request_id = request_id

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            logger = logging.getLogger(__name__)
            logger.error(
                "[%s] %s %s → 500 (%.1fms) unhandled: %s",
                request_id,
                request.method,
                request.url.path,
                duration_ms,
                exc,
            )
            raise

        duration_ms = (time.perf_counter() - start) * 1000

        # Log at INFO for normal, WARNING for slow (>3s), ERROR for 5xx
        logger = logging.getLogger(__name__)
        log_method = logger.info
        if response.status_code >= 500:
            log_method = logger.error
        elif response.status_code >= 400:
            log_method = logger.warning
        elif duration_ms > 3000:
            log_method = logger.warning

        log_method(
            "[%s] %s %s → %d (%.1fms)",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )

        # Add request_id to response headers for client-side correlation
        response.headers["X-Request-ID"] = request_id
        return response


app.add_middleware(RequestLoggingMiddleware)


# ---- Global exception handlers ----


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch all unhandled exceptions and return a clean 500 response."""
    logger = logging.getLogger(__name__)
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error(
        "[%s] Unhandled exception on %s %s: %s",
        request_id,
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "服务器内部错误，请稍后重试",
            "request_id": request_id,
        },
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Handle ValueError as 422 Unprocessable Entity."""
    logger = logging.getLogger(__name__)
    logger.warning("ValueError on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": str(exc)},
    )


# ---- Routers ----

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(worldview.router)
app.include_router(outline.router)
app.include_router(chapters.router)
app.include_router(export.router)
app.include_router(settings.router)
app.include_router(community.router)


@app.get("/api/health")
async def health_check():
    s = load_settings()
    return {
        "status": "ok",
        "app": app_settings.APP_NAME,
        "debug": app_settings.DEBUG,
        "llm_configured": bool(s.get("api_key", "")),
    }
