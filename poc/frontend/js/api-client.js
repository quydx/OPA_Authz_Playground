// Thin fetch wrapper around the FastAPI backend. API_BASE comes from
// config.js — a no-op locally, replaced by a ConfigMap on Kubernetes
// (see poc/k8s/07-frontend.yaml) that points at the backend's NodePort.
export const API_BASE = window.API_BASE || "http://localhost:8001";

export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((b) => b.detail)
      .catch(() => null);
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const withQuery = (path, params) => `${path}?${new URLSearchParams(params)}`;
