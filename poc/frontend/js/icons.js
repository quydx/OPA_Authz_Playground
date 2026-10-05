// Hand-drawn 24px stroke icons, inlined so the page has no icon-font or
// CDN dependency. Stroke colour follows `currentColor`.
import { raw } from "./dom.js";

const PATHS = {
  database:
    '<ellipse cx="12" cy="6" rx="7" ry="2.8"/><path d="M5 6v12c0 1.55 3.13 2.8 7 2.8s7-1.25 7-2.8V6"/><path d="M5 12c0 1.55 3.13 2.8 7 2.8s7-1.25 7-2.8"/>',
  layers: '<path d="M12 4 3.5 8.5 12 13l8.5-4.5z"/><path d="m3.5 13 8.5 4.5 8.5-4.5"/>',
  table: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9.5h17M9.5 9.5v10"/>',
  flow: '<rect x="3" y="4" width="6.5" height="5" rx="1.5"/><rect x="14.5" y="4" width="6.5" height="5" rx="1.5"/><rect x="8.75" y="15" width="6.5" height="5" rx="1.5"/><path d="M6.25 9v1.75a1.5 1.5 0 0 0 1.5 1.5h8.5a1.5 1.5 0 0 0 1.5-1.5V9M12 12.25V15"/>',
  bucket: '<path d="M4 6.5h16"/><path d="m5.5 6.5 1.35 12.15A1.5 1.5 0 0 0 8.34 20h7.32a1.5 1.5 0 0 0 1.5-1.35L18.5 6.5"/><path d="M9 3.5h6"/>',
  key: '<circle cx="8" cy="15.5" r="4"/><path d="m10.9 12.6 8.6-8.6M16.5 7l2.25 2.25M14.25 9.25l1.75 1.75"/>',
  diagram: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><path d="M10 6.5h4.5a2 2 0 0 1 2 2V14"/>',
  terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9.5 3 2.5-3 2.5M13 15h4"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  minus: '<path d="M6 12h12"/>',
  lock: '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
  user: '<circle cx="12" cy="8.5" r="4"/><path d="M4.5 20.5c1.4-3.6 4.2-5.5 7.5-5.5s6.1 1.9 7.5 5.5"/>',
  users:
    '<circle cx="9" cy="9" r="3.5"/><path d="M3 20c1-3.2 3.3-5 6-5s5 1.8 6 5"/><path d="M15.5 5.8a3.5 3.5 0 0 1 0 6.4M17.5 15.3c1.6.8 2.8 2.4 3.5 4.7"/>',
  building:
    '<path d="M4.5 20.5v-15A1.5 1.5 0 0 1 6 4h7a1.5 1.5 0 0 1 1.5 1.5v15M14.5 10h3.5a1.5 1.5 0 0 1 1.5 1.5v9M3 20.5h18M8 8h3M8 12h3M8 16h3"/>',
  "chevron-right": '<path d="m9.5 6 6 6-6 6"/>',
  "chevron-down": '<path d="m6 9.5 6 6 6-6"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
  "eye-off": '<path d="M4 4l16 16M10.6 6.1c.46-.07.93-.1 1.4-.1 6 0 9.5 6 9.5 6a17 17 0 0 1-2.6 3.3M6.5 7.6A16 16 0 0 0 2.5 12s3.5 6.5 9.5 6.5c1.6 0 3-.4 4.3-1"/>',
  file: '<path d="M13.5 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9z"/><path d="M13.5 3.5V9H19"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.75v.25"/>',
  alert: '<path d="M10.3 4.6 2.9 17.5a2 2 0 0 0 1.7 3h14.8a2 2 0 0 0 1.7-3L13.7 4.6a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4M12 16.75v.25"/>',
  play: '<path d="M8 5.5v13l10.5-6.5z"/>',
};

export function icon(name, cls = "") {
  return raw(
    `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${PATHS[name] || ""}</svg>`
  );
}
