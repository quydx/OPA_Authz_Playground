// Local/docker-compose default: no override — app.js's own
// `window.API_BASE || "http://localhost:8001"` fallback applies.
//
// In Kubernetes this exact file is replaced by a ConfigMap-mounted
// version (see poc/k8s/07-frontend.yaml) that computes API_BASE from
// the page's own hostname, since NodePort means the browser reaches the
// backend at whichever node IP it used for the frontend itself, not
// "localhost".

// Keycloak connection — app.js's own fallbacks below apply unless
// overridden here (same pattern as API_BASE above).
window.KEYCLOAK_URL = window.KEYCLOAK_URL || "http://localhost:8180";
window.KEYCLOAK_REALM = window.KEYCLOAK_REALM || "idma";
window.KEYCLOAK_CLIENT_ID = window.KEYCLOAK_CLIENT_ID || "idma-frontend";
