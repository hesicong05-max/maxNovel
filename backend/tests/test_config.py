"""Unit tests for config — settings validation and security."""

import warnings
import pytest

from app.config import Settings


class TestSettingsDefaults:
    def test_default_app_settings(self):
        s = Settings(DEBUG=True)
        assert s.APP_NAME == "小说世界观续写 Agent"
        assert s.HOST == "0.0.0.0"
        assert s.PORT == 8000

    def test_default_database_url(self):
        s = Settings(DEBUG=True)
        assert "sqlite" in s.DATABASE_URL

    def test_default_llm_settings(self):
        s = Settings(DEBUG=True)
        assert s.LLM_API_KEY == ""
        assert s.LLM_BASE_URL == "https://qianfan.baidubce.com/v2"
        assert s.LLM_MODEL == "ernie-4.5-turbo-128k"
        assert s.LLM_MAX_TOKENS == 4096
        assert s.LLM_TEMPERATURE == 0.8

    def test_default_rate_limits(self):
        s = Settings(DEBUG=True)
        assert "60" in s.RATE_LIMIT_DEFAULT
        assert "10" in s.RATE_LIMIT_LLM

    def test_default_jwt_settings(self):
        s = Settings(DEBUG=True)
        assert s.JWT_ALGORITHM == "HS256"
        assert s.JWT_EXPIRE_DAYS == 7

    def test_default_upload_limit(self):
        s = Settings(DEBUG=True)
        assert s.MAX_UPLOAD_SIZE == 10 * 1024 * 1024  # 10MB


class TestCORSOrigins:
    def test_cors_origins_parsed(self):
        s = Settings(DEBUG=True, CORS_ORIGINS="http://a.com,http://b.com")
        assert "http://a.com" in s.cors_origins_list
        assert "http://b.com" in s.cors_origins_list

    def test_cors_origins_with_spaces(self):
        s = Settings(DEBUG=True, CORS_ORIGINS=" http://a.com , http://b.com ")
        assert "http://a.com" in s.cors_origins_list
        assert "http://b.com" in s.cors_origins_list
        assert "" not in s.cors_origins_list

    def test_cors_origins_empty(self):
        s = Settings(DEBUG=True, CORS_ORIGINS="")
        assert s.cors_origins_list == []

    def test_cors_origins_single(self):
        s = Settings(DEBUG=True, CORS_ORIGINS="http://localhost:5173")
        assert s.cors_origins_list == ["http://localhost:5173"]


class TestSecurityValidation:
    def test_debug_mode_allows_empty_jwt(self):
        """In debug mode, empty JWT_SECRET should produce a warning, not error."""
        s = Settings(DEBUG=True, JWT_SECRET="")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            s.validate_security()
            assert any("insecure" in str(warning.message).lower() for warning in w)
        # Should have set a default
        assert s.JWT_SECRET != ""

    def test_production_mode_requires_jwt(self):
        """In production mode, empty JWT_SECRET should raise RuntimeError."""
        s = Settings(DEBUG=False, JWT_SECRET="")
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            s.validate_security()

    def test_production_with_jwt_secret_ok(self):
        """Production mode with a proper JWT_SECRET should pass."""
        s = Settings(DEBUG=False, JWT_SECRET="a-very-long-and-secure-secret-key-for-production-use-only")
        s.validate_security()
        assert s.JWT_SECRET == "a-very-long-and-secure-secret-key-for-production-use-only"

    def test_debug_mode_uses_default_secret(self):
        """Debug mode should set a default insecure secret."""
        s = Settings(DEBUG=True, JWT_SECRET="")
        s.validate_security()
        assert "dev-only" in s.JWT_SECRET
