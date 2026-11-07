"use strict";
/* App shell: model switcher, command palette, styled confirm dialog, search lanes. Loaded after app.js. */

// the page's security policy blocks style="" attributes, so sizes and colours are set from script instead
function paint(root = document) {
  $$("[data-w]", root).forEach(el => { el.style.width = `${el.dataset.w}%`; });
  $$("[data-bg]", root).forEach(el => { el.style.background = el.dataset.bg; });
}

const ACT = {};          // data-act="name" buttons; pages add their own handlers here
document.addEventListener("click", e => {
  const el = e.target.closest("[data-act]");
  if (el && ACT[el.dataset.act]) { e.preventDefault(); ACT[el.dataset.act](el); }
});

// a styled replacement for confirm(); resolves true only on the confirm button
function ask(title, body, ok = "Continue", danger = false) {
  return new Promise(resolve => {
    const box = $("#ask-modal"), m = bootstrap.Modal.getOrCreateInstance(box);
    $("#ask-title").textContent = title;
    $("#ask-body").textContent = body;
    const yes = $("#ask-yes");
    yes.textContent = ok;
    yes.className = `btn btn-sm ${danger ? "btn-danger" : "btn-ink"}`;
    let answer = false;
    yes.onclick = () => { answer = true; m.hide(); };
    box.addEventListener("hidden.bs.modal", () => resolve(answer), { once: true });
    m.show();
  });
}

// ---------------------------------------------------------------- model switcher

const msText = p => p.ok ? `${p.ms} ms` : (p.ready ? "not responding" : "");
const speed = p => !p.ok ? "down" : p.ms < 800 ? "fast" : p.ms < 2500 ? "ok" : "slow";

function switcherHTML() {
  const s = S.llm;
  if (!s) return "";
  const live = s.providers.filter(p => p.ready);
  const cur = s.chat && s.providers.find(p => p.id === s.chat.provider);
  if (!cur) return `<a class="llm-pill" href="#/settings"><span class="dot-live down"></span>Add an AI key</a>`;
  const row = (p, act, on) => `<li><button class="dropdown-item d-flex align-items-center gap-2 ${on ? "active" : ""}" data-act="${act}" data-pid="${p.id}" data-model="${esc(p.model)}">
      <span class="dot-live ${speed(p)}" aria-hidden="true"></span><span class="text-truncate"><b>${esc(p.label.split(" (")[0])}</b> <small class="opacity-75">${esc(p.model)}</small></span>
      <span class="ms-auto small opacity-75">${msText(p)}</span></button></li>`;
  return `<div class="dropdown"><button class="llm-pill" type="button" data-bs-toggle="dropdown" data-bs-display="static" aria-expanded="false" title="Model for chat, emails and letters">
      <span class="dot-live ${speed(cur)}" aria-hidden="true"></span><span class="llm-name">${esc(s.chat.model)}</span><span class="llm-ms">${cur.ok ? cur.ms + " ms" : "down"}</span>${bi("chevron-down", "small")}</button>
    <ul class="dropdown-menu dropdown-menu-end llm-menu">
      <li><h6 class="dropdown-header">Chat, emails and letters</h6></li>${live.map(p => row(p, "llm-chat", p.id === s.chat.provider)).join("")}
      <li><hr class="dropdown-divider"></li><li><h6 class="dropdown-header">Job screening</h6></li>
      ${live.map(p => row(p, "llm-screen", s.screening && p.id === s.screening.provider)).join("")}
      <li><hr class="dropdown-divider"></li>
      <li><button class="dropdown-item" data-act="llm-ping">${bi("arrow-repeat")} Check status now</button></li>
      <li><a class="dropdown-item" href="#/settings">${bi("gear")} Manage models</a></li></ul></div>`;
}

function drawSwitchers() {
  $$(".llm-switch").forEach(el => { el.innerHTML = switcherHTML(); });
  $$("[data-taskbox]").forEach(el => { el.innerHTML = taskPicker(el.dataset.taskbox); });
  $$("[data-jobmodels]").forEach(el => { el.innerHTML = jobModels(); });
}

const liveModels = () => (S.llm ? S.llm.providers : []).filter(p => p.ready);
const modelLabel = p => `${p.label.split(" (")[0]} · ${p.model}`;

