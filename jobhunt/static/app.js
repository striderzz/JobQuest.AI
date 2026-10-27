"use strict";
/* JobQuest.AI front end on Bootstrap 5.3. Plain JS, no build step; every server string is escaped before rendering. */

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = u => { try { const x = new URL(u); return x.protocol === "https:" || x.protocol === "http:" ? x.href : "#"; } catch { return "#"; } };
const store = {
  get(k, d) { try { const v = localStorage.getItem("jh:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("jh:" + k, typeof v === "string" ? v : JSON.stringify(v)); } catch { /* private mode */ } },
};
const narrow = () => window.matchMedia("(max-width: 991.98px)").matches;
const bi = (name, extra = "") => `<i class="bi bi-${name} ${extra}" aria-hidden="true"></i>`;

const CITIES = ["Bengaluru", "Hyderabad", "Pune", "Chennai", "Mumbai", "Delhi NCR", "Kolkata", "Ahmedabad", "Kochi",
  "Thiruvananthapuram", "Jaipur", "Chandigarh", "Coimbatore", "Indore"];
const STATUS = { saved: "Saved", applied: "Applied", screening: "Screening call", oa: "OA / Test", interviewing: "Interview", offer: "Offer", rejected: "Rejected", ghosted: "Ghosted", withdrawn: "Withdrawn", hidden: "Hidden" };
const TRACK = ["saved", "applied", "screening", "oa", "interviewing", "offer", "rejected", "ghosted", "withdrawn"];
const SORTS = [["best", "Best match"], ["newest", "Newest"], ["location", "Location (A–Z)"], ["company", "Company (A–Z)"],
  ["years", "Fewest years required"], ["added", "Recently added"]];

const S = {
  picked: new Set(),
  state: null, jobs: [], polling: null, sel: null, details: {},
  f: Object.assign({ q: "", role: "all", type: "all", tier: "all", days: 0, city: "", sort: "best", newOnly: false, contact: false, showAll: false },
    store.get("filters3", {})),
  t: Object.assign({ view: "table", sort: "applied", dir: -1, q: "", status: "all" }, store.get("tracker", {})),
};

async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: {} };
  if (opts.json !== undefined) { o.body = JSON.stringify(opts.json); o.headers["Content-Type"] = "application/json"; }
  if (opts.form) o.body = opts.form;
  const r = await fetch(path, o);
  let data = null;
  try { data = await r.json(); } catch { /* empty body */ }
  if (!r.ok) {
    const err = new Error((data && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || `Request failed (${r.status})`);
    err.status = r.status;
    throw err;
  }
  return data;
}

function toast(msg, ms = 3500, action = null) {
  $("#toast-body").textContent = msg;
  const b = $("#toast-action");
  b.classList.toggle("d-none", !action);
  b.onclick = action ? () => { bootstrap.Toast.getOrCreateInstance($("#toast")).hide(); action.run(); } : null;
  if (action) b.textContent = action.label;
  const t = bootstrap.Toast.getOrCreateInstance($("#toast"), { delay: ms });
  t.hide(); t.show();
}

function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? "yesterday" : `${d} days ago`;
}
const postedShort = d => d == null ? "" : d === 0 ? "Today" : `${d}d ago`;
const postedLong = d => d == null ? "Date unknown" : d === 0 ? "Posted today" : d === 1 ? "Posted yesterday" : `Posted ${d} days ago`;
const yearsText = y => y == null ? "Years not stated" : y === 0 ? "0 yrs · fresher" : `${y}+ yrs`;
const kindText = k => k === "ML" ? "ML / AI" : "SDE";
const place = j => j.cities.length ? j.cities.join(", ") : (j.remote ? "Remote · India" : j.location);

function verdictOf(j) {
  if (j.ai && !j.ai_rejected) return j.ai.verdict;
  const s = j.score || 0;
  return s >= 80 ? "strong" : s >= 65 ? "good" : s >= 50 ? "stretch" : "poor";
}

function avatar(name, size = "") {
  const words = String(name || "?").replace(/[^A-Za-z0-9 ]/g, " ").trim().split(/\s+/);
  const initials = ((words[0] || "?")[0] + (words[1] ? words[1][0] : (words[0] || "")[1] || "")).toUpperCase();
  let h = 0;
  for (const ch of String(name)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return `<div class="avatar c${h % 8} ${size}" aria-hidden="true">${esc(initials)}</div>`;
}

function ring(score, verdict, size = 52, big = false) {
  const v = Math.max(0, Math.min(100, Math.round(score || 0)));
  return `<div class="ring ${verdict} ${big ? "big" : ""}" title="Match score ${v} out of 100">
    <svg viewBox="0 0 36 36" width="${size}" height="${size}" aria-hidden="true">
      <circle class="trk" cx="18" cy="18" r="15.9155" pathLength="100"/>
      <circle class="val" cx="18" cy="18" r="15.9155" pathLength="100" stroke-dasharray="0 100" data-v="${v}"/></svg>
    <span class="rv" data-v="${v}">${big ? 0 : v}</span></div>`;
}

function animate(root) {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    $$("circle.val", root).forEach(c => { c.style.strokeDasharray = `${c.dataset.v} 100`; });
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    $$(".ring.big .rv", root).forEach(el => {
      const target = Number(el.dataset.v), t0 = performance.now();
      if (still) { el.textContent = target; return; }
      const step = t => { const p = Math.min(1, (t - t0) / 900); el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3))); if (p < 1) requestAnimationFrame(step); };
      requestAnimationFrame(step);
    });
  }));
}

function stagger(root, sel, step = 40) {
  $$(sel, root).forEach((el, i) => { el.style.animationDelay = `${Math.min(i, 14) * step}ms`; });
}

// ---------------------------------------------------------------- theme

const SKINS = window.JQ_SKINS;
function themeIcon() {
  const cur = document.documentElement.dataset.skin || "light";
  $("#theme-btn").innerHTML = bi("circle-half");
  const item = ([k, [, label]]) => `<li><button class="dropdown-item d-flex align-items-center gap-2 ${k === cur ? "active" : ""}" data-action="skin" data-skin="${k}"><span class="skin-dot skin-${k}"></span>${label}${k === cur ? ` ${bi("check2", "ms-auto")}` : ""}</button></li>`;
  const all = Object.entries(SKINS);
  $("#theme-menu").innerHTML = `<li><h6 class="dropdown-header">Light</h6></li>${all.filter(([, v]) => v[0] === "light").map(item).join("")}`
    + `<li><h6 class="dropdown-header">Dark</h6></li>${all.filter(([, v]) => v[0] === "dark").map(item).join("")}`
    + `<li><hr class="dropdown-divider"></li>`
    + `<li><button class="dropdown-item d-flex align-items-center gap-2" data-action="skin" data-skin="generate">${bi("magic")} Generate a random palette${cur === "custom" ? ` <span class="small text-body-secondary ms-auto">again</span>` : ""}</button></li>`
    + `<li><button class="dropdown-item d-flex align-items-center gap-2" data-action="skin" data-skin="random">${bi("shuffle")} Random named theme</button></li>`;
}
function setSkin(k) {
  let label;
  if (k === "generate") {
    const p = window.applySkin("custom", window.randomPalette());
    try { localStorage.setItem("jh:custom", JSON.stringify(p)); } catch { /* private mode */ }
    k = "custom"; label = `random ${p.mode} palette, hue ${p.hue}°`;
  } else {
    if (k === "random") { const ks = Object.keys(SKINS).filter(x => x !== document.documentElement.dataset.skin); k = ks[Math.floor(Math.random() * ks.length)]; }
    window.applySkin(k); label = SKINS[k][1];
  }
  try { localStorage.setItem("jh:skin", k); } catch { /* private mode */ }
  themeIcon();
  toast(`Theme: ${label}`, 1800);
}

// ---------------------------------------------------------------- shell

function renderShell() {
  const tracked = S.jobs.filter(j => TRACK.includes(j.status)).length;
  const tabs = [["jobs", "Jobs", "briefcase"], ["tracker", "Tracker", "kanban"], ["dashboard", "Dashboard", "bar-chart-line"], ["outreach", "Cold email", "envelope-at"], ["coach", "Coach", "chat-dots"], ["profile", "Profile", "person-badge"], ["settings", "Settings", "gear"]];
  $("#tabs").innerHTML = tabs.map(([v, label, ic]) => `<li class="nav-item"><a class="nav-link d-flex align-items-center gap-2" href="#/${v}" data-view="${v}">${bi(ic)}${label}${v === "tracker" && tracked ? `<span class="badge rounded-pill tag-line">${tracked}</span>` : ""}</a></li>`).join("");
  const lr = S.state.last_run;
  $("#last-run").textContent = lr ? `Updated ${ago(lr.finished)}` : "Not searched yet";
  const tg = S.state.target || { count: 0, target: 10 }, frac = Math.min(1, tg.count / tg.target), C = 2 * Math.PI * 9;
  $("#target-pill").innerHTML = `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><circle cx="12" cy="12" r="9" class="tr-bg"/><circle cx="12" cy="12" r="9" class="tr-fg ${frac >= 1 ? "done" : ""}" stroke-dasharray="${(frac * C).toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 12 12)"/></svg><span><b>${tg.count}</b>/${tg.target} today</span>`;
  $("#target-pill").title = frac >= 1 ? "Daily target met" : `${tg.target - tg.count} more applications to hit today's target`;
  const btn = $("#refresh-btn");
  if (!btn.disabled) btn.innerHTML = `${bi("arrow-repeat")} Find new jobs`;
  themeIcon();
  $$("#tabs .nav-link").forEach(a => a.classList.toggle("active", a.dataset.view === currentView()));
}

function showProgress(p) {
  const box = $("#progress"), btn = $("#refresh-btn");
  if (!p.running) { box.classList.add("d-none"); btn.disabled = false; btn.innerHTML = `${bi("arrow-repeat")} Find new jobs`; return; }
  box.classList.remove("d-none");
  btn.disabled = true; btn.innerHTML = `${bi("arrow-repeat", "spin")} Searching…`;
  $("#progress-label").textContent = p.phase || "Working";
  const pct = p.total ? Math.round(100 * p.done / p.total) : 100;
  $("#progress-bar").style.width = `${pct}%`;
  $("#progress-detail").textContent = p.total ? `${p.done} of ${p.total}${p.detail ? " · " + p.detail : ""}` : (p.detail || "");
}

async function loadAll() {
  const [st, js] = await Promise.all([api("/api/state"), api("/api/jobs")]);
  S.state = st; S.jobs = js.jobs; S.details = {};
  S.lastRunId = st.last_run ? st.last_run.id : null;
  renderShell(); route();
  if (st.refresh.running) startPolling();
}

// A window left open (say overnight) notices searches run elsewhere - the 9 AM task - and reloads its list.
setInterval(async () => {
  if (!S.state || S.polling || document.hidden && Math.random() < 0.5) return;
  try {
    const st = await api("/api/state");
    if (st.refresh.running) { startPolling(); return; }
    const id = st.last_run ? st.last_run.id : null;
    if (id !== S.lastRunId) {
      await loadAll();
      const n = st.last_run ? st.last_run.new : 0;
      toast(n ? `${n} new job${n === 1 ? "" : "s"} found` : "Jobs updated", 5000);
    } else {
      $("#last-run").textContent = st.last_run ? `Updated ${ago(st.last_run.finished)}` : "Not searched yet";
    }
  } catch { /* server restarting; try again next tick */ }
}, 60000);

function startPolling() {
  if (S.polling) return;
  const tick = async () => {
    let p;
    try { p = await api("/api/refresh"); } catch { S.polling = setTimeout(tick, 3000); return; }
    showProgress(p);
    if (p.running) { S.polling = setTimeout(tick, 1200); return; }
    S.polling = null;
    if (p.error) toast(`Search failed: ${p.error}`, 7000);
    else if (p.result) {
      const r = p.result;
      toast(`${r.new} new job${r.new === 1 ? "" : "s"} · ${r.kept} matching${r.ai_checked ? ` · AI checked ${r.ai_checked}` : ""}${r.ai_error ? " · AI: " + r.ai_error : ""}`, 6000);
    }
    await loadAll();
  };
  showProgress({ running: true, phase: "Starting" });
  tick();
}

async function startRefresh() {
  try { await api("/api/refresh", { method: "POST" }); startPolling(); }
  catch (e) { toast(e.message, 6000); }
}

// ---------------------------------------------------------------- router

