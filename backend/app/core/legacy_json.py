"""Fail-safe readers for historical JSON values stored in Text columns."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LegacyObjectListResult:
    """A normalized object list plus a non-sensitive error category."""

    items: list[dict[str, Any]]
    error_category: str | None = None

    @property
    def valid(self) -> bool:
        return self.error_category is None


def read_legacy_object_list(value: Any) -> LegacyObjectListResult:
    """Read a native list or a historical JSON string without changing storage.

    Only lists containing objects are accepted. Invalid values return an empty
    result with a category suitable for logs; the original value is never
    included in the result or error category.
    """

    if value is None:
        return LegacyObjectListResult(items=[])

    decoded = value
    # Historical PostgreSQL schemas declared these columns as Text while the
    # ORM declared JSON.  A JSON string written through that mismatch can be
    # stored with one extra encoding layer, so unwrap at most two string
    # layers.  The depth cap keeps malformed or adversarial input bounded.
    for _ in range(2):
        if not isinstance(decoded, str):
            break
        if not decoded.strip():
            return LegacyObjectListResult(items=[], error_category="empty_string")
        try:
            decoded = json.loads(decoded)
        except (TypeError, json.JSONDecodeError):
            return LegacyObjectListResult(items=[], error_category="malformed_json")

    if not isinstance(decoded, list):
        return LegacyObjectListResult(items=[], error_category="not_a_list")
    if any(not isinstance(item, dict) for item in decoded):
        return LegacyObjectListResult(items=[], error_category="item_not_an_object")

    return LegacyObjectListResult(items=copy.deepcopy(decoded))
