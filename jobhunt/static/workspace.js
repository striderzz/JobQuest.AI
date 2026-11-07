"use strict";
/* Coach conversations, the batch email composer, the Gmail wizard and MongoDB Atlas backups. */

// ---------------------------------------------------------------- Coach

function modelOptions(conv) {
  const live = (S.llm ? S.llm.providers : []).filter(p => p.ready);
  const cur = conv.provider ? `${conv.provider}|${conv.model}` : "";
  return `<option value="">Default (the switcher's model)</option>` + live.map(p => {
    const v = `${p.id}|${conv.provider === p.id && conv.model ? conv.model : p.model}`;
    return `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(p.label.split(" (")[0])} · ${esc(v.split("|")[1])}</option>`;
  }).join("");
}

function coachView(v) {
  S.conv = v.conversation; S.convId = v.conversation.id; S.convList = v.conversations;
  const list = $("#conv-list");
  if (!list) return;
  list.innerHTML = v.conversations.map(c => `<div class="conv ${c.id === S.convId ? "on" : ""}"><button class="conv-open text-truncate" data-act="conv-open" data-id="${c.id}">${c.kind === "prep" ? bi("mic") + " " : ""}${esc(c.title)}</button>
      <button class="conv-del" data-act="conv-del" data-id="${c.id}" aria-label="Delete this chat">${bi("trash")}</button></div>`).join("");
  $("#conv-title").textContent = v.conversation.title;
  $("#conv-model").innerHTML = modelOptions(v.conversation);
  $("#starters").classList.toggle("d-none", v.messages.length > 0);
  drawChat(v.messages);
}

async function openConv(id) {
  try { coachView(await api(`/api/ai/chat${id ? `?conversation_id=${id}` : ""}`)); } catch (e) { toast(e.message, 6000); }
}