const currentView = () => (location.hash.replace(/^#\//, "") || "jobs").split("?")[0];
function route() {
  if (!S.state) return;                           // a hash change can arrive before the first load finishes
  const view = currentView();
  (S.charts || []).forEach(c => c.destroy()); S.charts = [];
  $$("#tabs .nav-link").forEach(a => a.classList.toggle("active", a.dataset.view === view));
  ({ jobs: renderJobs, tracker: renderTracker, dashboard: renderDashboard, outreach: renderOutreach, coach: renderCoach, profile: renderProfile, settings: renderSettings }[view] || renderJobs)();
  const nav = bootstrap.Collapse.getInstance($("#nav"));
  if (nav) nav.hide();
}
window.addEventListener("hashchange", () => { hideSheet(); route(); window.scrollTo({ top: 0 }); });

// ---------------------------------------------------------------- jobs page

function banners() {
  const st = S.state, out = [];
  if (!st.ai.screening) out.push(`<div class="alert alert-info d-flex flex-wrap align-items-center gap-3 rise mt-4"><span class="flex-grow-1"><b>${st.ai.configured ? "Add a cloud LLM key for AI fit scores." : "Please add an LLM key."}</b> Any one provider works (Gemini, Groq and OpenRouter have free tiers). ${st.ai.configured ? "Your local model already powers cover letters and the Coach." : "It also turns on cover letters and the Coach."}</span><a class="btn btn-primary" href="#/settings">${bi("stars")} Add LLM key</a></div>`);
  const la = st.last_attempt;
  if (la && !la.ok && la.error && !st.refresh.running) out.push(`<div class="alert alert-danger rise mt-4"><b>The last search failed:</b> ${esc(la.error)}</div>`);
  else if (st.last_run && st.last_run.error && st.ai.configured) out.push(`<div class="alert alert-warning d-flex flex-wrap align-items-center gap-3 rise mt-4"><span class="flex-grow-1"><b>Some AI checks failed:</b> ${esc(st.last_run.error)}</span><a class="btn btn-outline-secondary" href="#/settings">Check settings</a></div>`);
  return out.join("");
}

const TIERS = { big_tech: "Big Tech (FAANG / MANGO)", us_unicorn: "US unicorn / scale-up", startup: "Indian startup", mid: "Mid-size & enterprise" };
const quick = (key, val, label, n) => `<button class="btn btn-sm rounded-pill ${S.f[key] === val ? "btn-ink" : "btn-outline-secondary"}" data-action="quick" data-k="${key}" data-v="${val}">${label} <span class="opacity-75">${n}</span></button>`;

function visible(j) {
  const f = S.f;
  // applied (or further along) jobs live in the Tracker, not in the list of jobs still to apply to
  if (!f.showAll && (!j.active || j.status === "hidden" || j.ai_rejected || (j.status && j.status !== "saved"))) return false;
  if (f.role !== "all" && j.kind !== f.role) return false;
  if (f.type === "intern" && j.level !== "Intern") return false;
  if (f.type === "fulltime" && j.level === "Intern") return false;
  if (f.tier !== "all" && j.tier !== f.tier) return false;
  if (f.days && (j.age_days == null || j.age_days > f.days)) return false;
  if (f.city === "Remote" && !j.remote) return false;
  if (f.city && f.city !== "Remote" && !j.cities.includes(f.city)) return false;
  if (f.newOnly && !j.is_new) return false;
  if (f.contact && !j.contacts.length) return false;
  if (f.q) {
    const hay = [j.title, j.company, j.location, ...j.matched, ...(j.ai ? j.ai.must_haves : [])].join(" ").toLowerCase();
    if (!f.q.toLowerCase().split(/\s+/).filter(Boolean).every(w => hay.includes(w))) return false;
  }
  return true;
}

function sortJobs(list) {
  const byScore = (a, b) => (b.score || 0) - (a.score || 0);
  const byAge = (a, b) => (a.age_days ?? 999) - (b.age_days ?? 999);
  // Named cities A–Z first, then remote-in-India, then anything unrecognised.
  const rank = j => j.cities.length ? 0 : j.remote ? 1 : 2;
  const cmp = {
    best: (a, b) => byScore(a, b) || byAge(a, b),
    newest: (a, b) => byAge(a, b) || byScore(a, b),
    location: (a, b) => rank(a) - rank(b) || (a.cities[0] || a.location).localeCompare(b.cities[0] || b.location) || byScore(a, b),
    company: (a, b) => a.company.localeCompare(b.company) || byScore(a, b),
    years: (a, b) => (a.years ?? 9) - (b.years ?? 9) || byScore(a, b),
    added: (a, b) => (b.first_seen || "").localeCompare(a.first_seen || "") || byScore(a, b),
  }[S.f.sort] || byScore;
  return list.slice().sort(cmp);
}

function jobCard(j) {
  const st = j.status;
  const years = j.years == null ? "" : `<span class="badge rounded-pill ${j.years >= 2 ? "tag-warn" : "tag"}">${yearsText(j.years)}</span>`;
  return `<div class="card job-card mb-2 ${j.id === S.sel ? "active" : ""} ${st === "hidden" || j.ai_rejected ? "dim" : ""}" data-id="${j.id}" data-action="select" tabindex="0" role="button" aria-label="${esc(j.title)} at ${esc(j.company)}">
    <div class="card-body d-flex gap-3 p-3">
      ${avatar(j.company)}
      <div class="flex-grow-1 min-w-0">
        <div class="title">${esc(j.title)}</div>
        <div class="small text-body-secondary mt-1"><span class="fw-semibold text-body">${esc(j.company)}</span> · ${esc(place(j))}</div>
        <div class="d-flex flex-wrap gap-1 mt-2">
          ${j.is_new ? '<span class="badge rounded-pill tag-ink">NEW</span>' : ""}
          <span class="badge rounded-pill tag">${kindText(j.kind)}</span>
          ${j.level === "Intern" ? '<span class="badge rounded-pill tag-line">Internship</span>' : j.level === "New grad" ? '<span class="badge rounded-pill tag-line">New grad</span>' : ""}
          ${j.tier && j.tier !== "mid" ? `<span class="badge rounded-pill tier-${j.tier}">${esc(TIERS[j.tier].split(" (")[0])}</span>` : ""}
          ${years}
          ${j.remote ? `<span class="badge rounded-pill tag-line">${bi("house")} Remote</span>` : ""}
          ${j.contacts.length ? `<span class="badge rounded-pill tag">${bi("envelope")} Contact</span>` : ""}
          ${st && st !== "hidden" ? `<span class="badge rounded-pill tag-ink">${bi("check2")} ${esc(STATUS[st])}</span>` : ""}
          ${j.ai_rejected ? '<span class="badge rounded-pill tag-bad">AI: breaks your rules</span>' : ""}
        </div>
      </div>
      <div class="d-flex flex-column align-items-center gap-1">${ring(j.score, verdictOf(j), 50)}<span class="age">${postedShort(j.age_days)}</span></div>
    </div></div>`;
}

function renderJobs() {
  const st = S.state, app = $("#app");
  if (!st.profile.resumes.length) {
    app.innerHTML = `<div class="hero my-4 rise"><h1>Welcome to <span class="text-accent">JobQuest.AI</span></h1><p class="lead text-body-secondary">Entry-level SDE and ML jobs in India, ranked against your resume.</p></div>
      <div class="card text-center p-5 rise"><div class="display-5 text-accent mb-2">${bi("file-earmark-arrow-up")}</div><h3>Start with your resume</h3>
      <p class="text-body-secondary">Upload it once. JobQuest.AI searches about 80 company job boards and ranks each job against it.</p>
      <div><a class="btn btn-primary btn-lg" href="#/profile">${bi("upload")} Add your resume</a></div></div>`;
    return;
  }
  const f = S.f;
  const live = S.jobs.filter(j => j.active && !j.ai_rejected && (!j.status || j.status === "saved"));
  const cities = [...new Set(S.jobs.filter(j => j.active).flatMap(j => j.cities))].sort();
  const nRejected = S.jobs.filter(j => j.active && j.ai_rejected).length;
  const opt = (val, label, cur) => `<option value="${esc(val)}" ${String(val) === String(cur) ? "selected" : ""}>${esc(label)}</option>`;
  const sw = (key, label, ic = "") => `<div class="form-check form-switch m-0"><input class="form-check-input" type="checkbox" role="switch" id="f-${key}" data-f="${key}" ${f[key] ? "checked" : ""}><label class="form-check-label fw-medium" for="f-${key}">${ic ? bi(ic) + " " : ""}${label}</label></div>`;
  const stat = (n, label) => `<div class="card stat lift px-3 py-2"><div class="num">${n}</div><div class="small text-body-secondary fw-medium">${label}</div></div>`;
  const links = st.links.map(g => `<div class="d-flex flex-wrap align-items-center gap-2 mb-2"><span class="fw-bold me-2 role-label">${esc(g.role)}</span>` +
    g.links.map(l => `<a class="btn btn-sm btn-outline-secondary site-link rounded-pill" href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener noreferrer" title="${esc(l.note)}">${esc(l.site)}${l.note === "internships" ? " · internships" : ""} ${bi("box-arrow-up-right")}</a>`).join("") + `</div>`).join("");

  app.innerHTML = `${banners()}
    <div class="hero d-flex flex-wrap align-items-end justify-content-between gap-3 my-4 rise">
      <div><h1 class="mb-1">Jobs picked <span class="text-accent">for you</span></h1>
        <div class="text-body-secondary">India only · entry level, under 3 years · ranked against your resume</div></div>
      <div class="d-flex flex-wrap gap-2">${stat(live.length, "matching")}${stat(live.filter(j => j.is_new).length, "new today")}${stat(live.filter(j => j.ai && j.ai.verdict === "strong").length, "strong fits")}${stat(S.jobs.filter(j => j.status === "applied").length, "applied")}</div>
    </div>
    <div class="card mb-3 rise"><div class="card-body">
      <div class="input-group input-group-lg mb-3"><span class="input-group-text bg-transparent">${bi("search")}</span>
        <input id="q" type="search" class="form-control" placeholder="Search title, company or skill… e.g. python, Bengaluru, Razorpay" value="${esc(f.q)}" aria-label="Search"></div>
      <div class="row g-2 align-items-end">
        <div class="col-6 col-md-4 col-xl-2"><label class="form-label small fw-semibold text-body-secondary mb-1">Sort by</label><select class="form-select" data-f="sort">${SORTS.map(([v, l]) => opt(v, l, f.sort)).join("")}</select></div>
        <div class="col-6 col-md-4 col-xl-2"><label class="form-label small fw-semibold text-body-secondary mb-1">Role</label><select class="form-select" data-f="role">${opt("all", "All roles", f.role)}${opt("SDE", "SDE", f.role)}${opt("ML", "ML / AI", f.role)}</select></div>
        <div class="col-6 col-md-4 col-xl-2"><label class="form-label small fw-semibold text-body-secondary mb-1">Type</label><select class="form-select" data-f="type">${opt("all", "All types", f.type)}${opt("fulltime", "Full-time", f.type)}${opt("intern", "Internships", f.type)}</select></div>
        <div class="col-6 col-md-4 col-xl-2"><label class="form-label small fw-semibold text-body-secondary mb-1">Company</label><select class="form-select" data-f="tier">${opt("all", "All companies", f.tier)}${Object.entries(TIERS).map(([k, v]) => opt(k, v, f.tier)).join("")}</select></div>
        <div class="col-6 col-md-4 col-xl-2"><label class="form-label small fw-semibold text-body-secondary mb-1">Posted</label><select class="form-select" data-f="days">${opt(0, "Any time", f.days)}${opt(1, "Last 24 hours", f.days)}${opt(3, "Last 3 days", f.days)}${opt(7, "Last 7 days", f.days)}</select></div>
        <div class="col-12 col-md-4 col-xl-2"><label class="form-label small fw-semibold text-body-secondary mb-1">City</label><select class="form-select" data-f="city">${opt("", "All of India", f.city)}${opt("Remote", "Remote (India)", f.city)}${cities.map(c => opt(c, c, f.city)).join("")}</select></div>
      </div>
      <div class="d-flex flex-wrap align-items-center gap-3 mt-3">${sw("newOnly", "New today")}${sw("contact", "Has HR contact", "envelope")}${sw("showAll", `Show applied, removed &amp; AI-rejected${nRejected ? ` (${nRejected})` : ""}`)}
        <span class="ms-auto d-flex flex-wrap gap-2">${quick("type", "intern", `${bi("mortarboard")} Internships`, S.jobs.filter(j => j.active && j.level === "Intern" && !["hidden", ...TRACK].includes(j.status)).length)}
          ${Object.entries(TIERS).filter(([k]) => k !== "mid").map(([k, v]) => quick("tier", k, v.split(" (")[0], S.jobs.filter(j => j.active && j.tier === k && !["hidden", ...TRACK].includes(j.status)).length)).join("")}</span>
      </div>
    </div></div>
    <div class="accordion mb-3 rise" id="sites"><div class="accordion-item">
      <h2 class="accordion-header"><button class="accordion-button collapsed fw-semibold" type="button" data-bs-toggle="collapse" data-bs-target="#sites-body">${bi("link-45deg")}&nbsp; Also search LinkedIn, Naukri, Indeed and more, pre-filtered for you</button></h2>
      <div id="sites-body" class="accordion-collapse collapse" data-bs-parent="#sites"><div class="accordion-body">${links}
        <div class="small text-body-secondary">These sites don't allow automated searching, so each link opens your own browser already filtered to India, entry level and the last 24 hours.</div></div></div>
    </div></div>
    <div class="d-flex align-items-center gap-2 mb-2 fw-medium" id="count-line"></div>
    <div class="row g-3"><div class="col-lg-5 col-xxl-4" id="list"></div><div class="col-lg-7 col-xxl-8"><div class="detail-col" id="detail-col"></div></div></div>`;
  renderList();
}

function renderList() {
  const list = sortJobs(S.jobs.filter(visible));
  const sortLabel = (SORTS.find(s => s[0] === S.f.sort) || SORTS[0])[1];
  $("#count-line").innerHTML = `<span class="fs-5 fw-bold">${list.length}</span> job${list.length === 1 ? "" : "s"} <span class="text-body-secondary">· sorted by ${esc(sortLabel.toLowerCase())}</span>`;
  if (!list.length) {
    $("#list").innerHTML = `<div class="card text-center p-4 rise"><div class="fs-1 text-body-secondary">${bi("search")}</div><h5>Nothing matches these filters</h5><p class="text-body-secondary mb-0">Try "Any time", "All roles" or "All of India", or press <b>Find new jobs</b>.</p></div>`;
    $("#detail-col").innerHTML = "";
    return;
  }
  if (!list.some(j => j.id === S.sel)) S.sel = narrow() ? null : list[0].id;
  $("#list").innerHTML = list.map(jobCard).join("");
  stagger($("#list"), ".job-card");
  animate($("#list"));
  if (!narrow() && S.sel) showDetail(S.sel, $("#detail-col"));
}

function selectJob(id) {
  S.sel = id;
  $$(".job-card").forEach(el => el.classList.toggle("active", Number(el.dataset.id) === id));
  if (narrow()) openSheet(id); else showDetail(id, $("#detail-col"));
}

// ---------------------------------------------------------------- job detail

function formatDesc(text) {
  const lines = String(text || "").split(/\n+/).map(l => l.trim()).filter(Boolean);
  if (!lines.length) return "<p>No description was provided by the source.</p>";
  let out = "", inList = false;
  for (const l of lines) {
    const m = l.match(/^[•\-*·▪●◦]\s*(.+)$/);
    if (m) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${esc(m[1])}</li>`; continue; }
    if (inList) { out += "</ul>"; inList = false; }
    const heading = l.length < 70 && !/[.!?,;]$/.test(l) && l.split(/\s+/).length <= 8 && /^[A-Z]/.test(l);
    out += heading ? `<h6>${esc(l.replace(/:$/, ""))}</h6>` : `<p>${esc(l)}</p>`;
  }
  return out + (inList ? "</ul>" : "");
}

function detailHTML(j) {
  const ai = j.ai, v = verdictOf(j), st = j.status, url = esc(safeUrl(j.url));
  const reasons = ai && ai.reasons && ai.reasons.length ? ai.reasons : j.reasons;
  const missing = ai ? ai.missing_skills : j.missing;
  const resumeToSend = (ai && ai.best_resume) || (S.state.profile.resumes.length > 1 ? j.best_resume : "");
  const btn = (status, label, onLabel, ic) => `<button class="btn ${st === status ? "btn-success" : "btn-outline-secondary"}" data-action="status" data-status="${status}" data-id="${j.id}">${bi(ic)} ${st === status ? onLabel : label}</button>`;
  return `<div class="card detail" data-id="${j.id}"><div class="card-body p-4">
    <div class="d-flex gap-3 align-items-start">${avatar(j.company, "lg")}
      <div class="min-w-0"><div class="fw-semibold">${esc(j.company)} <span class="text-body-secondary fw-normal">· via ${esc(j.source)}</span></div>
        <h2 class="h3 my-1">${esc(j.title)}</h2>
        <div class="d-flex flex-wrap gap-3 text-body-secondary small fw-medium">
          <span>${bi("geo-alt")} ${esc(place(j))}${j.remote ? " (remote)" : ""}</span><span>${bi("clock")} ${postedLong(j.age_days)}</span><span>${bi("briefcase")} ${esc(j.level)} · ${yearsText(j.years)}</span></div>
      </div></div>
    <div class="d-flex flex-wrap gap-2 mt-4">
      <a class="btn btn-primary btn-lg" href="${url}" target="_blank" rel="noopener noreferrer">Apply on company site ${bi("box-arrow-up-right")}</a>
      ${btn("saved", "Save", "Saved", "bookmark")}${btn("applied", "Mark applied", "Applied", "check2-circle")}${btn("hidden", "Remove", "Removed", "x-circle")}
    </div>

    <div class="sec"><div class="sec-title">${bi("stars")} ${ai ? "AI match analysis" : "Keyword match"}</div>
      <div class="match-box p-3 d-flex flex-wrap gap-4 align-items-center">${ring(j.score, v, 100, true)}
        <div class="flex-grow-1 minw-14"><div class="fs-5 fw-bold text-capitalize">${ai ? esc(ai.verdict) + " fit" : "Keyword score"}</div>
          <div class="small text-body-secondary">${ai ? `AI ${ai.fit_score}/100 · keywords ${Math.round(j.keyword_score || 0)}/100` : "Add an OpenAI key in Settings for an AI verdict"}</div>
          <ul class="mt-2 mb-0">${reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul></div>
      </div>
      ${j.ai_rejected ? `<div class="alert alert-danger mt-3 mb-0">The AI thinks this one breaks your rules (3+ years, not entry level, or not in India).</div>` : ""}
      ${j.ai_error ? `<div class="alert alert-warning mt-3 mb-0">Last AI check failed: ${esc(j.ai_error)}</div>` : ""}
      ${S.state.ai.configured ? `<button class="btn btn-sm btn-outline-secondary mt-3" data-action="assess" data-id="${j.id}">${bi("stars")} ${ai ? "Ask AI again" : "Ask AI to check this job"}</button>` : ""}
      <button class="btn btn-sm btn-outline-secondary mt-3" data-action="cover-letter" data-id="${j.id}">${bi("envelope-paper")} Cover letter</button>
    </div>

    ${j.matched.length || (missing && missing.length) ? `<div class="sec"><div class="sec-title">${bi("lightning")} Skills</div>
      ${j.matched.length ? `<div>${j.matched.map(s => `<span class="chip have">${bi("check2")} ${esc(s)}</span>`).join("")}</div>` : ""}
      ${missing && missing.length ? `<div class="small text-body-secondary mt-1 mb-1">You don't show these yet:</div><div>${missing.map(s => `<span class="chip miss">${esc(s)}</span>`).join("")}</div>` : ""}</div>` : ""}

    <div class="sec"><div class="sec-title">${bi("info-circle")} At a glance</div><div class="row g-2">
      <div class="col-6 col-xl-3"><div class="fact"><small>Experience</small><b>${yearsText(j.years)}</b></div></div>
      <div class="col-6 col-xl-3"><div class="fact"><small>Role</small><b>${kindText(j.kind)} · ${esc(j.level)}</b></div></div>
      ${ai && ai.work_mode && ai.work_mode !== "unknown" ? `<div class="col-6 col-xl-3"><div class="fact"><small>Work mode</small><b class="text-capitalize">${esc(ai.work_mode)}</b></div></div>` : ""}
      ${resumeToSend ? `<div class="col-6 col-xl-3"><div class="fact"><small>Send resume</small><b>${esc(resumeToSend)}</b></div></div>` : ""}
    </div>
    ${ai && ai.must_haves && ai.must_haves.length ? `<div class="small text-body-secondary mt-3">What they require</div><ul class="mb-0">${ai.must_haves.map(m => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}</div>

    <div class="sec"><div class="sec-title">${bi("envelope")} HR / hiring contacts in this posting</div>
      ${j.contacts.length ? j.contacts.map(c => `<div class="contact-row d-flex flex-wrap align-items-center gap-3 p-3 mb-2 lift">
          <div class="me-auto"><div class="fw-semibold">${esc(c.name || "Contact")}</div><div class="small text-body-secondary">${esc(c.role || "listed in the job description")}</div></div>
          ${c.email ? `<a class="fw-semibold text-break" href="mailto:${esc(c.email)}">${esc(c.email)}</a><button class="btn btn-sm btn-outline-secondary" data-copy="${esc(c.email)}">${bi("clipboard")} Copy</button>
            <button class="btn btn-sm btn-ink" data-action="cold-email" data-id="${j.id}" data-email="${esc(c.email)}" data-name="${esc(c.name || "")}" data-role="${esc(c.role || "")}">${bi("stars")} Draft cold email</button>` : ""}
          ${c.phone ? `<span>${bi("telephone")} ${esc(c.phone)}</span>` : ""}</div>`).join("")
        : `<div class="text-body-secondary">This posting doesn't list a recruiter or HR email. Most company job boards don't, so apply through the button above.</div>`}
    </div>

    <div class="sec"><div class="sec-title">${bi("journal-text")} Your notes</div>
      <div class="row g-2"><div class="col-md-5"><select class="form-select" id="d-status">${["", ...TRACK, "hidden"].map(s => `<option value="${s}" ${s === st ? "selected" : ""}>${s ? STATUS[s] : "Not tracked"}</option>`).join("")}</select></div>
        <div class="col-12"><textarea class="form-control" id="d-notes" rows="3" placeholder="Referral, recruiter, interview dates…">${esc(j.notes)}</textarea></div>
        <div class="col-12"><button class="btn btn-ink" data-action="save-details" data-id="${j.id}">${bi("save")} Save notes</button></div></div>
    </div>

    <div class="sec"><div class="sec-title">${bi("file-text")} Job description</div><div class="desc">${formatDesc(j.description)}</div></div>
  </div></div>`;
}

async function showDetail(id, host) {
  if (!host) return;
  let j = S.details[id];
  if (!j) {
    host.innerHTML = `<div class="card"><div class="card-body p-4"><div class="skel w-50"></div><div class="skel"></div><div class="skel w-75"></div><div class="skel"></div></div></div>`;
    try { j = await api(`/api/jobs/${id}`); S.details[id] = j; }
    catch (e) { host.innerHTML = `<div class="alert alert-danger">Couldn't load this job: ${esc(e.message)}</div>`; return; }
  }
  if (host.id === "detail-col" && S.sel !== id) return;            // a different job was clicked meanwhile
  host.innerHTML = detailHTML(j);
  host.scrollTop = 0;
  animate(host);
}

function openSheet(id) {
  showDetail(id, $("#sheet-body"));
  bootstrap.Offcanvas.getOrCreateInstance($("#sheet")).show();
}
function hideSheet() {
  const oc = bootstrap.Offcanvas.getInstance($("#sheet"));
  if (oc) oc.hide();
}

function mergeJob(updated) {
  const i = S.jobs.findIndex(x => x.id === updated.id);
  if (i < 0) return;
  const { description, ...rest } = updated;
  S.jobs[i] = rest;
  if (description !== undefined) S.details[updated.id] = updated; else delete S.details[updated.id];
}

function refreshViewsAfterChange(id) {
  renderShell();
  if (currentView() === "jobs" && $("#list")) {
    renderList();
    if ($("#sheet").classList.contains("show")) showDetail(id, $("#sheet-body"));
  } else route();
}

async function setStatus(id, status) {
  const j = S.jobs.find(x => x.id === id);
  const next = j && j.status === status ? "" : status;
  try {
    mergeJob(await api(`/api/jobs/${id}`, { method: "PATCH", json: { status: next } }));
    refreshViewsAfterChange(id);
    toast(next ? `Marked ${STATUS[next].toLowerCase()}` : "Status cleared", 2200);
  } catch (e) { toast(e.message); }
}

// ---------------------------------------------------------------- tracker

function trackerRows() {
  return S.jobs.filter(j => TRACK.includes(j.status));
}
const appliedDate = j => (j.applied_on || (j.status_at || "").slice(0, 10) || "");
function daysSince(iso) {
  if (!iso) return null;
  const d = new Date(iso.slice(0, 10) + "T00:00:00");
  return Math.max(0, Math.floor((Date.now() - d.getTime()) / 86400000));
}
const fmtDate = iso => iso ? new Date(iso.slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "2-digit" }) : "";

const TCOLS = [
  ["applied", "Applied", j => appliedDate(j)],
  ["company", "Company", j => j.company.toLowerCase()],
  ["role", "Role", j => j.title.toLowerCase()],
  ["type", "Type", j => (j.job_type || "").toLowerCase()],
  ["location", "Location", j => (j.location || "").toLowerCase()],
  ["source", "Source", j => (j.app_source || "").toLowerCase()],
  ["status", "Status", j => TRACK.indexOf(j.status)],
  ["days", "Days", j => daysSince(appliedDate(j)) ?? 9999],
  ["update", "Last update", j => j.last_update || ""],
  ["next", "Next step", j => (j.next_step || "").toLowerCase()],
  ["contact", "Contact", j => (j.contact || "").toLowerCase()],
  ["notes", "Notes", j => (j.notes || "").toLowerCase()],
];

function trackerFiltered() {
  const t = S.t, q = t.q.trim().toLowerCase();
  let rows = trackerRows().filter(j => t.status === "all" || j.status === t.status);
  if (q) rows = rows.filter(j => [j.company, j.title, j.location, j.app_source, j.job_type, j.notes, j.next_step, j.contact]
    .join(" ").toLowerCase().includes(q));
  const col = TCOLS.find(c => c[0] === t.sort) || TCOLS[0];
  return rows.sort((a, b) => { const x = col[2](a), y = col[2](b); return (x < y ? -1 : x > y ? 1 : 0) * t.dir || b.id - a.id; });
}

function renderTracker() {
  const all = trackerRows();
  const count = s => all.filter(j => j.status === s).length;
  const today = new Date().toISOString().slice(0, 10);
  const t = S.t;
  const chip = (val, label, n) => `<button class="btn btn-sm ${t.status === val ? "btn-ink" : "btn-outline-secondary"} rounded-pill" data-tstatus="${val}">${esc(label)} <span class="opacity-75">${n}</span></button>`;
  $("#app").innerHTML = `<div class="d-flex flex-wrap align-items-end justify-content-between gap-3 mt-4 mb-3 rise">
      <div><h1 class="h3 mb-1">Application tracker</h1><div class="text-body-secondary small">${all.length} applications · ${count("screening") + count("interviewing") + count("oa")} in progress · ${count("offer")} offers</div></div>
      <div class="d-flex flex-wrap gap-2">
        <button class="btn btn-sm btn-ink" data-bs-toggle="collapse" data-bs-target="#add-app">${bi("plus-lg")} Add</button>
        <label class="btn btn-sm btn-outline-secondary mb-0" for="import-file">${bi("file-earmark-spreadsheet")} Import Excel / CSV</label>
        <input type="file" id="import-file" class="d-none" accept=".xlsx,.csv">
        <button class="btn btn-sm btn-outline-secondary" data-action="export-csv">${bi("filetype-csv")} Export CSV</button>
        ${S.state.mail && S.state.mail.connected ? `<button class="btn btn-sm ${S.state.mail.pending ? "btn-ink" : "btn-outline-secondary"}" data-action="mail-open">${bi("envelope-check")} Email updates${S.state.mail.pending ? ` <span class="badge text-bg-light">${S.state.mail.pending}</span>` : ""}</button>` : ""}
        <button class="btn btn-sm btn-outline-secondary" data-action="backup-now">${bi("shield-check")} Back up</button>
        <a class="btn btn-sm btn-outline-secondary" href="/api/tracker/template.csv">${bi("download")} Template</a>
        <div class="btn-group btn-group-sm" role="group" aria-label="View">
          <button class="btn ${t.view === "table" ? "btn-ink" : "btn-outline-secondary"}" data-tview="table">${bi("table")} Table</button>
          <button class="btn ${t.view === "board" ? "btn-ink" : "btn-outline-secondary"}" data-tview="board">${bi("kanban")} Board</button>
          <a class="btn btn-outline-secondary" href="#/dashboard">${bi("bar-chart-line")} Dashboard</a>
        </div>
      </div></div>
    <div class="collapse mb-3" id="add-app"><div class="card"><div class="card-body p-3">
      <div class="row g-2 align-items-end">
        <div class="col-md-3"><label class="form-label small fw-semibold mb-1" for="m-company">Company</label><input class="form-control form-control-sm" id="m-company"></div>
        <div class="col-md-3"><label class="form-label small fw-semibold mb-1" for="m-role">Role</label><input class="form-control form-control-sm" id="m-role"></div>
        <div class="col-md-2"><label class="form-label small fw-semibold mb-1" for="m-date">Date applied</label><input type="date" class="form-control form-control-sm" id="m-date" value="${today}"></div>
        <div class="col-md-2"><label class="form-label small fw-semibold mb-1" for="m-status">Status</label><select class="form-select form-select-sm" id="m-status">${TRACK.map(s => `<option value="${s}" ${s === "applied" ? "selected" : ""}>${STATUS[s]}</option>`).join("")}</select></div>
        <div class="col-md-2"><label class="form-label small fw-semibold mb-1" for="m-type">Job type</label><input class="form-control form-control-sm" id="m-type" placeholder="Full-time"></div>
        <div class="col-md-2"><input class="form-control form-control-sm" id="m-location" placeholder="Location"></div>
        <div class="col-md-2"><input class="form-control form-control-sm" id="m-source" placeholder="Source (LinkedIn, Referral…)"></div>
        <div class="col-md-3"><input class="form-control form-control-sm" id="m-url" placeholder="Job link"></div>
        <div class="col-md-3"><input class="form-control form-control-sm" id="m-contact" placeholder="Contact"></div>
        <div class="col-md-2"><button class="btn btn-sm btn-ink w-100" data-action="add-app">${bi("check2")} Add</button></div>
      </div>
      <div class="small text-body-secondary mt-2">Import reads headings like Date Applied, Company, Role, Job Type, Location, Source, Job Link, Status, Last Update, Next Step, Contact, Notes. Missing cells stay blank. Every row is kept, even repeats; possible repeats are flagged so you can delete them.</div>
    </div></div></div>
    <div class="d-flex flex-wrap gap-2 align-items-center mb-2">
      <div class="input-group input-group-sm tracker-search"><span class="input-group-text">${bi("search")}</span><input type="search" class="form-control" id="tq" placeholder="Search company, role, notes…" value="${esc(t.q)}"></div>
      ${chip("all", "All", all.length)}${TRACK.filter(s => count(s) || ["applied", "screening", "oa", "interviewing", "offer", "rejected"].includes(s)).map(s => chip(s, STATUS[s], count(s))).join("")}
    </div>
    <div id="tracker-body"></div>
    <div class="modal fade" id="edit-modal" tabindex="-1" aria-hidden="true"><div class="modal-dialog modal-dialog-centered"><div class="modal-content"><div class="modal-body" id="edit-body"></div></div></div></div>`;
  $("#import-file").addEventListener("change", importTracker);
  renderTrackerBody();
}

function renderTrackerBody() {
  const rows = trackerFiltered(), t = S.t, host = $("#tracker-body");
  if (!trackerRows().length) {
    host.innerHTML = `<div class="card text-center p-4"><h5>No applications yet</h5><p class="text-body-secondary mb-0">Mark jobs as applied on the Jobs page, add one above, or import your Excel sheet.</p></div>`;
    return;
  }
  if (t.view === "board") {
    host.innerHTML = `<div class="board">${TRACK.map(s => {
      const items = rows.filter(j => j.status === s);
      return `<div class="board-col p-2"><div class="d-flex justify-content-between px-1 mb-1 small fw-bold text-uppercase"><span>${STATUS[s]}</span><span class="text-body-secondary">${items.length}</span></div>
        ${items.map(j => `<div class="bcard" data-id="${j.id}" data-action="edit-app" title="Click to edit"><div class="fw-semibold text-truncate">${esc(j.company)}</div><div class="small text-body-secondary text-truncate">${esc(j.title)} · ${fmtDate(appliedDate(j))}</div></div>`).join("") || `<div class="small text-body-secondary px-1">—</div>`}</div>`;
    }).join("")}</div>`;
    return;
  }
  const arrow = k => t.sort === k ? (t.dir > 0 ? " ▲" : " ▼") : "";
  const ids = new Set(rows.map(j => j.id));
  for (const id of S.picked) if (!ids.has(id)) S.picked.delete(id);
  const n = S.picked.size, allOn = n && n === rows.length;
  host.innerHTML = `<div class="bulk-bar card mb-2 ${n ? "" : "d-none"}"><div class="card-body py-2 px-3 d-flex flex-wrap align-items-center gap-2">
      <span class="fw-semibold">${n} selected</span>
      <select class="form-select form-select-sm w-auto" id="bulk-status" aria-label="Set status for selected"><option value="">Set status…</option>${TRACK.map(o => `<option value="${o}">${STATUS[o]}</option>`).join("")}</select>
      <button class="btn btn-sm btn-outline-danger" data-action="delete-picked">${bi("trash")} Delete</button>
      <button class="btn btn-sm btn-link" data-action="clear-picked">Clear</button></div></div>
    <div class="card"><div class="table-responsive tracker-wrap"><table class="table table-sm table-hover align-middle mb-0 tracker-table">
    <thead><tr><th class="w-pick"><input type="checkbox" class="form-check-input" id="pick-all" aria-label="Select all shown" ${allOn ? "checked" : ""}></th>${TCOLS.map(([k, label]) => `<th scope="col" data-tsort="${k}" class="${k === "notes" ? "w-notes" : ""}">${label}${arrow(k)}</th>`).join("")}<th></th></tr></thead>
    <tbody>${rows.map(j => {
      const d = daysSince(appliedDate(j));
      return `<tr data-id="${j.id}" class="${S.picked.has(j.id) ? "table-active" : ""}">
        <td class="w-pick"><input type="checkbox" class="form-check-input" data-pick ${S.picked.has(j.id) ? "checked" : ""} aria-label="Select"></td>
        <td class="text-nowrap">${fmtDate(appliedDate(j))}</td>
        <td class="fw-semibold">${esc(j.company)}</td>
        <td>${j.url ? `<a href="${esc(safeUrl(j.url))}" target="_blank" rel="noopener noreferrer" class="link-body-emphasis">${esc(j.title)} ${bi("box-arrow-up-right", "small")}</a>` : esc(j.title)}</td>
        <td class="text-nowrap">${esc(j.job_type)}</td>
        <td>${esc(j.location || (j.cities || []).join(", "))}</td>
        <td class="text-nowrap">${esc(j.app_source || (j.source !== "Added by you" ? "JobQuest.AI" : ""))}</td>
        <td><select class="form-select form-select-sm status-sel st-${j.status}" data-action="track-status" aria-label="Status">${TRACK.map(o => `<option value="${o}" ${o === j.status ? "selected" : ""}>${STATUS[o]}</option>`).join("")}</select></td>
        <td class="text-end ${d != null && d >= 14 && j.status === "applied" ? "text-warning-emphasis fw-semibold" : ""}">${d ?? ""}</td>
        <td class="text-nowrap">${fmtDate(j.last_update)}</td>
        <td>${esc(j.next_step)}</td>
        <td>${esc(j.contact)}</td>
        <td class="w-notes text-truncate" title="${esc(j.notes)}">${esc(j.notes)}</td>
        <td class="text-nowrap"><button class="btn btn-sm btn-link p-0 me-2" data-action="edit-app" aria-label="Edit">${bi("pencil")}</button><button class="btn btn-sm btn-link link-danger p-0" data-action="delete-app" aria-label="Delete">${bi("trash")}</button></td>
      </tr>`;
    }).join("") || `<tr><td colspan="14" class="text-center text-body-secondary py-4">Nothing matches.</td></tr>`}</tbody></table></div></div>
    <div class="small text-body-secondary mt-2">${rows.length} shown · click a heading to sort · "Days" turns amber after 14 days without a reply</div>`;
}

const EXPORT_STATUS = { saved: "Saved", applied: "Applied", screening: "Screening call", oa: "OA / Test", interviewing: "Interview", offer: "Offer",
  rejected: "Rejected", ghosted: "Ghosted", withdrawn: "Withdrawn" };

function csvCell(v) {
  let s = String(v ?? "");
  if (/^[=+@-]/.test(s)) s = "'" + s;                      // stop Excel treating text as a formula
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function exportCsv() {
  const rows = trackerFiltered();
  const head = ["Date Applied", "Company", "Role", "Job Type", "Location", "Source", "Job Link", "Status", "Last Update",
    "Days Since Applied", "Next Step", "Contact", "Notes"];
  const lines = [head.join(",")].concat(rows.map(j => [appliedDate(j), j.company, j.title, j.job_type,
    j.location || (j.cities || []).join(", "), j.app_source, j.url, EXPORT_STATUS[j.status] || j.status,
    (j.last_update || "").slice(0, 10), daysSince(appliedDate(j)) ?? "", j.next_step, j.contact, j.notes].map(csvCell).join(",")));
  const blob = new Blob(["\ufeff" + lines.join("\r\n") + "\r\n"], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `jobquest-tracker-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  toast(`Exported ${rows.length} application${rows.length === 1 ? "" : "s"}`, 3000);
}

async function backupNow() {
  try { const b = await api("/api/backups", { method: "POST" }); toast(`Backed up: ${b.counts.tracked} applications, ${b.counts.resumes} resume(s) → ${b.name}`, 6000); if (currentView() === "settings") renderSettings(); }
  catch (e) { toast(e.message, 6000); }
}

async function restoreFrom(name, file) {
  const what = name ? `the backup from ${name.replace(/^jobhuntai-|\.zip$/g, "")}` : `the file ${file.name}`;
  if (!confirm(`Replace everything in JobQuest.AI with ${what}?\n\nYour current data is backed up first, so you can undo this.`)) return;
  try {
    let r;
    if (name) r = await api(`/api/backups/${encodeURIComponent(name)}/restore`, { method: "POST" });
    else { const form = new FormData(); form.append("file", file); r = await api("/api/backups/restore-upload", { method: "POST", form }); }
    toast(`Restored: ${r.restored.tracked} applications, ${r.restored.resumes} resume(s).`, 6000);
    await loadAll();
  } catch (e) { toast(e.message, 8000); }
}

async function renderBackups() {
  const host = $("#backup-list");
  if (!host) return;
  try {
    const r = await api("/api/backups");
    $("#backup-folder").textContent = r.folder;
    host.innerHTML = r.backups.length ? `<table class="table table-sm align-middle mb-0 tracker-table"><thead><tr><th>When</th><th>Type</th><th>Applications</th><th>Size</th><th></th></tr></thead><tbody>${r.backups.map(b => `<tr>
      <td class="text-nowrap">${b.created ? new Date(b.created).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : esc(b.name)}</td>
      <td>${esc({ manual: "Manual", auto: "Daily", "pre-restore": "Before restore" }[b.kind] || b.kind)}</td>
      <td>${b.counts.tracked ?? "?"}</td><td>${b.size_kb} KB</td>
      <td class="text-end text-nowrap"><a class="btn btn-sm btn-outline-secondary" href="/api/backups/${encodeURIComponent(b.name)}">${bi("download")}</a>
        <button class="btn btn-sm btn-outline-secondary" data-action="restore-backup" data-name="${esc(b.name)}">Restore</button></td></tr>`).join("")}</tbody></table>`
      : `<div class="text-body-secondary small">No backups yet.</div>`;
  } catch (e) { host.textContent = e.message; }
}

const EDIT_FIELDS = [["company", "Company", 6], ["title", "Role", 6], ["applied_on", "Date applied", 4, "date"],
  ["job_type", "Job type", 4], ["location", "Location", 4], ["app_source", "Source", 4], ["url", "Job link", 8],
  ["last_update", "Last update", 4, "date"], ["next_step", "Next step", 6], ["contact", "Contact", 6]];

function openEdit(id) {
  const j = S.jobs.find(x => x.id === id);
  if (!j) return;
  const val = k => k === "applied_on" ? appliedDate(j) : k === "last_update" ? (j.last_update || "").slice(0, 10) : (j[k] || "");
  $("#edit-body").innerHTML = `<h5 class="mb-3">Edit application</h5>
    <div class="row g-2">
      ${EDIT_FIELDS.map(([k, label, w, type]) => `<div class="col-md-${w}"><label class="form-label small fw-semibold mb-1" for="e-${k}">${label}</label><input type="${type || "text"}" class="form-control form-control-sm" id="e-${k}" value="${esc(val(k))}"></div>`).join("")}
      <div class="col-md-4"><label class="form-label small fw-semibold mb-1" for="e-status">Status</label><select class="form-select form-select-sm" id="e-status">${TRACK.map(o => `<option value="${o}" ${o === j.status ? "selected" : ""}>${STATUS[o]}</option>`).join("")}</select></div>
      <div class="col-12"><label class="form-label small fw-semibold mb-1" for="e-notes">Notes</label><textarea class="form-control form-control-sm" id="e-notes" rows="3">${esc(j.notes)}</textarea></div>
    </div>
    <div class="d-flex gap-2 mt-3"><button class="btn btn-sm btn-outline-danger" data-action="delete-app" data-id="${j.id}">${bi("trash")} Delete</button>
      <button class="btn btn-sm btn-outline-secondary me-auto" data-action="cover-letter" data-id="${j.id}">${bi("envelope-paper")} Cover letter</button>
      <button class="btn btn-sm btn-outline-secondary" data-bs-dismiss="modal">Cancel</button><button class="btn btn-sm btn-ink" data-action="save-edit" data-id="${j.id}">Save</button></div>`;
  bootstrap.Modal.getOrCreateInstance($("#edit-modal")).show();
}

async function saveEdit(id) {
  const body = { status: $("#e-status").value, notes: $("#e-notes").value };
  for (const [k] of EDIT_FIELDS) body[k] = $(`#e-${k}`).value;
  try {
    mergeJob(await api(`/api/jobs/${id}`, { method: "PATCH", json: body }));
    bootstrap.Modal.getOrCreateInstance($("#edit-modal")).hide();
    renderShell(); renderTrackerBody(); toast("Saved.");
  } catch (e) { toast(e.message, 6000); }
}

async function deleteEntries(ids) {
  if (!ids.length) return;
  try {
    const r = await api("/api/tracker/delete", { method: "POST", json: { ids } });
    bootstrap.Modal.getInstance($("#edit-modal"))?.hide();
    S.picked.clear();
    await loadAll();
    toast(`Deleted ${r.deleted} application${r.deleted === 1 ? "" : "s"}`, 9000, { label: "Undo", run: async () => {
      try { await api("/api/tracker/undo", { method: "POST", json: { rows: r.undo } }); await loadAll(); toast("Restored.", 2500); }
      catch (e) { toast(e.message, 6000); }
    } });
  } catch (e) { toast(e.message, 6000); }
}

async function bulkStatus(status) {
  const ids = [...S.picked];
  try {
    for (const id of ids) mergeJob(await api(`/api/jobs/${id}`, { method: "PATCH", json: { status } }));
    S.picked.clear(); renderShell(); renderTracker();
    toast(`Moved ${ids.length} to ${STATUS[status]}`, 2500);
  } catch (e) { toast(e.message, 6000); }
}

async function addApplication() {
  const body = { company: $("#m-company").value.trim(), role: $("#m-role").value.trim(), date: $("#m-date").value,
    location: $("#m-location").value.trim(), url: $("#m-url").value.trim(), status: $("#m-status").value,
    job_type: $("#m-type").value.trim(), app_source: $("#m-source").value.trim(), contact: $("#m-contact").value.trim() };
  if (!body.company || !body.role) { toast("Company and role are both needed."); return; }
  try {
    const r = await api("/api/tracker", { method: "POST", json: body });
    await loadAll();
    toast(r.possible_duplicate ? `Added. Looks similar to: ${r.possible_duplicate}` : `Added ${body.role} at ${body.company}`, r.possible_duplicate ? 8000 : 3000);
  } catch (e) { toast(e.message, 6000); }
}

async function importTracker() {
  const f = $("#import-file").files[0];
  if (!f) return;
  const form = new FormData(); form.append("file", f);
  try {
    const r = await api("/api/tracker/import", { method: "POST", form });
    const dup = r.possible_duplicates ? ` · ${r.possible_duplicates} look like repeats (kept; tick and delete any you don't want)` : "";
    toast(`Imported ${r.added}${dup}${r.problems.length ? ` · ${r.problems.length} row note(s): ${r.problems.slice(0, 2).join("; ")}` : ""}`, 9000);
    await loadAll();
  } catch (e) { toast(e.message, 8000); }
}

// ---------------------------------------------------------------- profile

function renderProfile() {
  const p = S.state.profile, prefs = p.preferences;
  const resumes = p.resumes.map(r => `<div class="card lift mt-3"><div class="card-body">
      <div class="d-flex flex-wrap justify-content-between gap-2 align-items-baseline"><h5 class="mb-0">${esc(r.label)}</h5>
        ${r.ai_read ? '<span class="badge rounded-pill tag-ink">✓ Read by AI</span>' : `<span class="badge rounded-pill tag-line">${S.state.ai.configured ? "AI reads it on the next search" : "Keyword parsing only"}</span>`}</div>
      <div class="small text-body-secondary mb-2">${esc(r.filename)}</div>
      ${r.headline ? `<div class="fw-semibold">${esc(r.headline)}</div>` : ""}
      <div class="small mt-1">${r.education.map(e => esc([e.degree, e.field].filter(Boolean).join(" in ") + (e.school ? ", " + e.school : "") + (e.end ? ` (${e.end})` : ""))).join("<br>")}</div>
      <div class="small text-body-secondary my-2">${r.fulltime_months} months full-time · ${r.internship_months} months internships · aimed at ${esc(r.roles.map(kindText).join(" + "))}</div>
      <div>${r.skills.map(s => `<span class="chip plain">${esc(s)}</span>`).join("")}</div>
      ${r.strengths.length ? `<ul class="small mt-2">${r.strengths.map(s => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}
      <button class="btn btn-sm btn-outline-danger mt-2" data-action="delete-resume" data-id="${r.id}">${bi("trash")} Remove</button>
    </div></div>`).join("");
  const pick = (name, value, label, checked, id = "") => `<input type="checkbox" class="btn-check" id="${id || name + "-" + value.replace(/\s/g, "")}" name="${name}" value="${esc(value)}" autocomplete="off" ${checked ? "checked" : ""}><label class="btn btn-sm btn-outline-secondary rounded-pill" for="${id || name + "-" + value.replace(/\s/g, "")}">${esc(label)}</label>`;
  $("#app").innerHTML = `<div class="hero my-4 rise"><h1 class="mb-1">Your <span class="text-accent">profile</span></h1><div class="text-body-secondary">${esc(p.name || "Add a resume to begin")}${p.email ? " · " + esc(p.email) : ""}</div></div>
    <div class="row g-4">
      <div class="col-lg-7"><div class="card rise"><div class="card-body p-4">
        <h4>Resumes</h4><p class="text-body-secondary">Add each version you use, like one for SDE and one for ML. Every job is matched against all of them, and the job page names the one to send.</p>
        <label class="drop d-grid text-center p-4 gap-1" id="drop" for="resume-file"><span class="fs-2 text-accent">${bi("cloud-arrow-up")}</span><span class="fw-semibold">Drop a resume here or click to choose</span><span class="small text-body-secondary">PDF or DOCX, up to 5 MB</span></label>
        <input type="file" id="resume-file" class="d-none" accept=".pdf,.docx,.txt">
        <div class="row g-2 mt-2 align-items-center"><div class="col-sm-7"><input type="text" class="form-control" id="resume-label" placeholder="Name it (optional), e.g. SDE resume"></div>
          <div class="col-sm-5"><button class="btn btn-primary w-100" data-action="upload">${bi("upload")} Upload resume</button></div></div>
        <div class="small text-body-secondary mt-1" id="upload-msg"></div>
        ${resumes}
      </div></div></div>
      <div class="col-lg-5"><div class="card rise"><div class="card-body p-4">
        <h4>About you</h4><p class="text-body-secondary">The AI uses this when it judges each job.</p>
        <div class="row g-3">
          <div class="col-sm-6"><label class="form-label fw-semibold" for="p-city">Current city</label><select class="form-select" id="p-city">${["", ...CITIES].map(c => `<option value="${esc(c)}" ${c === prefs.current_city ? "selected" : ""}>${c || "Choose your city"}</option>`).join("")}</select></div>
          <div class="col-sm-6"><label class="form-label fw-semibold" for="p-phone">Phone</label><input type="tel" class="form-control" id="p-phone" value="${esc(prefs.phone)}" placeholder="+91 …"></div>
          ${p.resume_location ? `<div class="col-12 small text-body-secondary mt-1">Your resume still says ${esc(p.resume_location)}. The city above is what the AI uses.</div>` : ""}
          <div class="col-12"><div class="form-label fw-semibold">Cities you'd work in</div><div class="d-flex flex-wrap gap-2">${CITIES.map(c => pick("p-pref", c, c, prefs.preferred_cities.includes(c))).join("")}</div></div>
          <div class="col-12"><div class="form-label fw-semibold">Roles to search</div><div class="d-flex flex-wrap gap-2">
            ${pick("p-role", "SDE", "SDE / software engineer", prefs.roles.includes("SDE"))}${pick("p-role", "ML", "ML / AI / data science", prefs.roles.includes("ML"))}</div></div>
          <div class="col-12"><div class="form-check form-switch"><input class="form-check-input" type="checkbox" role="switch" id="p-intern" ${prefs.include_internships ? "checked" : ""}><label class="form-check-label" for="p-intern">Include internships</label></div></div>
          <div class="col-12"><label class="form-label fw-semibold" for="p-about">Note for the AI</label><textarea class="form-control" id="p-about" rows="3" placeholder="e.g. Back in India after a Master's in the US, can join immediately">${esc(prefs.about)}</textarea></div>
          <div class="col-12 d-flex flex-wrap align-items-center gap-2"><button class="btn btn-ink" data-action="save-profile">${bi("check2")} Save</button><span class="small text-body-secondary">Role and internship changes apply on the next search.</span></div>
        </div>
      </div></div></div>
    </div>`;
  const drop = $("#drop");
  ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, () => drop.classList.remove("over")));
  drop.addEventListener("drop", e => { e.preventDefault(); if (e.dataTransfer.files[0]) { $("#resume-file").files = e.dataTransfer.files; uploadResume(); } });
  $("#resume-file").addEventListener("change", () => { const f = $("#resume-file").files[0]; if (f) $("#upload-msg").textContent = `Selected: ${f.name}`; });
}

async function uploadResume() {
  const file = $("#resume-file").files[0];
  if (!file) { toast("Choose a resume file first."); return; }
  const form = new FormData();
  form.append("file", file); form.append("label", $("#resume-label").value);
  $("#upload-msg").textContent = S.state.ai.configured ? "Reading it with AI…" : "Reading it…";
  try {
    const r = await api("/api/resumes", { method: "POST", form });
    S.state.profile = r.profile;
    toast(r.ai_error ? `Added. The AI couldn't read it: ${r.ai_error}` : "Resume added. Press Find new jobs to search with it.", 6000);
    renderProfile();
  } catch (e) { $("#upload-msg").textContent = ""; toast(e.message, 6000); }
}

async function saveProfile() {
  const checked = name => $$(`input[name="${name}"]:checked`).map(i => i.value);
  const body = { current_city: $("#p-city").value, phone: $("#p-phone").value.trim(), preferred_cities: checked("p-pref"),
    roles: checked("p-role"), include_internships: $("#p-intern").checked, about: $("#p-about").value.trim() };
  if (!body.roles.length) { toast("Pick at least one role."); return; }
  try { const r = await api("/api/profile", { method: "PUT", json: body }); S.state.profile = r.profile; toast("Saved."); }
  catch (e) { toast(e.message); }
}

// ---------------------------------------------------------------- settings

function renderSettings() {
  const st = S.state, a = st.ai, lr = st.last_run, keys = st.optional_keys;
  const failed = lr ? Object.entries(lr.sources).filter(([, v]) => typeof v !== "number") : [];
  const tile = (n, label) => `<div class="col-6 col-md-3"><div class="fact tile lift"><div class="num">${n}</div><small>${label}</small></div></div>`;
  $("#app").innerHTML = `<div class="hero my-4 rise"><h1 class="mb-1"><span class="text-accent">Settings</span></h1><div class="text-body-secondary">AI providers, email, backups, the last search and extra job sources.</div></div>
    <div class="row g-4">
      <div class="col-lg-6">
        <div class="card rise mb-4"><div class="card-body p-4">
          <h4 class="mb-1">AI providers</h4>
          <p class="text-body-secondary small">Add a key for any one (or several). JobQuest.AI uses the first ready provider and falls back down the list. Keys stay on this PC in your .jobhuntai folder, never in OneDrive or git.</p>
          <div class="row g-2 mb-3">${tile("$" + a.spend.usd.toFixed(3), "OpenAI spend")}${tile(a.spend.checks, "AI job checks")}${tile(a.configured ? esc(a.provider.split(" (")[0]) : "None", "in use now")}</div>
          <div id="providers" class="vstack gap-2"><div class="text-body-secondary small">Loading…</div></div>
        </div></div>
        <div class="card rise"><div class="card-body p-4">
          <h4>More job sources (free keys)</h4><p class="text-body-secondary">These add many more fresh postings every day, including ones that also appear on LinkedIn and Naukri. Paste a key, save, then press Find new jobs.</p>
          ${[["jsearch_rapidapi_key", "JSearch (Google for Jobs)", "rapidapi.com → search JSearch → subscribe to the free Basic plan → copy X-RapidAPI-Key. Best single source."],
             ["adzuna_app_id", "Adzuna app ID", "developer.adzuna.com → sign up → create an app."], ["adzuna_app_key", "Adzuna app key", ""],
             ["jooble_key", "Jooble key", "jooble.org/api/about → request a key."]].map(([k, label, hint]) => `
            <label class="form-label fw-semibold mt-2" for="k-${k}">${label} ${keys[k] ? '<span class="badge text-bg-success">on</span>' : ""}</label>
            <input type="password" class="form-control" id="k-${k}" data-key="${k}" autocomplete="off" placeholder="${keys[k] ? "saved, leave blank to keep" : "paste key"}">
            ${hint ? `<div class="form-text">${hint}</div>` : ""}`).join("")}
          <button class="btn btn-ink mt-3" data-action="save-source-keys">${bi("check2")} Save keys</button>
        </div></div>
      </div>
      <div class="col-lg-6"><div class="card rise mb-4"><div class="card-body p-4">
        <h4 class="mb-1">Email (Gmail)</h4>
        ${st.mail.connected ? `<p class="mb-2"><span class="badge rounded-pill text-bg-success">✓ Connected</span> <b class="ms-1">${esc(st.mail.address)}</b>${st.mail.last_sync ? ` <span class="small text-body-secondary">· checked ${ago(st.mail.last_sync)}</span>` : ""}</p>
          <p class="small text-body-secondary">Every 12 hours JobQuest.AI reads recent job emails (read-only) and suggests tracker updates such as "Rejected" or "OA". Nothing changes until you approve. Emails are sent only when you press Send.</p>
          <div class="d-flex flex-wrap gap-2"><button class="btn btn-sm btn-ink" data-action="mail-sync">${bi("arrow-repeat")} Check email now</button><button class="btn btn-sm btn-outline-danger" data-action="mail-disconnect">Disconnect</button></div>`
        : `<p class="small text-body-secondary">Connect your Gmail to (1) get suggested tracker updates from recruiter emails and (2) send cold emails from the app. Uses a Google <b>app password</b>: turn on 2-Step Verification, then create one at <span class="user-select-all">myaccount.google.com/apppasswords</span>. It's stored only on this PC.</p>
          <div class="row g-2"><div class="col-sm-6"><input type="email" class="form-control form-control-sm" id="mail-addr" placeholder="you@gmail.com" autocomplete="off"></div>
            <div class="col-sm-6"><input type="password" class="form-control form-control-sm" id="mail-pw" placeholder="16-letter app password" autocomplete="off"></div>
            <div class="col-12"><button class="btn btn-sm btn-ink" data-action="mail-connect">${bi("plug")} Connect and test</button></div>
            <div class="col-12"><div id="mail-err" class="alert alert-danger py-2 small mb-0 d-none" role="alert"></div></div></div>`}
      </div></div>
      <div class="card rise mb-4"><div class="card-body p-4">
        <h4>Backup &amp; restore</h4>
        <p class="text-body-secondary small mb-2">Backs up your tracker, saved jobs, profile, resumes and settings (never your API keys). A daily backup is made automatically and the last 7 are kept, in <code id="backup-folder"></code> (outside OneDrive).</p>
        <div class="d-flex flex-wrap gap-2 mb-3"><button class="btn btn-sm btn-ink" data-action="backup-now">${bi("shield-check")} Back up now</button>
          <label class="btn btn-sm btn-outline-secondary mb-0" for="restore-file">${bi("upload")} Restore from a file…</label><input type="file" id="restore-file" class="d-none" accept=".zip"></div>
        <div id="backup-list" class="small"></div>
      </div></div>
      <div class="card rise"><div class="card-body p-4">
        <h4>Last search</h4>
        ${lr ? `<p class="text-body-secondary">Finished ${new Date(lr.finished).toLocaleString()}</p>
          <div class="row g-2 mb-3">${tile(lr.raw.toLocaleString(), "postings checked")}${tile(lr.kept, "India entry-level")}${tile(lr.new, "new")}${tile(lr.ai_checked, "AI checks")}</div>
          <p>${failed.length ? `<span class="text-danger fw-semibold">${failed.length} source(s) failed:</span> ${esc(failed.map(([k]) => k).join(", "))}` : `<span class="text-success fw-semibold">✓ All ${Object.keys(lr.sources).length} sources answered.</span>`}</p>
          <details><summary class="fw-semibold">All sources</summary><table class="table table-sm mt-2 mb-0"><thead><tr><th>Source</th><th>Postings</th></tr></thead><tbody>
            ${Object.entries(lr.sources).sort().map(([k, v]) => `<tr><td>${esc(k)}</td><td>${typeof v === "number" ? v : `<span class="text-danger">${esc(v)}</span>`}</td></tr>`).join("")}</tbody></table></details>`
          : "<p>No search yet.</p>"}
      </div></div></div>
    </div>`;
  wireSettings();
}

function wireSettings() {
  renderBackups();
  renderProviders();
  const f = $("#restore-file");
  if (f) f.addEventListener("change", () => { if (f.files[0]) restoreFrom(null, f.files[0]); f.value = ""; });
}

async function renderProviders(data) {
  const host = $("#providers");
  if (!host) return;
  try { data = data || await api("/api/ai/providers"); } catch (e) { host.textContent = e.message; return; }
  host.innerHTML = data.providers.map((p, i) => `<div class="prov ${p.ready ? "ready" : ""}" data-pid="${p.id}">
      <div class="d-flex align-items-center gap-2 flex-wrap">
        <span class="dot ${p.ready ? "on" : ""}" aria-hidden="true"></span><b>${esc(p.label)}</b>
        ${p.free ? '<span class="badge rounded-pill tag-line">free tier</span>' : ""}
        ${p.ready ? `<span class="small text-success fw-semibold">ready${p.key_hint ? " · " + esc(p.key_hint) : ""}</span>` : ""}
        <span class="ms-auto d-flex gap-1">${i ? `<button class="btn btn-sm btn-link p-0" data-action="prov-up" title="Try earlier">${bi("arrow-up")}</button>` : ""}
          <button class="btn btn-sm btn-link p-0" data-action="prov-open" aria-expanded="false">${bi("chevron-down")}</button></span>
      </div>
      <div class="prov-body d-none mt-2"><div class="row g-2 align-items-end">
        ${p.needs_key ? `<div class="col-sm-6"><input type="password" class="form-control form-control-sm" data-pkey autocomplete="off" placeholder="${p.ready ? "saved - leave blank to keep" : "paste API key"}"></div>` : `<div class="col-sm-6 small">${p.local_models.length ? "Installed: " + esc(p.local_models.join(", ")) : "Not running. Install Ollama, then run <code>ollama pull llama3.2</code>."}</div>`}
        <div class="col-sm-3"><input class="form-control form-control-sm" data-pmodel value="${esc(p.model)}" title="Model" aria-label="Model"></div>
        <div class="col-sm-3 d-flex gap-1"><button class="btn btn-sm btn-ink flex-grow-1" data-action="prov-save">Test</button>${p.ready && p.needs_key ? `<button class="btn btn-sm btn-outline-danger" data-action="prov-remove" title="Remove key">${bi("trash")}</button>` : ""}</div>
      </div><div class="form-text">Get a key: <span class="user-select-all">${esc(p.key_page)}</span></div></div>
    </div>`).join("");
}

async function providerAction(el, act) {
  const box = el.closest("[data-pid]"), pid = box.dataset.pid;
  if (act === "prov-open") { box.querySelector(".prov-body").classList.toggle("d-none"); return; }
  try {
    let data;
    if (act === "prov-save") {
      toast("Testing…", 20000);
      const r = await api(`/api/ai/providers/${pid}`, { method: "PUT", json: { api_key: box.querySelector("[data-pkey]")?.value.trim() || null, model: box.querySelector("[data-pmodel]").value.trim() || null } });
      toast(`Works · model ${r.model}`, 4000); data = r;
    } else if (act === "prov-remove") data = await api(`/api/ai/providers/${pid}`, { method: "DELETE" });
    else if (act === "prov-up") {
      const ids = $$("#providers [data-pid]").map(x => x.dataset.pid), k = ids.indexOf(pid);
      [ids[k - 1], ids[k]] = [ids[k], ids[k - 1]];
      data = await api("/api/ai/order", { method: "PUT", json: { order: ids } });
    }
    S.state = await api("/api/state"); renderShell(); renderProviders(data);
  } catch (e) { toast(e.message, 9000); }
}

// ---------------------------------------------------------------- dashboard (numbers from Python, charts by Chart.js)

const CAT = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2", "#EECA3B", "#B279A2", "#FF9DA6", "#9D755D", "#BAB0AC"];
const STATUS_LABEL = l => STATUS[l] || l;
const cssVar = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

function chartDefaults() {
  const Ch = window.Chart;
  Ch.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Ch.defaults.font.size = 12;
  Ch.defaults.color = cssVar("--bs-secondary-color");
  Ch.defaults.borderColor = cssVar("--bs-border-color");
  Ch.defaults.plugins.legend.labels.boxWidth = 10;
  Ch.defaults.plugins.tooltip.padding = 8;
  Ch.defaults.animation.duration = 450;
  Ch.defaults.maintainAspectRatio = false;
}

function chart(id, cfg) {
  const el = document.getElementById(id);
  if (!el || !window.Chart) return;
  S.charts.push(new window.Chart(el, cfg));
}

const hasData = list => list.some(x => x.n) && !(list.length === 1 && /not given/i.test(list[0].label));

async function renderDashboard() {
  (S.charts || []).forEach(c => c.destroy()); S.charts = [];
  let st;
  try { st = await api("/api/stats"); } catch (e) { $("#app").innerHTML = `<div class="alert alert-danger mt-4">${esc(e.message)}</div>`; return; }
  const k = st.kpis, td = st.today;
  const tile = (n, label, sub) => `<div class="col-6 col-md-3 col-xl"><div class="fact tile"><div class="num">${n}</div><small>${label}</small>${sub ? `<div class="tile-sub">${sub}</div>` : ""}</div></div>`;
  const card = (id, title, cls = "col-lg-6", h = "chart-md") => `<div class="${cls}"><div class="card h-100"><div class="card-body p-3"><div class="chart-title">${title}</div><div class="${h}"><canvas id="${id}"></canvas></div></div></div></div>`;
  const ins = S.insights || {};
  $("#app").innerHTML = `<div class="d-flex flex-wrap align-items-end justify-content-between gap-3 hero rise">
      <div><h1 class="h3 mb-1">Dashboard</h1><div class="text-body-secondary small">Live from your tracker · ${k.applications} applications · numbers computed locally, AI only writes the summary</div></div>
      <div class="d-flex flex-wrap align-items-center gap-2">
        <label class="small fw-semibold" for="target-in">Daily target</label>
        <input type="number" min="1" max="100" class="form-control form-control-sm target-in" id="target-in" value="${td.target}">
        <button class="btn btn-sm btn-ink" data-action="save-target">Save</button>
        <button class="btn btn-sm btn-outline-secondary" data-action="test-reminder" title="Reminders come at 1 pm and 6 pm while the app is open, and with the 9 am / 9 pm search">${bi("bell")} Test reminder</button>
        <a class="btn btn-sm btn-outline-secondary" href="#/tracker">${bi("table")} Table</a>
      </div></div>
    <div class="row g-2 mb-3">
      ${tile(`${td.count}<span class="of">/${td.target}</span>`, "today", td.met ? "target met ✓" : `${td.target - td.count} to go`)}
      ${tile(k.this_week, "this week", `${k.avg_per_day_30}/day avg (30d)`)}
      ${tile(k.applications, "applications", k.saved ? `${k.saved} saved` : "")}
      ${tile(`${k.streak}d`, "streak", `${k.target_days_met_30} target days (30d)`)}
      ${tile(`${k.response_rate}%`, "response rate", `${k.interview_rate}% reach interviews`)}
      ${tile(k.in_progress, "in progress", `${k.offers} offer${k.offers === 1 ? "" : "s"}`)}
    </div>
    <div class="card mb-3 insights"><div class="card-body p-3">
      <div class="d-flex flex-wrap align-items-center gap-2 mb-1"><div class="chart-title mb-0">${bi("stars")} AI insights &amp; advice</div>
        <span class="small text-body-secondary" id="ins-meta">${ins.at ? `${esc(ins.provider)} · ${ago(ins.at)}` : ""}</span>
        <button class="btn btn-sm btn-ink ms-auto" data-action="insights">${bi("stars")} ${ins.text ? "Refresh analysis" : "Analyse my search"}</button></div>
      <div id="ins-body" class="ins-body">${ins.text ? mdLite(ins.text) : `<span class="text-body-secondary">Sends only the numbers on this page (no company names, notes or contacts) to your AI provider and returns a summary, insights and next steps.</span>`}</div>
    </div></div>
    <div class="row g-3">
      ${card("c-day", "Applications per day · last 30 days", "col-lg-8")}
      ${card("c-status", "Status", "col-lg-4")}
      ${card("c-funnel", "Funnel · reached at least this stage")}
      ${card("c-week", "Per week · last 12 weeks")}
      ${card("c-company", "Top companies", "col-lg-6", "chart-lg")}
      ${card("c-role", "Role families", "col-lg-6", "chart-lg")}
      ${card("c-tier", "Company tiers", "col-lg-4")}
      ${card("c-weekday", "By weekday", "col-lg-4")}
      ${hasData(st.sources) ? card("c-source", "Where you applied", "col-lg-4") : ""}
      ${hasData(st.locations) ? card("c-loc", "Locations", "col-lg-4") : ""}
      ${hasData(st.job_types) ? card("c-type", "Job type", "col-lg-4") : ""}
    </div>`;
  if (!window.Chart) { toast("Charts couldn't load.", 5000); return; }
  chartDefaults();
  const ink = cssVar("--jh-ink"), soft = cssVar("--jh-soft");
  const noLegend = { plugins: { legend: { display: false } } };
  const day = st.per_day.map(d => new Date(d.date + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" }));
  chart("c-day", { type: "bar", data: { labels: day, datasets: [
      { type: "line", label: "Target", data: st.per_day.map(() => td.target), borderColor: CAT[3], borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0 },
      { label: "Applications", data: st.per_day.map(d => d.n), backgroundColor: ink, borderRadius: 3, maxBarThickness: 22 }] },
    options: { plugins: { legend: { position: "bottom" } }, scales: { x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 10 } }, y: { beginAtZero: true, ticks: { precision: 0 } } } } });
  chart("c-status", { type: "doughnut", data: { labels: st.status.map(x => STATUS_LABEL(x.label)), datasets: [{ data: st.status.map(x => x.n), backgroundColor: CAT, borderWidth: 1, borderColor: cssVar("--jh-card") }] },
    options: { cutout: "62%", plugins: { legend: { position: "right" } } } });
  chart("c-funnel", { type: "bar", data: { labels: st.funnel.map(x => x.label), datasets: [{ data: st.funnel.map(x => x.n), backgroundColor: st.funnel.map((_, i) => i ? CAT[i] : ink), borderRadius: 3 }] },
    options: { indexAxis: "y", ...noLegend, scales: { x: { type: "logarithmic", min: 0.8, ticks: { callback: v => [1, 10, 100, 1000].includes(v) ? v : "" } }, y: { grid: { display: false } } } } });
  chart("c-week", { type: "line", data: { labels: st.per_week.map(w => new Date(w.week + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" })),
      datasets: [{ data: st.per_week.map(w => w.n), borderColor: ink, backgroundColor: soft, fill: true, tension: .3, pointRadius: 3, pointBackgroundColor: ink }] },
    options: { ...noLegend, scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { precision: 0 } } } } });
  chart("c-company", { type: "bar", data: { labels: st.companies.map(x => x.label), datasets: [{ data: st.companies.map(x => x.n), backgroundColor: ink, borderRadius: 3, maxBarThickness: 18 }] },
    options: { indexAxis: "y", ...noLegend, scales: { x: { beginAtZero: true, ticks: { precision: 0 } }, y: { grid: { display: false } } } } });
  chart("c-role", { type: "pie", data: { labels: st.roles.map(x => x.label), datasets: [{ data: st.roles.map(x => x.n), backgroundColor: CAT, borderWidth: 1, borderColor: cssVar("--jh-card") }] },
    options: { plugins: { legend: { position: "right" } } } });
  chart("c-tier", { type: "doughnut", data: { labels: st.tiers.map(x => x.label), datasets: [{ data: st.tiers.map(x => x.n), backgroundColor: [CAT[0], CAT[1], CAT[2], CAT[9]], borderWidth: 1, borderColor: cssVar("--jh-card") }] }, options: { cutout: "55%", plugins: { legend: { position: "bottom" } } } });
  chart("c-weekday", { type: "bar", data: { labels: st.weekday.map(x => x.label), datasets: [{ data: st.weekday.map(x => x.n), backgroundColor: ink, borderRadius: 3 }] },
    options: { ...noLegend, scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { precision: 0 } } } } });
  if (hasData(st.sources)) chart("c-source", { type: "doughnut", data: { labels: st.sources.map(x => x.label), datasets: [{ data: st.sources.map(x => x.n), backgroundColor: CAT, borderWidth: 1, borderColor: cssVar("--jh-card") }] }, options: { cutout: "55%", plugins: { legend: { position: "bottom" } } } });
  if (hasData(st.locations)) chart("c-loc", { type: "bar", data: { labels: st.locations.map(x => x.label), datasets: [{ data: st.locations.map(x => x.n), backgroundColor: CAT[0], borderRadius: 3 }] }, options: { indexAxis: "y", ...noLegend, scales: { x: { beginAtZero: true, ticks: { precision: 0 } }, y: { grid: { display: false } } } } });
  if (hasData(st.job_types)) chart("c-type", { type: "pie", data: { labels: st.job_types.map(x => x.label), datasets: [{ data: st.job_types.map(x => x.n), backgroundColor: CAT, borderWidth: 1, borderColor: cssVar("--jh-card") }] }, options: { plugins: { legend: { position: "bottom" } } } });
  if (!S.insights) api("/api/ai/insights").then(r => { if (r && r.text) { S.insights = r; $("#ins-body").innerHTML = mdLite(r.text); $("#ins-meta").textContent = `${r.provider} · ${ago(r.at)}`; } }).catch(() => {});
}

async function runInsights() {
  if (needAI()) return;
  const btn = $("[data-action=insights]"); btn.disabled = true;
  $("#ins-body").innerHTML = `<span class="spin">${bi("arrow-repeat")}</span> Analysing your numbers…`;
  try {
    S.insights = await api("/api/ai/insights?force=true", { method: "POST" });
    $("#ins-body").innerHTML = mdLite(S.insights.text);
    $("#ins-meta").textContent = `${S.insights.provider} · just now`;
  } catch (e) { $("#ins-body").textContent = ""; toast(e.message, 9000); }
  btn.disabled = false;
}

async function saveTarget() {
  try {
    S.state.target = await api("/api/settings/target", { method: "PUT", json: { target: Number($("#target-in").value) } });
    renderShell(); renderDashboard(); toast(`Daily target: ${S.state.target.target}`, 2000);
  } catch (e) { toast(e.message, 5000); }
}

// ---------------------------------------------------------------- Gmail: review suggested updates, compose and send

async function mailConnect() {
  try {
    toast("Testing the login with Gmail…", 15000);
    S.state.mail = await api("/api/mail/settings", { method: "PUT", json: { address: $("#mail-addr").value, app_password: $("#mail-pw").value } });
    toast("Gmail connected.", 3000); renderSettings();
  } catch (e) { const box = $("#mail-err"); if (box) { box.textContent = e.message; box.classList.remove("d-none"); } toast("Gmail didn't connect - see the message under the form.", 4000); }
}

async function mailSync(openAfter = true) {
  try {
    toast("Checking Gmail…", 30000);
    const r = await api("/api/mail/sync", { method: "POST" });
    S.state.mail = r; toast(r.proposals.length ? `${r.proposals.length} suggested update(s)` : "No new application updates.", 3500);
    if (openAfter && r.proposals.length) showMailReview(r.proposals); else if (currentView() === "settings") renderSettings(); else route();
  } catch (e) { toast(e.message, 9000); }
}

async function openMailReview() {
  try { showMailReview((await api("/api/mail")).proposals); } catch (e) { toast(e.message, 7000); }
}

function showMailReview(items) {
  $("#ai-modal-body").innerHTML = `<div class="d-flex align-items-center gap-2 mb-1"><h5 class="mb-0">${bi("envelope-check")} Suggested updates from Gmail</h5>
      <button class="btn btn-sm btn-outline-secondary ms-auto" data-action="mail-sync">${bi("arrow-repeat")} Check again</button></div>
    <div class="small text-body-secondary mb-3">Read-only scan of the last 14 days. Tick the ones that are right and apply them; the email subject is added to the notes.</div>
    ${items.length ? `<div class="vstack gap-2">${items.map(p => `<label class="prov d-flex gap-3 align-items-start"><input type="checkbox" class="form-check-input mt-1" data-mail="${esc(p.msg_id)}" checked>
        <div class="flex-grow-1"><div><b>${esc(p.company)}</b> · ${esc(p.role)} <span class="ms-1 small">${esc(STATUS[p.current] || p.current)} → <b>${esc(STATUS[p.proposed] || p.proposed)}</b></span></div>
          <div class="small fw-semibold mt-1">${esc(p.subject)}</div><div class="small text-body-secondary">${esc(p.from_name || p.from)} · ${p.date ? fmtDate(p.date) : ""} — ${esc(p.snippet)}</div></div></label>`).join("")}</div>
      <div class="d-flex gap-2 mt-3"><button class="btn btn-sm btn-ink" data-action="mail-apply">${bi("check2")} Apply ticked</button><button class="btn btn-sm btn-outline-secondary" data-action="mail-dismiss">Dismiss ticked</button></div>`
      : `<div class="text-body-secondary py-3">Nothing to review. New rejections, OAs, interview invites and offers will show up here.</div>`}`;
  bootstrap.Modal.getOrCreateInstance($("#ai-modal")).show();
}

async function mailReview(apply) {
  const ids = $$("[data-mail]").filter(x => x.checked).map(x => x.dataset.mail);
  if (!ids.length) return;
  try {
    const r = await api("/api/mail/review", { method: "POST", json: { ids, apply } });
    toast(apply ? `Updated ${r.applied} application(s)` : `Dismissed ${ids.length}`, 3000);
    await loadAll(); showMailReview(r.proposals);
  } catch (e) { toast(e.message, 7000); }
}

// one compose window for every "send" in the app; nothing is sent until Send is pressed
function openCompose({ to = "", text = "", jobId = null, contactId = null } = {}) {
  const subj = (text.match(/^Subject:\s*(.+)$/mi) || [, ""])[1].trim();
  const body = text.replace(/^Subject:.*$/mi, "").trim();
  const ok = S.state.mail && S.state.mail.connected;
  $("#ai-modal-body").innerHTML = `<h5 class="mb-2">${bi("send")} Send email</h5>
    ${ok ? `<div class="small text-body-secondary mb-2">From ${esc(S.state.mail.address)}</div>` : `<div class="alert alert-warning py-2 small">Connect Gmail in Settings to send from here, or use "Open in email app".</div>`}
    <div class="vstack gap-2"><input class="form-control form-control-sm" id="cm-to" placeholder="To" value="${esc(to)}">
      <input class="form-control form-control-sm" id="cm-subj" placeholder="Subject" value="${esc(subj)}">
      <textarea class="form-control letter" id="cm-body" rows="12">${esc(body)}</textarea></div>
    <div class="d-flex flex-wrap gap-2 mt-3 align-items-center">${ok ? `<button class="btn btn-sm btn-ink" data-action="cm-send" data-job="${jobId || ""}" data-contact="${contactId || ""}">${bi("send")} Send</button>` : ""}
      <button class="btn btn-sm btn-outline-secondary" data-action="cm-mailto">${bi("envelope-open")} Open in email app</button>
      <span class="small text-body-secondary ms-auto">Attach your resume in Gmail if the post asks for it.</span></div>`;
  bootstrap.Modal.getOrCreateInstance($("#ai-modal")).show();
}

async function composeSend(el) {
  const to = $("#cm-to").value.trim(), subject = $("#cm-subj").value.trim(), body = $("#cm-body").value;
  if (!confirm(`Send this email to ${to} from ${S.state.mail.address}?`)) return;
  el.disabled = true;
  try {
    const r = await api("/api/mail/send", { method: "POST", json: { to, subject, body, job_id: Number(el.dataset.job) || null, contact_id: el.dataset.contact || null } });
    if (el.dataset.contact) markContactSent(el.dataset.contact, false);
    bootstrap.Modal.getInstance($("#ai-modal"))?.hide();
    toast(r.tracked_job_id ? `Sent to ${to} · counted as an application` : `Sent to ${to}`, 4000);
    await loadAll();
  } catch (e) { toast(e.message, 9000); el.disabled = false; }
}

// ---------------------------------------------------------------- cold email desk (its own tab)

async function renderOutreach() {
  $("#app").innerHTML = `<div class="hero rise"><h1 class="h3 mb-1">Cold email</h1><div class="text-body-secondary small">Drop screenshots of recruiter posts, LinkedIn profiles or job ads (or paste the text). JobQuest.AI pulls out the contacts, then drafts a tailored email for each one from your resume. Addresses are only taken from what you provide - never guessed.</div></div>
    ${S.state.ai.configured ? "" : `<div class="alert alert-warning"><b>Please add an LLM key</b> in Settings - extraction and drafting need one (screenshots need a vision model: OpenAI, Gemini or Anthropic).</div>`}
    <div class="row g-3">
      <div class="col-lg-5"><div class="card h-100"><div class="card-body p-3">
        <div class="chart-title">1 · Add screenshots or text</div>
        <label class="drop d-block text-center p-3 mb-2" for="or-files" id="or-drop">${bi("images", "fs-3 d-block mb-1")}<b>Choose screenshots</b> or drop them here<div class="small text-body-secondary">PNG / JPG, several at once · Ctrl+V pastes a screenshot</div></label>
        <input type="file" id="or-files" class="d-none" multiple accept="image/png,image/jpeg,image/webp">
        <div id="or-shots" class="d-flex flex-wrap gap-2 mb-2"></div>
        <textarea class="form-control form-control-sm" id="or-text" rows="5" placeholder="…or paste a LinkedIn post, job ad or email signature"></textarea>
        <button class="btn btn-sm btn-ink mt-2" data-action="or-extract">${bi("magic")} Extract contacts</button> <span class="small text-body-secondary" id="or-status"></span>
      </div></div></div>
      <div class="col-lg-7"><div class="card h-100"><div class="card-body p-3">
        <div class="d-flex flex-wrap align-items-center gap-2 mb-2"><div class="chart-title mb-0">2 · Contacts</div><span class="small text-body-secondary" id="or-count"></span>
          <button class="btn btn-sm btn-outline-secondary ms-auto" data-action="or-postings" title="Collect every recruiter / HR email printed in the jobs JobQuest.AI found">${bi("search")} Find in job postings</button>
          <button class="btn btn-sm btn-outline-secondary" data-action="or-add">${bi("plus-lg")} Add manually</button></div>
        <div class="d-flex flex-wrap gap-2 mb-2">
          <div class="input-group input-group-sm flex-grow-1 w-auto"><span class="input-group-text">${bi("search")}</span><input type="search" class="form-control" id="or-q" placeholder="Search name, email, company, role…" value="${esc(S.orQ || "")}"></div>
          <select class="form-select form-select-sm w-auto" id="or-show" aria-label="Show"><option value="all">All</option><option value="email">With email</option><option value="unsent">Not sent yet</option><option value="sent">Sent</option></select></div>
        <div id="or-list" class="vstack gap-2 or-list"></div>
      </div></div></div>
    </div>`;
  S.orShots = [];
  $("#or-files").addEventListener("change", e => addShots([...e.target.files]));
  const drop = $("#or-drop");
  ["dragover", "dragenter"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(t => drop.addEventListener(t, () => drop.classList.remove("over")));
  drop.addEventListener("drop", e => { e.preventDefault(); addShots([...e.dataTransfer.files].filter(f => f.type.startsWith("image/"))); });
  try { S.orContacts = (await api("/api/outreach")).contacts; } catch { S.orContacts = []; }
  drawContacts();
}

async function addShots(files) {
  for (const f of files.slice(0, 8 - S.orShots.length)) {
    try { S.orShots.push({ name: f.name || "screenshot.png", data_url: await shrinkImage(f, 1600) }); } catch (e) { toast(e.message, 5000); }
  }
  $("#or-shots").innerHTML = S.orShots.map((x, i) => `<span class="shot"><img src="${x.data_url}" alt="${esc(x.name)}"><button class="btn btn-sm btn-link p-0" data-action="or-unshot" data-i="${i}" aria-label="Remove">${bi("x-circle-fill")}</button></span>`).join("");
}

function drawContacts() {
  const all = S.orContacts || [], q = (S.orQ || "").trim().toLowerCase(), show = $("#or-show")?.value || "all";
  const list = all.filter(c => (!q || [c.name, c.email, c.title, c.company, c.job_role, c.location, c.notes].join(" ").toLowerCase().includes(q))
    && (show === "all" || (show === "email" && c.email) || (show === "unsent" && c.email && !c.sent_at) || (show === "sent" && c.sent_at)));
  $("#or-count").textContent = all.length ? (list.length === all.length ? `${all.length} saved` : `${list.length} of ${all.length}`) : "";
  $("#or-list").innerHTML = list.length ? list.map(c => `<div class="prov" data-cid="${esc(c.id)}">
      <div class="row g-1">
        <div class="col-sm-4"><input class="form-control form-control-sm" data-k="name" value="${esc(c.name)}" placeholder="Name"></div>
        <div class="col-sm-8"><input class="form-control form-control-sm" data-k="email" value="${esc(c.email)}" placeholder="Email (from the screenshot)"></div>
        <div class="col-sm-4"><input class="form-control form-control-sm" data-k="title" value="${esc(c.title)}" placeholder="Their title"></div>
        <div class="col-sm-4"><input class="form-control form-control-sm" data-k="company" value="${esc(c.company)}" placeholder="Company"></div>
        <div class="col-sm-4"><input class="form-control form-control-sm" data-k="job_role" value="${esc(c.job_role)}" placeholder="Hiring for"></div>
        <div class="col-12"><input class="form-control form-control-sm" data-k="notes" value="${esc(c.notes)}" placeholder="Notes (e.g. subject line they asked for)"></div>
      </div>
      <div class="d-flex flex-wrap gap-2 mt-2 align-items-center">
        <button class="btn btn-sm btn-ink" data-action="or-draft">${bi("stars")} Draft email</button>
        <button class="btn btn-sm btn-outline-secondary" data-action="or-track">${bi("kanban")} Add to tracker</button>
        ${c.sent_at ? `<span class="badge rounded-pill text-bg-success">sent ${fmtDate(c.sent_at)} · counted</span>` : `<button class="btn btn-sm btn-outline-secondary" data-action="or-marksent" title="Sent it from your own email app? This files it as an application.">${bi("check2-circle")} Mark as sent</button>`}
        <button class="btn btn-sm btn-link link-danger ms-auto p-0" data-action="or-del">${bi("trash")} Remove</button></div>
    </div>`).join("") : `<div class="text-body-secondary small py-4 text-center">${all.length ? "Nothing matches the search." : "No contacts yet. Add screenshots or text on the left, or press Find in job postings."}</div>`;
}

let orSaveTimer;
function saveContactsSoon() { clearTimeout(orSaveTimer); orSaveTimer = setTimeout(() => api("/api/outreach", { method: "PUT", json: { contacts: S.orContacts } }).catch(e => toast(e.message)), 500); }
const contactOf = el => S.orContacts.find(c => c.id === el.closest("[data-cid]").dataset.cid);

async function orExtract() {
  if (needAI()) return;
  const btn = $("[data-action=or-extract]"); btn.disabled = true;
  $("#or-status").innerHTML = `<span class="spin">${bi("arrow-repeat")}</span> Reading…`;
  try {
    const r = await api("/api/ai/extract-contacts", { method: "POST", json: { text: $("#or-text").value, images: S.orShots } });
    S.orContacts = r.contacts; drawContacts();
    $("#or-status").textContent = `Found ${r.found} · ${r.provider}`;
    S.orShots = []; $("#or-shots").innerHTML = ""; $("#or-text").value = "";
  } catch (e) { $("#or-status").textContent = ""; toast(e.message, 9000); }
  btn.disabled = false;
}

async function orDraft(el) {
  if (needAI()) return;
  const c = contactOf(el);
  el.disabled = true; el.innerHTML = `<span class="spin">${bi("arrow-repeat")}</span> Drafting…`;
  try {
    const r = await api("/api/ai/outreach-email", { method: "POST", json: { contact: c } });
    openCompose({ to: c.email, text: r.text, contactId: c.id, jobId: Number(c.job_id) || null });
  } catch (e) { toast(e.message, 9000); }
  el.disabled = false; el.innerHTML = `${bi("stars")} Draft email`;
}

function markContactSent(id, save = true) {
  const c = (S.orContacts || []).find(x => x.id === id);
  if (c) { c.sent_at = new Date().toISOString(); if (save) saveContactsSoon(); if (currentView() === "outreach") drawContacts(); }
}

async function orMarkSent(el) {
  const c = contactOf(el);
  clearTimeout(orSaveTimer);
  try {
    await api("/api/outreach", { method: "PUT", json: { contacts: S.orContacts } });       // keep any unsaved edits
    const r = await api("/api/outreach/mark-sent", { method: "POST", json: { contact_id: c.id } });
    S.orContacts = r.contacts; drawContacts(); await loadAll();
    toast(`Counted: ${c.company} is in the tracker as Applied`, 3500);
  } catch (e) { toast(e.message, 7000); }
}

async function orTrack(el) {
  const c = contactOf(el);
  if (!c.company) { toast("Add the company first.", 3000); return; }
  try {
    await api("/api/tracker", { method: "POST", json: { company: c.company, role: c.job_role || "Cold outreach", status: "saved",
      date: new Date().toISOString().slice(0, 10), contact: [c.name, c.email].filter(Boolean).join(" "), app_source: "Cold email", location: c.location || "" } });
    toast(`Added ${c.company} to the tracker`, 3000); await loadAll();
  } catch (e) { toast(e.message, 7000); }
}

// ---------------------------------------------------------------- cold email from a job posting's contact

async function draftColdEmail(el) {
  if (needAI()) return;
  const id = Number(el.dataset.id);
  $("#ai-modal-body").innerHTML = `<h5 class="mb-1">${bi("envelope")} Cold email</h5><div class="small text-body-secondary mb-3">To ${esc(el.dataset.name || el.dataset.email)} &lt;${esc(el.dataset.email)}&gt; - a contact printed in this posting.</div>
    <div id="ce-out"><span class="spin">${bi("arrow-repeat")}</span> Drafting from your resume and the job description…</div>`;
  bootstrap.Modal.getOrCreateInstance($("#ai-modal")).show();
  try {
    const r = await api("/api/ai/cold-email", { method: "POST", json: { job_id: id, email: el.dataset.email, name: el.dataset.name, role: el.dataset.role } });
    S.coldTo = r.to;
    $("#ce-out").innerHTML = `<textarea class="form-control letter" id="ce-text" rows="12">${esc(r.text)}</textarea>
      <div class="d-flex flex-wrap gap-2 mt-2 align-items-center"><button class="btn btn-sm btn-ink" data-action="ce-compose" data-id="${id}">${bi("send")} Review &amp; send</button>
        <button class="btn btn-sm btn-outline-secondary" data-action="ce-mail">${bi("envelope-open")} Open in email app</button>
        <button class="btn btn-sm btn-outline-secondary" data-action="ce-copy">${bi("clipboard")} Copy</button>
        <span class="small text-body-secondary ms-auto">by ${esc(r.provider)} · review before sending</span></div>`;
  } catch (e) { $("#ce-out").textContent = ""; toast(e.message, 9000); }
}

// ---------------------------------------------------------------- cover letter

function needAI() {
  if (S.state.ai.configured) return false;
  toast("Please add an LLM key in Settings → AI providers first.", 5000);
  location.hash = "#/settings";
  return true;
}

function openCoverLetter(id) {
  const j = S.jobs.find(x => x.id === id) || {};
  const rs = S.state.profile.resumes || [];
  bootstrap.Modal.getInstance($("#edit-modal"))?.hide();
  $("#ai-modal-body").innerHTML = `<h5 class="mb-1">${bi("envelope-paper")} Cover letter</h5><div class="small text-body-secondary mb-3">Uses your local model (Ollama) if it's running, otherwise your first AI provider.</div>
    ${S.state.ai.configured ? "" : `<div class="alert alert-warning py-2 small">Please add an LLM key in Settings → AI providers.</div>`}
    <div class="row g-2">
      <div class="col-md-6"><input class="form-control form-control-sm" id="cl-company" placeholder="Company" value="${esc(j.company || "")}"></div>
      <div class="col-md-6"><input class="form-control form-control-sm" id="cl-role" placeholder="Role" value="${esc(j.title || "")}"></div>
      <div class="col-12"><textarea class="form-control form-control-sm" id="cl-jd" rows="5" placeholder="Paste the job description${j.id ? " (leave blank to use the saved one)" : ""}"></textarea></div>
      <div class="col-md-6"><select class="form-select form-select-sm" id="cl-resume" aria-label="Resume">${rs.map(r => `<option value="${r.id}">${esc(r.label)}</option>`).join("")}</select></div>
      <div class="col-12"><label class="btn btn-sm btn-outline-secondary mb-0" for="cl-files">${bi("paperclip")} Add LinkedIn PDF or other files</label>
        <input type="file" id="cl-files" class="d-none" multiple accept=".pdf,.docx,.txt,.md"><span class="small text-body-secondary ms-2">Optional, up to 4. Used only for this letter, not saved.</span>
        <div id="cl-attached" class="d-flex flex-wrap gap-2 mt-2"></div></div>
      <div class="col-md-6"><select class="form-select form-select-sm" id="cl-tone" aria-label="Tone"><option>warm and professional</option><option>confident and concise</option><option>formal</option><option>enthusiastic</option></select></div>
    </div>
    <div class="d-flex gap-2 mt-3"><button class="btn btn-sm btn-ink" data-action="cl-make" data-id="${j.id || ""}">${bi("stars")} Write it</button><span class="small text-body-secondary align-self-center" id="cl-status"></span></div>
    <div id="cl-out" class="mt-3"></div>`;
  S.clExtra = [];
  $("#cl-files").addEventListener("change", addContextFiles);
  bootstrap.Modal.getOrCreateInstance($("#ai-modal")).show();
}

function drawAttached() {
  $("#cl-attached").innerHTML = S.clExtra.map((x, i) => `<span class="file-chip">${bi("file-earmark-text")} ${esc(x.name)} <button class="btn btn-sm btn-link p-0" data-action="cl-unattach" data-i="${i}" aria-label="Remove">${bi("x")}</button></span>`).join("");
}

async function addContextFiles() {
  const input = $("#cl-files");
  for (const f of [...input.files].slice(0, 4 - S.clExtra.length)) {
    const form = new FormData(); form.append("file", f);
    try { S.clExtra.push(await api("/api/ai/context-file", { method: "POST", form })); drawAttached(); }
    catch (e) { toast(`${f.name}: ${e.message}`, 7000); }
  }
  input.value = "";
}

async function downloadLetterPdf() {
  try {
    const res = await fetch("/api/ai/cover-letter.pdf", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: $("#cl-text").value, company: $("#cl-company").value, role: $("#cl-role").value }) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Couldn't make the PDF");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await res.blob());
    a.download = (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || "cover-letter.pdf";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  } catch (e) { toast(e.message, 7000); }
}

async function makeCoverLetter(id) {
  if (needAI()) return;
  const btn = $("[data-action=cl-make]"); btn.disabled = true;
  $("#cl-status").innerHTML = `<span class="spin">${bi("arrow-repeat")}</span> Writing…`;
  try {
    const r = await api("/api/ai/cover-letter", { method: "POST", json: { job_id: id || null, jd: $("#cl-jd").value, company: $("#cl-company").value,
      role: $("#cl-role").value, resume_id: Number($("#cl-resume").value) || null, tone: $("#cl-tone").value, extra: S.clExtra || [] } });
    $("#cl-status").textContent = `by ${r.provider}`;
    $("#cl-out").innerHTML = `<textarea class="form-control letter" id="cl-text" rows="14">${esc(r.text)}</textarea>
      <div class="d-flex gap-2 mt-2"><button class="btn btn-sm btn-ink" data-action="cl-pdf">${bi("filetype-pdf")} Download PDF</button><button class="btn btn-sm btn-outline-secondary" data-action="cl-copy">${bi("clipboard")} Copy</button><button class="btn btn-sm btn-outline-secondary" data-action="cl-save">${bi("download")} Save .txt</button></div>
      <div class="form-text">Edit the text above first if you like; the PDF uses what's in the box.</div>`;
  } catch (e) { $("#cl-status").textContent = ""; toast(e.message, 9000); }
  btn.disabled = false;
}

function saveText(text, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

// ---------------------------------------------------------------- coach

const COACH_STARTERS = ["Draft a one-page CV for SDE roles from my resume", "Draft an email to the recruiter in my attachment",
  "Write a cover letter for the job description I attached", "Review my resume for ML engineer roles in India",
  "How do I get more interview calls?", "Plan my week to hit 10 applications a day"];

async function renderCoach() {
  $("#app").innerHTML = `<div class="hero my-4 rise"><h1 class="mb-1"><span class="text-accent">Coach</span></h1><div class="text-body-secondary">Ask about your resume, applications, interviews or cold outreach. It knows your profile and tracker numbers.</div></div>
    ${S.state.ai.configured ? "" : `<div class="alert alert-warning d-flex align-items-center gap-3"><span class="flex-grow-1"><b>Please add an LLM key.</b> The Coach needs one AI provider.</span><a class="btn btn-sm btn-ink" href="#/settings">Add key</a></div>`}
    <div class="card coach rise"><div class="card-body p-0 d-flex flex-column">
      <div id="chat-log" class="chat-log p-3" aria-live="polite"></div>
      <div class="p-3 border-top">
        <div class="d-flex flex-wrap gap-2 mb-2" id="starters">${COACH_STARTERS.map(q => `<button class="btn btn-sm btn-outline-secondary rounded-pill" data-action="coach-ask" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="chat-attached" class="d-flex flex-wrap gap-2 mb-2"></div>
        <div class="input-group"><label class="btn btn-outline-secondary mb-0 d-flex align-items-center" for="chat-files" title="Attach CVs, job descriptions, recruiter screenshots or photos">${bi("paperclip")}</label>
          <input type="file" id="chat-files" class="d-none" multiple accept=".pdf,.docx,.txt,.md,image/png,image/jpeg,image/webp">
          <textarea class="form-control" id="chat-in" rows="2" placeholder="Ask anything, or attach files and photos… (Enter to send, Shift+Enter for a new line)"></textarea>
          <button class="btn btn-ink" data-action="coach-send" aria-label="Send">${bi("send")}</button></div>
        <div class="d-flex justify-content-between small text-body-secondary mt-1"><span>Sends your profile, tracker counts and any attachments to your AI provider, never your keys. Photos need a vision model (OpenAI, Gemini, Anthropic).</span><button class="btn btn-sm btn-link p-0" data-action="coach-clear">Clear chat</button></div>
      </div></div></div>`;
  S.chatFiles = []; S.chatImages = [];
  $("#chat-files").addEventListener("change", attachChatFiles);
  try { drawChat((await api("/api/ai/chat")).messages); } catch (e) { toast(e.message); }
}

function shrinkImage(file, max = 1400) {           // photos go to the AI as JPEG, at most 1400 px on the long side
  return new Promise((resolve, reject) => {
    const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => {
      const k = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Couldn't read that image")); };
    img.src = url;
  });
}

async function attachChatFiles() {
  const input = $("#chat-files");
  for (const f of [...input.files]) {
    if (S.chatFiles.length + S.chatImages.length >= 6) { toast("Up to 6 attachments per message.", 3000); break; }
    try {
      if (f.type.startsWith("image/")) S.chatImages.push({ name: f.name, data_url: await shrinkImage(f) });
      else { const form = new FormData(); form.append("file", f); S.chatFiles.push(await api("/api/ai/context-file", { method: "POST", form })); }
    } catch (e) { toast(`${f.name}: ${e.message}`, 7000); }
  }
  input.value = "";
  drawChatAttached();
}

function drawChatAttached() {
  const host = $("#chat-attached");
  if (!host) return;
  host.innerHTML = [...S.chatFiles.map((x, i) => ["f", i, x.name, "file-earmark-text"]), ...S.chatImages.map((x, i) => ["i", i, x.name, "image"])]
    .map(([t, i, name, ic]) => `<span class="file-chip">${bi(ic)} ${esc(name)} <button class="btn btn-sm btn-link p-0" data-action="chat-unattach" data-t="${t}" data-i="${i}" aria-label="Remove">${bi("x")}</button></span>`).join("");
}

const draftTitle = t => (t.match(/^#\s+(.+)$/m) || t.match(/^Subject:\s*(.+)$/mi) || [, "Coach draft"])[1].trim().slice(0, 60);

async function downloadDoc(text, kind) {
  try {
    const res = await fetch("/api/ai/document", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, kind, title: draftTitle(text) }) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Couldn't make the file");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await res.blob());
    a.download = (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || `draft.${kind}`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  } catch (e) { toast(e.message, 7000); }
}

function mdLite(t) {             // escape first, then a little markdown: bold, inline code, bullets
  return esc(t).replace(/^#{1,3} (.+)$/gm, "<b class=\"h\">$1</b>").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/^\s*[-*] (.*)$/gm, "• $1").replace(/\n{3,}/g, "\n\n").replace(/(<b class="h">[^<]*<\/b>)\n+/g, "$1")
    .replace(/\n/g, "<br>");
}

function drawChat(msgs, pending) {
  const log = $("#chat-log");
  if (!log) return;
  S.chatMsgs = msgs;
  log.innerHTML = (msgs.length ? msgs.map((m, i) => `<div class="msg ${m.role}"><div class="bubble">${mdLite(m.content)}${(m.attached || []).length ? `<div class="att">${bi("paperclip")} ${esc(m.attached.join(", "))}</div>` : ""}</div>${m.role === "assistant" ? `<div class="via d-flex flex-wrap gap-2 align-items-center">${m.provider ? esc(m.provider) : ""}
      <button class="btn btn-sm btn-link p-0" data-action="msg-copy" data-i="${i}">${bi("clipboard")} Copy</button>
      <button class="btn btn-sm btn-link p-0" data-action="msg-pdf" data-i="${i}">${bi("filetype-pdf")} PDF</button>
      <button class="btn btn-sm btn-link p-0" data-action="msg-docx" data-i="${i}">${bi("filetype-docx")} Word</button>
      ${/^Subject:/mi.test(m.content) ? `<button class="btn btn-sm btn-link p-0" data-action="msg-send" data-i="${i}">${bi("send")} Send…</button><button class="btn btn-sm btn-link p-0" data-action="msg-mail" data-i="${i}">${bi("envelope")} Open in email app</button>` : ""}</div>` : ""}</div>`).join("")
    : `<div class="text-center text-body-secondary py-5">${bi("chat-dots", "fs-2 d-block mb-2")}Start with a question or tap a suggestion below.</div>`)
    + (pending ? `<div class="msg user"><div class="bubble">${mdLite(pending)}</div></div><div class="msg assistant"><div class="bubble typing"><span></span><span></span><span></span></div></div>` : "");
  log.scrollTop = log.scrollHeight;
}

async function coachSend(text) {
  text = (text || $("#chat-in").value).trim();
  const files = S.chatFiles || [], images = S.chatImages || [];
  if ((!text && !files.length && !images.length) || needAI()) return;
  $("#chat-in").value = "";
  const prev = S.chatMsgs || [];
  const names = [...files, ...images].map(x => x.name);
  drawChat(prev, (text || "(files only)") + (names.length ? `\n📎 ${names.join(", ")}` : ""));
  S.chatFiles = []; S.chatImages = []; drawChatAttached();
  try { drawChat((await api("/api/ai/chat", { method: "POST", json: { message: text, files, images } })).messages); }
  catch (e) { drawChat(prev); S.chatFiles = files; S.chatImages = images; drawChatAttached(); toast(e.message, 9000); }
}

function mailtoFrom(text, toAddr) {
  const subj = (text.match(/^Subject:\s*(.+)$/mi) || [, ""])[1].trim();
  const body = text.replace(/^Subject:.*$/mi, "").trim();
  const to = toAddr || (text.match(/[\w.+-]+@[\w-]+\.[\w.-]+/) || [""])[0];
  location.href = `mailto:${encodeURIComponent(to)}?subject=${encodeURIComponent(subj)}&body=${encodeURIComponent(body)}`;
}

// ---------------------------------------------------------------- events

document.addEventListener("click", async e => {
  const copyBtn = e.target.closest("[data-copy]");
  if (copyBtn) {
    try { await navigator.clipboard.writeText(copyBtn.dataset.copy); toast(`Copied ${copyBtn.dataset.copy}`, 2000); } catch { toast("Couldn't copy"); }
    return;
  }
  if (e.target.closest("a[href]")) return;
  const tb = e.target.closest("[data-tsort],[data-tstatus],[data-tview]");
  if (tb) {
    const t = S.t;
    if (tb.dataset.tsort) { t.dir = t.sort === tb.dataset.tsort ? -t.dir : (tb.dataset.tsort === "applied" || tb.dataset.tsort === "update" ? -1 : 1); t.sort = tb.dataset.tsort; }
    if (tb.dataset.tstatus) t.status = tb.dataset.tstatus;
    if (tb.dataset.tview) t.view = tb.dataset.tview;
    store.set("tracker", t);
    return tb.dataset.tsort ? renderTrackerBody() : renderTracker();
  }
  const el = e.target.closest("[data-action]");
  if (!el) return;
  const holder = el.closest("[data-id]");
  const id = Number(el.dataset.id || (holder && holder.dataset.id));
  switch (el.dataset.action) {
    case "select": return selectJob(id);
    case "refresh": return startRefresh();
    case "status": return setStatus(id, el.dataset.status);
    case "upload": return uploadResume();
    case "save-profile": return saveProfile();
    case "add-app": return addApplication();
    case "export-csv": return exportCsv();
    case "backup-now": return backupNow();
    case "restore-backup": return restoreFrom(el.dataset.name);
    case "edit-app": return openEdit(id);
    case "save-edit": return saveEdit(id);
    case "delete-app": return deleteEntries([id]);
    case "delete-picked": return deleteEntries([...S.picked]);
    case "clear-picked": S.picked.clear(); return renderTrackerBody();
    case "skin": setSkin(el.dataset.skin); return currentView() === "dashboard" ? renderDashboard() : undefined;
    case "insights": return runInsights();
    case "quick": S.f[el.dataset.k] = S.f[el.dataset.k] === el.dataset.v ? "all" : el.dataset.v; store.set("filters3", S.f); return renderJobs();
    case "cold-email": return draftColdEmail(el);
    case "ce-compose": return openCompose({ to: S.coldTo, text: $("#ce-text").value, jobId: Number(el.dataset.id) });
    case "msg-send": { const t = S.chatMsgs[Number(el.dataset.i)].content; return openCompose({ to: (t.match(/[\w.+-]+@[\w-]+\.[\w.-]+/) || [""])[0], text: t }); }
    case "cm-send": return composeSend(el);
    case "cm-mailto": mailtoFrom(`Subject: ${$("#cm-subj").value}\n${$("#cm-body").value}`, $("#cm-to").value);
      toast("After you send it, press Mark as sent on the contact so it counts as an application.", 6000); return;
    case "mail-connect": return mailConnect();
    case "mail-disconnect": if (confirm("Disconnect Gmail? The app password is removed from this PC.")) { S.state.mail = await api("/api/mail/settings", { method: "DELETE" }); renderSettings(); } return;
    case "mail-sync": return mailSync();
    case "mail-open": return openMailReview();
    case "mail-apply": return mailReview(true);
    case "mail-dismiss": return mailReview(false);
    case "or-extract": return orExtract();
    case "or-unshot": S.orShots.splice(Number(el.dataset.i), 1); return addShots([]);
    case "or-add": S.orContacts.unshift({ id: Math.random().toString(16).slice(2, 12), name: "", email: "", title: "", company: "", job_role: "", notes: "" }); saveContactsSoon(); return drawContacts();
    case "or-del": S.orContacts = S.orContacts.filter(c => c !== contactOf(el)); saveContactsSoon(); return drawContacts();
    case "or-draft": return orDraft(el);
    case "or-postings": try { const r = await api("/api/outreach/from-postings", { method: "POST" }); S.orContacts = r.contacts; drawContacts(); toast(r.added ? `Added ${r.added} contact(s) from job postings` : "No new contacts in the postings.", 3500); } catch (e) { toast(e.message, 7000); } return;
    case "or-track": return orTrack(el);
    case "or-marksent": return orMarkSent(el);
    case "ce-copy": try { await navigator.clipboard.writeText($("#ce-text").value); toast("Copied", 1500); } catch { toast("Couldn't copy"); } return;
    case "ce-mail": return mailtoFrom($("#ce-text").value, S.coldTo);
    case "save-target": return saveTarget();
    case "test-reminder": try { await api("/api/notify/test", { method: "POST" }); toast("Sent - check the corner of your screen.", 3000); } catch (e) { toast(e.message, 7000); } return;
    case "prov-open": case "prov-save": case "prov-remove": case "prov-up": return providerAction(el, el.dataset.action);
    case "cover-letter": return openCoverLetter(id);
    case "cl-make": return makeCoverLetter(Number(el.dataset.id) || null);
    case "cl-pdf": return downloadLetterPdf();
    case "cl-unattach": S.clExtra.splice(Number(el.dataset.i), 1); return drawAttached();
    case "cl-copy": try { await navigator.clipboard.writeText($("#cl-text").value); toast("Copied", 1500); } catch { toast("Couldn't copy"); } return;
    case "cl-save": return saveText($("#cl-text").value, `cover-letter-${($("#cl-company").value || "job").replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.txt`);
    case "coach-send": return coachSend();
    case "coach-ask": return coachSend(el.dataset.q);
    case "chat-unattach": (el.dataset.t === "f" ? S.chatFiles : S.chatImages).splice(Number(el.dataset.i), 1); return drawChatAttached();
    case "msg-copy": try { await navigator.clipboard.writeText(S.chatMsgs[Number(el.dataset.i)].content); toast("Copied", 1500); } catch { toast("Couldn't copy"); } return;
    case "msg-pdf": return downloadDoc(S.chatMsgs[Number(el.dataset.i)].content, "pdf");
    case "msg-docx": return downloadDoc(S.chatMsgs[Number(el.dataset.i)].content, "docx");
    case "msg-mail": return mailtoFrom(S.chatMsgs[Number(el.dataset.i)].content);
    case "coach-clear": try { drawChat((await api("/api/ai/chat", { method: "DELETE" })).messages); } catch (e) { toast(e.message); } return;
    case "save-source-keys": {
      const body = {};
      $$("input[data-key]").forEach(i => { if (i.value.trim()) body[i.dataset.key] = i.value.trim(); });
      if (!Object.keys(body).length) { toast("Paste at least one key."); return; }
      try { await api("/api/settings/keys", { method: "PUT", json: body }); toast("Saved. Press Find new jobs to search with the new sources.", 6000); await loadAll(); }
      catch (err) { toast(err.message); }
      return;
    }
    case "delete-resume":
      if (!confirm("Remove this resume from JobQuest.AI? Your original file isn't touched.")) return;
      try { const r = await api(`/api/resumes/${el.dataset.id}`, { method: "DELETE" }); S.state.profile = r.profile; renderProfile(); } catch (err) { toast(err.message); }
      return;
    case "assess":
      el.disabled = true; el.innerHTML = `${bi("arrow-repeat", "spin")} Asking the AI…`;
      try { const j = await api(`/api/jobs/${el.dataset.id}/assess`, { method: "POST" }); mergeJob(j); refreshViewsAfterChange(j.id); toast("AI check updated.", 2500); }
      catch (err) { toast(err.message, 7000); el.disabled = false; el.innerHTML = `${bi("stars")} Ask AI again`; }
      return;
    case "save-details":
      try {
        const j = await api(`/api/jobs/${el.dataset.id}`, { method: "PATCH", json: { status: $("#d-status").value, notes: $("#d-notes").value } });
        mergeJob(j); refreshViewsAfterChange(j.id); toast("Saved.");
      } catch (err) { toast(err.message); }
      return;
  }
});

document.addEventListener("paste", e => {
  if (currentView() !== "outreach") return;
  const imgs = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith("image/"));
  if (imgs.length) { e.preventDefault(); addShots(imgs); }
});

document.addEventListener("keydown", e => {
  const card = e.target.closest && e.target.closest(".job-card");
  if (!card) return;
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectJob(Number(card.dataset.id)); }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const next = e.key === "ArrowDown" ? card.nextElementSibling : card.previousElementSibling;
    if (next && next.classList.contains("job-card")) { next.focus(); selectJob(Number(next.dataset.id)); }
  }
});

document.addEventListener("change", async e => {
  const t = e.target;
  if (t.dataset.f) {
    S.f[t.dataset.f] = t.type === "checkbox" ? t.checked : (t.dataset.f === "days" ? Number(t.value) : t.value);
    store.set("filters3", S.f);
    if (t.dataset.f === "showAll") renderJobs(); else renderList();
    return;
  }
  if (t.id === "pick-all" || "pick" in t.dataset) {
    if (t.id === "pick-all") trackerFiltered().forEach(j => t.checked ? S.picked.add(j.id) : S.picked.delete(j.id));
    else { const id = Number(t.closest("[data-id]").dataset.id); t.checked ? S.picked.add(id) : S.picked.delete(id); }
    return renderTrackerBody();
  }
  if (t.id === "bulk-status" && t.value) return bulkStatus(t.value);
  if (t.id === "or-show") return drawContacts();
  if (t.dataset.action === "track-status") {
    const id = Number(t.closest("[data-id]").dataset.id);
    try { mergeJob(await api(`/api/jobs/${id}`, { method: "PATCH", json: { status: t.value } })); renderShell(); renderTracker(); toast(t.value ? `Moved to ${STATUS[t.value]}` : "Removed from tracker", 2000); } catch (err) { toast(err.message); }
  }
});

let qTimer;
document.addEventListener("keydown", e => {
  if (e.target.id === "chat-in" && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); coachSend(); }
});

document.addEventListener("input", e => {
  if (e.target.id === "or-q") { S.orQ = e.target.value; return drawContacts(); }
  const cEl = e.target.closest && e.target.closest("[data-cid]");
  if (cEl && e.target.dataset.k) { contactOf(e.target)[e.target.dataset.k] = e.target.value; saveContactsSoon(); return; }
  if (e.target.id === "tq") {
    clearTimeout(qTimer);
    qTimer = setTimeout(() => { S.t.q = e.target.value; store.set("tracker", S.t); renderTrackerBody(); }, 150);
    return;
  }
  if (e.target.id !== "q") return;
  clearTimeout(qTimer);
  qTimer = setTimeout(() => { S.f.q = e.target.value; store.set("filters3", S.f); renderList(); }, 180);
});

let wasNarrow = narrow();
window.addEventListener("resize", () => {
  if (narrow() !== wasNarrow) { wasNarrow = narrow(); hideSheet(); if ($("#list")) renderList(); }
});

loadAll().catch(err => {
  $("#app").innerHTML = `<div class="alert alert-danger mt-4">Couldn't load JobQuest.AI: ${esc(err.message)}</div>`;
});
