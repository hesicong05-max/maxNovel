"""Unit tests for settings_store — LLM settings persistence."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.core.settings_store import (
    load_settings,
    save_settings,
    get_api_key,
    DEFAULT_SETTINGS,
    SETTINGS_FILE,
    _set_file_permissions,
)


# ─── Default settings tests ──────────────────────────────────

class TestDefaultSettings:
    def test_default_keys_present(self):
        assert "api_key" in DEFAULT_SETTINGS
        assert "base_url" in DEFAULT_SETTINGS
        assert "model" in DEFAULT_SETTINGS
        assert "temperature" in DEFAULT_SETTINGS
        assert "max_tokens" in DEFAULT_SETTINGS

    def test_default_values(self):
        assert DEFAULT_SETTINGS["api_key"] == ""
        assert DEFAULT_SETTINGS["base_url"] == "https://qianfan.baidubce.com/v2"
        assert DEFAULT_SETTINGS["model"] == "ernie-4.5-turbo-128k"
        assert DEFAULT_SETTINGS["temperature"] == 0.8
        assert DEFAULT_SETTINGS["max_tokens"] == 4096


# ─── load_settings tests ─────────────────────────────────────

class TestLoadSettings:
    def test_load_from_env_api_key(self):
        """Environment variable should take highest priority."""
        with patch.dict(os.environ, {"LLM_API_KEY": "sk-env-key", "LLM_BASE_URL": "https://custom.api.com/v1", "LLM_MODEL": "gpt-4-turbo"}):
            # Clear file-based settings
            with patch("app.core.settings_store.SETTINGS_FILE") as mock_file:
                mock_file.exists.return_value = False
                settings = load_settings()
                assert settings["api_key"] == "sk-env-key"
                assert settings["base_url"] == "https://custom.api.com/v1"
                assert settings["model"] == "gpt-4-turbo"

    def test_load_from_file(self):
        """Should load from settings file when no env var."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("LLM_API_KEY", None)
            os.environ.pop("LLM_BASE_URL", None)
            os.environ.pop("LLM_MODEL", None)

            mock_file = MagicMock()
            mock_file.exists.return_value = True
            file_data = {
                "api_key": "sk-file-key",
                "base_url": "https://file.api.com/v1",
                "model": "gpt-4o-mini",
                "temperature": 0.5,
                "max_tokens": 2048,
            }
            mock_open = patch("builtins.open", mock_open_func(json.dumps(file_data)))

            with patch("app.core.settings_store.SETTINGS_FILE", mock_file), \
                 mock_open:
                settings = load_settings()
                assert settings["api_key"] == "sk-file-key"
                assert settings["model"] == "gpt-4o-mini"

    def test_load_with_corrupt_file_falls_back(self):
        """Should fall back to defaults when file is corrupt."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("LLM_API_KEY", None)

            mock_file = MagicMock()
            mock_file.exists.return_value = True
            mock_open = patch("builtins.open", mock_open_func("not valid json"))

            with patch("app.core.settings_store.SETTINGS_FILE", mock_file), \
                 mock_open:
                settings = load_settings()
                # Should fall back to config defaults
                assert isinstance(settings, dict)
                assert "api_key" in settings

    def test_load_falls_back_to_config(self):
        """Should fall back to app.config settings when no env or file."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("LLM_API_KEY", None)
            os.environ.pop("LLM_BASE_URL", None)
            os.environ.pop("LLM_MODEL", None)

            mock_file = MagicMock()
            mock_file.exists.return_value = False

            with patch("app.core.settings_store.SETTINGS_FILE", mock_file):
                settings = load_settings()
                assert isinstance(settings, dict)
                assert "api_key" in settings
                assert "base_url" in settings


class mock_open_func:
    """Context manager to mock open() with given content."""
    def __init__(self, content):
        self.content = content
    def __call__(self, *args, **kwargs):
        self._mock = MagicMock()
        self._mock.__enter__ = MagicMock(return_value=MagicMock())
        self._mock.__exit__ = MagicMock(return_value=None)
        self._mock.__enter__.return_value.read.return_value = self.content
        self._mock.__enter__.return_value.write = MagicMock()
        return self._mock


