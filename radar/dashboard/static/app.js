"use strict";
// All values are inserted with textContent (never innerHTML) because news
// titles and names come from external sources.

const LEVELS = [
  { key: "ALERTA MAXIMA", label: "\u{1F534} ALERTA MÁXIMA", color: "var(--max)" },
  { key: "ALERTA ALTA", label: "\u{1F7E0} ALERTA ALTA", color: "var(--alta)" },
  { key: "VIGILAR", label: "\u{1F7E1} VIGILAR", color: "var(--vig)" },
  { key: "SIN ALERTA", label: "SIN ALERTA", color: "var(--line)" },
];
const SIGNALS = [
  ["reverse_split", "Reverse split", 20], ["float", "Float", 20], ["volume", "Volumen", 25],
  ["catalyst", "Catalizador", 20], ["price", "Precio", 15],
];
const ND = "N/D";
const state = { rows: [], filters: new Set() };

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "onclick") node.addEventListener("click", v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const num = (v, d = 2) => {
  if (v === null || v === undefined) return ND;
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return Number(v).toFixed(d);
};
const price = (v) => (v === null || v === undefined ? ND : "$" + Number(v).toFixed(v < 1 ? 4 : 2));
const pct = (v) => (v === null || v === undefined ? ND : (v > 0 ? "+" : "") + Number(v).toFixed(1) + "%");
const pctCell = (v) => el("td", { class: v > 0 ? "pos" : v < 0 ? "neg" : "" }, pct(v));
const time = (iso) => (iso ? iso.replace("T", " ").slice(0, 16) : ND);

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(url + " " + r.status);
  return r.json();
}

// ------------------------------------------------------------ filters
const FILTERS = {
  p1: (r) => r.price !== null && r.price < 1,
  p5: (r) => r.price !== null && r.price >= 1 && r.price < 5,
  p20: (r) => r.price !== null && r.price >= 5 && r.price <= 20,
  f1: (r) => r.float_shares !== null && r.float_shares < 1e6,
  f5: (r) => r.float_shares !== null && r.float_shares < 5e6,
  rs: (r) => !!r.rs_status,
  v5: (r) => r.rvol !== null && r.rvol > 5,
  pm: (r) => r.phase === "premarket" || (r.premarket_change_pct !== null && Math.abs(r.premarket_change_pct) >= 5),
};
const PRICE_KEYS = ["p1", "p5", "p20"];

function passes(row) {
  const active = [...state.filters].filter((f) => f !== "all");
  const priceOn = active.filter((f) => PRICE_KEYS.includes(f));
  if (priceOn.length && !priceOn.some((f) => FILTERS[f](row))) return false; // price ranges: OR
  return active.filter((f) => !PRICE_KEYS.includes(f)).every((f) => FILTERS[f](row)); // rest: AND
}

// ------------------------------------------------------------ main table
const HEAD = ["Ticker", "Precio", "Variación", "Subida 5 ses.", "Float", "Vol/Media", "Reverse split",
  "Catalizador", "RS", "Fl", "Vol", "Cat", "Pre", "Bonus", "Puntuación"];

function rsLabel(r) {
  if (!r.rs_status) return "—";
  return `${r.rs_status} ${r.rs_ratio ? "1:" + r.rs_ratio : ""} ${r.rs_date || ""}`.trim();
}

function rowEl(r) {
  const tr = el("tr", { onclick: () => openDetail(r.ticker) },
    el("td", { class: "l" }, el("b", {}, r.ticker), " ",
      r.data_blocked ? el("span", { class: "flag", title: "Posible dato no ajustado por split" }, "VERIFICAR") : null,
      r.run_up_pct !== null && r.run_up_pct >= 200 ? el("span", { class: "flag", title: "El movimiento ya se ha producido" }, "TARDE") : null),
    el("td", {}, price(r.price)),
    pctCell(r.change_pct),
    pctCell(r.run_up_pct),
    el("td", {}, num(r.float_shares)),
    el("td", {}, r.rvol === null ? ND : r.rvol.toFixed(1) + "x"),
    el("td", { class: "l" }, rsLabel(r)),
    el("td", {}, r.n_catalysts ? String(r.n_catalysts) : "—"),
    ...SIGNALS.map(([k]) => el("td", {}, num(r["score_" + k], 0))),
    el("td", {}, num(r.bonus, 0)),
    el("td", { class: "score" }, String(r.total)),
  );
  return tr;
}

