"""Tests para api/auth.py - verificacion del ID token de Cognito."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException

POOL_ID = "us-east-1_h1uG5pYAS"
CLIENT_ID = "76b244eh09k8tto893sqeron6m"
ISSUER = f"https://cognito-idp.us-east-1.amazonaws.com/{POOL_ID}"

# Grupo que Cognito agrega solo por tener Google como IdP federado. NO es un rol.
IDP_GROUP = f"{POOL_ID}_Google"


def _payload(**over):
    base = {
        "sub": "cognito-sub-1",
        "email": "docente@colegio.cl",
        "token_use": "id",
        "aud": CLIENT_ID,
        "iss": ISSUER,
        # Sin grupo el rol cae a PENDING y get_current_user corta con 403, asi
        # que el caso base es un usuario ya habilitado.
        "cognito:groups": ["TEACHER"],
    }
    base.update(over)
    return base


class TestGetCurrentUser:
    def _call(self, authorization=None, token=None):
        from api.auth import get_current_user
        return get_current_user(authorization=authorization, token=token)

    def _cognito(self, payload=None, decode_error=None, signing_key_error=None):
        """Mockea PyJWKClient + jwt.decode + las vars de modulo de Cognito."""
        import api.auth as auth_module
        auth_module._jwks_client = None

        client = MagicMock()
        if signing_key_error:
            client.get_signing_key_from_jwt.side_effect = signing_key_error
        else:
            client.get_signing_key_from_jwt.return_value = MagicMock()

        decode = (
            patch("api.auth.jwt.decode", side_effect=decode_error)
            if decode_error
            else patch("api.auth.jwt.decode", return_value=payload or _payload())
        )
        return (
            patch("api.auth.PyJWKClient", return_value=client),
            decode,
            patch("api.auth.COGNITO_USER_POOL_ID", POOL_ID),
            patch("api.auth.COGNITO_CLIENT_ID", CLIENT_ID),
        )

    # -- entrada ---------------------------------------------------------------

    def test_no_token_raises_401(self):
        with pytest.raises(HTTPException) as e:
            self._call()
        assert e.value.status_code == 401

    def test_invalid_authorization_scheme_raises_401(self):
        with pytest.raises(HTTPException) as e:
            self._call(authorization="Basic abc123")
        assert e.value.status_code == 401

    def test_bearer_token_valid(self):
        a, b, c, d = self._cognito()
        with a, b, c, d:
            result = self._call(authorization="Bearer valid.jwt.token")
        assert result["sub"] == "cognito-sub-1"

    def test_query_param_token_valid(self):
        """El ?token= sigue vivo para los links de descarga del .docx."""
        a, b, c, d = self._cognito()
        with a, b, c, d:
            result = self._call(token="valid.jwt.token")
        assert result["sub"] == "cognito-sub-1"

    # -- verificacion contra Cognito -------------------------------------------

    def test_decode_usa_rs256_audience_e_issuer_de_cognito(self):
        a, b, c, d = self._cognito()
        with a, b as decode, c, d:
            self._call(authorization="Bearer valid.jwt.token")
        kwargs = decode.call_args.kwargs
        assert kwargs["algorithms"] == ["RS256"]
        assert kwargs["audience"] == CLIENT_ID
        assert kwargs["issuer"] == ISSUER

    def test_access_token_rechazado(self):
        a, b, c, d = self._cognito(payload=_payload(token_use="access"))
        with a, b, c, d:
            with pytest.raises(HTTPException) as e:
                self._call(authorization="Bearer access.token")
        assert e.value.status_code == 401
        assert "ID token" in e.value.detail

    # -- claims ----------------------------------------------------------------

    def test_rol_por_lista_blanca_ignora_el_grupo_del_idp(self):
        a, b, c, d = self._cognito(
            payload=_payload(**{"cognito:groups": [IDP_GROUP, "SUPERADMIN"]})
        )
        with a, b, c, d:
            result = self._call(authorization="Bearer valid.jwt.token")
        # Con groups[0] habria salido el grupo autogenerado de Google.
        assert result["role"] == "SUPERADMIN"

    # El default es PENDING (sin acceso), no TEACHER: cualquiera puede
    # registrarse con Google, asi que un token valido no alcanza para entrar.
    def test_sin_grupo_real_rechaza_con_403(self):
        a, b, c, d = self._cognito(payload=_payload(**{"cognito:groups": [IDP_GROUP]}))
        with a, b, c, d:
            with pytest.raises(HTTPException) as e:
                self._call(authorization="Bearer valid.jwt.token")
        assert e.value.status_code == 403

    def test_sin_claim_de_grupos_rechaza_con_403(self):
        payload = _payload()
        payload.pop("cognito:groups")
        a, b, c, d = self._cognito(payload=payload)
        with a, b, c, d:
            with pytest.raises(HTTPException) as e:
                self._call(authorization="Bearer valid.jwt.token")
        assert e.value.status_code == 403

    def test_colegio_id_desde_atributo_custom(self):
        a, b, c, d = self._cognito(
            payload=_payload(**{"custom:colegioId": "colegio-uuid-1"})
        )
        with a, b, c, d:
            result = self._call(authorization="Bearer valid.jwt.token")
        assert result["colegioId"] == "colegio-uuid-1"

    def test_colegio_id_none_si_no_viene(self):
        a, b, c, d = self._cognito()
        with a, b, c, d:
            result = self._call(authorization="Bearer valid.jwt.token")
        assert result["colegioId"] is None

    # -- errores ---------------------------------------------------------------

    def test_expired_token_raises_401(self):
        import jwt
        a, b, c, d = self._cognito(decode_error=jwt.ExpiredSignatureError())
        with a, b, c, d:
            with pytest.raises(HTTPException) as e:
                self._call(authorization="Bearer expired.token")
        assert e.value.status_code == 401
        assert "expirado" in e.value.detail.lower()

    def test_invalid_token_raises_401(self):
        import jwt
        a, b, c, d = self._cognito(decode_error=jwt.InvalidTokenError())
        with a, b, c, d:
            with pytest.raises(HTTPException) as e:
                self._call(authorization="Bearer bad.token")
        assert e.value.status_code == 401

    def test_kid_desconocido_es_401_y_no_500(self):
        """Un token de OTRO emisor (el kid no esta en el JWKS) es culpa del cliente.

        Regresion concreta: con Supabase esto devolvia 500, y el front nunca
        disparaba handleAuthFailure - el usuario veia "el servidor fallo" en vez
        de volver al login.
        """
        from jwt import PyJWKClientError
        a, b, c, d = self._cognito(
            signing_key_error=PyJWKClientError("Unable to find a signing key that matches")
        )
        with a, b, c, d:
            with pytest.raises(HTTPException) as e:
                self._call(authorization="Bearer foreign.token")
        assert e.value.status_code == 401

    def test_jwks_inalcanzable_es_503(self):
        """Si no se puede ALCANZAR el JWKS, si es culpa del servidor."""
        from jwt import PyJWKClientConnectionError
        a, b, c, d = self._cognito(
            signing_key_error=PyJWKClientConnectionError("connection refused")
        )
        with a, b, c, d:
            with pytest.raises(HTTPException) as e:
                self._call(authorization="Bearer some.token")
        assert e.value.status_code == 503

    def test_missing_user_pool_id_raises_500(self):
        import api.auth as auth_module
        auth_module._jwks_client = None
        with patch("api.auth.COGNITO_USER_POOL_ID", ""), patch("api.auth.COGNITO_CLIENT_ID", CLIENT_ID):
            with pytest.raises(HTTPException) as e:
                self._call(authorization="Bearer some.token")
        assert e.value.status_code == 500

    def test_missing_client_id_raises_500(self):
        import api.auth as auth_module
        auth_module._jwks_client = None
        with patch("api.auth.COGNITO_USER_POOL_ID", POOL_ID), patch("api.auth.COGNITO_CLIENT_ID", ""):
            with pytest.raises(HTTPException) as e:
                self._call(authorization="Bearer some.token")
        assert e.value.status_code == 500

    def test_unexpected_exception_raises_500(self):
        a, b, c, d = self._cognito(signing_key_error=RuntimeError("unexpected"))
        with a, b, c, d:
            with pytest.raises(HTTPException) as e:
                self._call(authorization="Bearer some.token")
        assert e.value.status_code == 500
