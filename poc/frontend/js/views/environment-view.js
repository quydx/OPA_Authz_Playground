// Environment page — where every service is reachable, and the lab-only
// credentials. Airflow passwords are read live from the apiserver's logs by
// the backend on Kubernetes (/api/airflow/credentials); elsewhere the page
// shows the command to fetch them instead.
import { api } from "../api-client.js";
import { copyText, html, render, toast } from "../dom.js";
import { icon } from "../icons.js";
import { callout, pageHeader } from "../ui-parts.js";
import { airflowLogCmd, credentials, serviceAddress, services } from "../services-config.js";

let servicesEl, credsEl;
let livePasswords = {};
const revealed = new Set();

const copyBtn = (value, label) =>
  html`<button type="button" class="icon-btn icon-btn-sm" data-copy="${value}" aria-label="Copy ${label}" title="Copy">${icon("copy")}</button>`;

function renderServices() {
  render(servicesEl, html`<ul class="service-list">${services().map((svc) => {
    const addr = serviceAddress(svc);
    let target;
    if (!addr) target = html`<span class="badge">internal only</span>`;
    else if (svc.tcp) target = html`<code>${addr}</code>${copyBtn(addr, svc.name)}`;
    else target = html`<a href="${addr}" target="_blank" rel="noopener" class="service-link"><code>${addr}</code>${icon("external")}</a>`;
    return html`<li class="service-row">
      <span class="service-dot${addr ? "" : " is-internal"}"></span>
      <div class="service-text"><strong>${svc.name}</strong><span class="muted">${svc.desc || ""}</span></div>
      <div class="service-target">${target}</div>
    </li>`;
  })}</ul>`);
}

function secretCell(c, i) {
  const pass = c.pass || (c.service === "Airflow" ? livePasswords[c.user] : null);
  if (!pass) {
    return c.service === "Airflow"
      ? html`<span class="muted small">generated at startup</span><div class="cmd"><code>${airflowLogCmd()}</code>${copyBtn(airflowLogCmd(), "command")}</div>`
      : html`<span class="muted">n/a</span>`;
  }
  const shown = revealed.has(i);
  return html`<span class="secret"><code>${shown ? pass : "••••••••••"}</code>
    <button type="button" class="icon-btn icon-btn-sm" data-reveal="${i}" aria-label="${shown ? "Hide" : "Show"} secret">${icon(shown ? "eye-off" : "eye")}</button>
    ${copyBtn(pass, "secret")}</span>`;
}

function renderCreds() {
  render(credsEl, html`<div class="pane grid-wrap"><table class="list-table creds-table">
    <thead><tr><th>Service</th><th>User</th><th>Secret</th><th>Notes</th></tr></thead>
    <tbody>${credentials().map((c, i) => html`<tr>
      <td><strong>${c.service}</strong></td>
      <td>${c.user ? html`<code>${c.user}</code>` : html`<span class="muted">n/a</span>`}</td>
      <td>${secretCell(c, i)}</td>
      <td class="muted">${c.notes}</td>
    </tr>`)}</tbody>
  </table></div>`);
}

export default {
  mount(root) {
    render(root, html`
      ${pageHeader({
        eyebrow: "Platform",
        title: "Environment",
        lede: "Addresses are built from this page's own hostname, so the list works on docker-compose (localhost) and on Kubernetes NodePorts alike.",
      })}
      <section class="section"><h2 class="section-title">Services</h2><div data-services></div></section>
      <section class="section">
        <h2 class="section-title">Demo credentials</h2>
        ${callout("Lab-only values, identical across docker-compose and Kubernetes. Never reuse them for a real deployment.", "warn")}
        <div data-creds></div>
      </section>`);
    servicesEl = root.querySelector("[data-services]");
    credsEl = root.querySelector("[data-creds]");
    root.addEventListener("click", async (e) => {
      const t = e.target.closest("[data-copy],[data-reveal]");
      if (!t) return;
      if (t.dataset.reveal) {
        const i = Number(t.dataset.reveal);
        revealed.has(i) ? revealed.delete(i) : revealed.add(i);
        return renderCreds();
      }
      toast((await copyText(t.dataset.copy)) ? "Copied to clipboard" : "Copy failed — select the text instead", { tone: "info" });
    });
  },
  async refresh() {
    renderServices();
    renderCreds();
    try {
      const res = await api("/api/airflow/credentials");
      livePasswords = res.available ? res.passwords || {} : {};
    } catch {
      livePasswords = {}; // endpoint unreachable — fall back to the log command
    }
    renderCreds();
  },
};
