import os
from typing import Any

import jwt
from fastapi import Header, HTTPException, Query
from jwt import PyJWKClient, PyJWKClientConnectionError, PyJWKClientError

COGNITO_REGION = os.environ.get("COGNITO_REGION", "us-east-1")
COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "")

# Roles reales del sistema. Cognito agrega AUTOMATICAMENTE un grupo por cada IdP
# federado, con formato `<userPoolId>_<Provider>` (en este pool:
# `us-east-1_h1uG5pYAS_Google`). Eso NO es un rol.
#
# Por eso el rol se resuelve por lista blanca y nunca con `groups[0]`.
# Misma logica que `prisma-front/src/services/cognitoAuth.js`.
ROLES = ("SUPERADMIN", "ADMIN", "TEACHER")

# PENDING no es un grupo de Cognito: es la AUSENCIA de uno. Cualquiera puede
# registrarse con Google o Microsoft, asi que un token valido NO alcanza para
# tener acceso; hace falta que un admin asigne un grupo. El default era TEACHER,
# lo que convertia a cualquier cuenta de Google en docente.
PENDING_ROLE = "PENDING"
DEFAULT_ROLE = PENDING_ROLE

_jwks_client: PyJWKClient | None = None


def _issuer() -> str:
    if not COGNITO_USER_POOL_ID:
        raise HTTPException(status_code=500, detail="COGNITO_USER_POOL_ID no configurado")
    return f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        # PyJWKClient cachea las claves publicas internamente; el issuer no cambia
        # en runtime, asi que se instancia una sola vez por proceso.
        _jwks_client = PyJWKClient(f"{_issuer()}/.well-known/jwks.json")
    return _jwks_client


def resolve_role(groups: Any) -> str:
    items = groups if isinstance(groups, list) else []
    return next((role for role in ROLES if role in items), DEFAULT_ROLE)


def get_current_user(
    authorization: str | None = Header(None),
    token: str | None = Query(None),
) -> dict:
    """
    Acepta el JWT desde el header `Authorization: Bearer <token>` o como query
    param `?token=<token>`.

    El query param quedo desde la epoca del SSE (`EventSource` no admite headers
    custom). El chat ya migro a polling y usa el header, pero se mantiene porque
    los links de descarga del .docx se abren en una pestana nueva, donde tampoco
    se pueden poner headers.
    """
    raw_token = None
    if authorization and authorization.lower().startswith("bearer "):
        raw_token = authorization.split(" ", 1)[1]
    elif token:
        raw_token = token

    if not raw_token:
        raise HTTPException(status_code=401, detail="Token de autorización requerido")

    if not COGNITO_CLIENT_ID:
        raise HTTPException(status_code=500, detail="COGNITO_CLIENT_ID no configurado")

    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(raw_token)
        payload = jwt.decode(
            raw_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=COGNITO_CLIENT_ID,
            issuer=_issuer(),
        )
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except PyJWKClientConnectionError as exc:
        # No se pudo ALCANZAR el JWKS del pool (red, DNS, Cognito caido). Eso si es
        # culpa del servidor: 503, no 401. Va antes que PyJWKClientError porque es
        # una subclase suya.
        raise HTTPException(status_code=503, detail=f"No se pudo obtener el JWKS de Cognito: {exc}")
    except (jwt.InvalidTokenError, PyJWKClientError) as exc:
        # PyJWKClientError (ya descartada la de conexion) se dispara cuando el `kid`
        # del token no esta en el JWKS del pool, es decir: token de otro emisor. Es
        # un error del cliente, no del servidor -> 401 y no 500. Con 500 el front no
        # dispara handleAuthFailure y el usuario ve "el servidor fallo" en vez de
        # volver al login.
        raise HTTPException(status_code=401, detail=f"Token inválido: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error al verificar token: {exc}")

    # El front manda el ID TOKEN, no el access token: es el unico que trae `email`
    # y `name`. El access token de Cognito ademas no lleva `aud`, asi que ni
    # llegaria hasta aca.
    if payload.get("token_use") != "id":
        raise HTTPException(status_code=401, detail="Se esperaba un ID token")

    role = resolve_role(payload.get("cognito:groups"))

    # Choke point unico: cortar aca cubre todo el router del chat, que no tiene
    # chequeos de rol por endpoint.
    if role == PENDING_ROLE:
        raise HTTPException(
            status_code=403,
            detail="Cuenta pendiente de autorización. Contacta al administrador.",
        )

    payload["role"] = role
    payload["colegioId"] = payload.get("custom:colegioId") or None
    return payload
