"""Validates Keycloak-issued access tokens on every protected API call.

Keycloak is the authentication service (see IDMA Authen & Authz.MD); this
backend never sees a password, only the JWT the frontend obtained from
Keycloak's own login flow. `aud` isn't checked here — the "trino" audience
Keycloak stamps into the token (see keycloak/realm-idma.json's
trino-audience mapper) is meaningful to Trino's own JWT authenticator, not
to the backend, which only needs to know *who* the caller is.
"""

import os

import jwt
from fastapi import HTTPException, Request
from jwt import PyJWKClient

KEYCLOAK_ISSUER = os.environ.get("KEYCLOAK_ISSUER", "http://localhost:8180/realms/idma")
KEYCLOAK_JWKS_URL = os.environ.get(
    "KEYCLOAK_JWKS_URL", "http://keycloak:8080/realms/idma/protocol/openid-connect/certs"
)

# Caches the JWKS response and only re-fetches once the referenced key id
# isn't in the cache — cheap enough to share one client for the process.
_jwk_client = PyJWKClient(KEYCLOAK_JWKS_URL)


class AuthedUser:
    def __init__(self, username: str, token: str):
        self.username = username
        self.token = token


def get_current_user(request: Request) -> AuthedUser:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Authorization: Bearer <token> header")
    token = header.split(" ", 1)[1].strip()

    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=KEYCLOAK_ISSUER,
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as err:
        raise HTTPException(status_code=401, detail=f"invalid token: {err}") from err

    username = claims.get("preferred_username")
    if not username:
        raise HTTPException(status_code=401, detail="token has no preferred_username claim")
    return AuthedUser(username=username, token=token)