// one area's model: the Dashboard's analysis, or Cold email
function taskPicker(task) {
  const s = S.llm;
  if (!s) return "";
  if (s.force) return `<span class="small text-body-secondary">${bi("lock")} ${esc(s.force.model)} for all tasks</span>`;
  const cur = s.tasks[task] || {};
  return `<select class="form-select form-select-sm w-auto" data-task="${task}" aria-label="Model for this page"><option value="">Model: default (${esc(s.chat ? s.chat.model : "none")})</option>
    ${liveModels().map(p => `<option value="${p.id}|${esc(p.model)}" ${cur.provider === p.id ? "selected" : ""}>${esc(modelLabel(p))}</option>`).join("")}</select>`;
}

// tick several models for job search: the first reads every job, the others compare on the best ones
function jobModels() {
  const s = S.llm;
  if (!s || !s.screening) return "";
  if (s.force) return `<span class="small text-body-secondary">${bi("lock")} Searching with ${esc(s.force.model)}</span>`;
  const extra = new Set(s.compare.map(c => c.provider));
  return `<div class="dropdown"><button class="btn btn-sm btn-outline-secondary dropdown-toggle" type="button" data-bs-toggle="dropdown" data-bs-auto-close="outside" aria-expanded="false">${bi("cpu")} Search with ${1 + extra.size} model${extra.size ? "s" : ""}</button>
    <div class="dropdown-menu p-2 job-models">${liveModels().map(p => { const main = p.id === s.screening.provider; return `<div class="form-check"><input class="form-check-input" type="checkbox" id="jm-${p.id}" data-jm="${p.id}" data-model="${esc(p.model)}" ${main || extra.has(p.id) ? "checked" : ""} ${main ? "disabled" : ""}><label class="form-check-label" for="jm-${p.id}">${esc(modelLabel(p))}${main ? " · main" : ""}</label></div>`; }).join("")}
    <div class="small text-body-secondary mt-1">The main model reads every job. Ticked models also read the best ${s.compare_jobs}. Change the main model in the top bar.</div></div></div>`;
}

document.addEventListener("change", async e => {
  const t = e.target;
  try {
    if (t.dataset.task) {
      const [provider, model] = t.value ? t.value.split("|") : ["", ""];
      S.llm = await api("/api/ai/task", { method: "PUT", json: { task: t.dataset.task, provider, model } });
      toast(provider ? `This page now uses ${model}` : "This page uses the default model", 2200);
    } else if (t.dataset.jm) {
      const models = $$("[data-jm]").filter(c => c.checked && !c.disabled).map(c => ({ provider: c.dataset.jm, model: c.dataset.model }));
      S.llm = await api("/api/ai/compare", { method: "PUT", json: { models } });
      toast("Saved. It applies on the next search.", 2500);
    } else if (t.id === "force-model") {
      const [provider, model] = t.value ? t.value.split("|") : ["", ""];
      if (provider) toast(`Testing ${model}…`, 15000);
      S.llm = await api("/api/ai/force", { method: "PUT", json: { provider, model } });
      toast(provider ? `${model} now does every task` : "Each task uses its own model again", 3500);
      drawSwitchers(); const b = $("#compare-box"); if (b) b.innerHTML = compareHTML();
    } else if (t.dataset.provOn) {
      const data = await api(`/api/ai/providers/${t.dataset.provOn}/enabled`, { method: "PUT", json: { enabled: t.checked } });
      S.state = await api("/api/state"); renderShell(); renderProviders(data);
      await loadSwitcher(); const b = $("#compare-box"); if (b) b.innerHTML = compareHTML();
    }
  } catch (err) {
    toast(err.message, 8000);
    await loadSwitcher(); const b = $("#compare-box"); if (b) b.innerHTML = compareHTML();
  }
});

async function loadSwitcher(fresh = false) {
  try { S.llm = await api(`/api/ai/status${fresh ? "?fresh=true" : ""}`); drawSwitchers(); }
  catch { /* the next tick tries again */ }
}
setInterval(() => { if (!document.hidden) loadSwitcher(); }, 60000);

ACT["llm-ping"] = async () => { toast("Checking every model…", 2500); await loadSwitcher(true); toast("Status updated.", 1800); };
ACT["llm-chat"] = async el => {
  try { S.llm = await api("/api/ai/active", { method: "PUT", json: { provider: el.dataset.pid, model: el.dataset.model } }); drawSwitchers(); toast(`Now using ${el.dataset.model}`, 2200); }
  catch (e) { toast(e.message, 6000); }
};
ACT["llm-screen"] = async el => {
  const json = { provider: el.dataset.pid, model: el.dataset.model, screening: true };
  try {
    const r = await api("/api/ai/active", { method: "PUT", json });
    if (r.needs_confirm) {
      const go = await ask("Change the screening model?", `${el.dataset.model} will read ${r.rechecks} job${r.rechecks === 1 ? "" : "s"} again, up to ${r.limit} per search, which uses tokens on that account.`, "Change model");
      if (!go) return;
      S.llm = await api("/api/ai/active", { method: "PUT", json: { ...json, confirmed: true } });
    } else S.llm = r;
    drawSwitchers(); toast(`Screening with ${el.dataset.model}`, 2500);
  } catch (e) { toast(e.message, 6000); }
};

