/* Summerfield Studio - single-page app (no build step). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const app = $("#app");
const LANGS = { en: "English", vi: "Tiếng Việt", es: "Español" };
let ME = null;
let BRAND = null;
let timers = [];

// ------------------------------------------------------------------ utils

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = "toast"), 3400);
}
async function api(method, path, body) {
  const opt = { method, headers: { "x-studio": "1" }, credentials: "same-origin" };
  if (body !== undefined) { opt.headers["content-type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  if (r.status === 401 && !path.startsWith("/api/login")) { ME = null; go("#/login"); throw new Error("Please sign in"); }
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || `Error ${r.status}`);
  return data;
}
function upload(path, form, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", path);
    x.setRequestHeader("x-studio", "1");
    x.upload.onprogress = e => e.lengthComputable && onProgress && onProgress(e.loaded / e.total);
    x.onload = () => {
      let d = {};
      try { d = JSON.parse(x.responseText); } catch { /* not json */ }
      x.status < 300 ? resolve(d) : reject(new Error(d.detail || `Upload failed (${x.status})`));
    };
    x.onerror = () => reject(new Error("Network error - check your connection and try again"));
    x.send(form);
  });
}
function go(hash) { if (location.hash !== hash) location.hash = hash; else route(); }
function ago(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}
const STATUS_TEXT = { new: "New", queued: "Waiting", starting: "Starting", running: "Working", working: "Working", ready: "Ready", done: "Done", failed: "Needs attention", cancelled: "Cancelled" };
function pill(status) { return `<span class="pill ${esc(status)}"><span class="dot"></span>${esc(STATUS_TEXT[status] || status)}</span>`; }
function icon(name) {
  const p = {
    download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>', upload: '<path d="M12 20V9M7 14l5-5 5 5M5 4h14"/>',
    play: '<path d="M8 5v14l11-7z"/>', film: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10 9.5v5l4.5-2.5z"/>',
    file: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/>', refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
    trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>', x: '<path d="M6 6l12 12M18 6L6 18"/>',
  }[name];
  return `<svg viewBox="0 0 24 24">${p}</svg>`;
}
function clearTimers() { timers.forEach(clearInterval); timers = []; }
function md(text) {
  // tiny, safe markdown: headings, bullets, bold, code, fenced blocks
  const out = [];
  let inList = false, inCode = false, code = [];
  for (const raw of String(text).split("\n")) {
    if (raw.startsWith("```")) {
      if (inCode) { out.push(`<pre>${esc(code.join("\n"))}</pre>`); code = []; }
      inCode = !inCode; continue;
    }
    if (inCode) { code.push(raw); continue; }
    const line = esc(raw).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
    if (/^- /.test(raw)) { if (!inList) { out.push("<ul>"); inList = true; } out.push(`<li>${line.slice(2)}</li>`); continue; }
    if (inList) { out.push("</ul>"); inList = false; }
    if (/^#{1,3} /.test(raw)) out.push(`<h2>${line.replace(/^#{1,3} /, "")}</h2>`);
    else if (raw.trim()) out.push(`<p>${line}</p>`);
  }
  if (inList) out.push("</ul>");
  return out.join("");
}
function dateToCard(v) { // yyyy-mm-dd -> mm/dd/yyyy as printed on the card
  if (!v) return "";
  const [y, m, d] = v.split("-");
  return `${m}/${d}/${y}`;
}
function cardFields(prefix, info = {}, defaults = {}) {
  const v = k => esc(info[k] ?? "");
  const ph = k => esc(defaults[k] ? `default: ${defaults[k]}` : "");
  return `
  <div class="grid2">
    <label class="f"><span>Approved by</span><input type="text" name="approved_by" value="${v("approved_by")}" placeholder="${ph("approved_by") || "e.g. Karina Fong"}"></label>
    <label class="f"><span>Approver title</span><input type="text" name="approver_title" value="${v("approver_title")}" placeholder="${ph("approver_title") || "e.g. R&D Specialist"}"></label>
    <label class="f"><span>SOP version</span><input type="text" name="version" value="${v("version")}" placeholder="${ph("version") || "1.0"}"></label>
    <label class="f"><span>Effective date</span><input type="date" name="effective_date"><small class="hint">${info.effective ? "Currently " + esc(info.effective) : "Leave empty for today"}</small></label>
  </div>
  <label class="f"><span>Training video link <small>(QR code on the card; {lang} becomes en / vi / es)</small></span>
    <input type="url" name="video_url" value="${v("video_url")}" placeholder="https://…/popcorn-chicken-{lang}.mp4"></label>`;
}
function readFields(form) {
  const f = new FormData(form);
  return {
    approved_by: (f.get("approved_by") || "").trim(), approver_title: (f.get("approver_title") || "").trim(),
    version: (f.get("version") || "").trim(), effective: dateToCard(f.get("effective_date")),
    video_url: (f.get("video_url") || "").trim(),
  };
}
function dropzone(name, accept, title, hint, required = false) {
  return `<label class="drop" data-drop>
    <input type="file" name="${name}" accept="${accept}" ${required ? "required" : ""}>
    ${icon("upload")}<div class="big">${esc(title)}</div><div class="hint">${esc(hint)}</div><div class="file"></div></label>`;
}
function wireDrops(root) {
  $$("[data-drop]", root).forEach(z => {
    const input = $("input", z), label = $(".file", z);
    const show = () => (label.textContent = input.files[0] ? `${input.files[0].name} · ${(input.files[0].size / 1048576).toFixed(1)} MB` : "");
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach(e => z.addEventListener(e, ev => { ev.preventDefault(); z.classList.add("over"); }));
    ["dragleave", "drop"].forEach(e => z.addEventListener(e, ev => { ev.preventDefault(); z.classList.remove("over"); }));
    z.addEventListener("drop", ev => { if (ev.dataTransfer.files.length) { input.files = ev.dataTransfer.files; show(); } });
  });
}

// ------------------------------------------------------------------ shell

function chrome(show, tab) {
  $("#top").hidden = !show;
  $("#stripe").hidden = !show;
  $$("#tabs a").forEach(a => a.classList.toggle("on", a.dataset.tab === tab));
  if (ME) {
    $("#accountBtn").textContent = (ME.name || ME.email).trim()[0].toUpperCase();
    $("#teamTab").hidden = ME.role !== "admin";
  }
}
$("#accountBtn").addEventListener("click", e => {
  e.stopPropagation();
  let m = $("#menu");
  if (m) { m.remove(); return; }
  m = document.createElement("div");
  m.className = "menu"; m.id = "menu";
  m.innerHTML = `<div class="who"><b>${esc(ME.name)}</b><span>${esc(ME.email)} · ${esc(ME.role)}</span></div>
    <button data-a="pw">Change password</button><button data-a="out">Sign out</button>`;
  document.body.append(m);
  m.addEventListener("click", async ev => {
    const a = ev.target.dataset.a;
    if (a === "out") { await api("POST", "/api/logout"); ME = null; m.remove(); go("#/login"); }
    if (a === "pw") {
      const pw = prompt("New password (at least 8 characters)");
      if (pw) { try { await api("POST", "/api/me/password", { password: pw }); toast("Password changed"); } catch (err) { toast(err.message, true); } }
      m.remove();
    }
  });
});
document.addEventListener("click", () => $("#menu")?.remove());

async function loadBrandMark() {
  try {
    BRAND = await api("GET", "/api/brand");
    const src = BRAND.assets.logo_mark;
    $("#brandMark").innerHTML = src ? `<img src="${esc(src)}" alt="">` : "";
  } catch { /* not signed in yet */ }
}

// ------------------------------------------------------------------ router

const routes = [
  [/^#\/login$/, viewLogin, false], [/^#\/setup$/, viewSetup, false],
  [/^#\/?$/, viewProjects, "videos"], [/^#\/new$/, viewNew, "new"], [/^#\/p\/(\w+)$/, viewProject, "videos"],
  [/^#\/cards$/, viewCards, "cards"], [/^#\/brand$/, viewBrand, "brand"], [/^#\/team$/, viewTeam, "team"],
];
async function route() {
  clearTimers();
  $("#menu")?.remove();
  const h = location.hash || "#/";
  for (const [re, fn, tab] of routes) {
    const m = h.match(re);
    if (!m) continue;
    if (tab !== false && !ME) {
      try { ME = await api("GET", "/api/me"); loadBrandMark(); } catch { return; }
    }
    chrome(tab !== false, tab);
    window.scrollTo(0, 0);
    try { await fn(...m.slice(1)); } catch (e) { if (e.message !== "Please sign in") app.innerHTML = `<div class="error-box">${esc(e.message)}</div>`; }
    return;
  }
  go("#/");
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------------ auth views

async function viewLogin() {
  const st = await api("GET", "/api/setup-status");
  if (st.needs_admin) return go("#/setup");
  app.innerHTML = `<div class="auth"><form class="card" id="f">
    <span class="kicker">welcome back</span><h1>Summerfield Studio</h1>
    <p class="sub">Training videos and SOP cards, on brand every time.</p><br>
    <label class="f"><span>Email</span><input type="email" name="email" required autocomplete="username"></label>
    <label class="f"><span>Password</span><input type="password" name="password" required autocomplete="current-password"></label>
    <button class="btn green" style="width:100%">Sign in</button></form></div>`;
  $("#f").addEventListener("submit", async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    try { await api("POST", "/api/login", { email: f.get("email"), password: f.get("password") }); ME = null; go("#/"); }
    catch (err) { toast(err.message, true); }
  });
}
async function viewSetup() {
  app.innerHTML = `<div class="auth"><form class="card" id="f">
    <span class="kicker">let's get started</span><h1>Set up Studio</h1>
    <p class="sub">Create the first admin account. You can invite the rest of the team after.</p><br>
    <label class="f"><span>Your name</span><input type="text" name="name" required></label>
    <label class="f"><span>Email</span><input type="email" name="email" required autocomplete="username"></label>
    <label class="f"><span>Password <small>(8+ characters)</small></span><input type="password" name="password" minlength="8" required autocomplete="new-password"></label>
    <button class="btn green" style="width:100%">Create admin account</button></form></div>`;
  $("#f").addEventListener("submit", async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    try { await api("POST", "/api/setup", Object.fromEntries(f)); ME = null; go("#/brand"); toast("Welcome! Start by setting up the brand kit."); }
    catch (err) { toast(err.message, true); }
  });
}

// ------------------------------------------------------------------ projects

async function viewProjects() {
  app.innerHTML = `<div class="page-head"><div><span class="kicker">training library</span><h1>Videos</h1></div>
    <a class="btn green" href="#/new">${icon("film")} New video</a></div><div class="projects">${'<div class="skeleton" style="height:240px"></div>'.repeat(3)}</div>`;
  const render = list => {
    const grid = $(".projects");
    if (!grid) return;
    if (!list.length) {
      grid.outerHTML = `<div class="empty-state"><span class="kicker">nothing here yet</span><h2>Make your first training video</h2>
        <p>Upload raw footage and the recipe notes. Studio edits it on brand, narrates it, translates it to Vietnamese and Spanish, and writes the SOP card.</p>
        <a class="btn green" href="#/new">${icon("film")} New video</a></div>`;
      return;
    }
    grid.innerHTML = list.map(p => `<a class="pcard" href="#/p/${esc(p.id)}">
      ${p.thumb ? `<div class="thumb" style="background-image:url('${esc(p.thumb)}')"></div>` : `<div class="thumb empty"><span>in the making</span></div>`}
      <div class="body"><h3>${esc(p.title)}</h3><div class="meta">${pill(p.status)}
        ${p.languages.map(l => `<span class="lang">${esc(l.toUpperCase())}</span>`).join("")}<span>· ${ago(p.created_at)}</span></div></div></a>`).join("");
  };
  render(await api("GET", "/api/projects"));
  timers.push(setInterval(async () => { try { render(await api("GET", "/api/projects")); } catch { /* ignore */ } }, 8000));
}

async function viewNew() {
  const keys = BRAND?.keys || {};
  app.innerHTML = `<div class="page-head"><div><span class="kicker">new video</span><h1>Make a training video</h1>
    <p class="sub">Studio plans, edits, narrates, reviews and improves it on its own, then translates it and writes the SOP card.</p></div></div>
  <form id="f" class="cols">
    <div class="stack">
      <div class="card"><h2>Footage</h2><p class="sub">Raw video from the kitchen. Landscape, steady, readouts visible for a couple of seconds.</p>
        <label class="f"><span>Title</span><input type="text" name="title" placeholder="e.g. Popcorn Chicken Marinating" required></label>
        ${dropzone("raw", "video/*,.mp4,.mov,.m4v", "Drop the raw footage here", "or tap to choose · MP4 or MOV", true)}
      </div>
      <div class="card"><h2>Recipe notes</h2><p class="sub">Amounts, temperatures, times, common mistakes. Studio never invents numbers - anything missing becomes a question for you.</p>
        <label class="f"><span>Notes</span><textarea name="notes_text" placeholder="Batch: 40 lb chicken thigh (8 bags × 5 lb)&#10;Powder: 2.40 lb (1.08 kg) Elite Spice Marinating Powder&#10;Water: 4 cups (32 oz) cold filtered&#10;Common mistakes: adding all powder at once…"></textarea></label>
        ${dropzone("notes_file", ".pdf,.txt,.md", "Or attach an SOP / recipe file", "PDF, TXT or Markdown")}
      </div>
    </div>
    <div class="stack">
      <div class="card"><h2>Output</h2>
        <label class="f"><span>Languages</span></label><div class="checks">
          <label class="chip-check"><input type="checkbox" name="lang" value="en" checked disabled><span>English</span></label>
          <label class="chip-check"><input type="checkbox" name="lang" value="vi" checked><span>Tiếng Việt</span></label>
          <label class="chip-check"><input type="checkbox" name="lang" value="es" checked><span>Español</span></label></div><br>
        <label class="f"><span>Voiceover</span></label><div class="checks">
          <label class="chip-check"><input type="radio" name="voice" value="real" ${keys.voice ? "checked" : "disabled"}><span>Brand voice</span></label>
          <label class="chip-check"><input type="radio" name="voice" value="preview" ${keys.voice ? "" : "checked"}><span>Silent preview</span></label></div>
        <p class="hint" style="margin:8px 0 0">${keys.voice ? "Preview renders the edit with silent, correctly-timed narration - no voice cost. You can add the voice afterwards." : "The voice service isn't connected yet, so this run uses silent preview narration."}</p>
      </div>
      <div class="card"><details><summary>SOP card details <span class="hint">(optional - can be filled in later)</span></summary><br>${cardFields("n", {}, BRAND?.card_info || {})}</details></div>
      ${keys.claude === false ? `<div class="note-box">The AI isn't connected yet - an admin needs to add the Claude API key to the server before videos can be made.</div>` : ""}
      <div class="card"><button class="btn green" style="width:100%" id="go" ${keys.claude === false ? "disabled" : ""}>${icon("film")} Make the video</button>
        <div class="progressbar" hidden><i></i></div><p class="hint" id="upmsg" style="margin:8px 0 0"></p></div>
    </div>
  </form>`;
  wireDrops(app);
  $("#f").addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.target, fd = new FormData();
    const raw = form.raw.files[0];
    if (!raw) return toast("Choose the raw footage first", true);
    fd.append("title", form.title.value);
    fd.append("raw", raw);
    fd.append("notes_text", form.notes_text.value);
    if (form.notes_file.files[0]) fd.append("notes_file", form.notes_file.files[0]);
    fd.append("languages", ["en", ...$$("input[name=lang]:checked", form).map(i => i.value).filter(v => v !== "en")].join(","));
    fd.append("voice", (form.querySelector("input[name=voice]:checked") || {}).value || "preview");
    Object.entries(readFields(form)).forEach(([k, v]) => fd.append(k, v));
    const btn = $("#go"), bar = $(".progressbar"), msg = $("#upmsg");
    btn.disabled = true; bar.hidden = false;
    try {
      const res = await upload("/api/projects", fd, p => { $("i", bar).style.width = `${Math.round(p * 100)}%`; msg.textContent = p < 1 ? `Uploading… ${Math.round(p * 100)}%` : "Starting…"; });
      go(`#/p/${res.id}`);
      toast("Uploaded - Studio is on it");
    } catch (err) { toast(err.message, true); btn.disabled = false; bar.hidden = true; msg.textContent = ""; }
  });
}

