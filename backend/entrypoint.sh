#!/bin/bash
# Entrypoint script — runs Alembic migrations then starts uvicorn
set -e

echo "=== Running database migrations ==="
alembic upgrade head

echo "=== Starting uvicorn server ==="
# The backend is only exposed to the trusted frontend proxy in docker-compose.
# Trust its sanitized forwarding headers so rate limits and view/like dedup use
# the real client address instead of treating every visitor as one IP.
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --proxy-headers \
    --forwarded-allow-ips="*"