// which extra models read the best jobs on each search (Settings)
function compareHTML() {
  const s = S.llm;
  if (!s) return "";
  const force = `<h5 class="mt-3 mb-1">One model for every task</h5><p class="small text-body-secondary mb-2">Chat, emails, letters, analysis and job screening all use this model, and other choices are ignored. It's tested first, so it must be a working chat model. If it fails, the task fails instead of switching to another model.</p>
    <select class="form-select form-select-sm w-auto" id="force-model" aria-label="One model for every task"><option value="">Off: each task uses its own model</option>${liveModels().map(p => `<option value="${p.id}|${esc(p.model)}" ${s.force && s.force.provider === p.id ? "selected" : ""}>${esc(modelLabel(p))}</option>`).join("")}</select>`;
  if (s.force) return force;
  const main = s.screening ? s.screening.provider : "";
  const on = new Set(s.compare.map(c => c.provider));
  const rows = s.providers.filter(p => p.ready && p.id !== main).map(p => `<div class="form-check"><input class="form-check-input" type="checkbox" id="cmp-${p.id}" data-cmp="${p.id}" data-model="${esc(p.model)}" ${on.has(p.id) ? "checked" : ""}>
      <label class="form-check-label" for="cmp-${p.id}">${esc(p.label.split(" (")[0])} <span class="small text-body-secondary">${esc(p.model)}${p.id === "ollama" ? " · free, slower" : ""}</span></label></div>`).join("");
  return `${force}<h5 class="mt-3 mb-1">Compare models on each search</h5>
    <p class="small text-body-secondary mb-2">Your screening model reads every job as usual. Models ticked here also read the best jobs, and their scores appear on the job cards. They never change your main score.</p>
    ${rows || `<div class="small text-body-secondary">Add a second provider above to compare models.</div>`}
    ${rows ? `<div class="d-flex align-items-center gap-2 mt-2"><label class="small fw-semibold" for="cmp-jobs">Jobs per model</label><input type="number" min="1" max="150" class="form-control form-control-sm target-in" id="cmp-jobs" value="${s.compare_jobs}">
      <button class="btn btn-sm btn-ink" data-act="cmp-save">Save</button></div>` : ""}`;
}
ACT["custom-add"] = async el => {
  const err = $("#cu-err");
  err.classList.add("d-none"); el.disabled = true; toast("Testing the model…", 20000);
  try {
    const data = await api("/api/ai/custom", { method: "POST", json: { label: $("#cu-label").value, base_url: $("#cu-url").value, model: $("#cu-model").value, api_key: $("#cu-key").value } });
    toast(`Added ${data.model}`, 4000);
    S.state = await api("/api/state"); renderShell(); renderProviders(data);
    await loadSwitcher(true); const b = $("#compare-box"); if (b) b.innerHTML = compareHTML();
  } catch (e) { err.textContent = e.message; err.classList.remove("d-none"); el.disabled = false; toast("That model didn't work.", 3000); }
};
ACT["cmp-save"] = async () => {
  const models = $$("[data-cmp]").filter(c => c.checked).map(c => ({ provider: c.dataset.cmp, model: c.dataset.model }));
  try { S.llm = await api("/api/ai/compare", { method: "PUT", json: { models, jobs: Number($("#cmp-jobs").value) } }); toast("Saved. It applies on the next search.", 3000); }
  catch (e) { toast(e.message, 6000); }
};

// one lane per model while a search runs
function drawLanes(p) {
  const host = $("#progress-lanes");
  if (!host) return;
  const lanes = p.models || [];
  host.classList.toggle("d-none", !lanes.length);
  host.innerHTML = lanes.map(l => {
    const pct = l.total ? Math.round(100 * (l.done || 0) / l.total) : (l.state === "done" ? 100 : 0);
    return `<div class="lane ${l.state}"><span class="lane-name">${esc(l.model)}${l.main ? " · main" : ""}</span>
      <span class="lane-track"><span class="lane-fill" data-w="${pct}"></span></span>
      <span class="lane-count">${l.state === "failed" ? `${bi("exclamation-triangle")} failed` : `${l.done || 0}/${l.total || 0}`}</span></div>`;
  }).join("");
  paint(host);
}

