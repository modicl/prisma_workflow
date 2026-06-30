"""Tests para api/auth.py — verificación JWT Supabase."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException


def _make_payload(sub="user-123", email="doc@test.com", aud="authenticated"):
    return {"sub": sub, "email": email, "aud": aud}


class TestGetCurrentUser:
    """Tests de get_current_user en distintos escenarios de token."""

    def _call(self, authorization=None, token=None):
        from api.auth import get_current_user
        return get_current_user(authorization=authorization, token=token)

    def _mock_jwks(self, payload=None, side_effect=None):
        """Devuelve un contexto que mockea PyJWKClient + jwt.decode."""
        import jwt
        mock_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_client.get_signing_key_from_jwt.return_value = mock_signing_key

        if side_effect:
            decode_mock = patch("api.auth.jwt.decode", side_effect=side_effect)
        else:
            decode_mock = patch("api.auth.jwt.decode", return_value=payload or _make_payload())

        jwks_mock = patch("api.auth.PyJWKClient", return_value=mock_client)
        return jwks_mock, decode_mock

    def test_no_token_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            self._call(authorization=None, token=None)
        assert exc_info.value.status_code == 401

    def _with_supabase(self, jwks_mock, decode_mock):
        """Combina mocks de JWKS + decode + variable de módulo SUPABASE_URL."""
        import api.auth as auth_module
        auth_module._jwks_client = None
        return (
            jwks_mock,
            decode_mock,
            patch("api.auth.SUPABASE_URL", "https://test.supabase.co"),
        )

    def test_bearer_token_valid(self):
        jwks_mock, decode_mock = self._mock_jwks()
        m1, m2, m3 = self._with_supabase(jwks_mock, decode_mock)
        with m1, m2, m3:
            result = self._call(authorization="Bearer valid.jwt.token")
        assert result["sub"] == "user-123"

    def test_query_param_token_valid(self):
        jwks_mock, decode_mock = self._mock_jwks()
        m1, m2, m3 = self._with_supabase(jwks_mock, decode_mock)
        with m1, m2, m3:
            result = self._call(token="valid.jwt.token")
        assert result["sub"] == "user-123"

    def test_invalid_authorization_scheme_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            self._call(authorization="Basic abc123")
        assert exc_info.value.status_code == 401

    def test_expired_token_raises_401(self):
        import jwt
        jwks_mock, decode_mock = self._mock_jwks(side_effect=jwt.ExpiredSignatureError())
        m1, m2, m3 = self._with_supabase(jwks_mock, decode_mock)
        with m1, m2, m3:
            with pytest.raises(HTTPException) as exc_info:
                self._call(authorization="Bearer expired.token")
        assert exc_info.value.status_code == 401
        assert "expirado" in exc_info.value.detail.lower()

    def test_invalid_token_raises_401(self):
        import jwt
        jwks_mock, decode_mock = self._mock_jwks(side_effect=jwt.InvalidTokenError())
        m1, m2, m3 = self._with_supabase(jwks_mock, decode_mock)
        with m1, m2, m3:
            with pytest.raises(HTTPException) as exc_info:
                self._call(authorization="Bearer bad.token")
        assert exc_info.value.status_code == 401

    def test_missing_supabase_url_raises_500(self):
        import api.auth as auth_module
        auth_module._jwks_client = None
        with patch("api.auth.SUPABASE_URL", ""):
            with pytest.raises(HTTPException) as exc_info:
                self._call(authorization="Bearer some.token")
        assert exc_info.value.status_code == 500

    def test_unexpected_exception_raises_500(self):
        import api.auth as auth_module
        auth_module._jwks_client = None
        mock_client = MagicMock()
        mock_client.get_signing_key_from_jwt.side_effect = RuntimeError("unexpected")
        with patch("api.auth.PyJWKClient", return_value=mock_client), \
             patch("api.auth.SUPABASE_URL", "https://test.supabase.co"):
            with pytest.raises(HTTPException) as exc_info:
                self._call(authorization="Bearer some.token")
        assert exc_info.value.status_code == 500
