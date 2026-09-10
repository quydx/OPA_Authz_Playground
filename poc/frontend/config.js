// Local/docker-compose default: no override — app.js's own
// `window.API_BASE || "http://localhost:8001"` fallback applies.
//
// In Kubernetes this exact file is replaced by a ConfigMap-mounted
// version (see poc/k8s/07-frontend.yaml) that computes API_BASE from
// the page's own hostname, since NodePort means the browser reaches the
// backend at whichever node IP it used for the frontend itself, not
// "localhost".
