"""Unit tests for community API helpers — view dedup, IP hashing."""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.api.community import _ip_hash, _should_count_view, _view_cache, _VIEW_DEDUP_TTL


# ─── IP hash tests ────────────────────────────────────────────

class TestIPHash:
    def test_returns_hex_string(self):
        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.1"
        result = _ip_hash(mock_request)
        assert isinstance(result, str)
        assert len(result) == 16
        # Should be hex
        int(result, 16)

    def test_same_ip_same_hash(self):
        mock_request = MagicMock()
        mock_request.client.host = "10.0.0.1"
        h1 = _ip_hash(mock_request)
        h2 = _ip_hash(mock_request)
        assert h1 == h2

    def test_different_ip_different_hash(self):
        mock1 = MagicMock()
        mock1.client.host = "10.0.0.1"
        mock2 = MagicMock()
        mock2.client.host = "10.0.0.2"
        assert _ip_hash(mock1) != _ip_hash(mock2)

    def test_no_client_returns_unknown_hash(self):
        mock_request = MagicMock()
        mock_request.client = None
        result = _ip_hash(mock_request)
        assert isinstance(result, str)
        assert len(result) == 16

    def test_ipv6_address(self):
        mock_request = MagicMock()
        mock_request.client.host = "::1"
        result = _ip_hash(mock_request)
        assert isinstance(result, str)
        assert len(result) == 16


# ─── View dedup tests ────────────────────────────────────────

class TestShouldCountView:
    def setup_method(self):
        """Clear cache before each test."""
        _view_cache.clear()

    def test_first_view_counted(self):
        """First view from an IP should be counted."""
        assert _should_count_view("hash1", "novel1") is True

    def test_duplicate_within_ttl_not_counted(self):
        """Second view from same IP within TTL should not be counted."""
        _should_count_view("hash1", "novel1")
        assert _should_count_view("hash1", "novel1") is False

    def test_different_novel_counted(self):
        """Same IP viewing different novel should be counted."""
        _should_count_view("hash1", "novel1")
        assert _should_count_view("hash1", "novel2") is True

    def test_different_ip_same_novel_counted(self):
        """Different IP viewing same novel should be counted."""
        _should_count_view("hash1", "novel1")
        assert _should_count_view("hash2", "novel1") is True

    def test_view_after_ttl_counted(self):
        """View after TTL expiry should be counted."""
        _should_count_view("hash1", "novel1")
        # Simulate TTL expiry by backdating the cache entry
        _view_cache[("hash1", "novel1")] = time.time() - _VIEW_DEDUP_TTL - 1
        assert _should_count_view("hash1", "novel1") is True

    def test_cache_clears_when_too_large(self):
        """Cache should be cleared when it exceeds 10000 entries."""
        # Fill cache to threshold
        for i in range(10001):
            _view_cache[(f"hash{i}", f"novel{i}")] = time.time()
        # Next call should clear cache and count the view
        assert _should_count_view("new_hash", "new_novel") is True
        # Cache should have been cleared (only 1 entry now)
        assert len(_view_cache) == 1
