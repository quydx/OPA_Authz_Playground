// "Viewing as" identity switcher (top bar) and the acting-user card
// (sidebar). There is no login in this POC — picking a demo identity is
// what every OPA check on every page is evaluated against.
import { currentUser, orgLabel, setUser, state } from "./state.js";
import { html, render } from "./dom.js";
import { icon } from "./icons.js";
import { avatar } from "./ui-parts.js";

const switchEl = document.getElementById("viewas");
const cardEl = document.getElementById("identity-card");
let open = false;

export function renderIdentity() {
  const u = currentUser();
  if (!u) return;
  render(switchEl, html`
    <button type="button" class="viewas-btn" aria-haspopup="listbox" aria-expanded="${String(open)}" data-viewas-toggle>
      ${avatar(u, "avatar-sm")}
      <span class="viewas-text"><span class="viewas-eyebrow">Viewing as</span><strong>${u.label}</strong></span>
      ${icon("chevron-down", "viewas-caret")}
    </button>
    ${open
      ? html`<div class="menu" role="listbox" aria-label="Demo identities">
          ${state.users.map(
            (x) => html`<button type="button" role="option" aria-selected="${String(x.id === u.id)}" class="menu-item" data-user="${x.id}">
              ${avatar(x)}
              <span class="menu-item-text"><strong>${x.label}</strong><span>${x.title} · ${x.org}${x.groups.length ? ` · ${x.groups.join(", ")}` : ""}</span></span>
              ${x.id === u.id ? icon("check", "tone-accent") : ""}
            </button>`
          )}
          <p class="menu-foot">Demo identities, no login. Every check on every page runs as the selected user.</p>
        </div>`
      : ""}`);

  render(cardEl, html`
    <p class="identity-eyebrow">Acting as</p>
    <div class="identity-main">${avatar(u)}<div><strong>${u.label}</strong><span>${u.title}</span></div></div>
    <dl class="identity-kv">
      <div><dt>org</dt><dd title="${u.org}">${orgLabel(u.org)}</dd></div>
      <div><dt>groups</dt><dd>${u.groups.length ? u.groups.join(", ") : "none"}</dd></div>
      ${u.region !== undefined ? html`<div><dt>region</dt><dd>${u.region || "—"}</dd></div><div><dt>hr</dt><dd>${u.hr ? "yes" : "no"}</dd></div>` : ""}
    </dl>`);
}

// focus: "selected" (menu item) | "toggle" (the button) | undefined (leave it)
function setOpen(next, focus) {
  open = next;
  renderIdentity();
  if (focus === "selected") switchEl.querySelector('[aria-selected="true"]')?.focus();
  if (focus === "toggle") switchEl.querySelector("[data-viewas-toggle]")?.focus();
}

switchEl.addEventListener("click", (e) => {
  const pick = e.target.closest("[data-user]");
  if (pick) {
    open = false;
    if (pick.dataset.user !== state.userId) setUser(pick.dataset.user); // re-renders via subscription
    else renderIdentity();
    switchEl.querySelector("[data-viewas-toggle]")?.focus();
  } else if (e.target.closest("[data-viewas-toggle]")) {
    setOpen(!open, open ? "toggle" : "selected");
  }
});

// composedPath() is captured at dispatch, so it still includes the switcher
// even when the click target was replaced by the re-render above.
document.addEventListener("click", (e) => {
  if (open && !e.composedPath().includes(switchEl)) setOpen(false);
});

switchEl.addEventListener("keydown", (e) => {
  if (!open) return;
  if (e.key === "Escape") return setOpen(false, "toggle");
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  e.preventDefault();
  const items = [...switchEl.querySelectorAll("[data-user]")];
  const i = items.indexOf(document.activeElement);
  const next = e.key === "ArrowDown" ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
  items[next].focus();
});
