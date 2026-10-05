"""The backend's own connection to Keycloak's Admin REST API — the thing
that makes the Users tab's CRUD real rather than cosmetic: creating a
user here is what lets that person actually log in and get a JWT, same
as alice/bob/carol/dave/erin already can (see keycloak/realm-idma.json).

Authenticates as its own service account (client `idma-backend-admin`,
confined to realm-management's manage-users/view-users client roles —
see keycloak/realm-idma.json) rather than the master admin/admin login,
same boundary-of-least-privilege spirit as storage_client.py's per-org
MinIO credentials.

Deliberately plain urllib, matching opa_client.py — one more dependency
would be overkill for the handful of calls this needs.
"""
import json
import os
import time
import urllib.error
import urllib.request

KEYCLOAK_ADMIN_BASE_URL = os.environ.get("KEYCLOAK_ADMIN_BASE_URL", "http://keycloak:8080")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "idma")
KEYCLOAK_ADMIN_CLIENT_ID = os.environ.get("KEYCLOAK_ADMIN_CLIENT_ID", "idma-backend-admin")
KEYCLOAK_ADMIN_CLIENT_SECRET = os.environ.get("KEYCLOAK_ADMIN_CLIENT_SECRET", "idma-backend-admin-secret")

ADMIN_USERS_URL = f"{KEYCLOAK_ADMIN_BASE_URL}/admin/realms/{KEYCLOAK_REALM}/users"
TOKEN_URL = f"{KEYCLOAK_ADMIN_BASE_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"


class KeycloakAdminError(Exception):
    """Wraps any failure talking to Keycloak's admin API — main.py turns
    this into a 502 rather than a raw stack trace."""


_token_cache = {"token": None, "expires_at": 0.0}


def _request(method: str, url: str, body: dict | None = None) -> tuple[int, bytes, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {_admin_token()}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as err:
        return err.code, err.read(), dict(err.headers or {})


def _admin_token() -> str:
    """Client-credentials grant, cached until 30s before it expires — same
    idea as the frontend's own keycloak.updateToken(30)."""
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 30:
        return _token_cache["token"]

    form = (
        f"grant_type=client_credentials&client_id={KEYCLOAK_ADMIN_CLIENT_ID}"
        f"&client_secret={KEYCLOAK_ADMIN_CLIENT_SECRET}"
    ).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as err:
        raise KeycloakAdminError(f"could not authenticate to Keycloak admin API: {err.read().decode()}") from err
    except Exception as err:  # noqa: BLE001
        raise KeycloakAdminError(f"could not reach Keycloak admin API: {err}") from err

    _token_cache["token"] = body["access_token"]
    _token_cache["expires_at"] = time.time() + body["expires_in"]
    return _token_cache["token"]


def _find_user_id(username: str) -> str | None:
    status, body, _ = _request("GET", f"{ADMIN_USERS_URL}?username={username}&exact=true")
    if status != 200:
        raise KeycloakAdminError(f"lookup for '{username}' failed ({status}): {body.decode()}")
    matches = json.loads(body)
    return matches[0]["id"] if matches else None


def create_user(username: str, first_name: str, last_name: str, email: str, password: str) -> str:
    """Creates the Keycloak account and sets its (non-temporary) password
    in one shot. Returns the new user's Keycloak id."""
    payload = {
        "username": username,
        "enabled": True,
        "emailVerified": True,
        "firstName": first_name,
        "lastName": last_name,
        "email": email,
        "credentials": [{"type": "password", "value": password, "temporary": False}],
    }
    status, body, headers = _request("POST", ADMIN_USERS_URL, payload)
    if status == 409:
        raise KeycloakAdminError(f"a Keycloak user named '{username}' already exists")
    if status != 201:
        raise KeycloakAdminError(f"user creation failed ({status}): {body.decode()}")

    location = headers.get("Location", "")
    user_id = location.rsplit("/", 1)[-1]
    if not user_id:
        raise KeycloakAdminError("user created but Keycloak returned no id")
    return user_id


def update_user(
    username: str,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    password: str | None = None,
) -> None:
    """Only touches the fields the caller actually passed — reads the
    existing representation first so omitted fields survive the PUT
    (Keycloak's user-update endpoint replaces the whole representation)."""
    user_id = _find_user_id(username)
    if not user_id:
        raise KeycloakAdminError(f"no Keycloak user named '{username}'")

    status, body, _ = _request("GET", f"{ADMIN_USERS_URL}/{user_id}")
    if status != 200:
        raise KeycloakAdminError(f"could not read '{username}' before updating ({status}): {body.decode()}")
    current = json.loads(body)

    if first_name is not None:
        current["firstName"] = first_name
    if last_name is not None:
        current["lastName"] = last_name
    if email is not None:
        current["email"] = email

    status, body, _ = _request("PUT", f"{ADMIN_USERS_URL}/{user_id}", current)
    if status != 204:
        raise KeycloakAdminError(f"user update failed ({status}): {body.decode()}")

    if password:
        status, body, _ = _request(
            "PUT",
            f"{ADMIN_USERS_URL}/{user_id}/reset-password",
            {"type": "password", "value": password, "temporary": False},
        )
        if status != 204:
            raise KeycloakAdminError(f"password reset failed ({status}): {body.decode()}")


def delete_user(username: str) -> None:
    """Idempotent, same spirit as minio/init.sh: deleting a user that's
    already gone from Keycloak (or never made it there) is not an error —
    the app-level row is what main.py actually gates on."""
    user_id = _find_user_id(username)
    if not user_id:
        return
    status, body, _ = _request("DELETE", f"{ADMIN_USERS_URL}/{user_id}")
    if status != 204:
        raise KeycloakAdminError(f"user deletion failed ({status}): {body.decode()}")
