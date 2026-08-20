import os

import jwt
from fastapi import Header, HTTPException, Query
from jwt import PyJWKClient

COGNITO_REGION = os.environ.get("COGNITO_REGION", "us-east-1")
COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "")

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        if not COGNITO_USER_POOL_ID:
            raise HTTPException(status_code=500, detail="COGNITO_USER_POOL_ID no configurado")
        issuer = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
        _jwks_client = PyJWKClient(f"{issuer}/.well-known/jwks.json")
    return _jwks_client


def get_current_user(
    authorization: str | None = Header(None),
    token: str | None = Query(None),
) -> dict:
    """
    Acepta el JWT desde el header Authorization: Bearer <token>
    o como query param ?token=<token> (necesario para EventSource y links de descarga).
    """
    raw_token = None
    if authorization and authorization.lower().startswith("bearer "):
        raw_token = authorization.split(" ", 1)[1]
    elif token:
        raw_token = token

    if not raw_token:
        raise HTTPException(status_code=401, detail="Token de autorización requerido")

    issuer = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"

    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(raw_token)
        payload = jwt.decode(
            raw_token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"verify_aud": False},
        )
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al verificar token: {e}")
    return payload
