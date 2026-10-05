// Grant / revoke side panel, reachable from every page. Shows the exact
// policy row that will be written and — before anything is saved — who it
// will actually take effect for once same_org() is applied.
import { changePolicy, isGroup, membersOf, orgOf, state, userById } from "../state.js";
import { html, render, toast } from "../dom.js";
import { icon } from "../icons.js";
import { orgBadge, policyTuple } from "../ui-parts.js";
import { memberChip } from "../access-explainer.js";

const host = document.getElementById("drawer-root");
const KIND_LABEL = { data: "Data · Trino", jobs: "Job · Airflow" };
let form = null; // { kind, sub, obj, act }
let lastFocus = null;

function optionsFor(kind) {
  const o = kind === "jobs" ? state.options.jobs : state.options.data;
  return { resources: o.resources, actions: kind === "jobs" ? o.actions : ["select"] };
}

const option = (value, current) => html`<option value="${value}"${value === current ? " selected" : ""}>${value}</option>`;

export function openGrantDrawer(prefill = {}) {
  const kind = prefill.kind || (prefill.obj?.startsWith("airflow.dag.") ? "jobs" : "data");
  const { resources, actions } = optionsFor(kind);
  form = {
    kind,
    sub: prefill.sub || state.userId,
    obj: resources.includes(prefill.obj) ? prefill.obj : resources[0],
    act: actions.includes(prefill.act) ? prefill.act : actions[0],
  };
  lastFocus = document.activeElement;
  renderShell();
  host.hidden = false;
  requestAnimationFrame(() => host.classList.add("is-open"));
  host.querySelector("select[name=sub]").focus();
}

function closeDrawer() {
  host.classList.remove("is-open");
  setTimeout(() => {
    host.hidden = true;
    host.innerHTML = "";
  }, 240);
  lastFocus?.focus?.();
}

function renderShell() {
  const { resources, actions } = optionsFor(form.kind);
  const subjects = state.options.data.subjects;
  render(host, html`
    <div class="drawer-backdrop" data-close></div>
    <aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
      <header class="drawer-head">
        <div><p class="eyebrow">authz · policies</p><h2 id="drawer-title">Grant or revoke access</h2></div>
        <button type="button" class="icon-btn" data-close aria-label="Close">${icon("x")}</button>
      </header>
      <div class="drawer-body">
        <div class="field">
          <span class="field-label">Resource type</span>
          <div class="segmented" role="radiogroup" aria-label="Resource type">
            ${Object.entries(KIND_LABEL).map(
              ([k, label]) => html`<button type="button" role="radio" aria-checked="${String(k === form.kind)}"
                class="segmented-btn${k === form.kind ? " is-active" : ""}" data-kind="${k}">${label}</button>`
            )}
          </div>
        </div>
        <label class="field"><span class="field-label">Principal</span>
          <select name="sub">
            <optgroup label="Users">${subjects.filter((s) => !isGroup(s)).map((s) => option(s, form.sub))}</optgroup>
            <optgroup label="Groups">${subjects.filter(isGroup).map((s) => option(s, form.sub))}</optgroup>
          </select>
        </label>
        <label class="field"><span class="field-label">Resource</span>
          <select name="obj">${resources.map((r) => option(r, form.obj))}</select>
        </label>
        <label class="field"><span class="field-label">Action</span>
          <select name="act"${actions.length === 1 ? " disabled" : ""}>${actions.map((a) => option(a, form.act))}</select>
          ${form.kind === "data" ? html`<span class="field-help">Trino reads are the only action the data layer checks.</span>` : ""}
        </label>
        <div data-dynamic></div>
      </div>
      <footer class="drawer-foot" data-foot></footer>
    </aside>`);
  renderDynamic();
}

