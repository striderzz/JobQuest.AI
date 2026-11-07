"use strict";
/* Charts built from a spec: the oracle's answers, the chart builder and pinned charts all draw through here.
   Every number comes from stats.query in Python. */

const ROUND_CHARTS = ["pie", "doughnut", "polar"];
const niceName = s => String(s).replace(/_/g, " ");

function specBody(c, id) {
  if (c.type === "stat") return `<div class="spec-stat">${esc(c.value)}${c.unit ? `<small>${esc(c.unit)}</small>` : ""}</div><div class="small text-body-secondary">${c.n} ${c.n === 1 ? "item" : "items"} counted</div>`;
  if (c.columns) {
    const shown = c.rows.length;
    return `<div class="table-responsive oc-table"><table class="table table-sm mb-0 data-table"><thead><tr>${c.columns.map(h => `<th>${esc(niceName(h))}</th>`).join("")}</tr></thead>
      <tbody>${c.rows.map(r => `<tr>${r.map(v => `<td>${esc(v ?? "")}</td>`).join("")}</tr>`).join("") || `<tr><td colspan="${c.columns.length}">No data.</td></tr>`}</tbody></table></div>${c.total > shown ? `<div class="small text-body-secondary">Top ${shown} of ${c.total}</div>` : ""}`;
  }
  const size = (c.points || c.labels).length;
  if (!size) return `<div class="small text-body-secondary">No data for this yet.</div>`;
  return `<div class="oc-canvas"><canvas id="${id}" role="img" aria-label="${esc(c.title)}"></canvas></div>${c.total > size ? `<div class="small text-body-secondary">Top ${size} of ${c.total}</div>` : ""}`;
}

/* tools: pin (show Pin), unpin (a pin id) */
function specCard(c, id, tools = {}) {
  S.specs = S.specs || {}; S.specs[id] = c;
  const btn = (act, icon, label) => `<button class="btn btn-sm btn-link p-0" data-act="${act}" data-spec="${id}" title="${label}" aria-label="${label}">${bi(icon)}</button>`;
  return `<div class="oc-chart"><div class="d-flex align-items-center gap-2 mb-1"><div class="fw-semibold me-auto">${esc(c.title)}</div>
    ${c.type !== "stat" ? btn("chart-csv", "filetype-csv", "Download CSV") : ""}${c.labels || c.points ? btn("chart-png", "image", "Download image") : ""}
    ${tools.pin && c.spec ? btn("chart-pin", "pin-angle", "Pin to dashboard") : ""}${tools.unpin ? `<button class="btn btn-sm btn-link p-0" data-act="chart-unpin" data-pin="${esc(tools.unpin)}" title="Remove" aria-label="Remove">${bi("x-lg")}</button>` : ""}</div>${specBody(c, id)}</div>`;
}

function drawSpec(c, id, store) {
  const el = document.getElementById(id);
  if (!el || !window.Chart) return;
  chartDefaults();
  const C = palette(), surface = cssVar("--jh-card"), t = c.type;
  const value = { beginAtZero: true }, cat = { grid: { display: false }, ticks: { maxRotation: 0 } };
  if (t === "scatter") {
    store.push(new window.Chart(el, { type: "scatter", data: { datasets: [{ data: c.points, backgroundColor: C[0], pointRadius: 4 }] },
      options: { plugins: { legend: { display: false }, tooltip: { callbacks: { label: x => `${x.raw.label}: ${x.raw.x}, ${x.raw.y}` } } },
        scales: { x: { ...value, title: { display: true, text: niceName(c.x) } }, y: { ...value, title: { display: true, text: niceName(c.y) } } } } }));
    return;
  }
  const round = ROUND_CHARTS.includes(t), line = t === "line" || t === "area", stacked = t === "stacked_bar";
  const series = c.series || [{ label: niceName(c.metric || ""), values: c.values }];
  const datasets = series.map((s, i) => {
    const hue = C[i % C.length];
    if (round) return { data: s.values, backgroundColor: C, borderColor: surface, borderWidth: 2 };
    if (t === "radar") return { label: s.label, data: s.values, borderColor: hue, backgroundColor: hue + "33", borderWidth: 2, pointRadius: 2 };
    if (line) return { label: s.label, data: s.values, borderColor: hue, backgroundColor: hue + "33", fill: t === "area", borderWidth: 2, tension: .3, pointRadius: 2 };
    const d = { label: s.label, data: s.values, backgroundColor: hue, borderRadius: 4, maxBarThickness: 18 };
    return t === "histogram" ? { ...d, barPercentage: 1, categoryPercentage: .96 } : d;
  });
  const many = series.length > 1;
  const legend = { display: round || many, position: round ? "right" : "bottom" };
  const sideways = t === "horizontal_bar" || (["bar", "stacked_bar", "grouped_bar"].includes(t) && c.labels.length > 6);
  const scales = round || t === "radar" ? {} : sideways ? { x: { ...value, stacked }, y: { ...cat, stacked, ticks: { autoSkip: false } } }
    : { x: { ...cat, stacked, ticks: { ...cat.ticks, maxTicksLimit: 10 } }, y: { ...value, stacked } };
  const type = t === "polar" ? "polarArea" : t === "radar" || t === "pie" || t === "doughnut" ? t : line ? "line" : "bar";
  store.push(new window.Chart(el, { type, data: { labels: c.labels, datasets }, options: { indexAxis: sideways ? "y" : "x", plugins: { legend }, scales } }));
}

