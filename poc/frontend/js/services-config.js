// Endpoints and lab credentials shown on the Environment page. These are the
// docker-compose defaults; on Kubernetes the config.js ConfigMap
// (poc/k8s/07-frontend.yaml) overrides window.SERVICES / window.CREDENTIALS /
// window.AIRFLOW_LOG_CMD with NodePorts and marks OPA/Postgres internal-only.
const DEFAULT_SERVICES = [
  { name: "Frontend", port: 3000, path: "/", desc: "this UI" },
  { name: "Backend API", port: 8001, path: "/api/health", desc: "FastAPI" },
  { name: "Trino", port: 8080, path: "/ui/", desc: "coordinator UI" },
  { name: "Airflow", port: 8082, path: "/", desc: "webserver" },
  { name: "MinIO API", port: 9000, path: "/", desc: "S3 endpoint" },
  { name: "MinIO Console", port: 9001, path: "/", desc: "browser UI" },
  { name: "OPA", port: 8181, path: "/health", desc: "policy engine" },
  { name: "Postgres", port: 5433, desc: "authz · demo · airflow DBs", tcp: true },
];

// Same demo-only values as poc/docker-compose.yml, poc/k8s/01-secrets.yaml and
// backend/app/seed.py's ORG_STORAGE. Airflow passwords are not listed here:
// SimpleAuthManager mints random ones (docker-compose pins them via
// AIRFLOW_DEMO_PASSWORDS; the backend serves them live either way).
const DEFAULT_CREDENTIALS = [
  { service: "Postgres", user: "poc", pass: "pocpass", notes: "one instance, three databases: authz, demo, airflow" },
  { service: "MinIO (console / admin)", user: "pocadmin", pass: "pocadminpass", notes: "full admin — MinIO Console login" },
  { service: "MinIO — org-001 (Acme Retail)", user: "org001svc", pass: "org001SecretKey123", notes: "scoped to org-001's bucket only — native MinIO IAM, no OPA involved" },
  { service: "MinIO — org-002 (Globex Logistics)", user: "org002svc", pass: "org002SecretKey456", notes: "scoped to org-002's bucket only" },
  { service: "Airflow", user: "alice", pass: null, notes: "USER · org-001 · grp_data_eng" },
  { service: "Airflow", user: "bob", pass: null, notes: "USER · org-001" },
  { service: "Airflow", user: "carol", pass: null, notes: "USER · org-001 · grp_hr" },
  { service: "Airflow", user: "dave", pass: null, notes: "VIEWER · org-001" },
  { service: "Airflow", user: "erin", pass: null, notes: "USER · org-002 · grp_data_eng (same group as alice, different org — isolation still holds)" },
  { service: "Trino", user: null, pass: null, notes: "no authentication — client sends user= directly; trino.rego is the only gate" },
  { service: "OPA", user: null, pass: null, notes: "no authentication — internal service only" },
];

const DEFAULT_AIRFLOW_LOG_CMD = 'docker compose exec airflow-apiserver cat /opt/airflow/simple_auth_manager_passwords.json.generated';

export const services = () => window.SERVICES || DEFAULT_SERVICES;
export const credentials = () => window.CREDENTIALS || DEFAULT_CREDENTIALS;
export const airflowLogCmd = () => window.AIRFLOW_LOG_CMD || DEFAULT_AIRFLOW_LOG_CMD;

// Built from this page's own hostname, so the same list works on localhost
// and on whichever Kubernetes node address was used to reach the UI.
export function serviceAddress(svc) {
  if (!svc.port) return null;
  const host = window.location.hostname;
  return svc.tcp ? `${host}:${svc.port}` : `${window.location.protocol}//${host}:${svc.port}${svc.path || "/"}`;
}

export function serviceUrl(name) {
  const svc = services().find((s) => s.name === name);
  return svc && !svc.tcp ? serviceAddress(svc) : null;
}