function renderGroups() {
  const box = document.getElementById("groups");
  box.replaceChildren();
  const showAll = state.filters.has("all");
  for (const lvl of LEVELS) {
    if (lvl.key === "SIN ALERTA" && !showAll) continue;
    const rows = state.rows.filter((r) => r.level === lvl.key && passes(r)).sort((a, b) => b.total - a.total);
    box.append(el("h2", {}, el("span", { class: "dot", style: `background:${lvl.color}` }), `${lvl.label} (${rows.length})`));
    if (!rows.length) { box.append(el("div", { class: "empty" }, "Ninguna")); continue; }
    box.append(el("div", { class: "table-wrap" },
      el("table", {}, el("thead", {}, el("tr", {}, HEAD.map((h, i) => el("th", { class: i === 0 || i === 6 ? "l" : "" }, h)))),
        el("tbody", {}, rows.map(rowEl)))));
  }
}

// ------------------------------------------------------------ detail
function kv(label, value, src) {
  return [el("div", {}, label), el("div", {}, value, src ? el("div", { class: "src" }, src) : null)];
}
const sv = (item, fmt = num) => (item && item.value !== null && item.value !== undefined ? fmt(item.value) : ND);
const ssrc = (item) => (item ? `${item.source} · ${item.as_of || "fecha N/D"}${item.note ? " · " + item.note : ""}` : "");

async function openDetail(ticker) {
  const panel = document.getElementById("detail");
  panel.replaceChildren(el("p", {}, "Cargando " + ticker + "..."));
  panel.classList.add("open");
  const det = await getJSON("/api/ticker/" + encodeURIComponent(ticker));
  const p = det.payload;
  if (!p) { panel.replaceChildren(el("p", {}, "Sin datos")); return; }
  const d = p.data, s = p.score;
  const rs = d.reverse_split;
  const signals = s.signals.map((sig) => el("div", {},
    el("div", {}, `${SIGNALS.find(([k]) => k === sig.name)[1]}: ${sig.points}/${sig.max_points}${sig.active ? " (activa)" : ""}`),
    el("div", { class: "bar" }, el("i", { style: `width:${(100 * sig.points) / sig.max_points}%` })),
    el("div", { class: "src" }, sig.detail)));
  const cats = d.catalysts.length
    ? el("ul", { class: "cats" }, d.catalysts.map((c) => el("li", {},
        `[${c.published.slice(0, 10)}] ${c.category} (${c.impact}${c.official ? ", oficial" : ""}) — ${c.title} · `,
        c.url && /^https:\/\//.test(c.url) ? el("a", { href: c.url, target: "_blank", rel: "noopener noreferrer" }, c.source) : c.source)))
    : el("div", {}, ND);

  panel.replaceChildren(...[
    el("button", { class: "close", onclick: () => panel.classList.remove("open") }, "Cerrar"),
    el("h3", {}, `${d.ticker} — ${sv(d.name, String)}`),
    el("p", {}, el("b", {}, `${s.total}/100 · ${s.level}`), ` · señales ${s.raw_total} + bonus ${s.bonus}${s.capped ? " (limitada: faltan señales o datos a verificar)" : ""}`),
    s.late_note ? el("div", { class: "flag" }, s.late_note) : null,
    d.quality_notes.length ? el("div", { class: "flag" }, d.quality_notes.join(" | ")) : null,
    el("div", { class: "kv" },
      kv("Trade Republic", d.tradable ? d.tradable.value : ND, d.tradable ? d.tradable.source : ""),
      kv("Precio actual", sv(d.price, price), ssrc(d.price)),
      kv("Variación", sv(d.change_pct, pct), ssrc(d.prev_close) && "Cierre anterior " + sv(d.prev_close, price) + " · " + ssrc(d.prev_close)),
      kv("Premarket", sv(d.premarket_change_pct, pct), ssrc(d.premarket_change_pct)),
      kv("Subida desde mínimo 5 sesiones", sv(d.run_up_pct, pct), ssrc(d.run_up_pct)),
      kv("Market cap", sv(d.market_cap), ssrc(d.market_cap)),
      kv("Shares outstanding", sv(d.shares_outstanding), ssrc(d.shares_outstanding)),
      kv("Public float", sv(d.float_shares), ssrc(d.float_shares)),
      kv("Volumen actual", sv(d.volume), ssrc(d.volume)),
      kv("Volumen medio 20 días", sv(d.avg_volume_20d), ssrc(d.avg_volume_20d)),
      kv("Ratio de volumen", d.volume && d.avg_volume_20d && d.avg_volume_20d.value ? (d.volume.value / d.avg_volume_20d.value).toFixed(2) + "x" : ND, ""),
      kv("Volumen premarket", sv(d.premarket_volume), ssrc(d.premarket_volume)),
      kv("Último reverse split", rs ? `${rs.status} ${rs.ratio ? "1:" + rs.ratio : "ratio N/D"} · ${rs.event_date}` : ND,
        rs ? rs.source + (rs.effective_date ? " · efectivo " + rs.effective_date : "") : ""),
      kv("Actualizado", time(d.updated_at) + " ET", "fase " + d.phase),
    ),
    el("h4", {}, "Puntuación por señal"), ...signals,
    el("h4", {}, "Noticias / catalizadores (7 días)"), cats,
    rs && rs.url && /^https:\/\//.test(rs.url) ? el("p", {}, el("a", { href: rs.url, target: "_blank", rel: "noopener noreferrer" }, "Documento SEC del reverse split")) : null,
    el("h4", {}, "Alertas de este ticker"),
    det.alerts.length ? el("div", { class: "table-wrap" }, alertsTable(det.alerts)) : el("div", {}, "Ninguna"),
  ].filter(Boolean));
}

// ------------------------------------------------------------ alerts & stats
function alertsTable(rows) {
  const head = ["Fecha", "Ticker", "Tipo", "Nivel", "Punt.", "Precio", "Var.", "Vol/Media", "Máx 1h", "Máx 4h", "Máx 1d", "Máx 5d", "Mín 5d"];
  return el("table", {},
    el("thead", {}, el("tr", {}, head.map((h, i) => el("th", { class: i < 4 ? "l" : "" }, h)))),
    el("tbody", {}, rows.map((a) => el("tr", { onclick: () => openDetail(a.ticker) },
      el("td", { class: "l" }, time(a.ts)), el("td", { class: "l" }, el("b", {}, a.ticker)),
      el("td", { class: "l" }, a.kind + (a.notified ? "" : " (no notificada)")), el("td", { class: "l" }, a.level),
      el("td", { class: "score" }, String(a.total)), el("td", {}, price(a.price)), pctCell(a.change_pct),
      el("td", {}, a.rvol === null ? ND : a.rvol.toFixed(1) + "x"),
      pctCell(a.max_pct_1h), pctCell(a.max_pct_4h), pctCell(a.max_pct_1d), pctCell(a.max_pct_5d), pctCell(a.min_pct_5d)))));
}

function statsTable(title, groups) {
  const head = ["Grupo", "n (1d)", "Media 1h", "Media 4h", "Media 1d", "Acierto 1d", "Media 5d", "Acierto 5d"];
  const f = (v, suf = "%") => (v === null ? ND : v.toFixed(1) + suf);
  return [el("h2", {}, title), el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, head.map((h, i) => el("th", { class: i === 0 ? "l" : "" }, h)))),
    el("tbody", {}, Object.entries(groups).map(([name, g]) => el("tr", {},
      el("td", { class: "l" }, name), el("td", {}, String(g["1d"].n)),
      el("td", {}, f(g["1h"].media)), el("td", {}, f(g["4h"].media)), el("td", {}, f(g["1d"].media)),
      el("td", {}, f(g["1d"].acierto)), el("td", {}, f(g["5d"].media)), el("td", {}, f(g["5d"].acierto)))))))];
}

