"""Shared test fixtures."""

import os
import sys
from pathlib import Path

# Ensure DEBUG mode for tests (avoids JWT_SECRET validation error)
os.environ.setdefault("DEBUG", "true")

# Ensure backend is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
