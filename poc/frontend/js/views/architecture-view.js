// Architecture page — static diagram of how the POC fits together. Colours
// come from CSS tokens (see .arch-* in views.css) so it follows the theme.
import { html, raw, render } from "../dom.js";
import { pageHeader } from "../ui-parts.js";

const box = (x, y, w, h, title, lines, cls = "") =>
  `<g class="arch-node ${cls}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10"/>
   <text x="${x + w / 2}" y="${y + 26}" class="arch-title">${title}</text>
   ${lines.map((l, i) => `<text x="${x + w / 2}" y="${y + 45 + i * 16}" class="arch-caption">${l}</text>`).join("")}</g>`;

const arrow = (x1, y1, x2, y2, kind) =>
  `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="arch-edge arch-edge-${kind}" marker-end="url(#arch-arrow-${kind})"/>`;

const label = (x, y, text, kind, anchor = "middle") =>
  `<text x="${x}" y="${y}" class="arch-label arch-label-${kind}" text-anchor="${anchor}">${text}</text>`;

const marker = (kind) =>
  `<marker id="arch-arrow-${kind}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="arch-head-${kind}"/></marker>`;

const DIAGRAM = `
<svg viewBox="0 0 1200 540" class="arch-svg" role="img" aria-label="Architecture: frontend, backend, OPA, Trino, Airflow, MinIO and Postgres">
  <defs>${["plain", "opa", "minio"].map(marker).join("")}</defs>
  ${arrow(600, 80, 600, 126, "plain")}${label(612, 108, "HTTP · /api/*", "plain", "start")}
  ${arrow(720, 162, 896, 162, "plain")}${label(808, 152, "read / write policy rows", "plain")}
  ${arrow(480, 162, 304, 162, "minio")}${label(392, 152, "S3 · per-org credential", "minio")}
  ${arrow(600, 194, 600, 296, "opa")}${label(612, 238, "check app.allow", "opa", "start")}${label(612, 254, "PUT policy_data", "opa", "start")}
  ${arrow(520, 194, 222, 296, "plain")}${label(352, 236, "SQL as user=&lt;demo user&gt;", "plain", "end")}
  ${arrow(300, 336, 476, 336, "opa")}${label(388, 326, "trino-opa plugin", "opa")}${label(388, 356, "allow · rowFilters · mask", "opa")}
  ${arrow(900, 336, 724, 336, "opa")}${label(812, 326, "opa_auth_manager", "opa")}${label(812, 356, "airflow.allow", "opa")}
  ${arrow(170, 372, 170, 446, "plain")}${label(182, 414, "sales · hr · org2_sales", "plain", "start")}
  ${arrow(1030, 372, 1030, 446, "plain")}${label(1042, 414, "DAG runs · task state", "plain", "start")}
  <line x1="600" y1="372" x2="600" y2="446" class="arch-edge arch-edge-dashed"/>
  ${box(480, 24, 240, 56, "Frontend", ["static HTML · ES modules · nginx"])}
  ${box(480, 130, 240, 64, "Backend", ["FastAPI — the app's own gate"], "is-opa")}
  ${box(40, 130, 260, 64, "MinIO", ["one bucket + IAM user per org"], "is-minio")}
  ${box(900, 130, 260, 64, "Postgres · authz", ["policies · groups · resource_org"])}
  ${box(40, 300, 260, 72, "Trino", ["SystemAccessControl → OPA", "checks every SELECT itself"])}
  ${box(480, 300, 240, 72, "OPA", ["app · trino · airflow.rego", "→ one shared authz.rego"], "is-opa is-core")}
  ${box(900, 300, 260, 72, "Airflow 3", ["custom BaseAuthManager (AIP-56)", "is_authorized_dag → OPA"])}
  ${box(40, 450, 260, 64, "Postgres · demo", ["the data Trino reads"])}
  ${box(480, 450, 240, 64, "policy_data", ["in-memory mirror of authz"], "is-ghost")}
  ${box(900, 450, 260, 64, "Postgres · airflow", ["Airflow's metadata DB"])}
</svg>`;

const POINTS = [
  ["Backend", "data.app.allow", "Gates the app's own request path before a Trino connection is even opened."],
  ["Trino", "data.trino.allow · rowFilters · columnMask", "Holds for any client — CLI, JDBC, BI tools — not just traffic from this backend."],
  ["Airflow", "data.airflow.allow", "Every view / trigger / logs / code request inside Airflow, UI or API."],
  ["MinIO", "IAM policy, no OPA", "A credential boundary: each org's key can only ever reach its own bucket."],
];

export default {
  mount(root) {
    render(root, html`
      ${pageHeader({
        eyebrow: "Platform",
        title: "Architecture",
        lede: html`One policy engine, one system of record, enforced at three independent points — plus storage isolation that deliberately doesn't use OPA.`,
      })}
      <div class="arch-legend">
        <span><i class="legend-swatch legend-opa"></i>OPA decision</span>
        <span><i class="legend-swatch legend-minio"></i>MinIO IAM, no OPA</span>
        <span><i class="legend-swatch legend-plain"></i>Plain plumbing (HTTP, SQL)</span>
      </div>
      <div class="pane arch-pane"><div class="arch-scroll">${raw(DIAGRAM)}</div></div>
      <ol class="points">${POINTS.map(
        ([name, rule, text], i) => html`<li class="point${name === "MinIO" ? " is-minio" : ""}">
          <span class="point-index">${String(i + 1).padStart(2, "0")}</span>
          <div><h3>${name} <code>${rule}</code></h3><p>${text}</p></div>
        </li>`
      )}</ol>
      <p class="muted-text arch-footnote">One Postgres instance, three databases (<code>demo</code>, <code>authz</code>, <code>airflow</code>). The backend never calls Airflow: Airflow's own auth manager asks OPA directly, whatever this UI does.</p>`);
  },
  refresh() {},
};
