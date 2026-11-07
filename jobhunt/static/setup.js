"use strict";
/* First-run setup: create a profile, connect an AI model, connect Gmail. Every step after the profile can be skipped. */

const SETUP_STEPS = ["Profile", "AI model", "Gmail", "Ready"];

function renderSetup() {
  S.setupStep = S.setupStep || 0;
  const step = S.setupStep;
  $("#app").innerHTML = `<div class="setup rise">
    <div class="setup-head"><img src="/static/icon.png" width="56" height="56" alt=""><div><h1 class="mb-0">Set up JobQuest<span class="text-accent">.AI</span></h1>
      <div class="text-body-secondary">Everything you enter stays on this PC.</div></div></div>
    <ol class="setup-steps">${SETUP_STEPS.map((s, i) => `<li class="${i === step ? "now" : i < step ? "done" : ""}" ${i === step ? 'aria-current="step"' : ""}><span>${i < step ? bi("check2") : i + 1}</span>${s}</li>`).join("")}</ol>
    <div class="card"><div class="card-body p-4" id="setup-body"></div></div></div>`;
  [setupProfile, setupAI, setupGmail, setupReady][step]();
}

const setupGo = n => { S.setupStep = n; renderSetup(); window.scrollTo({ top: 0 }); };
const setupNav = (next, skip = true) => `<div class="d-flex flex-wrap align-items-center gap-2 mt-4">
    ${S.setupStep ? `<button class="btn btn-outline-secondary" data-act="setup-back">${bi("arrow-left")} Back</button>` : ""}
    <span class="ms-auto"></span>
    ${skip ? `<button class="btn btn-link" data-act="setup-skip">Skip for now</button>` : ""}${next}</div>`;

function setupResumes() {
  const host = $("#su-resumes");
  if (host) host.innerHTML = S.state.profile.resumes.map(r => `<div class="small mt-2">${bi("check2-circle", "text-success")} ${esc(r.filename)}</div>`).join("");
}

function setupProfile() {
  const p = S.state.profile, prefs = p.preferences;
  const role = (v, label) => `<input type="checkbox" class="btn-check" id="su-role-${v}" name="su-role" value="${v}" autocomplete="off" ${prefs.roles.includes(v) ? "checked" : ""}><label class="btn btn-sm btn-outline-secondary rounded-pill" for="su-role-${v}">${label}</label>`;
  $("#setup-body").innerHTML = `<h4>Create your profile</h4><p class="text-body-secondary">Your resume is what every job gets ranked against.</p>
    <label class="drop d-grid text-center p-4 gap-1" id="su-drop" for="su-file"><span class="fs-2 text-accent">${bi("cloud-arrow-up")}</span><span class="fw-semibold">Drop your resume here or click to choose</span><span class="small text-body-secondary">PDF or DOCX, up to 5 MB</span></label>
    <input type="file" id="su-file" class="d-none" accept=".pdf,.docx,.txt">
    <div class="small text-body-secondary mt-1" id="su-msg" role="status"></div><div id="su-resumes"></div>
    <div class="row g-3 mt-1">
      <div class="col-sm-6"><label class="form-label fw-semibold" for="su-name">Your name</label><input class="form-control" id="su-name" value="${esc(p.name)}" autocomplete="name"></div>
      <div class="col-sm-6"><label class="form-label fw-semibold" for="su-city">Current city</label><select class="form-select" id="su-city">${["", ...CITIES].map(c => `<option value="${esc(c)}" ${c === prefs.current_city ? "selected" : ""}>${c || "Choose your city"}</option>`).join("")}</select></div>
      <div class="col-sm-6"><div class="form-label fw-semibold">Roles to search</div><div class="d-flex flex-wrap gap-2">${role("SDE", "Software")}${role("ML", "ML / AI / data")}</div></div>
      <div class="col-sm-6"><label class="form-label fw-semibold" for="su-years">Experience, up to</label><select class="form-select" id="su-years">${[[0, "No experience"], [1, "1 year"], [2, "2 years"], [3, "3 years"], [4, "4 years"], [5, "5 years"]].map(([v, l]) => `<option value="${v}" ${v === prefs.max_years ? "selected" : ""}>${l}</option>`).join("")}</select></div>
      <div class="col-12"><div class="form-check form-switch"><input class="form-check-input" type="checkbox" role="switch" id="su-intern" ${prefs.include_internships ? "checked" : ""}><label class="form-check-label" for="su-intern">Include internships</label></div></div>
      <div class="col-12"><div id="su-err" class="alert alert-danger py-2 small mb-0 d-none" role="alert"></div></div>
    </div>${setupNav(`<button class="btn btn-primary" data-act="setup-profile">Continue ${bi("arrow-right")}</button>`, false)}`;
  setupResumes();
  const drop = $("#su-drop"), file = $("#su-file");
  ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, () => drop.classList.remove("over")));
  drop.addEventListener("drop", e => { e.preventDefault(); if (e.dataTransfer.files[0]) setupUpload(e.dataTransfer.files[0]); });
  file.addEventListener("change", () => { if (file.files[0]) setupUpload(file.files[0]); file.value = ""; });
}

async function setupUpload(file) {
  const msg = $("#su-msg"), form = new FormData();
  form.append("file", file); form.append("label", "");
  msg.classList.remove("text-danger"); msg.textContent = `Reading ${file.name}…`;
  try {
    const r = await api("/api/resumes", { method: "POST", form });
    S.state.profile = r.profile; msg.textContent = ""; setupResumes();
    const name = $("#su-name");
    if (name && !name.value.trim()) name.value = r.profile.name || "";
  } catch (e) { msg.textContent = e.message; msg.classList.add("text-danger"); }
}