function impact(sub, resOrg) {
  if (!resOrg) {
    return html`<p class="callout callout-warn">${icon("alert")}<span>This resource has no organization tag. The row is stored, but <code>same_org()</code> denies everyone.</span></p>`;
  }
  const members = (isGroup(sub) ? membersOf(sub) : [sub]).map((id) => {
    const org = userById(id)?.org || null;
    return { id, org, effective: org === resOrg };
  });
  if (!isGroup(sub)) {
    const m = members[0];
    return m.effective
      ? ""
      : html`<p class="callout callout-warn">${icon("alert")}<span><code>${sub}</code> is in <code>${m.org}</code>, this resource is in <code>${resOrg}</code>. The row is stored, but <code>same_org()</code> keeps denying it.</span></p>`;
  }
  if (!members.length) return html`<p class="callout callout-info">${icon("info")}<span><code>${sub}</code> has no members yet.</span></p>`;
  return html`
    <div class="field">
      <span class="field-label">Takes effect for</span>
      <div class="member-list">${members.map(memberChip)}</div>
      ${members.some((m) => !m.effective) ? html`<span class="field-help">Members marked ✕ are in another organization — <code>same_org()</code> blocks them.</span>` : ""}
    </div>`;
}

function renderDynamic() {
  const { sub, obj, act } = form;
  const exists = state.policies.p.some(([s, o, a]) => s === sub && o === obj && a === act);
  const resOrg = orgOf(obj);
  render(host.querySelector("[data-dynamic]"), html`
    <div class="policy-preview">
      <div class="policy-preview-row">${policyTuple(sub, obj, act)}<span class="tag${exists ? " tag-accent" : ""}">${exists ? "exists" : "new row"}</span></div>
      <p class="policy-preview-meta">Resource belongs to ${orgBadge(resOrg)}</p>
    </div>
    ${impact(sub, resOrg)}
    <ol class="flow-mini">
      <li>Row written to <code>authz.policies</code> (Postgres)</li>
      <li>Backend pushes the snapshot — <code>PUT /v1/data/policy_data</code></li>
      <li>Backend, Trino and Airflow see it on their next OPA check</li>
    </ol>`);
  render(host.querySelector("[data-foot]"), html`
    <button type="button" class="btn btn-ghost" data-close>Cancel</button>
    <span class="spacer"></span>
    <button type="button" class="btn btn-danger-soft" data-submit="revoke"${exists ? "" : " disabled"}>Revoke</button>
    <button type="button" class="btn btn-primary" data-submit="grant"${exists ? " disabled" : ""}>Grant</button>`);
}

async function submit(btn) {
  const kind = btn.dataset.submit;
  const { sub, obj, act } = form;
  btn.disabled = true;
  try {
    await changePolicy(kind, { sub, obj, act });
    closeDrawer();
    toast(`${kind === "grant" ? "Granted" : "Revoked"} p(${sub}, ${obj}, ${act}) — pushed to OPA`, { tone: "allow" });
  } catch (err) {
    toast(`Couldn't ${kind}: ${err.message}`, { tone: "deny" });
    btn.disabled = false;
  }
}

host.addEventListener("click", (e) => {
  if (e.target.closest("[data-close]")) return closeDrawer();
  const kindBtn = e.target.closest("[data-kind]");
  if (kindBtn && kindBtn.dataset.kind !== form.kind) {
    const { resources, actions } = optionsFor(kindBtn.dataset.kind);
    form = { ...form, kind: kindBtn.dataset.kind, obj: resources[0], act: actions[0] };
    renderShell();
    host.querySelector(`[data-kind="${form.kind}"]`).focus();
    return;
  }
  const btn = e.target.closest("[data-submit]");
  if (btn && !btn.disabled) submit(btn);
});

host.addEventListener("change", (e) => {
  if (["sub", "obj", "act"].includes(e.target.name)) {
    form[e.target.name] = e.target.value;
    renderDynamic();
  }
});

// Escape closes; Tab stays inside the panel while it's open.
document.addEventListener("keydown", (e) => {
  if (!host.classList.contains("is-open")) return;
  if (e.key === "Escape") return closeDrawer();
  if (e.key !== "Tab") return;
  const focusables = [...host.querySelectorAll("button:not([disabled]), select:not([disabled])")];
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
});
