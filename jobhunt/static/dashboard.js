"use strict";
/* Dashboard: every number comes from /api/stats (Python); this file only lays it out. */

const DTABS = [["overview", "Overview", "speedometer2"], ["charts", "Charts", "bar-chart-line"], ["companies", "Companies", "buildings"], ["roles", "Roles", "person-workspace"],
  ["replies", "Replies", "funnel"], ["stats", "More", "three-dots"]];
const TAB_GROUP = { directory: "companies", found: "stats", usage: "stats" };
const tabOn = k => (TAB_GROUP[S.dTab] || S.dTab) === k;
const subNav = list => `<div class="btn-group btn-group-sm mb-3" role="group">${list.map(([k, l]) => `<button class="btn ${k === S.dTab ? "btn-ink" : "btn-outline-secondary"}" data-act="d-tab" data-k="${k}">${l}</button>`).join("")}</div>`;
const COMPANY_NAV = [["companies", "Numbers"], ["directory", "Websites"]], MORE_NAV = [["stats", "Statistics"], ["found", "Jobs found"], ["usage", "AI usage"]];
const STATUS_HUE = { applied: 0, screening: 2, oa: 3, interviewing: 6, offer: 5, rejected: 7, ghosted: 9, withdrawn: 8 };   // colour follows the status, not its rank

const dark = () => document.documentElement.dataset.bsTheme === "dark";
const palette = () => dark()
  ? ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767", "#8d9aa8", "#a58f6c"]
  : ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948", "#74818f", "#8f7a58"];
const num = (v, unit = "") => v == null ? "–" : `${Number.isInteger(v) ? v : v.toFixed(1)}${unit ? " " + unit : ""}`;
const usd = v => v < 0.01 && v > 0 ? "< $0.01" : `$${v.toFixed(2)}`;
const compact = n => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n);
const FEATURES = { screening: "Job screening", coach: "Coach", interview: "Interview prep", email: "Cold email", cover_letter: "Cover letters", dashboard: "Dashboard", resume: "Reading resumes", cleanup: "Name cleanup", test: "Key tests" };

function chartCard(id, title, cls = "col-lg-6", h = "chart-md", note = "") {
  return `<div class="${cls}"><div class="card h-100"><div class="card-body p-3"><div class="chart-title">${title}</div>${note ? `<div class="small text-body-secondary mb-2">${note}</div>` : ""}<div class="${h}"><canvas id="${id}" role="img" aria-label="${esc(title)}"></canvas></div></div></div></div>`;
}
const empty = msg => `<div class="empty-note">${bi("inbox")} ${esc(msg)}</div>`;

// pie plus a ranked list, so a slice never depends on colour alone
function pieCard(id, title, pie, apps) {
  if (!pie.slices.length) return `<div class="col-lg-6"><div class="card h-100"><div class="card-body p-3"><div class="chart-title">${title}</div>${empty("No applications in this range.")}</div></div></div>`;
  const c = palette();
  return `<div class="col-lg-6"><div class="card h-100"><div class="card-body p-3"><div class="chart-title">${title}</div>
    <div class="small text-body-secondary mb-2">These ${pie.slices.length} cover ${pie.covered}% of ${apps} applications, across ${pie.groups} in total.</div>
    <div class="pie-row"><div class="pie-box"><canvas id="${id}" role="img" aria-label="${esc(title)}"></canvas></div>
    <ol class="pie-list">${pie.slices.map((s, i) => `<li><span class="sw" data-bg="${c[i]}"></span><span class="text-truncate">${esc(s.label)}</span><b>${s.n}</b><small>${s.share}%</small></li>`).join("")}</ol></div></div></div></div>`;
}