async function renderCoach() {
  $("#app").innerHTML = `${S.state.ai.configured ? "" : `<div class="alert alert-warning d-flex align-items-center gap-3"><span class="flex-grow-1"><b>Please add an LLM key.</b> The Coach needs one AI provider.</span><a class="btn btn-sm btn-ink" href="#/settings">Add key</a></div>`}
    <div class="coach-grid"><aside class="conv-side card"><div class="p-2 border-bottom d-grid gap-2"><button class="btn btn-sm btn-ink" data-act="conv-new">${bi("plus-lg")} New chat</button>
        <select class="form-select form-select-sm" id="prep-job" aria-label="Prepare for an interview"><option value="">Prepare for an interview…</option>${S.jobs.filter(j => ["oa", "interviewing", "screening", "applied"].includes(j.status)).sort((a, b) => TRACK.indexOf(b.status) - TRACK.indexOf(a.status)).slice(0, 60).map(j => `<option value="${j.id}">${esc(j.company)} · ${esc(j.title)}</option>`).join("")}</select></div>
      <div id="conv-list" class="conv-list"></div></aside>
    <div class="card coach"><div class="card-body p-0 d-flex flex-column">
      <div class="d-flex flex-wrap align-items-center gap-2 px-3 py-2 border-bottom"><b class="text-truncate me-auto" id="conv-title"></b>
        <label class="small text-body-secondary" for="conv-model">Model for this chat</label><select class="form-select form-select-sm w-auto" id="conv-model"></select></div>
      <div id="chat-log" class="chat-log p-3" aria-live="polite"></div>
      <div class="p-3 border-top">
        <div class="d-flex flex-wrap gap-2 mb-2" id="starters">${COACH_STARTERS.map(q => `<button class="btn btn-sm btn-outline-secondary rounded-pill" data-action="coach-ask" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="chat-attached" class="d-flex flex-wrap gap-2 mb-2"></div>
        <div class="input-group"><label class="btn btn-outline-secondary mb-0 d-flex align-items-center" for="chat-files" title="Attach CVs, job descriptions, recruiter screenshots or photos">${bi("paperclip")}</label>
          <input type="file" id="chat-files" class="d-none" multiple accept=".pdf,.docx,.txt,.md,image/png,image/jpeg,image/webp">
          <textarea class="form-control" id="chat-in" rows="2" placeholder="Ask anything, or attach files… (Enter to send, Shift+Enter for a new line)"></textarea>
          <button class="btn btn-ink" data-action="coach-send" aria-label="Send">${bi("send")}</button></div>
        <div class="small text-body-secondary mt-1">Sends your profile, tracker counts and attachments to the chosen AI provider, never your keys. Photos need a vision model.</div>
      </div></div></div></div>`;
  S.chatFiles = []; S.chatImages = [];
  $("#chat-files").addEventListener("change", attachChatFiles);
  await loadSwitcher();
  await openConv(S.convId);
}

ACT["conv-open"] = el => openConv(Number(el.dataset.id));
ACT["conv-new"] = async () => { try { coachView(await api("/api/ai/conversations", { method: "POST", json: {} })); $("#chat-in").focus(); } catch (e) { toast(e.message, 6000); } };
ACT["conv-del"] = async el => {
  if (!await ask("Delete this chat?", "Its messages are removed from this PC.", "Delete", true)) return;
  try { S.convId = null; coachView(await api(`/api/ai/conversations/${el.dataset.id}`, { method: "DELETE" })); } catch (e) { toast(e.message, 6000); }
};
ACT["next-step"] = el => { const box = $("#chat-in"); box.value = el.dataset.q; box.focus(); };
document.addEventListener("change", async e => {
  if (e.target.id === "conv-model") {
    const [provider, model] = e.target.value ? e.target.value.split("|") : ["", ""];
    try { S.conv = (await api(`/api/ai/conversations/${S.convId}`, { method: "PATCH", json: { provider, model } })).conversation; toast("This chat's model is saved.", 1800); } catch (err) { toast(err.message, 6000); }
  }
  if (e.target.id === "prep-job" && e.target.value) ACT["prep"]({ dataset: { id: e.target.value } });
});

// ---------------------------------------------------------------- batch composer

const MERGE_HELP = "{first_name} {company} {role} {title}";
const unsent = () => (S.orContacts || []).filter(c => c.email && !c.sent_at);

function batchStep1() {
  const list = unsent();
  S.batch = S.batch || { subject: "{role} at {company}", body: "Hi {first_name},\n\n\n\nThanks,\n" + (S.state.profile.name || "") };
  $("#ai-modal-body").innerHTML = `<h5 class="mb-1">${bi("send")} Batch email</h5><div class="small text-body-secondary mb-3">Tick who to write to, then fill a template or let AI write each one. Nothing is sent until you review every draft.</div>
    ${list.length ? `<div class="batch-list mb-3">${list.map(c => `<label class="batch-row"><input type="checkbox" class="form-check-input mt-0" data-bpick="${esc(c.id)}" checked><span class="text-truncate"><b>${esc(c.name || c.email)}</b> · ${esc(c.company || "no company")} · ${esc(c.job_role || "no role")}</span><small class="ms-auto text-body-secondary">${esc(c.email)}</small></label>`).join("")}</div>
    <input class="form-control form-control-sm mb-2" id="b-subj" value="${esc(S.batch.subject)}" aria-label="Subject template" placeholder="Subject">
    <textarea class="form-control form-control-sm letter" id="b-body" rows="7" aria-label="Message template">${esc(S.batch.body)}</textarea>
    <div class="form-text">Fields you can use: ${esc(MERGE_HELP)}</div>
    <div class="d-flex flex-wrap gap-2 mt-3"><button class="btn btn-sm btn-ink" data-act="batch-fill">${bi("input-cursor-text")} Fill template</button><button class="btn btn-sm btn-outline-secondary" data-act="batch-ai">${bi("stars")} Write each with AI</button><span class="small text-body-secondary align-self-center" id="b-status"></span></div>`
    : `<div class="text-body-secondary py-3">No unsent contacts with an email yet.</div>`}`;
  bootstrap.Modal.getOrCreateInstance($("#ai-modal")).show();
}

const pickedIds = () => $$("[data-bpick]").filter(x => x.checked).map(x => x.dataset.bpick);

async function saveContactsNow() { clearTimeout(orSaveTimer); await api("/api/outreach", { method: "PUT", json: { contacts: S.orContacts } }); }

ACT["batch-open"] = async () => {
  if (!S.orContacts) try { S.orContacts = (await api("/api/outreach")).contacts; } catch { S.orContacts = []; }
  await saveContactsNow().catch(() => {});
  batchStep1();
};
ACT["batch-fill"] = async () => {
  const ids = pickedIds();
  if (!ids.length) return toast("Tick at least one contact.");
  S.batch = { subject: $("#b-subj").value, body: $("#b-body").value };
  try { const r = await api("/api/outreach/drafts", { method: "POST", json: { ids, ...S.batch } }); S.orContacts = r.contacts; batchReview(ids); }
  catch (e) { toast(e.message, 6000); }
};
ACT["batch-ai"] = async el => {
  if (needAI()) return;
  const ids = pickedIds();
  if (!ids.length) return toast("Tick at least one contact.");
  el.disabled = true;
  let done = 0;
  for (const id of ids) {
    $("#b-status").textContent = `Writing ${++done} of ${ids.length}…`;
    const c = S.orContacts.find(x => x.id === id);
    try { await api("/api/ai/outreach-email", { method: "POST", json: { contact: c, save: true } }); } catch (e) { toast(`${c.email}: ${e.message}`, 6000); }
  }
  S.orContacts = (await api("/api/outreach")).contacts;
  batchReview(ids);
};

function batchReview(ids) {
  S.batchIds = ids;
  const rows = ids.map(id => S.orContacts.find(c => c.id === id)).filter(Boolean);
  const rs = S.state.profile.resumes || [];
  const ok = c => c.draft_subject && c.draft_body && c.status !== "needs_edit";
  $("#ai-modal-body").innerHTML = `<h5 class="mb-1">${bi("list-check")} Review ${rows.length} draft${rows.length === 1 ? "" : "s"}</h5><div class="small text-body-secondary mb-3">Edit anything. Edits save as you go. Untick any you don't want to send.</div>
    <div class="vstack gap-2 batch-review">${rows.map(c => `<details class="draft ${ok(c) ? "" : "bad"}" data-did="${esc(c.id)}"><summary><input type="checkbox" class="form-check-input mt-0" data-bsend="${esc(c.id)}" ${ok(c) ? "checked" : ""} aria-label="Send to ${esc(c.email)}"><b class="text-truncate">${esc(c.name || c.email)}</b><span class="text-truncate small text-body-secondary">${esc(c.draft_subject)}</span>${c.error ? `<span class="badge tag-bad ms-auto">${esc(c.error)}</span>` : ""}</summary>
        <div class="p-2 vstack gap-2"><div class="small text-body-secondary">To ${esc(c.email)}</div><input class="form-control form-control-sm" data-dk="draft_subject" value="${esc(c.draft_subject)}" aria-label="Subject">
        <textarea class="form-control form-control-sm letter" data-dk="draft_body" rows="8" aria-label="Message">${esc(c.draft_body)}</textarea>
        <div class="input-group input-group-sm"><span class="input-group-text">${bi("paperclip")} Resume</span><select class="form-select" data-dk="resume_id"><option value="">None</option>${rs.map(r => `<option value="${r.id}" ${r.id === c.resume_id ? "selected" : ""}>${esc(r.filename)}</option>`).join("")}</select></div></div></details>`).join("")}</div>
    <div class="d-flex flex-wrap gap-2 mt-3 align-items-center"><button class="btn btn-sm btn-outline-secondary" data-act="batch-open">${bi("arrow-left")} Back</button><button class="btn btn-sm btn-ink ms-auto" data-act="batch-send" id="b-send">${bi("send")} Send</button></div>
    <div id="b-progress" class="mt-2"></div>`;
  countSend();
}

function countSend() { const n = $$("[data-bsend]").filter(x => x.checked).length, b = $("#b-send"); if (b) { b.disabled = !n; b.innerHTML = `${bi("send")} Send ${n}`; } }

document.addEventListener("change", e => {
  if (e.target.dataset.bsend !== undefined) return countSend();
  const k = e.target.dataset.dk;
  if (!k) return;
  const id = e.target.closest("[data-did]").dataset.did;
  const value = k === "resume_id" ? (Number(e.target.value) || 0) : e.target.value;
  api(`/api/outreach/${encodeURIComponent(id)}`, { method: "PATCH", json: { [k]: value } }).then(c => {
    Object.assign(S.orContacts.find(x => x.id === id), c);
    const row = e.target.closest("details"); row.classList.remove("bad"); row.querySelector(".badge")?.remove();
  }).catch(err => toast(err.message, 6000));
});
document.addEventListener("click", e => { if (e.target.dataset && e.target.dataset.bsend !== undefined) e.stopPropagation(); }, true);

ACT["batch-send"] = async () => {
  const ids = $$("[data-bsend]").filter(x => x.checked).map(x => x.dataset.bsend);
  if (!(S.state.mail && S.state.mail.connected)) return toast("Connect Gmail in Settings first.", 5000);
  const ob = await api("/api/outbox");
  const mins = Math.ceil(ids.length * ob.gap_seconds / 60);
  const who = ids.map(id => S.orContacts.find(c => c.id === id).email);
  const list = who.slice(0, 6).join(", ") + (who.length > 6 ? ` and ${who.length - 6} more` : "");
  if (!await ask(`Send ${ids.length} email${ids.length === 1 ? "" : "s"} from ${S.state.mail.address}?`, `To ${list}. They go one at a time, about ${mins} min in all, and you can stop at any point. ${ob.sent_today} of today's ${ob.daily_cap} are already used.`, `Send ${ids.length}`)) return;
  try { await api("/api/outbox/start", { method: "POST", json: { ids, confirm_count: ids.length } }); watchOutbox(); }
  catch (e) { toast(e.message, 9000); }
};

