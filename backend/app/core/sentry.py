"""Sentry SDK initialization for error monitoring.

If SENTRY_DSN is not set, Sentry is not initialized (no-op).
This allows development without a Sentry account.
"""

import logging

from app.config import settings

logger = logging.getLogger(__name__)


def init_sentry() -> bool:
    """Initialize Sentry SDK if a DSN is configured.

    Returns:
        True if Sentry was initialized, False otherwise.
    """
    if not settings.SENTRY_DSN:
        logger.info("Sentry DSN not set — error monitoring disabled")
        return False

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
        environment="production" if not settings.DEBUG else "development",
        # Send PII (email, username) for better error context
        send_default_pii=True,
    )

    logger.info("Sentry initialized successfully (env=%s)", "production" if not settings.DEBUG else "development")
    return True