function specCsv(c) {
  let table;
  if (c.columns) table = [c.columns, ...c.rows];
  else if (c.points) table = [[c.group || "name", c.x, c.y], ...c.points.map(p => [p.label, p.x, p.y])];
  else {
    const series = c.series || [{ label: c.metric || "value", values: c.values }];
    table = [["label", ...series.map(s => s.label)], ...c.labels.map((l, i) => [l, ...series.map(s => s.values[i])])];
  }
  const cell = v => { let s = String(v ?? ""); if (/^[=+\-@]/.test(s)) s = "'" + s; return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  return table.map(r => r.map(cell).join(",")).join("\r\n");
}

function saveAs(href, name) {
  const a = document.createElement("a");
  a.href = href; a.download = name; a.click();
}
const fileName = c => (c.title || "chart").replace(/[^\w-]+/g, "-").toLowerCase();

ACT["chart-csv"] = el => {
  const c = S.specs[el.dataset.spec], url = URL.createObjectURL(new Blob(["﻿" + specCsv(c)], { type: "text/csv" }));
  saveAs(url, `${fileName(c)}.csv`);
  setTimeout(() => URL.revokeObjectURL(url), 5000);
};
ACT["chart-png"] = el => {
  const canvas = document.getElementById(el.dataset.spec);
  if (canvas) saveAs(canvas.toDataURL("image/png"), `${fileName(S.specs[el.dataset.spec])}.png`);
};
ACT["chart-pin"] = async el => {
  try {
    await api("/api/charts/pin", { method: "POST", json: { spec: S.specs[el.dataset.spec].spec } });
    toast("Pinned. Find it under My charts.", 3000);
    if (S.dTab === "charts" && currentView() === "dashboard") loadPins();
  } catch (e) { toast(e.message, 6000); }
};
ACT["chart-unpin"] = async el => {
  try { await api(`/api/charts/pin/${el.dataset.pin}`, { method: "DELETE" }); loadPins(); } catch (e) { toast(e.message, 6000); }
};

// ---------------------------------------------------------------- My charts tab

const builderValue = id => $(`#cb-${id}`)?.value || "";

function builderSpec() {
  const type = builderValue("type"), spec = { type, dataset: builderValue("data"), title: "" };
  if (builderValue("from")) spec.where = { from: builderValue("from") };
  if (type === "histogram") spec.measure = builderValue("measure");
  else if (type !== "stat" && type !== "list") { spec.group_by = builderValue("group"); spec.top = Number(builderValue("top")); }
  if (type !== "histogram" && type !== "list") spec.metric = builderValue("metric");
  if (type === "scatter") { spec.x = "applications"; spec.y = builderValue("metric"); }
  if (["stacked_bar", "grouped_bar", "line", "area"].includes(type) && builderValue("split")) spec.split_by = builderValue("split");
  const name = (spec.measure || spec.metric || "applications");
  spec.title = type === "list" ? "My applications" : `${niceName(name)}${spec.group_by ? " by " + niceName(spec.group_by) : ""}`.replace(/^./, x => x.toUpperCase());
  return spec;
}

function builderFields() {
  const type = builderValue("type"), data = builderValue("data"), o = S.chartOptions;
  const show = (id, on) => $(`#cb-${id}`)?.closest("[data-cb]").classList.toggle("d-none", !on);
  const fill = (id, list) => { const el = $(`#cb-${id}`), was = el.value; el.innerHTML = list.map(v => `<option value="${esc(v)}">${esc(niceName(v))}</option>`).join(""); if (list.includes(was)) el.value = was; };
  fill("metric", o.metrics[data]);
  const grouped = !["histogram", "stat", "list"].includes(type);
  show("group", grouped); show("top", grouped);
  show("metric", type !== "histogram" && type !== "list");
  show("measure", type === "histogram");
  show("split", ["stacked_bar", "grouped_bar", "line", "area"].includes(type));
}

async function builderPreview() {
  const host = $("#cb-out");
  if (!host) return;
  try {
    const c = await api("/api/charts/preview", { method: "POST", json: { spec: builderSpec() } });
    (S.cbChart || []).forEach(x => x.destroy()); S.cbChart = [];
    host.innerHTML = specCard(c, "cb-canvas", { pin: true });
    drawSpec(c, "cb-canvas", S.cbChart);
  } catch (e) { host.innerHTML = `<div class="small text-danger">${esc(e.message)}</div>`; }
}

function chartsTab() {
  return `<div class="row g-3"><div class="col-lg-4"><div class="card"><div class="card-body p-3"><div class="chart-title">Build a chart</div><div class="row g-2" id="cb-form"></div></div></div></div>
      <div class="col-lg-8"><div class="card h-100"><div class="card-body p-3" id="cb-out"></div></div></div></div>
    <div class="chart-title mt-4">Pinned</div><div class="row g-3" id="pin-grid"><div class="col-12"><div class="skel w-50"></div></div></div>`;
}

async function loadPins() {
  let r;
  try { r = await api("/api/charts"); } catch (e) { toast(e.message, 6000); return; }
  S.chartOptions = r.options;
  const form = $("#cb-form");
  if (form && !form.children.length) {
    const pick = (id, label, list, extra = "") => `<div class="col-6" data-cb><label class="form-label small mb-0" for="cb-${id}">${label}</label><select class="form-select form-select-sm" id="cb-${id}">${extra}${list.map(v => `<option value="${esc(v)}">${esc(niceName(v))}</option>`).join("")}</select></div>`;
    form.innerHTML = pick("type", "Chart", r.options.types) + pick("data", "Data", r.options.datasets) + pick("group", "Group by", r.options.groups) + pick("split", "Split by", r.options.groups, `<option value="">None</option>`)
      + pick("metric", "Show", r.options.metrics.applications) + pick("measure", "Measure", r.options.measures) + pick("top", "How many", ["10", "20", "30", "60"])
      + `<div class="col-6" data-cb><label class="form-label small mb-0" for="cb-from">Since</label><input type="date" class="form-control form-control-sm" id="cb-from"></div>`;
    $("#cb-group").value = "month";
    form.addEventListener("change", () => { builderFields(); builderPreview(); });
    builderFields(); builderPreview();
  }
  const grid = $("#pin-grid");
  if (!grid) return;
  (S.pinCharts || []).forEach(x => x.destroy()); S.pinCharts = [];
  grid.innerHTML = r.pins.length ? r.pins.map(p => `<div class="col-lg-6"><div class="card h-100"><div class="card-body p-3">${p.error
      ? `<div class="d-flex"><span class="small text-body-secondary me-auto">This chart can't be drawn any more.</span><button class="btn btn-sm btn-link p-0" data-act="chart-unpin" data-pin="${esc(p.id)}">Remove</button></div>`
      : specCard(p.chart, `pin-${p.id}`, { unpin: p.id })}</div></div></div>`).join("")
    : `<div class="col-12">${empty("Pin a chart here from the builder or the oracle. It stays up to date.")}</div>`;
  r.pins.forEach(p => { if (p.chart) drawSpec(p.chart, `pin-${p.id}`, S.pinCharts); });
}