# ─── save_settings tests ─────────────────────────────────────

class TestSaveSettings:
    def test_save_merges_with_defaults(self):
        """save_settings should merge with defaults."""
        with patch("builtins.open", MagicMock()):
            with patch("app.core.settings_store._set_file_permissions"):
                result = save_settings({"api_key": "sk-new-key"})
                assert result["api_key"] == "sk-new-key"
                assert result["base_url"] == DEFAULT_SETTINGS["base_url"]
                assert result["model"] == DEFAULT_SETTINGS["model"]

    def test_save_overrides_defaults(self):
        with patch("builtins.open", MagicMock()):
            with patch("app.core.settings_store._set_file_permissions"):
                result = save_settings({
                    "api_key": "sk-key",
                    "model": "claude-3-opus",
                    "temperature": 0.3,
                })
                assert result["model"] == "claude-3-opus"
                assert result["temperature"] == 0.3

    def test_save_writes_file(self):
        """Should actually write to the file."""
        mock_open = MagicMock()
        with patch("builtins.open", mock_open):
            with patch("app.core.settings_store._set_file_permissions"):
                save_settings({"api_key": "sk-write-test"})
                mock_open.assert_called_once()
                # Check write was called
                handle = mock_open.return_value.__enter__.return_value
                handle.write.assert_called()

    def test_save_sets_file_permissions(self):
        """Should call _set_file_permissions after saving."""
        with patch("builtins.open", MagicMock()):
            with patch("app.core.settings_store._set_file_permissions") as mock_perm:
                save_settings({"api_key": "sk-key"})
                mock_perm.assert_called_once()


# ─── get_api_key tests ────────────────────────────────────────

class TestGetApiKey:
    def test_get_api_key_from_env(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "sk-env-key-123"}):
            with patch("app.core.settings_store.SETTINGS_FILE") as mock_file:
                mock_file.exists.return_value = False
                key = get_api_key()
                assert key == "sk-env-key-123"

    def test_get_api_key_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("LLM_API_KEY", None)
            with patch("app.core.settings_store.SETTINGS_FILE") as mock_file:
                mock_file.exists.return_value = False
                key = get_api_key()
                assert key == ""


# ─── File permissions tests ───────────────────────────────────

class TestFilePermissions:
    def test_set_file_permissions(self, tmp_path):
        """Should set 0600 permissions on the file."""
        test_file = tmp_path / "test_settings.json"
        test_file.write_text("{}")
        _set_file_permissions(test_file)
        stat = test_file.stat()
        # Check that only owner has read/write (0o600)
        assert stat.st_mode & 0o777 == 0o600

    def test_set_permissions_nonexistent_file(self):
        """Should not raise when file doesn't exist."""
        with patch("os.chmod", side_effect=OSError("not found")):
            # Should not raise
            _set_file_permissions(Path("/nonexistent/file.json"))


# ─── Round-trip test ─────────────────────────────────────────

class TestSettingsRoundTrip:
    def test_save_then_load_from_file(self, tmp_path):
        """Save settings to a temp file, then load them back."""
        settings_file = tmp_path / "llm_settings.json"

        with patch("app.core.settings_store.SETTINGS_FILE", settings_file), \
             patch.dict(os.environ, {}, clear=True):
            os.environ.pop("LLM_API_KEY", None)
            os.environ.pop("LLM_BASE_URL", None)
            os.environ.pop("LLM_MODEL", None)

            # Save
            save_settings({
                "api_key": "sk-round-trip",
                "base_url": "https://custom.api.com/v1",
                "model": "gpt-4-turbo",
                "temperature": 0.7,
                "max_tokens": 8192,
            })

            # Load
            loaded = load_settings()
            assert loaded["api_key"] == "sk-round-trip"
            assert loaded["base_url"] == "https://custom.api.com/v1"
            assert loaded["model"] == "gpt-4-turbo"
            assert loaded["temperature"] == 0.7
            assert loaded["max_tokens"] == 8192

            # Verify file permissions
            stat = settings_file.stat()
            assert stat.st_mode & 0o777 == 0o600