function spreadTable(a) {
  const row = m => `<tr><th scope="row">${esc(m.label)}</th><td>${m.n}</td><td>${num(m.mean)}</td><td>${num(m.median)}</td><td>${num(m.min)}</td><td>${num(m.max)}</td><td>${num(m.stdev)}</td><td>${num(m.p25)}</td><td>${num(m.p75)}</td></tr>`;
  return `<div class="card"><div class="table-responsive"><table class="table table-sm align-middle mb-0 data-table"><thead><tr><th>Measure</th><th>Count</th><th>Average</th><th>Median</th><th>Min</th><th>Max</th><th>Std dev</th><th>25%</th><th>75%</th></tr></thead>
    <tbody>${a.metrics.map(row).join("")}</tbody></table></div></div>
    <div class="small text-body-secondary mt-2">Reply times use ${a.wait_tracked} of ${a.replies} replies: only those recorded since status history began. Salary comes from ${a.salary_coverage.with} of ${a.salary_coverage.total} jobs (${a.salary_coverage.percent}%), because most postings don't show pay, and uses each range's midpoint. Figures are never estimated.</div>`;
}

const TCOLS_D = [["name", "Name"], ["applied", "Applied"], ["active", "Active"], ["replies", "Replies"], ["interviews", "Screening+"], ["offers", "Offers"], ["rejected", "Rejected"], ["ghosted", "Ghosted"], ["reply_rate", "Reply %"], ["median_wait", "Median wait"], ["last", "Last applied"]];

