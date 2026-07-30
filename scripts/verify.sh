#!/usr/bin/env bash
set -euo pipefail

VERIFY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERIFY_PYTHON="${PYTHON_BIN:-python3}"

"$VERIFY_PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'

(
  cd "$VERIFY_ROOT/backend"
  DEBUG=true \
  JWT_SECRET=local-verification-only-not-for-production \
    "$VERIFY_PYTHON" -m pytest tests -v --tb=short
  "$VERIFY_PYTHON" -m bandit -r app --severity-level high -f json
)

(
  cd "$VERIFY_ROOT/frontend"
  npm run verify
)