// ---------------------------------------------------------------- command palette (Ctrl+K)

const PAGES = [["jobs", "Jobs", "briefcase"], ["outreach", "Cold email", "envelope-at"], ["tracker", "Tracker", "kanban"], ["dashboard", "Dashboard", "bar-chart-line"],
  ["coach", "Coach", "chat-dots"], ["profile", "Profile", "person-badge"], ["settings", "Settings", "gear"]];

function paletteItems(q) {
  q = q.trim().toLowerCase();
  const hit = s => !q || s.toLowerCase().includes(q);
  const out = PAGES.filter(p => hit(p[1])).map(([v, l, ic]) => ({ ic, label: `Go to ${l}`, run: () => { location.hash = `#/${v}`; } }));
  [["arrow-repeat", "Find new jobs", () => startRefresh()], ["plus-lg", "Add an application", () => { location.hash = "#/tracker"; setTimeout(() => $("#add-app")?.classList.add("show"), 200); }],
    ["send", "Compose a batch of cold emails", () => { location.hash = "#/outreach"; setTimeout(() => ACT["batch-open"](), 400); }],
    ["file-earmark-spreadsheet", "Export tracker to Excel", () => { location.href = "/api/tracker/export.xlsx"; }],
    ["shield-check", "Back up now", () => backupNow()]].forEach(([ic, label, run]) => { if (hit(label)) out.push({ ic, label, run }); });
  if (q.length >= 2) {
    S.jobs.filter(j => `${j.company} ${j.title}`.toLowerCase().includes(q)).slice(0, 8).forEach(j => out.push({
      ic: TRACK.includes(j.status) ? "kanban" : "briefcase", label: `${j.title} · ${j.company}`,
      run: () => { if (TRACK.includes(j.status)) { S.t.q = j.company; location.hash = "#/tracker"; route(); } else { S.f.q = j.company; location.hash = "#/jobs"; route(); } } }));
  }
  return out.slice(0, 12);
}

function drawPalette() {
  S.palItems = paletteItems($("#pal-in").value);
  S.palIdx = Math.min(S.palIdx || 0, Math.max(0, S.palItems.length - 1));
  $("#pal-list").innerHTML = S.palItems.map((x, i) => `<button class="pal-item ${i === S.palIdx ? "on" : ""}" data-act="pal-run" data-i="${i}">${bi(x.ic)}<span class="text-truncate">${esc(x.label)}</span></button>`).join("")
    || `<div class="p-3 small text-body-secondary">Nothing found.</div>`;
}
function openPalette() {
  $("#pal-in").value = ""; S.palIdx = 0; drawPalette();
  const box = $("#palette");
  box.addEventListener("shown.bs.modal", () => $("#pal-in").focus(), { once: true });
  bootstrap.Modal.getOrCreateInstance(box).show();
}
function runPalette(i) { const it = S.palItems[i]; bootstrap.Modal.getInstance($("#palette"))?.hide(); if (it) it.run(); }
ACT["pal-open"] = openPalette;
ACT["pal-run"] = el => runPalette(Number(el.dataset.i));
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); return openPalette(); }
  if (e.target.id !== "pal-in") return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); S.palIdx = (S.palIdx + (e.key === "ArrowDown" ? 1 : -1) + S.palItems.length) % (S.palItems.length || 1); drawPalette(); }
  if (e.key === "Enter") { e.preventDefault(); runPalette(S.palIdx); }
});
document.addEventListener("input", e => { if (e.target.id === "pal-in") { S.palIdx = 0; drawPalette(); } });

ACT["nav-slim"] = () => { const on = document.body.classList.toggle("nav-slim"); store.set("slim", on ? "1" : ""); };
if (store.get("slim", "") === 1 || store.get("slim", "") === "1") document.body.classList.add("nav-slim");

ACT["prep"] = async el => {
  if (needAI()) return;
  toast("Writing your prep sheet…", 20000);
  try {
    const v = await api("/api/ai/interview-prep", { method: "POST", json: { job_id: Number(el.dataset.id) } });
    bootstrap.Modal.getInstance($("#edit-modal"))?.hide();
    S.convId = v.conversation.id; toast("Prep sheet ready.", 2000);
    if (currentView() === "coach") renderCoach(); else location.hash = "#/coach";
  } catch (e) { toast(e.message, 8000); }
};
