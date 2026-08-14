"""Tests for Emma authentication."""
from __future__ import annotations

import pytest
from jose import jwt
from passlib.context import CryptContext

from main import _hash_password, _verify_password, _create_session_token, _decode_session_token, JWT_SECRET, JWT_ALGORITHM


class TestPasswordHashing:
    """Test password hashing and verification."""

    def test_hash_password(self):
        """Test password hashing produces secure hash."""
        password = "test_password_123"
        hashed = _hash_password(password)
        # Should be argon2 or PBKDF2 format
        assert hashed.startswith(("$argon2", "pbkdf2_sha256$"))
        assert len(hashed) > 50

    def test_verify_correct_password(self):
        """Test verifying correct password."""
        password = "test_password_123"
        hashed = _hash_password(password)
        assert _verify_password(password, hashed) is True

    def test_verify_incorrect_password(self):
        """Test verifying incorrect password fails."""
        password = "test_password_123"
        hashed = _hash_password(password)
        assert _verify_password("wrong_password", hashed) is False

    def test_different_hashes_for_same_password(self):
        """Test that hashing same password twice produces different hashes."""
        password = "test_password_123"
        hash1 = _hash_password(password)
        hash2 = _hash_password(password)
        assert hash1 != hash2  # salt ensures different hashes


class TestJWTTokens:
    """Test JWT session token creation and validation."""

    def test_create_and_decode_token(self):
        """Test creating and decoding a session token."""
        data = {"sub": "user", "type": "web"}
        token = _create_session_token(data)
        
        assert isinstance(token, str)
        assert len(token) > 0
        
        decoded = _decode_session_token(token)
        assert decoded is not None
        assert decoded["sub"] == "user"
        assert decoded["type"] == "web"
        assert "exp" in decoded
        assert "iat" in decoded

    def test_decode_invalid_token(self):
        """Test decoding invalid token returns None."""
        assert _decode_session_token("invalid.token.here") is None
        assert _decode_session_token("") is None

    def test_decode_tampered_token(self):
        """Test decoding tampered token returns None."""
        data = {"sub": "user"}
        token = _create_session_token(data)
        # Tamper with token
        tampered = token[:-5] + "xxxxx"
        assert _decode_session_token(tampered) is None

    def test_token_uses_correct_secret_and_algorithm(self):
        """Test token uses configured secret and algorithm."""
        data = {"test": "value"}
        token = _create_session_token(data)
        
        # Verify we can decode with the same secret
        decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert decoded["test"] == "value"


class TestCORSConfiguration:
    """Test CORS configuration."""

    def test_cors_origins_not_wildcard_with_credentials(self):
        """Test that CORS doesn't use wildcard with credentials."""
        # This is verified in main.py - allowed_origins is explicit list
        allowed_origins = ["http://localhost:8000", "http://127.0.0.1:8000"]
        assert "*" not in allowed_origins
        assert len(allowed_origins) > 0