// stages shown while a video is being made, derived from the pipeline log
function stages(log, langs, status) {
  const has = s => log.includes(s);
  const scores = [...log.matchAll(/\[en\] round (\d+) director score \*\*(\d+)\*\*/g)];
  const best = scores.length ? scores[scores.length - 1] : null;
  const list = [
    { t: "Watching the footage & planning the edit", d: "Picks the clips, writes the narration and captions", done: has("round 1:") || has("over the"), now: has("Planning") },
    { t: "Editing & director review", d: best ? `Round ${best[1]} score <span class="score">${best[2]}/100</span>` : "Renders, watches the result, fixes it, repeats",
      done: has("[en] shipped") || has("[en] final render") || has("[en] kept the best") || has("director made no further"), now: has("[en] round") },
  ];
  langs.filter(l => l !== "en").forEach(l => {
    const name = { vi: "Vietnamese", es: "Spanish" }[l] || l;
    list.push({ t: `${LANGS[l] || l} version`, d: "Translated narration, captions and labels", done: has(`[${l}] shipped`) || has(`[${l}] final render`) || has(`[${l}] kept`) || has(`## SOP card`), now: has(`## ${name}`) });
  });
  list.push({ t: "SOP card", d: "Written from the finished video, reviewed, translated", done: has("## Done"), now: has("## SOP card") });
  let seenNow = false;
  return list.map((s, i) => {
    let cls = s.done ? "done" : "";
    if (!s.done && !seenNow && (s.now || (i > 0 && list[i - 1].done) || i === 0) && ["running", "starting", "queued"].includes(status)) { cls = "now"; seenNow = true; }
    if (!s.done && status === "failed" && !seenNow) { cls = "fail"; seenNow = true; }
    return `<li class="${cls}"><span class="ic"></span><div><div class="t">${esc(s.t)}</div><div class="d">${s.d}</div></div></li>`;
  }).join("");
}