function watchOutbox() {
  const tick = async () => {
    let s;
    try { s = await api("/api/outbox"); } catch { S.obTimer = setTimeout(tick, 3000); return; }
    const box = $("#b-progress");
    if (box) box.innerHTML = `<div class="d-flex align-items-center gap-2"><div class="progress flex-grow-1" role="progressbar" aria-label="Sending"><div class="progress-bar" data-w="${s.total ? 100 * (s.sent + s.failed) / s.total : 0}"></div></div>
      <span class="small">${s.sent} sent${s.failed ? ` · ${s.failed} failed` : ""} of ${s.total}</span>${s.running ? `<button class="btn btn-sm btn-outline-danger" data-act="batch-stop">${bi("stop-circle")} Stop</button>` : ""}</div>${s.error ? `<div class="small text-danger mt-1">${esc(s.error)}</div>` : ""}`;
    if (box) paint(box);
    if (s.running) { $("#b-send")?.setAttribute("disabled", ""); S.obTimer = setTimeout(tick, 2000); return; }
    toast(`${s.stopped ? "Stopped" : "Finished"}: ${s.sent} sent${s.failed ? `, ${s.failed} failed` : ""}.`, 7000);
    S.orContacts = (await api("/api/outreach")).contacts;
    await loadAll();
  };
  tick();
}
ACT["batch-stop"] = async () => { await api("/api/outbox/stop", { method: "POST" }); toast("Stopping after the current email.", 3000); };