async function renderStats() {
  const hit = Number(document.getElementById("hit").value) || 20;
  const rep = await getJSON("/api/stats?hit=" + hit);
  const bySignal = {};
  for (const [label, g] of Object.entries(rep.por_senal)) {
    bySignal[label + " — activa"] = g.activa;
    bySignal[label + " — inactiva"] = g.inactiva;
  }
  const byCount = Object.fromEntries(Object.entries(rep.por_num_senales).map(([k, v]) => [k + " señales activas", v]));
  document.getElementById("stats").replaceChildren(
    el("p", {}, `${rep.eventos} eventos (primera detección por ticker y día). Acierto = subida máxima posterior ≥ ${rep.umbral_acierto_pct}%.`),
    ...statsTable("Por nivel", rep.por_nivel),
    ...statsTable("Por señal", bySignal),
    ...statsTable("Por número de señales activas", byCount),
  );
}

async function renderAlerts() {
  const rows = await getJSON("/api/alerts?limit=500");
  const t = document.getElementById("alerts-table");
  t.replaceWith(Object.assign(alertsTable(rows), { id: "alerts-table" }));
}

async function refresh() {
  try {
    const data = await getJSON("/api/latest");
    state.rows = data.rows;
    const c = data.cycle;
    document.getElementById("meta").textContent = c
      ? `Último ciclo: ${time(c.ts)} ET · fase ${c.phase} · ${c.n_candidates ?? 0} analizados`
      : "Sin datos todavía: ejecuta python -m radar scan";
    renderGroups();
  } catch (e) {
    document.getElementById("meta").textContent = "Error cargando datos: " + e.message;
  }
}

document.getElementById("filters").addEventListener("change", (e) => {
  const f = e.target.dataset.f;
  if (!f) return;
  e.target.checked ? state.filters.add(f) : state.filters.delete(f);
  renderGroups();
});
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("on", x === b));
  for (const tab of ["now", "alerts", "stats"]) document.getElementById("tab-" + tab).hidden = tab !== b.dataset.tab;
  if (b.dataset.tab === "alerts") renderAlerts();
  if (b.dataset.tab === "stats") renderStats();
}));
document.getElementById("hit").addEventListener("change", renderStats);

refresh();
setInterval(refresh, 60000);