ACT["setup-profile"] = async () => {
  const err = $("#su-err"), fail = t => { err.textContent = t; err.classList.remove("d-none"); };
  const name = $("#su-name").value.trim(), roles = $$('input[name="su-role"]:checked').map(i => i.value);
  if (!name) return fail("Enter your name.");
  if (!roles.length) return fail("Pick at least one role.");
  try {
    const r = await api("/api/profile", { method: "PUT", json: { name, roles, current_city: $("#su-city").value, max_years: Number($("#su-years").value), include_internships: $("#su-intern").checked } });
    S.state.profile = r.profile; setupGo(1);
  } catch (e) { fail(e.message); }
};

async function setupAI() {
  const host = $("#setup-body");
  host.innerHTML = `<h4>Connect an AI model</h4><div class="text-body-secondary small">Loading…</div>`;
  let data;
  try { data = await api("/api/ai/providers"); } catch (e) { host.textContent = e.message; return; }
  const ready = data.providers.filter(p => p.ready), keyed = data.providers.filter(p => p.needs_key);
  keyed.sort((a, b) => b.free - a.free);
  host.innerHTML = `<h4>Connect an AI model</h4><p class="text-body-secondary">The AI scores jobs and writes your emails. Paste a key from any one service. Gemini and Groq have free tiers. Your key is encrypted with your Windows account.</p>
    ${ready.length ? `<div class="alert alert-success py-2 small">${bi("check2-circle")} Ready: ${ready.map(p => esc(p.label)).join(", ")}</div>` : ""}
    <div class="row g-3">
      <div class="col-sm-6"><label class="form-label fw-semibold" for="su-prov">Service</label><select class="form-select" id="su-prov">${keyed.map(p => `<option value="${esc(p.id)}" data-page="${esc(p.key_page)}">${esc(p.label)}${p.free ? " (free tier)" : ""}</option>`).join("")}</select></div>
      <div class="col-sm-6"><label class="form-label fw-semibold" for="su-key">API key</label><input type="password" class="form-control" id="su-key" autocomplete="off"></div>
      <div class="col-12 d-flex flex-wrap align-items-center gap-2"><a class="btn btn-sm btn-outline-secondary" id="su-keylink" target="_blank" rel="noopener noreferrer">Get a key ${bi("box-arrow-up-right")}</a>
        <button class="btn btn-sm btn-ink" data-act="setup-key">${bi("plug")} Test and save</button></div>
      <div class="col-12"><div id="su-err" class="alert alert-danger py-2 small mb-0 d-none" role="alert"></div></div>
    </div>${setupNav(ready.length ? `<button class="btn btn-primary" data-act="setup-next">Continue ${bi("arrow-right")}</button>` : "", !ready.length)}`;
  const prov = $("#su-prov"), link = () => { $("#su-keylink").href = safeUrl("https://" + prov.selectedOptions[0].dataset.page); };
  prov.addEventListener("change", link); link();
}

ACT["setup-key"] = async () => {
  const err = $("#su-err"), key = $("#su-key").value.trim();
  err.classList.add("d-none");
  if (!key) { err.textContent = "Paste your API key first."; err.classList.remove("d-none"); return; }
  try {
    toast("Testing…", 20000);
    await api(`/api/ai/providers/${$("#su-prov").value}`, { method: "PUT", json: { api_key: key, model: null } });
    S.state = await api("/api/state");
    toast("It works.", 2500); loadSwitcher(); setupGo(2);
  } catch (e) { toast("That key didn't work.", 2500); err.textContent = e.message; err.classList.remove("d-none"); }
};

function setupGmail() {
  const m = S.state.mail;
  $("#setup-body").innerHTML = `<h4>Connect Gmail</h4>${m.connected
    ? `<div class="alert alert-success py-2 small">${bi("check2-circle")} Connected as ${esc(m.address)}</div>${setupNav(`<button class="btn btn-primary" data-act="setup-next">Continue ${bi("arrow-right")}</button>`, false)}`
    : `${gmailWizard()}${setupNav("")}`}`;
}

function setupReady() {
  const st = S.state, row = (ok, text, todo) => `<li class="mb-2">${ok ? bi("check2-circle", "text-success") : bi("circle", "text-body-secondary")} ${ok ? text : `<span class="text-body-secondary">${todo}</span>`}</li>`;
  const hasResume = st.profile.resumes.length > 0;
  $("#setup-body").innerHTML = `<h4>You're ready${st.profile.name ? ", " + esc(st.profile.name.split(" ")[0]) : ""}</h4>
    <ul class="list-unstyled mt-3">
      ${row(hasResume, "Resume added", "No resume yet: you can add it in Profile")}
      ${row(st.ai.configured, `AI connected: ${esc((st.ai.provider || "").split(" (")[0])}`, "No AI model yet: add one in Settings")}
      ${row(st.mail.connected, `Gmail connected: ${esc(st.mail.address || "")}`, "Gmail not connected: you can do this in Settings")}
    </ul>${setupNav(`<button class="btn btn-primary" data-act="setup-finish" data-find="${hasResume ? 1 : ""}">${hasResume ? `${bi("search")} Find my first jobs` : "Open JobQuest.AI"}</button>`, false)}`;
}

ACT["setup-back"] = () => setupGo(S.setupStep - 1);
ACT["setup-next"] = ACT["setup-skip"] = () => setupGo(S.setupStep + 1);
ACT["setup-open"] = () => { S.setupStep = 0; location.hash = "#/setup"; };
ACT["setup-finish"] = async el => {
  try {
    await api("/api/setup/done", { method: "POST" });
    S.state.setup.needed = false; S.setupStep = 0;
    location.hash = "#/jobs";
    if (el.dataset.find) startRefresh();
  } catch (e) { toast(e.message, 5000); }
};
