"""Rate limiting configuration using slowapi.

Apply @limiter.limit() to specific endpoints or use the middleware for global limits.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings


# Create limiter — uses client IP as the key
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
    storage_uri="memory://",  # In-memory; for production use Redis
)
