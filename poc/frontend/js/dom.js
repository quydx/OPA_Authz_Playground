// Tiny templating + DOM helpers. `html` escapes every interpolated value
// unless it was itself produced by `html`/`raw`, so API data (table rows,
// file names, error messages) can never inject markup into the page.
const RAW = Symbol("raw");

export const raw = (s) => ({ [RAW]: String(s) });

function fmt(v) {
  if (v === null || v === undefined || v === false) return "";
  if (Array.isArray(v)) return v.map(fmt).join("");
  if (typeof v === "object" && RAW in v) return v[RAW];
  return escapeHtml(String(v));
}

export function html(strings, ...values) {
  return raw(strings.reduce((out, s, i) => out + fmt(values[i - 1]) + s));
}

export function render(el, tpl) {
  el.innerHTML = fmt(tpl);
}

export function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export function toast(message, { tone = "info", actionLabel, onAction } = {}) {
  const host = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = `toast toast-${tone}`;
  el.setAttribute("role", "status");
  const text = document.createElement("span");
  text.textContent = message;
  el.append(text);
  const timer = setTimeout(dismiss, 5200);
  function dismiss() {
    clearTimeout(timer);
    el.classList.add("is-leaving");
    setTimeout(() => el.remove(), 220);
  }
  if (actionLabel) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "toast-action";
    btn.textContent = actionLabel;
    btn.addEventListener("click", () => {
      dismiss();
      onAction();
    });
    el.append(btn);
  }
  host.append(el);
}

// navigator.clipboard only exists on secure origins — a Kubernetes NodePort
// over plain http isn't one, so fall back to the legacy execCommand path.
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;opacity:0";
    document.body.append(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(d);
}