async function viewProject(pid) {
  let p = await api("GET", `/api/projects/${pid}`);
  let lang = "en", logText = "", offset = 0, jobId = null;
  const active = () => p.latest_job && ["queued", "starting", "running"].includes(p.latest_job.status);

  const draw = () => {
    const results = (p.results || []).filter(r => r.video || r.card);
    const cur = results.find(r => r.code === lang) || results[0];
    const working = active();
    const isMake = p.latest_job?.kind === "make";
    app.innerHTML = `<div class="page-head"><div><span class="kicker">${working ? "in the making" : "training video"}</span><h1>${esc(p.title)}</h1>
      <div class="meta" style="margin-top:8px">${pill(p.status)} ${p.languages.map(l => `<span class="lang">${esc(l.toUpperCase())}</span>`).join("")}<span>· ${ago(p.created_at)}</span></div></div>
      <div class="row">${working ? `<button class="btn danger small" id="cancel">${icon("x")} Stop</button>` : ""}
        <button class="btn ghost small" id="del">${icon("trash")} Delete</button></div></div>
    <div class="cols"><div class="stack">
      ${working || (p.latest_job?.status === "failed" && isMake && !results.length) ? `<div class="card"><h2>${working ? (isMake ? "Making your video" : "Updating") : "Something went wrong"}</h2>
        <p class="sub">${working ? (isMake ? "This takes a while - roughly an hour for a 5-minute video in three languages. You can close this page; it keeps going." : "Working on the change…") : "See the details below. You can try again."}</p>
        ${isMake ? `<ul class="steps" id="steps">${stages(logText, p.languages, p.latest_job.status)}</ul>` : ""}
        ${p.latest_job?.error ? `<div class="error-box">${esc(p.latest_job.error)}</div>` : ""}
        <details ${working ? "" : "open"} style="margin-top:12px"><summary>Live log</summary><div class="log" id="log">${esc(logText.slice(-30000))}</div></details>
        ${!working ? `<br><button class="btn green" id="retry">${icon("refresh")} Try again</button>` : ""}</div>` : ""}
      ${cur ? `<div class="card">
        <div class="ltabs">${results.map(r => `<button data-l="${esc(r.code)}" class="${r.code === cur.code ? "on" : ""}">${esc(LANGS[r.code] || r.name)}</button>`).join("")}</div>
        ${cur.video ? `<video controls playsinline preload="metadata" ${p.thumb ? `poster="${esc(p.thumb)}"` : ""} src="${esc(cur.video)}"></video>` : ""}
        <div class="downloads">
          ${cur.video ? `<a class="btn soft small" href="${esc(cur.video)}" download>${icon("download")} Video${cur.seconds ? ` · ${Math.floor(cur.seconds / 60)}:${String(Math.round(cur.seconds % 60)).padStart(2, "0")}` : ""}</a>` : ""}
          ${cur.srt ? `<a class="btn soft small" href="${esc(cur.srt)}" download>${icon("download")} Subtitles</a>` : ""}
          ${cur.card ? `<a class="btn soft small" href="${esc(cur.card)}" target="_blank">${icon("file")} SOP card PDF</a>` : ""}
        </div>
        ${cur.card_pages?.length ? `<div class="pages">${cur.card_pages.map(u => `<a href="${esc(u)}" target="_blank"><img src="${esc(u)}?t=${Date.now()}" alt="SOP card page" loading="lazy"></a>`).join("")}</div>` : ""}
      </div>` : ""}
      ${p.review_log ? `<div class="card"><details><summary>Director's review log</summary><div class="md" style="margin-top:10px">${md(p.review_log)}</div></details></div>` : ""}
    </div>
    <div class="stack">
      ${p.open_questions?.length ? `<div class="card"><h3>Please confirm</h3><p class="sub" style="margin-bottom:6px">Facts Studio couldn't verify from the notes or footage:</p><ul class="qs">${p.open_questions.map(q => `<li>${esc(q)}</li>`).join("")}</ul></div>` : ""}
      ${results.some(r => r.card) ? `<div class="card"><h2>SOP card</h2><p class="sub">Change the printed details instantly, or describe a change and Studio edits every language.</p>
        <form id="fields"><details><summary>Card details</summary><br>${cardFields("c", p.card_info, BRAND?.card_info || {})}
          <button class="btn green small" ${working ? "disabled" : ""}>Update details</button></details></form><br>
        <form id="edit"><label class="f"><span>Request a change</span><textarea name="request" placeholder="e.g. Step 3: stop when the scale reads 2.45 lb. Add 'stir every 30 seconds'."></textarea></label>
          <div class="row"><button class="btn small" ${working ? "disabled" : ""}>Apply change</button>
          <button type="button" class="btn ghost small" id="regen" ${working ? "disabled" : ""}>${icon("refresh")} Rewrite card</button></div></form></div>` : ""}
      ${!working && p.has_plan ? `<div class="card"><h3>Make it again</h3><p class="sub">Re-render with the same edit - e.g. switch from silent preview to the brand voice.</p>
        <div class="row"><button class="btn small green" data-remake="real" ${BRAND?.keys?.voice ? "" : "disabled"}>With brand voice</button>
        <button class="btn small ghost" data-remake="preview">Silent preview</button></div></div>` : ""}
    </div></div>`;

    $$(".ltabs button").forEach(b => b.addEventListener("click", () => { lang = b.dataset.l; draw(); }));
    $("#del")?.addEventListener("click", async () => {
      if (!confirm("Delete this video, its translations and cards?")) return;
      await api("DELETE", `/api/projects/${pid}`); toast("Deleted"); go("#/");
    });
    $("#cancel")?.addEventListener("click", async () => { await api("POST", `/api/jobs/${p.latest_job.id}/cancel`); toast("Stopped"); refresh(); });
    $("#retry")?.addEventListener("click", () => remake("real"));
    $$("[data-remake]").forEach(b => b.addEventListener("click", () => remake(b.dataset.remake)));
    $("#fields")?.addEventListener("submit", async e => {
      e.preventDefault();
      try { await api("POST", `/api/projects/${pid}/card/fields`, readFields(e.target)); toast("Updating the card details…"); refresh(); }
      catch (err) { toast(err.message, true); }
    });
    $("#edit")?.addEventListener("submit", async e => {
      e.preventDefault();
      const request = e.target.request.value.trim();
      if (!request) return toast("Describe the change first", true);
      try { await api("POST", `/api/projects/${pid}/card/edit`, { request }); toast("Editing the card in every language…"); refresh(); }
      catch (err) { toast(err.message, true); }
    });
    $("#regen")?.addEventListener("click", async () => {
      if (!confirm("Rewrite the SOP card from the finished video? This replaces the current cards.")) return;
      try { await api("POST", `/api/projects/${pid}/card/regenerate`, readFields($("#fields"))); toast("Rewriting the card…"); refresh(); }
      catch (err) { toast(err.message, true); }
    });
    const lg = $("#log");
    if (lg) lg.scrollTop = lg.scrollHeight;
  };

  async function remake(voice) {
    try { await api("POST", `/api/projects/${pid}/remake`, { voice, keep_plan: true }); toast("Started again"); refresh(); }
    catch (err) { toast(err.message, true); }
  }
  async function poll() {
    if (!p.latest_job) return;
    if (jobId !== p.latest_job.id) { jobId = p.latest_job.id; logText = ""; offset = 0; }
    const r = await api("GET", `/api/jobs/${jobId}?since=${offset}`);
    if (r.log) { logText += r.log; offset = r.offset; }
    return r.job.status;
  }
  async function refresh() {
    p = await api("GET", `/api/projects/${pid}`);
    await poll();
    draw();
  }
  await poll();
  draw();
  timers.push(setInterval(async () => {
    if (!active()) return;
    try {
      const status = await poll();
      if (["done", "failed", "cancelled"].includes(status)) { await refresh(); if (status === "done") toast("Finished ✓"); return; }
      const lg = $("#log"), st = $("#steps");
      if (lg) { const atEnd = lg.scrollTop + lg.clientHeight >= lg.scrollHeight - 30; lg.textContent = logText.slice(-30000); if (atEnd) lg.scrollTop = lg.scrollHeight; }
      if (st) st.innerHTML = stages(logText, p.languages, "running");
    } catch { /* transient */ }
  }, 2500));
}

// ------------------------------------------------------------------ edit any card

async function viewCards() {
  app.innerHTML = `<div class="page-head"><div><span class="kicker">sop cards</span><h1>Edit a card</h1>
    <p class="sub">Upload any SOP card PDF - one Studio made, or an older one - and say what to change. Studio keeps the approved format.</p></div></div>
  <div class="cols"><form class="card" id="f">
    ${dropzone("pdf", ".pdf,application/pdf", "Drop the card PDF here", "or tap to choose", true)}<br>
    <label class="f"><span>What should change?</span><textarea name="request" placeholder="e.g. Change the approver to Jamie Tran. Step 5: use 6 pans instead of 8. Add a 'Never' item: reuse marinade."></textarea>
      <small class="hint">Only changing the details below? Leave this empty - it's instant.</small></label>
    <details><summary>Card details</summary><br>${cardFields("t", {}, {})}</details><br>
    <label class="f"><span>Also make</span></label><div class="checks">
      <label class="chip-check"><input type="checkbox" name="lang" value="vi"><span>Tiếng Việt</span></label>
      <label class="chip-check"><input type="checkbox" name="lang" value="es"><span>Español</span></label></div><br>
    <button class="btn green" style="width:100%" id="go">Update the card</button>
    <div class="progressbar" hidden><i></i></div></form>
    <div class="stack" id="result"><div class="card"><h3>How it works</h3><ul class="qs">
      <li>Cards made by Studio carry their own editable source, so edits are exact.</li>
      <li>Other PDFs are read by Claude and rebuilt in the template, reusing their photos.</li>
      <li>Content changes bump the version (1.0 → 1.1) and set today's date, unless you set them.</li></ul></div></div></div>`;
  wireDrops(app);
  $("#f").addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.target;
    if (!form.pdf.files[0]) return toast("Choose the card PDF first", true);
    const fd = new FormData();
    fd.append("pdf", form.pdf.files[0]);
    fd.append("request", form.request.value);
    fd.append("languages", $$("input[name=lang]:checked", form).map(i => i.value).join(","));
    Object.entries(readFields(form)).forEach(([k, v]) => fd.append(k, v));
    const btn = $("#go"), bar = $(".progressbar", form);
    btn.disabled = true; bar.hidden = false;
    try {
      const res = await upload("/api/tools/edit-card", fd, p => ($("i", bar).style.width = `${Math.round(p * 100)}%`));
      $("#result").innerHTML = `<div class="card"><h2>Updating…</h2><ul class="steps"><li class="now"><span class="ic"></span><div><div class="t">Working on your card</div><div class="d" id="st">Starting</div></div></li></ul><div class="log" id="log"></div></div>`;
      let off = 0, txt = "";
      const h = setInterval(async () => {
        const r = await api("GET", `/api/jobs/${res.job_id}?since=${off}`);
        if (r.log) { txt += r.log; off = r.offset; $("#log").textContent = txt.slice(-8000); }
        $("#st").textContent = STATUS_TEXT[r.job.status] || r.job.status;
        if (["done", "failed", "cancelled"].includes(r.job.status)) {
          clearInterval(h);
          btn.disabled = false; bar.hidden = true;
          if (r.job.status !== "done") { $("#result").insertAdjacentHTML("afterbegin", `<div class="error-box">${esc(r.job.error || "Failed")}</div>`); return; }
          const out = await api("GET", `/api/tools/edit-card/${res.tool_id}`);
          $("#result").innerHTML = out.files.map(f => `<div class="card"><h3>${esc(f.name)}</h3>
            <div class="downloads"><a class="btn green small" href="${esc(f.url)}" target="_blank">${icon("download")} Open PDF</a></div>
            <div class="pages">${f.pages.map(u => `<a href="${esc(u)}" target="_blank"><img src="${esc(u)}" alt="card page"></a>`).join("")}</div></div>`).join("");
          toast("Card updated ✓");
        }
      }, 2000);
      timers.push(h);
    } catch (err) { toast(err.message, true); btn.disabled = false; bar.hidden = true; }
  });
}

