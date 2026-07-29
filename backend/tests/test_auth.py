"""Unit tests for auth module — password hashing, JWT, project ownership."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import bcrypt
import jwt as pyjwt
import pytest
from fastapi import HTTPException

from app.config import settings
from app.core.auth import (
    create_access_token,
    decode_access_token,
    get_project_for_owner,
    hash_password,
    password_needs_rehash,
    verify_password,
)

# ─── Password hashing tests ──────────────────────────────────


class TestPasswordHashing:
    def test_hash_password_returns_bcrypt_hash(self):
        hashed = hash_password("mypassword")
        assert hashed != "mypassword"
        assert hashed.startswith("bcrypt-sha256$$2")

    def test_hash_password_different_each_time(self):
        """Same password should produce different hashes (random salt)."""
        h1 = hash_password("samepass")
        h2 = hash_password("samepass")
        assert h1 != h2

    def test_verify_password_correct(self):
        hashed = hash_password("mypassword")
        assert verify_password("mypassword", hashed) is True

    def test_verify_password_wrong(self):
        hashed = hash_password("mypassword")
        assert verify_password("wrongpass", hashed) is False

    def test_verify_password_empty(self):
        hashed = hash_password("mypassword")
        assert verify_password("", hashed) is False

    def test_hash_and_verify_unicode_password(self):
        """Chinese characters in password should work."""
        pwd = "密码123!@#"
        hashed = hash_password(pwd)
        assert verify_password(pwd, hashed) is True

    def test_hash_long_password(self):
        """Long passwords should use every byte, not bcrypt's first 72 bytes."""
        pwd = "a" * 100
        hashed = hash_password(pwd)
        assert verify_password(pwd, hashed) is True
        assert verify_password("a" * 99 + "b", hashed) is False

    def test_legacy_bcrypt_hash_still_verifies_and_needs_rehash(self):
        password = "legacy-password"
        legacy_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        assert verify_password(password, legacy_hash) is True
        assert password_needs_rehash(legacy_hash) is True
        assert password_needs_rehash(hash_password(password)) is False


# ─── JWT token tests ─────────────────────────────────────────


class TestJWTToken:
    def test_create_token_contains_required_claims(self):
        token = create_access_token("user123", "testuser")
        payload = pyjwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        assert payload["sub"] == "user123"
        assert payload["username"] == "testuser"
        assert "iat" in payload
        assert "jti" in payload
        assert "exp" in payload

    def test_create_token_jti_is_unique(self):
        """Each token should have a unique jti."""
        t1 = create_access_token("user1", "user1")
        t2 = create_access_token("user1", "user1")
        p1 = pyjwt.decode(t1, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        p2 = pyjwt.decode(t2, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        assert p1["jti"] != p2["jti"]

    def test_decode_valid_token(self):
        token = create_access_token("user456", "alice")
        payload = decode_access_token(token)
        assert payload["sub"] == "user456"
        assert payload["username"] == "alice"

    def test_decode_invalid_token_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token("invalid.jwt.token")
        assert exc_info.value.status_code == 401
        assert "无效的 Token" in exc_info.value.detail

    def test_decode_empty_token_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token("")
        assert exc_info.value.status_code == 401

    def test_decode_token_with_wrong_secret_raises(self):
        """Token signed with different secret should be rejected."""
        wrong_token = pyjwt.encode(
            {"sub": "user1", "exp": 9999999999},
            "wrong-secret",
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(wrong_token)
        assert exc_info.value.status_code == 401

    def test_token_expiration(self):
        """Expired token should raise 401."""
        # Create an already-expired token
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        payload = {
            "sub": "user1",
            "username": "testuser",
            "iat": now - timedelta(days=10),
            "jti": "test-jti",
            "exp": now - timedelta(days=1),  # expired yesterday
        }
        expired_token = pyjwt.encode(
            payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM
        )
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(expired_token)
        assert exc_info.value.status_code == 401
        assert "过期" in exc_info.value.detail


# ─── Project ownership tests ─────────────────────────────────


class TestProjectOwnership:
    @pytest.mark.asyncio
    async def test_get_project_for_owner_not_found(self):
        """Should raise 404 when project doesn't exist."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        mock_user = MagicMock()
        mock_user.id = "user1"

        with pytest.raises(HTTPException) as exc_info:
            await get_project_for_owner("nonexistent", mock_user, mock_db)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_project_for_owner_owner(self):
        """Owner should get the project."""
        mock_project = MagicMock()
        mock_project.id = "proj1"
        mock_project.owner_id = "user1"

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_project
        mock_db.execute.return_value = mock_result

        mock_user = MagicMock()
        mock_user.id = "user1"

        result = await get_project_for_owner("proj1", mock_user, mock_db)
        assert result is mock_project

    @pytest.mark.asyncio
    async def test_get_project_for_owner_wrong_user_403(self):
        """Non-owner should get 403."""
        mock_project = MagicMock()
        mock_project.id = "proj1"
        mock_project.owner_id = "user1"

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_project
        mock_db.execute.return_value = mock_result

        mock_user = MagicMock()
        mock_user.id = "user2"  # Different user

        with pytest.raises(HTTPException) as exc_info:
            await get_project_for_owner("proj1", mock_user, mock_db)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_get_project_for_owner_null_owner_id(self):
        """Ownerless legacy projects must not be exposed to arbitrary users."""
        mock_project = MagicMock()
        mock_project.id = "proj1"
        mock_project.owner_id = None  # Legacy project

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_project
        mock_db.execute.return_value = mock_result

        mock_user = MagicMock()
        mock_user.id = "anyuser"

        with pytest.raises(HTTPException) as exc_info:
            await get_project_for_owner("proj1", mock_user, mock_db)
        assert exc_info.value.status_code == 403


# ─── Integration: hash → verify → token round-trip ──────────


class TestAuthRoundTrip:
    def test_full_auth_flow(self):
        """Simulate: register password → hash → verify → create token → decode."""
        password = "SecureP@ss123"
        # Hash
        hashed = hash_password(password)
        # Verify
        assert verify_password(password, hashed) is True
        assert verify_password("wrong", hashed) is False
        # Create token
        token = create_access_token("user_abc", "john")
        # Decode
        payload = decode_access_token(token)
        assert payload["sub"] == "user_abc"
        assert payload["username"] == "john"