// ---------------------------------------------------------------- Gmail wizard

function gmailWizard() {
  return `<p class="small text-body-secondary">Connect Gmail to send cold emails from here. Your inbox is never read. Your password is encrypted on this PC.</p>
    <ol class="wizard">
      <li><b>Turn on 2-Step Verification</b><div class="small text-body-secondary">Google only offers app passwords when it's on.</div><a class="btn btn-sm btn-outline-secondary mt-1" href="https://myaccount.google.com/signinoptions/twosv" target="_blank" rel="noopener noreferrer">Open Google security ${bi("box-arrow-up-right")}</a></li>
      <li><b>Create an app password</b><div class="small text-body-secondary">Name it JobQuest.AI and copy what Google shows.</div><a class="btn btn-sm btn-outline-secondary mt-1" href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener noreferrer">Open app passwords ${bi("box-arrow-up-right")}</a></li>
      <li><b>Paste it here</b><div class="row g-2 mt-1"><div class="col-sm-6"><input type="email" class="form-control form-control-sm" id="mail-addr" placeholder="you@gmail.com" autocomplete="off" aria-label="Gmail address"></div>
        <div class="col-sm-6"><input type="password" class="form-control form-control-sm" id="mail-pw" placeholder="App password" autocomplete="off" aria-label="App password"></div>
        <div class="col-12"><button class="btn btn-sm btn-ink" data-action="mail-connect">${bi("plug")} Connect and test</button></div>
        <div class="col-12"><div id="mail-err" class="alert alert-danger py-2 small mb-0 d-none" role="alert"></div></div></div></li></ol>`;
}

// ---------------------------------------------------------------- MongoDB Atlas