// ------------------------------------------------------------------ brand kit

async function viewBrand() {
  const b = BRAND = await api("GET", "/api/brand");
  const admin = ME.role === "admin";
  const colorRoles = ["card_bg", "stripe_a", "primary", "text", "accent", "caption_bg", "danger", "phase_c", "tone_blue"];
  app.innerHTML = `<div class="page-head"><div><span class="kicker">brand kit</span><h1>${esc(b.name)}</h1>
    <p class="sub">Everything Studio follows on every video and card.</p></div></div>
  <div class="cols"><div class="stack">
    <div class="card"><h2>Connections</h2><p class="sub">Keys live on the server, never in the browser.</p>
      <div class="keys"><span class="key ${b.keys.claude ? "ok" : ""}"><i></i>Claude AI ${b.keys.claude ? "connected" : "not connected"}</span>
      <span class="key ${b.keys.voice ? "ok" : ""}"><i></i>Brand voice ${b.keys.voice ? "connected" : "not connected"}</span></div>
      ${!b.keys.claude || !b.keys.voice ? `<p class="hint" style="margin-top:10px">Set ANTHROPIC_API_KEY and ELEVENLABS_API_KEY in the server's environment, then restart it.</p>` : ""}</div>
    <div class="card"><h2>Logo & mascot</h2><p class="sub">Used on title cards, the closing card and the SOP card.</p>
      <div class="assets">${[["logo_horizontal", "Horizontal logo"], ["logo_mark", "Logomark"], ["mascot", "Mascot"]].map(([k, l]) => `
        <div class="asset"><div class="img">${b.assets[k] ? `<img src="${esc(b.assets[k])}" alt="${l}">` : `<span class="missing">missing</span>`}</div>
        <div class="label">${l}</div>${admin ? `<label class="btn soft small">Replace<input type="file" accept="image/png" data-asset="${k}" hidden></label>` : ""}</div>`).join("")}</div>
      ${admin ? `<details style="margin-top:14px"><summary>Rebuild from the guideline PDF and sample video</summary><form id="assets" style="margin-top:12px">
        ${dropzone("guideline", ".pdf", "Brand guideline PDF", "logo comes from page 1", true)}<br>
        ${dropzone("sample", "video/*", "Approved sample video (optional)", "mascot is cut from its title card")}<br>
        <button class="btn small">Rebuild assets</button></form></details>` : ""}</div>
    <div class="card"><h2>Colours</h2><p class="sub">From the guideline - updated automatically when you upload a new one.</p>
      <div class="swatches">${colorRoles.map(k => `<div class="sw"><i style="background:${esc(b.colors[k])}"></i><div><b>${esc(k.replace(/_/g, " "))}</b>${esc(b.colors[k])}</div></div>`).join("")}</div></div>
    <div class="card"><details><summary>Style guide Studio follows</summary><div class="md" style="margin-top:10px">${md(b.style_guide)}</div></details></div>
  </div>
  <div class="stack">
    ${admin ? `<div class="card"><h2>Keep it current</h2><p class="sub">Upload a new version anytime - every future video and card follows it.</p>
      <form data-up="/api/brand/guideline" data-field="pdf">${dropzone("pdf", ".pdf", "New brand guideline", "updates colours, fonts and rules")}<button class="btn small" style="margin-top:8px">Apply guideline</button></form><br>
      <form data-up="/api/brand/example" data-field="video">${dropzone("video", "video/*", "Approved finished video", "the look the director compares against")}
        <label class="f" style="margin-top:8px"><input type="text" name="name" placeholder="Short name, e.g. popcorn-chicken" required></label><button class="btn small">Learn this example</button></form><br>
      <form data-up="/api/brand/card-reference" data-field="pdf">${dropzone("pdf", ".pdf", "Approved SOP card", "the card format to match")}<button class="btn small" style="margin-top:8px">Use as card format</button></form></div>` : ""}
    <div class="card"><h2>Defaults</h2><p class="sub">Voice and the details printed on new SOP cards.</p>
      <form id="defaults">
        <label class="f"><span>ElevenLabs voice ID</span><input type="text" name="voice_id" value="${esc(b.voice.voice_id)}" ${admin ? "" : "disabled"} placeholder="pick one voice and keep it"></label>
        <label class="f"><span>Approved by</span><input type="text" name="approved_by" value="${esc(b.card_info.approved_by)}" ${admin ? "" : "disabled"}></label>
        <label class="f"><span>Approver title</span><input type="text" name="approver_title" value="${esc(b.card_info.approver_title)}" ${admin ? "" : "disabled"}></label>
        <label class="f"><span>SOP version</span><input type="text" name="version" value="${esc(b.card_info.version)}" ${admin ? "" : "disabled"}></label>
        <label class="f"><span>Training video link</span><input type="url" name="video_url" value="${esc(b.card_info.video_url)}" ${admin ? "" : "disabled"}></label>
        <label class="f"><span>Series label</span><input type="text" name="series_label" value="${esc(b.template.series_label)}" ${admin ? "" : "disabled"}></label>
        <label class="f"><span>Badge text</span><input type="text" name="badge_text" value="${esc(b.template.badge_text)}" ${admin ? "" : "disabled"}></label>
        ${admin ? `<button class="btn green small">Save defaults</button>` : ""}</form></div>
    ${b.card_reference.length ? `<div class="card"><h3>Card format reference</h3><div class="refs" style="margin-top:10px">${b.card_reference.map(u => `<img src="${esc(u)}" alt="">`).join("")}</div></div>` : ""}
    ${b.example_frames.length ? `<div class="card"><h3>Video reference frames</h3><div class="refs" style="margin-top:10px">${b.example_frames.map(u => `<img src="${esc(u)}" alt="">`).join("")}</div></div>` : ""}
    ${b.jobs.length ? `<div class="card"><h3>Recent brand updates</h3>${b.jobs.map(j => `<div class="meta" style="margin-top:8px">${pill(j.status)}<span>${esc(j.kind.replace("_", " "))} · ${ago(j.created_at)}</span></div>${j.error ? `<div class="error-box">${esc(j.error)}</div>` : ""}`).join("")}</div>` : ""}
  </div></div>`;
  wireDrops(app);
  $$("[data-asset]").forEach(inp => inp.addEventListener("change", async () => {
    const fd = new FormData(); fd.append("file", inp.files[0]);
    try { await upload(`/api/brand/asset/${inp.dataset.asset}`, fd); toast("Updated"); loadBrandMark(); viewBrand(); } catch (err) { toast(err.message, true); }
  }));
  $("#defaults")?.addEventListener("submit", async e => {
    e.preventDefault();
    try { await api("POST", "/api/brand/settings", Object.fromEntries(new FormData(e.target))); toast("Saved"); } catch (err) { toast(err.message, true); }
  });
  $$("form[data-up]").forEach(f => f.addEventListener("submit", async e => {
    e.preventDefault();
    const file = f.querySelector("input[type=file]").files[0];
    if (!file) return toast("Choose a file first", true);
    const fd = new FormData(f);
    try { await upload(f.dataset.up, fd); toast("Uploaded - applying in the background"); setTimeout(viewBrand, 800); } catch (err) { toast(err.message, true); }
  }));
  $("#assets")?.addEventListener("submit", async e => {
    e.preventDefault();
    try { await upload("/api/brand/extract-assets", new FormData(e.target)); toast("Rebuilding assets…"); setTimeout(viewBrand, 4000); } catch (err) { toast(err.message, true); }
  });
  if (b.jobs.some(j => ["queued", "starting", "running"].includes(j.status))) timers.push(setInterval(() => viewBrand(), 6000));
}

// ------------------------------------------------------------------ team

async function viewTeam() {
  if (ME.role !== "admin") return go("#/");
  const users = await api("GET", "/api/users");
  app.innerHTML = `<div class="page-head"><div><span class="kicker">who can use studio</span><h1>Team</h1></div></div>
  <div class="cols"><div class="card"><table class="users"><tr><th>Name</th><th>Email</th><th>Role</th><th></th></tr>
    ${users.map(u => `<tr><td>${esc(u.name)}</td><td>${esc(u.email)}</td><td>${esc(u.role)}</td>
      <td style="text-align:right">${u.id !== ME.id ? `<button class="btn danger small" data-del="${u.id}">Remove</button>` : ""}</td></tr>`).join("")}</table></div>
  <form class="card" id="f"><h2>Add a person</h2><p class="sub">Share the password with them; they can change it from the account menu.</p>
    <label class="f"><span>Name</span><input type="text" name="name" required></label>
    <label class="f"><span>Email</span><input type="email" name="email" required></label>
    <label class="f"><span>Temporary password</span><input type="text" name="password" minlength="8" required></label>
    <label class="f"><span>Role</span><select name="role"><option value="editor">Editor - make videos and cards</option><option value="admin">Admin - also brand kit and team</option></select></label>
    <button class="btn green">Add</button></form></div>`;
  $("#f").addEventListener("submit", async e => {
    e.preventDefault();
    try { await api("POST", "/api/users", Object.fromEntries(new FormData(e.target))); toast("Added"); viewTeam(); } catch (err) { toast(err.message, true); }
  });
  $$("[data-del]").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("Remove this person?")) return;
    await api("DELETE", `/api/users/${b.dataset.del}`); toast("Removed"); viewTeam();
  }));
}

// ------------------------------------------------------------------ start

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
(async () => {
  if (!location.hash || location.hash === "#") location.hash = "#/";
  try { ME = await api("GET", "/api/me"); loadBrandMark(); } catch { /* not signed in */ }
  if (!ME && !["#/login", "#/setup"].includes(location.hash)) { location.hash = "#/login"; return; }
  route();
})();
