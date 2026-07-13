"""Rate limiting configuration using slowapi.

Apply @limiter.limit() to specific endpoints or use the middleware for global limits.
Supports Redis for multi-process deployments (set RATE_LIMIT_STORAGE_URI).
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings


# Create limiter — uses client IP as the key
# storage_uri defaults to memory:// (single process)
# Set RATE_LIMIT_STORAGE_URI=redis://localhost:6379 for multi-process
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
    storage_uri=settings.RATE_LIMIT_STORAGE_URI,
)