function dataTable(rows, key) {
  const st = S.dSort[key] || (S.dSort[key] = { k: "applied", dir: -1, q: "" });
  const q = st.q.toLowerCase();
  const list = rows.filter(r => !q || r.name.toLowerCase().includes(q)).sort((a, b) => {
    const x = a[st.k] ?? -1, y = b[st.k] ?? -1;
    return (typeof x === "string" ? x.localeCompare(y) : x - y) * st.dir || a.name.localeCompare(b.name);
  });
  return `<div class="d-flex align-items-center gap-2 mb-2"><div class="input-group input-group-sm table-search"><span class="input-group-text">${bi("search")}</span><input type="search" class="form-control" data-dq="${key}" value="${esc(st.q)}" placeholder="Filter…" aria-label="Filter the table"></div>
      <span class="small text-body-secondary">Top ${Math.min(10, list.length)} of ${list.length}</span><a class="btn btn-sm btn-outline-secondary ms-auto" href="/api/tracker/export.xlsx">${bi("file-earmark-spreadsheet")} Export to Excel</a></div>
    <div class="card"><div class="table-responsive data-wrap"><table class="table table-sm table-hover align-middle mb-0 data-table"><thead><tr>${TCOLS_D.map(([k, l]) => `<th scope="col" role="button" data-act="d-sort" data-t="${key}" data-k="${k}">${l}${st.k === k ? (st.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>
    <tbody>${list.slice(0, 10).map(r => `<tr data-act="d-drill" data-name="${esc(r.name)}" title="Show these in the Tracker"><th scope="row" class="text-truncate cell-name">${esc(r.name)}</th><td>${r.applied}</td><td>${r.active}</td><td>${r.replies}</td><td>${r.interviews}</td><td>${r.offers}</td><td>${r.rejected}</td><td>${r.ghosted}</td><td>${r.reply_rate}%</td><td>${r.median_wait == null ? "–" : r.median_wait + " d"}</td><td class="text-nowrap">${fmtDate(r.last)}</td></tr>`).join("") || `<tr><td colspan="11" class="text-center text-body-secondary py-4">Nothing matches.</td></tr>`}</tbody></table></div></div>`;
}

function usageHTML(u) {
  if (!u.calls) return empty("Token use is recorded from now on. Run a search or ask the Coach something and it appears here.");
  const table = (rows, label, name = x => x) => `<div class="card h-100"><div class="table-responsive"><table class="table table-sm align-middle mb-0 data-table"><thead><tr><th>${label}</th><th>Calls</th><th>Tokens in</th><th>Tokens out</th><th>Cost</th><th>Median time</th></tr></thead>
    <tbody>${rows.map(r => `<tr><th scope="row" class="text-truncate cell-name">${esc(name(r.label))}</th><td>${r.calls}</td><td>${compact(r.tokens_in)}</td><td>${compact(r.tokens_out)}</td><td>${r.unpriced === r.calls ? "price unknown" : usd(r.cost) + (r.unpriced ? " +" : "")}</td><td>${r.median_ms == null ? "–" : (r.median_ms / 1000).toFixed(1) + " s"}</td></tr>`).join("")}</tbody></table></div></div>`;
  return `<div class="kpi-row mb-3">${kpi(usd(u.cost), "cost at list prices", u.unpriced ? `${u.unpriced} calls have no known price` : "")}${kpi(compact(u.tokens_in + u.tokens_out), "tokens", `${compact(u.tokens_in)} in · ${compact(u.tokens_out)} out`)}${kpi(u.calls, "AI calls")}</div>
    <div class="row g-3">${chartCard("c-use", "Tokens by day", "col-12", "chart-sm")}<div class="col-xl-7">${table(u.by_model, "Model")}</div><div class="col-xl-5">${table(u.by_feature, "Used for", x => FEATURES[x] || x)}</div></div>
    <div class="small text-body-secondary mt-2">Costs use the list prices in config.toml. Free tiers and local models can cost you less.</div>`;
}

const kpi = (n, label, sub = "") => `<div class="kpi"><div class="num">${n}</div><div class="kpi-label">${label}</div>${sub ? `<div class="tile-sub">${sub}</div>` : ""}</div>`;

const STAGE_LABEL = { applied: "Applied", screening: "Screening call", oa: "OA / Test", interviewing: "Interview", offer: "Offer" };

function drawDirectory() {
  const host = $("#dir-rows");
  if (!host || !S.dirList) return;
  const q = ($("#dir-q")?.value || "").toLowerCase(), show = $("#dir-show")?.value || "all";
  const list = S.dirList.filter(c => (!q || `${c.name} ${c.roles.join(" ")}`.toLowerCase().includes(q)) && (show === "all" || (show === "replied") === c.replied));
  $("#dir-count").textContent = `${list.length} of ${S.dirList.length} companies`;
  const out = (u, label, ic, cls = "btn-outline-secondary") => `<a class="btn btn-sm ${cls}" href="${esc(safeUrl(u))}" target="_blank" rel="noopener noreferrer">${bi(ic)} ${label}</a>`;
  host.innerHTML = list.map(c => `<div class="dir-row"><div class="min-w-0">${avatar(c.name)}</div>
      <div class="dir-info"><div class="fw-semibold text-truncate">${esc(c.name)}</div><div class="small text-body-secondary text-truncate">${esc(c.roles.join(" · "))}</div></div>
      <div class="dir-meta small text-body-secondary"><span>${esc(c.tier_label.split(" (")[0])}</span><span>${c.applied} applied · last ${fmtDate(c.last)}</span><span>${c.replied ? `${bi("reply")} ${esc(STAGE_LABEL[c.best])}` : "No reply yet"}</span></div>
      <div class="dir-links">${out(c.careers, "Careers in India", "briefcase", "btn-ink")}${out(c.website, "Website", "globe2")}${c.job_url ? out(c.job_url, "Your job link", "link-45deg") : ""}</div></div>`).join("") || empty("Nothing matches.");
}

async function loadDirectory() {
  try { S.dirList = (await api("/api/companies/applied")).companies; drawDirectory(); }
  catch (e) { if ($("#dir-rows")) $("#dir-rows").textContent = e.message; }
}

function delta(cmp, key) {
  if (!cmp) return "all time";
  const [now, before] = cmp[key], d = now - before;
  return `${d > 0 ? "+" : ""}${d} vs previous ${cmp.days} days`;
}

function calendarCard(c) {
  const level = n => !n ? 0 : Math.max(1, Math.ceil(4 * n / c.max));
  const cells = `<i class="cal-pad"></i>`.repeat(c.pad) + c.days.map(d => `<i class="cal-c l${level(d.n)}" title="${fmtDate(d.date)}: ${d.n}"></i>`).join("");
  return `<div class="card h-100"><div class="card-body p-3"><div class="chart-title">Your last 6 months</div>
    <div class="small text-body-secondary mb-2">${c.total} applications on ${c.active_days} days</div><div class="cal-wrap"><div class="cal" role="img" aria-label="${c.total} applications on ${c.active_days} days">${cells}</div></div></div></div>`;
}

function followUpCard(f) {
  return `<div class="card h-100"><div class="card-body p-3"><div class="chart-title">Follow up${f.n ? ` (${f.n})` : ""}</div>
    ${f.items.length ? `<div class="small text-body-secondary mb-2">No reply after ${f.after}+ days</div><div class="fu-list">${f.items.map(x => `<button class="fu" data-act="d-drill" data-name="${esc(x.company)}"><span class="text-truncate">${esc(x.company)}</span><small>${x.days}d</small></button>`).join("")}</div>` : empty("Nothing to chase right now.")}</div></div>`;
}

function tabBody(st) {
  const k = st.kpis, td = st.today, ex = st.explore, a = ex.analysis;
  const ins = S.insights || {};
  const toneIc = { good: "graph-up-arrow", warn: "exclamation-triangle", info: "lightbulb" };
  switch (S.dTab) {
    case "charts": return chartsTab();
    case "directory": return `${subNav(COMPANY_NAV)}<div class="d-flex flex-wrap align-items-center gap-2 mb-2"><div class="input-group input-group-sm table-search"><span class="input-group-text">${bi("search")}</span><input type="search" class="form-control" id="dir-q" placeholder="Search company or role…" aria-label="Search companies"></div>
        <select class="form-select form-select-sm w-auto" id="dir-show" aria-label="Show"><option value="all">All companies</option><option value="replied">Replied</option><option value="silent">No reply yet</option></select><span class="small text-body-secondary" id="dir-count"></span></div>
      <div class="small text-body-secondary mb-2">Every company you've applied to, all time. Careers and Website open a search, so you land on the company's real page.</div>
      <div class="card"><div id="dir-rows" class="dir-list">${S.dirList ? "" : `<div class="p-3"><div class="skel w-75"></div><div class="skel"></div></div>`}</div></div>`;
    case "stats": return `${subNav(MORE_NAV)}${spreadTable(a)}<div class="row g-3 mt-1">${chartCard("c-weekday", "Applications by weekday", "col-lg-6")}${ex.wait.some(w => w.n) ? chartCard("c-wait", "Days until a first reply", "col-lg-6") : ""}</div>`;
    case "companies": return `${subNav(COMPANY_NAV)}<div class="row g-3 mb-3">${pieCard("p-company", "Top 10 companies by applications", a.company_pie, a.applications)}${ex.company_status.companies.length ? chartCard("c-cstat", "Top companies by outcome", "col-lg-6", "chart-lg") : ""}</div>${dataTable(a.companies, "companies")}`;
    case "roles": return `<div class="row g-3 mb-3">${pieCard("p-title", "Top 10 roles by applications", a.title_pie, a.applications)}${chartCard("c-role", "Role families", "col-lg-6", "chart-lg")}</div>${dataTable(a.titles, "titles")}<div class="chart-title mt-3">Role families</div>${dataTable(a.families, "families")}`;
    case "replies": return `<div class="row g-3">${chartCard("c-funnel", "Funnel · reached at least this stage")}${chartCard("c-status", "Status now")}
      ${ex.rates.tier.length ? chartCard("c-rtier", "Reply rate by company tier", "col-lg-4") : ""}${ex.rates.role.length ? chartCard("c-rrole", "Reply rate by role family", "col-lg-4") : ""}${ex.rates.source.length ? chartCard("c-rsrc", "Reply rate by where you applied", "col-lg-4") : ""}</div>
      ${a.replies < 20 ? `<div class="small text-body-secondary mt-2">Only ${a.replies} repl${a.replies === 1 ? "y" : "ies"} so far, so treat these rates as an early signal.</div>` : ""}`;
    case "found": return `${subNav(MORE_NAV)}<div class="row g-3">${hasData(ex.found.companies) ? chartCard("c-fcomp", "Jobs found by company", "col-lg-6", "chart-lg") : ""}${hasData(ex.found.roles) ? chartCard("c-frole", "Jobs found by role family", "col-lg-3", "chart-lg") : ""}${hasData(ex.found.tiers) ? chartCard("c-ftier", "Jobs found by company tier", "col-lg-3", "chart-lg") : ""}</div>${ex.found.total ? "" : empty("No jobs found in this range.")}`;
    case "usage": return subNav(MORE_NAV) + usageHTML(st.usage);
    default: return `<div class="kpi-row mb-3">
        ${kpi(`${td.count}<span class="of">/${td.target}</span>`, "today", td.met ? "target met" : `${td.target - td.count} to go`)}${kpi(k.applications, "applications", delta(st.compare, "applications"))}
        ${kpi(`${k.response_rate}%`, "reply rate", delta(st.compare, "replies"))}${kpi(`${k.streak}d`, "streak", `${k.this_week} this week`)}</div>
      <div class="row g-3 mb-3"><div class="col-lg-8">${calendarCard(st.calendar)}</div><div class="col-lg-4">${followUpCard(st.follow_ups)}</div></div>
      <div class="oracle mb-3"><div class="d-flex align-items-center gap-2 mb-2"><div class="chart-title mb-0">${bi("eye")} The oracle</div><span class="small text-body-secondary">patterns in your own numbers</span>
        <div class="ms-auto btn-group btn-group-sm"><button class="btn btn-outline-secondary" data-action="oracle-prev" aria-label="Previous">${bi("chevron-left")}</button><button class="btn btn-outline-secondary" data-action="oracle-next" aria-label="Next">${bi("chevron-right")}</button></div></div>
        <div class="oracle-track" id="oracle-track">${ex.oracle.map(o => `<div class="oracle-card tone-${o.tone}"><div class="oc-ic">${bi(toneIc[o.tone] || "lightbulb")}</div><div><div class="fw-semibold">${esc(o.title)}</div><div class="small text-body-secondary mt-1">${esc(o.tip)}</div></div></div>`).join("")}</div></div>
      <div class="row g-3 mb-3">${chartCard("c-day", `Applications by ${ex.grain}`, "col-12")}</div>
      <div class="card insights"><div class="card-body p-3"><div class="d-flex flex-wrap align-items-center gap-2 mb-1"><div class="chart-title mb-0">${bi("stars")} AI insights</div>
        <span class="small text-body-secondary" id="ins-meta">${ins.at ? `${esc(ins.provider)} · ${ago(ins.at)}` : ""}</span><span class="ms-auto" data-taskbox="dashboard"></span><button class="btn btn-sm btn-ink" data-action="insights">${bi("stars")} ${ins.text ? "Refresh analysis" : "Analyse my search"}</button></div>
        <div id="ins-body" class="ins-body">${ins.text ? mdLite(ins.text) : `<span class="text-body-secondary">Sends only these numbers (no company names, notes or contacts) to your AI provider.</span>`}</div></div></div>`;
  }
}

function drawCharts(st) {
  if (!window.Chart) return;
  chartDefaults();
  const ex = st.explore, a = ex.analysis, C = palette(), surface = cssVar("--jh-card");
  const axes = (horizontal = false) => { const v = { beginAtZero: true, ticks: { precision: 0 } }, c = { grid: { display: false }, ticks: { autoSkip: true, maxRotation: 0, maxTicksLimit: 12 } }; return horizontal ? { x: v, y: { grid: { display: false } } } : { x: c, y: v }; };
  const noLegend = { legend: { display: false } };
  const bars = (id, labels, data, color, horizontal = false, tip) => chart(id, { type: "bar", data: { labels, datasets: [{ data, backgroundColor: color, borderRadius: 4, maxBarThickness: 18 }] },
    options: { indexAxis: horizontal ? "y" : "x", plugins: { ...noLegend, ...(tip ? { tooltip: { callbacks: { label: tip } } } : {}) }, scales: axes(horizontal) } });
  const ring = (id, list, colors, legend = "bottom", name = x => x) => chart(id, { type: "doughnut", data: { labels: list.map(x => name(x.label)), datasets: [{ data: list.map(x => x.n), backgroundColor: colors, borderWidth: 2, borderColor: surface }] }, options: { cutout: "60%", plugins: { legend: { display: !!legend, position: legend || "bottom" } } } });
  const rate = (id, list) => bars(id, list.map(x => x.label), list.map(x => x.rate), C[0], true, c => `${c.raw}% of ${list[c.dataIndex].n} applications`);
  const pie = (id, p) => { if (p.slices.length) chart(id, { type: "pie", data: { labels: p.slices.map(s => s.label), datasets: [{ data: p.slices.map(s => s.n), backgroundColor: C, borderWidth: 2, borderColor: surface }] },
    options: { plugins: { ...noLegend, tooltip: { callbacks: { label: c => `${c.raw} applications, ${p.slices[c.dataIndex].share}% of all` } } } } }); };
  const others = l => l.filter(x => x.label !== "Others");
  switch (S.dTab) {
    case "stats":
      bars("c-weekday", st.weekday.map(x => x.label), st.weekday.map(x => x.n), C[0]);
      if (ex.wait.some(w => w.n)) bars("c-wait", ex.wait.map(x => x.label), ex.wait.map(x => x.n), C[2], true);
      break;
    case "companies": {
      pie("p-company", a.company_pie);
      const cs = ex.company_status;
      if (cs.companies.length) chart("c-cstat", { type: "bar", data: { labels: cs.companies, datasets: cs.statuses.map((s, i) => ({ label: STATUS_LABEL(s), data: cs.data[i], backgroundColor: C[STATUS_HUE[s]], borderColor: surface, borderWidth: 1, maxBarThickness: 18 })).filter(d => d.data.some(Boolean)) },
        options: { indexAxis: "y", plugins: { legend: { position: "bottom" } }, scales: { x: { stacked: true, beginAtZero: true, ticks: { precision: 0 } }, y: { stacked: true, grid: { display: false } } } } });
      break;
    }
    case "roles":
      pie("p-title", a.title_pie);
      bars("c-role", others(st.roles).map(x => x.label), others(st.roles).map(x => x.n), C[0], true);
      break;
    case "replies":
      bars("c-funnel", st.funnel.map(x => x.label), st.funnel.map(x => x.n), C[0], true);
      ring("c-status", st.status, st.status.map(x => C[STATUS_HUE[x.label]]), "right", STATUS_LABEL);
      if (ex.rates.tier.length) rate("c-rtier", ex.rates.tier);
      if (ex.rates.role.length) rate("c-rrole", ex.rates.role);
      if (ex.rates.source.length) rate("c-rsrc", ex.rates.source);
      break;
    case "found":
      if (hasData(ex.found.companies)) bars("c-fcomp", others(ex.found.companies).map(x => x.label), others(ex.found.companies).map(x => x.n), C[0], true);
      if (hasData(ex.found.roles)) ring("c-frole", ex.found.roles, C);
      if (hasData(ex.found.tiers)) ring("c-ftier", ex.found.tiers, C);
      break;
    case "usage":
      if (st.usage.calls) chart("c-use", { type: "bar", data: { labels: st.usage.by_day.map(d => fmtDate(d.label)), datasets: [{ label: "Tokens in", data: st.usage.by_day.map(d => d.tokens_in), backgroundColor: C[0], maxBarThickness: 18 }, { label: "Tokens out", data: st.usage.by_day.map(d => d.tokens_out), backgroundColor: C[1], maxBarThickness: 18 }] },
        options: { plugins: { legend: { position: "bottom" } }, scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, beginAtZero: true } } } });
      break;
    default: {
      const per = { day: 1, week: 7, month: 30 }[ex.grain];
      chart("c-day", { type: "bar", data: { labels: ex.series.map(x => x.label), datasets: [
          { type: "line", label: "Target", data: ex.series.map(() => st.today.target * per), borderColor: C[8], borderDash: [5, 4], borderWidth: 2, pointRadius: 0 },
          { label: "Replies", data: ex.series.map(x => x.responded), backgroundColor: C[2], borderRadius: 4, maxBarThickness: 18 },
          { label: "Applications", data: ex.series.map(x => x.applied), backgroundColor: C[0], borderRadius: 4, maxBarThickness: 18 }] },
        options: { plugins: { legend: { position: "bottom" } }, scales: axes() } });
    }
  }
}

async function renderDashboard() {
  (S.charts || []).forEach(c => c.destroy()); S.charts = [];
  S.dRange = S.dRange || "30d"; S.dGrain = S.dGrain || "auto"; S.dCompany = S.dCompany || ""; S.dFamily = S.dFamily || "";
  S.dTab = S.dTab || "overview"; S.dSort = S.dSort || {};
  const host = $("#app");
  if (!host.querySelector(".dash-tabs")) host.innerHTML = `<div class="skel w-50 mt-3"></div><div class="skel"></div><div class="skel w-75"></div>`;
  let st;
  try { st = await api(`/api/stats?${new URLSearchParams({ range: S.dRange, grain: S.dGrain, company: S.dCompany, family: S.dFamily })}`); }
  catch (e) { host.innerHTML = `<div class="alert alert-danger mt-4">${esc(e.message)}</div>`; return; }
  S.dData = st;
  const ex = st.explore;
  const seg = (attr, cur, opts) => `<div class="btn-group btn-group-sm" role="group">${opts.map(([v, l]) => `<button class="btn ${v === cur ? "btn-ink" : "btn-outline-secondary"}" data-${attr}="${v}">${l}</button>`).join("")}</div>`;
  host.innerHTML = `<div class="d-flex flex-wrap align-items-center gap-2 mb-3 dash-filters">
      ${seg("drange", S.dRange, [["7d", "7 days"], ["30d", "30 days"], ["90d", "90 days"], ["12m", "12 months"], ["all", "All time"]])}
      <select class="form-select form-select-sm w-auto" id="d-company" aria-label="Filter by company"><option value="">All companies</option>${st.options.companies.map(c => `<option ${c === S.dCompany ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
      <select class="form-select form-select-sm w-auto" id="d-family" aria-label="Filter by role"><option value="">All roles</option>${st.options.families.map(c => `<option ${c === S.dFamily ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
      ${S.dCompany || S.dFamily ? `<button class="btn btn-sm btn-link" data-action="d-clear">Clear filters</button>` : ""}<span class="small text-body-secondary ms-auto">${fmtDate(ex.start)} – ${fmtDate(ex.end)}</span></div>
    <div class="dash-tabs" role="tablist">${DTABS.map(([k, l, ic]) => `<button class="dash-tab ${tabOn(k) ? "on" : ""}" role="tab" aria-selected="${tabOn(k)}" data-act="d-tab" data-k="${k}">${bi(ic)} ${l}</button>`).join("")}</div>
    <div id="dash-body">${tabBody(st)}</div>
    <button class="btn btn-ink oracle-fab" data-action="oracle-chat-open">${bi("chat-dots")} Ask the oracle</button>
    <div class="oracle-chat card d-none" id="oracle-chat"><div class="card-body p-0 d-flex flex-column">
      <div class="d-flex flex-wrap align-items-center gap-2 px-3 py-2 border-bottom"><b>${bi("eye")} Ask the oracle</b><span data-taskbox="dashboard"></span><button class="btn btn-sm btn-link ms-auto p-0" data-action="oracle-chat-reset" title="Clear the chat">${bi("trash")}</button><button class="btn-close" data-action="oracle-chat-close" aria-label="Close"></button></div>
      <div class="oc-msgs flex-grow-1 p-3" id="oc-msgs"></div>
      <div class="px-3 pb-2 d-flex flex-wrap gap-1">${["Where am I losing?", "Stacked bar of status by month", "Reply rate by tier", "List applications waiting over 14 days", "Scatter of applications and replies by company"].map(q => `<button class="btn btn-sm btn-outline-secondary rounded-pill" data-action="oracle-ask" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
      <form class="d-flex gap-2 p-3 pt-1" id="oc-form"><input class="form-control form-control-sm" id="oc-in" placeholder="Ask about your job search…" autocomplete="off"><button class="btn btn-sm btn-ink" aria-label="Send">${bi("send")}</button></form></div></div>`;
  $("#oc-form").addEventListener("submit", e => { e.preventDefault(); oracleAsk($("#oc-in").value); });
  drawSwitchers();
  afterTab(st);
}

function afterTab(st) {
  paint($("#dash-body"));
  if (S.dTab === "directory") { if (S.dirList) drawDirectory(); loadDirectory(); }
  if (S.dTab === "charts") loadPins();
  $(".dash-filters")?.classList.toggle("d-none", S.dTab === "charts");      // the builder has its own choices
  clearInterval(S.oracleTimer);
  if (S.dTab === "overview") {
    S.oracleTimer = setInterval(() => { if (currentView() !== "dashboard") return clearInterval(S.oracleTimer); if (!document.querySelector(".oracle:hover")) oracleScroll(1, true); }, 7000);
    if (!S.insights) api("/api/ai/insights").then(r => { if (r && r.text && $("#ins-body")) { S.insights = r; $("#ins-body").innerHTML = mdLite(r.text); $("#ins-meta").textContent = `${r.provider} · ${ago(r.at)}`; } }).catch(() => {});
  }
  drawCharts(st);
}

function redrawTab() {
  (S.charts || []).forEach(c => c.destroy()); S.charts = [];
  $("#dash-body").innerHTML = tabBody(S.dData);
  drawSwitchers();
  afterTab(S.dData);
}

ACT["d-tab"] = el => { S.dTab = el.dataset.k; $$(".dash-tab").forEach(b => { const on = tabOn(b.dataset.k); b.classList.toggle("on", on); b.setAttribute("aria-selected", on); }); redrawTab(); };
ACT["d-sort"] = el => { const st = S.dSort[el.dataset.t]; st.dir = st.k === el.dataset.k ? -st.dir : (el.dataset.k === "name" ? 1 : -1); st.k = el.dataset.k; redrawTab(); };
ACT["d-drill"] = el => { S.t.q = el.dataset.name; S.t.status = "all"; store.set("tracker", S.t); location.hash = "#/tracker"; };
document.addEventListener("input", e => {
  const k = e.target.dataset && e.target.dataset.dq;
  if (!k) return;
  S.dSort[k].q = e.target.value;
  clearTimeout(S.dqTimer);
  S.dqTimer = setTimeout(() => { redrawTab(); const i = $(`[data-dq="${k}"]`); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }, 200);
});
document.addEventListener("input", e => { if (e.target.id === "dir-q") drawDirectory(); });
document.addEventListener("change", e => { if (e.target.id === "dir-show") drawDirectory(); });