async function drawCloud() {
  const host = $("#cloud-card");
  if (!host) return;
  let s;
  try { s = await api("/api/cloud"); } catch (e) { host.textContent = e.message; return; }
  host.innerHTML = s.connected ? `<p class="mb-2"><span class="badge rounded-pill text-bg-success">${bi("check2")} Connected</span> <b class="ms-1">${esc(s.host)}</b>${s.last ? ` <span class="small text-body-secondary">· last sync ${ago(s.last)}</span>` : ""}</p>
      ${s.summary ? `<div class="small text-body-secondary mb-2">${esc(s.summary)}</div>` : ""}
      <div class="form-check form-switch mb-2"><input class="form-check-input" type="checkbox" role="switch" id="cloud-auto" ${s.auto ? "checked" : ""}><label class="form-check-label" for="cloud-auto">Sync when the app opens and every ${s.hours} hours</label></div>
      <div class="d-flex flex-wrap gap-2 mb-2"><button class="btn btn-sm btn-ink" data-act="cloud-sync">${bi("arrow-repeat")} Sync now</button><button class="btn btn-sm btn-outline-secondary" data-act="cloud-backup">${bi("cloud-upload")} Back up now</button><button class="btn btn-sm btn-outline-secondary" data-act="cloud-list">${bi("clock-history")} Show backups</button><button class="btn btn-sm btn-outline-danger" data-act="cloud-off">Disconnect</button></div>
      <div id="cloud-list" class="small"></div><div class="form-text">To add another PC, install JobQuest.AI there and connect with the same connection string and passphrase. Your applications, cold email contacts, Coach chats and resumes sync. API keys and Gmail stay on each PC. The newest ${s.keep} snapshots are kept.</div>`
    : `<p class="small text-body-secondary">Use your data on several PCs, and keep it safe if one is lost. Your tracker and resumes sync through your own Atlas cluster, encrypted here before upload. API keys are never included.</p>
      <ol class="wizard"><li><b>Get your connection string</b><div class="small text-body-secondary">In Atlas: Connect, then Drivers. Add this PC's IP under Network Access.</div></li>
      <li><b>Paste it, with your database user's password</b><input type="password" class="form-control form-control-sm mt-1" id="cloud-uri" placeholder="mongodb+srv://user:password@cluster0.xxxxx.mongodb.net/" autocomplete="off" aria-label="Connection string"></li>
      <li><b>Choose a backup passphrase</b><div class="small text-body-secondary">At least 10 characters, and the same on every PC. Save it somewhere safe: without it, your cloud data can't be opened.</div><input type="password" class="form-control form-control-sm mt-1" id="cloud-pass" autocomplete="new-password" aria-label="Backup passphrase"></li></ol>
      <button class="btn btn-sm btn-ink" data-act="cloud-on">${bi("plug")} Connect and test</button><div id="cloud-err" class="alert alert-danger py-2 small mt-2 mb-0 d-none" role="alert"></div>`;
}

ACT["cloud-on"] = async el => {
  el.disabled = true; toast("Testing the connection…", 12000);
  try { await api("/api/cloud", { method: "PUT", json: { uri: $("#cloud-uri").value, passphrase: $("#cloud-pass").value } }); toast("MongoDB Atlas connected.", 3000); drawCloud(); }
  catch (e) { const b = $("#cloud-err"); b.textContent = e.message; b.classList.remove("d-none"); el.disabled = false; toast("Atlas didn't connect.", 3000); }
};
ACT["cloud-off"] = async () => { if (await ask("Disconnect MongoDB Atlas?", "The connection is removed from this PC. Backups already in Atlas stay there.", "Disconnect", true)) { await api("/api/cloud", { method: "DELETE" }); drawCloud(); } };
ACT["cloud-backup"] = async el => { el.disabled = true; toast("Encrypting and uploading…", 20000); try { const b = await api("/api/cloud/backups", { method: "POST" }); toast(`Backed up ${b.counts.tracked} applications to Atlas.`, 5000); drawCloud(); } catch (e) { toast(e.message, 9000); el.disabled = false; } };
ACT["cloud-sync"] = async el => {
  el.disabled = true; toast("Syncing…", 30000);
  try { const r = await api("/api/cloud/sync", { method: "POST" }); toast(r.summary, 6000); if (r.pulled) await loadAll(); else drawCloud(); }
  catch (e) { toast(e.message, 9000); el.disabled = false; }
};
ACT["cloud-list"] = async () => {
  const host = $("#cloud-list");
  try {
    const r = await api("/api/cloud/backups");
    host.innerHTML = r.backups.length ? `<table class="table table-sm align-middle mb-2 data-table"><tbody>${r.backups.map(b => `<tr><td>${new Date(b.created).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</td><td>${b.counts.tracked ?? "?"} applications</td><td>${b.size_kb} KB</td><td class="text-end"><button class="btn btn-sm btn-outline-secondary" data-act="cloud-restore" data-id="${esc(b.id)}">Restore</button></td></tr>`).join("")}</tbody></table>` : "No backups in Atlas yet.";
  } catch (e) { host.textContent = e.message; }
};
ACT["cloud-restore"] = async el => {
  if (!await ask("Restore this backup?", "Everything in JobQuest.AI is replaced with it. Your current data is saved locally first, so you can undo this.", "Restore", true)) return;
  try { const r = await api(`/api/cloud/backups/${el.dataset.id}/restore`, { method: "POST" }); toast(`Restored ${r.restored.tracked} applications.`, 6000); await loadAll(); }
  catch (e) { toast(e.message, 9000); }
};
document.addEventListener("change", async e => {
  if (e.target.id !== "cloud-auto") return;
  try { await api("/api/cloud/auto", { method: "PUT", json: { auto: e.target.checked } }); toast(e.target.checked ? "Auto sync is on." : "Auto sync is off.", 2500); }
  catch (err) { toast(err.message, 6000); e.target.checked = false; }
});
