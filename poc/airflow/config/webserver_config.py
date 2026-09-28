"""Flask-AppBuilder config for FabAuthManager — Keycloak OIDC login.

Loaded from [fab] config_file (defaults to /opt/airflow/webserver_config.py,
see docker-compose.yml's mount). This is the piece that makes clicking
"Airflow" in the frontend's Services tab log in automatically as the same
Keycloak user, the same way trino/config.properties' OAUTH2 authenticator
does for Trino's Web UI.

Flask-AppBuilder ships a built-in "keycloak" OAuth provider
(FabAirflowSecurityManagerOverride.get_oauth_user_info in
apache-airflow-providers-fab), but its userinfo call
(`self.oauth_remotes[provider].get("openid-connect/userinfo")`) came
back a genuine 401 from Keycloak every time, including with the token
passed explicitly — never fully root-caused, verified only that the
exact same call succeeds via a plain `requests.get` with a manually
attached `Authorization: Bearer` header, so something about how Authlib
issues this specific request trips Keycloak's check. Sidestepped
entirely: because `jwks_uri` is configured above, Authlib already
verifies the ID token itself and hands back its decoded claims as
`resp["userinfo"]` — same claims (`preferred_username`, `email`, ...),
no extra network call needed. KeycloakSecurityManager below reads that
instead of hitting the endpoint.

Every Keycloak-authenticated user gets the same flat AUTH_USER_REGISTRATION_ROLE
below — Dag-level authorization doesn't depend on it at all (OPAAuthManager
overrides is_authorized_dag() to ask OPA regardless of role); this only
governs the handful of non-Dag resources (connections, variables, pools,
configuration) OPAAuthManager leaves untouched, same as the old
SimpleAuthManager setup, just without a per-user role list.
"""

from flask_appbuilder.security.manager import AUTH_OAUTH
from airflow.providers.fab.auth_manager.security_manager.override import (
    FabAirflowSecurityManagerOverride,
)


class KeycloakSecurityManager(FabAirflowSecurityManagerOverride):
    def get_oauth_user_info(self, provider, resp):
        if provider != "keycloak":
            return super().get_oauth_user_info(provider, resp)
        data = resp["userinfo"]
        return {
            "username": data.get("preferred_username", ""),
            "first_name": data.get("given_name", ""),
            "last_name": data.get("family_name", ""),
            "email": data.get("email", ""),
        }


SECURITY_MANAGER_CLASS = KeycloakSecurityManager

AUTH_TYPE = AUTH_OAUTH
AUTH_USER_REGISTRATION = True
AUTH_USER_REGISTRATION_ROLE = "User"
AUTH_ROLES_SYNC_AT_LOGIN = True

OAUTH_PROVIDERS = [
    {
        "name": "keycloak",
        "icon": "fa-key",
        "token_key": "access_token",
        "remote_app": {
            "client_id": "airflow-webserver",
            "client_secret": "airflow-webserver-secret",
            # Server-side calls (token exchange, userinfo) — internal docker
            # network address, same split as trino/config.properties.
            "api_base_url": "http://keycloak:8080/realms/idma/protocol/",
            "access_token_url": "http://keycloak:8080/realms/idma/protocol/openid-connect/token",
            "jwks_uri": "http://keycloak:8080/realms/idma/protocol/openid-connect/certs",
            "request_token_url": None,
            # Browser-facing redirect — must be reachable from the host.
            "authorize_url": "http://localhost:8180/realms/idma/protocol/openid-connect/auth",
            "client_kwargs": {"scope": "openid email profile"},
        },
    }
]
