// App shell: sidebar navigation, hash router, theme toggle, and boot.
// Each view module exports { mount(section), refresh() }; a view is mounted
// once, then refreshed whenever it's shown or the user/policies change.
import { API_BASE } from "./api-client.js";
import { bootstrap, subscribe } from "./state.js";
import { html, render, toast } from "./dom.js";
import { icon } from "./icons.js";
import { renderIdentity } from "./identity.js";
import { emptyState, skeleton } from "./ui-parts.js";
import catalogView from "./views/catalog-view.js";
import jobsView from "./views/jobs-view.js";
import storageView from "./views/storage-view.js";
import policiesView from "./views/policies-view.js";
import architectureView from "./views/architecture-view.js";
import environmentView from "./views/environment-view.js";

const ROUTES = [
  { id: "catalog", group: "Explore", label: "Catalog", hint: "Trino", icon: "database", view: catalogView },
  { id: "jobs", group: "Explore", label: "Jobs", hint: "Airflow", icon: "flow", view: jobsView },
  { id: "storage", group: "Explore", label: "Storage", hint: "MinIO", icon: "bucket", view: storageView },
  { id: "policies", group: "Govern", label: "Access policies", hint: "authz", icon: "key", view: policiesView },
  { id: "architecture", group: "Platform", label: "Architecture", icon: "diagram", view: architectureView },
  { id: "environment", group: "Platform", label: "Environment", icon: "terminal", view: environmentView },
];

const navEl = document.getElementById("nav");
const crumbsEl = document.getElementById("crumbs");
const pageEl = document.getElementById("page");
const themeBtn = document.getElementById("theme-toggle");
const sections = new Map();
let active = null;

function renderNav() {
  const groups = [...new Set(ROUTES.map((r) => r.group))];
  render(navEl, groups.map((g) => html`
    <p class="nav-group">${g}</p>
    ${ROUTES.filter((r) => r.group === g).map(
      (r) => html`<a class="nav-link" href="#/${r.id}" data-route="${r.id}">${icon(r.icon)}<span>${r.label}</span>${r.hint ? html`<span class="nav-hint">${r.hint}</span>` : ""}</a>`
    )}`));
}

const routeFromHash = () => ROUTES.find((r) => r.id === location.hash.replace(/^#\/?/, "")) || ROUTES[0];

async function refreshActive() {
  try {
    await active.view.refresh();
  } catch (err) {
    console.error(err);
    toast(`Couldn't load ${active.label}: ${err.message}`, { tone: "deny" });
  }
}

function show(route) {
  active = route;
  navEl.querySelectorAll("[data-route]").forEach((a) => {
    if (a.dataset.route === route.id) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  render(crumbsEl, html`<span>iHub</span>${icon("chevron-right")}<span>${route.group}</span>${icon("chevron-right")}<strong>${route.label}</strong>`);
  let section = sections.get(route.id);
  if (!section) {
    section = document.createElement("section");
    section.className = "view";
    section.setAttribute("aria-label", route.label);
    pageEl.append(section);
    route.view.mount(section);
    sections.set(route.id, section);
  }
  sections.forEach((s, id) => (s.hidden = id !== route.id));
  section.classList.remove("is-entering");
  void section.offsetWidth; // restart the enter animation
  section.classList.add("is-entering");
  document.title = `${route.label} · iHub Access Console`;
  refreshActive();
}

// ---- theme: follows the OS until the user picks one ------------------
function effectiveTheme() {
  return document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
function renderThemeButton() {
  const dark = effectiveTheme() === "dark";
  render(themeBtn, icon(dark ? "sun" : "moon"));
  themeBtn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
}
themeBtn.addEventListener("click", () => {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem("ihub-theme", next);
  } catch {
    /* preference just won't persist */
  }
  renderThemeButton();
});

function renderBootError(err) {
  render(pageEl, emptyState({
    iconName: "alert",
    tone: "empty-error",
    title: "Can't reach the backend",
    body: html`Tried <code>${API_BASE}</code> — ${err.message}. Is the stack up? <code>docker compose up -d --build</code>`,
    action: html`<button type="button" class="btn btn-primary" onclick="location.reload()">Retry</button>`,
  }));
}

(async function start() {
  renderNav();
  renderThemeButton();
  render(pageEl, skeleton(6));
  try {
    await bootstrap();
  } catch (err) {
    return renderBootError(err);
  }
  pageEl.innerHTML = "";
  renderIdentity();
  subscribe(() => {
    renderIdentity();
    refreshActive();
  });
  window.addEventListener("hashchange", () => show(routeFromHash()));
  show(routeFromHash());
})();
