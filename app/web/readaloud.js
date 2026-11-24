// 全文朗读与跟读对比（支持视频与纯音频，多语言适配）
"use strict";

const LESSON_ID = lessonIdFromPath();
const $ = sel => document.querySelector(sel);
const videoEl = $("#video");
const audioEl = $("#audioPlayer");
const mine = $("#mine");
let media = videoEl;
let player = new RangePlayer(videoEl);
const sentsEl = $("#sents");

let D = null, lessonLang = "sv", sents = [], idx = 0;
let recs = [];
let following = false;
let cancelWait = null;

mountUiLangPicker();
applyStaticI18n();

const optAuto = $("#opt-autorec"), optGap = $("#opt-gap"), optTr = $("#opt-tr");
optAuto.checked = lsGet("ra.autorec", "0") === "1";
optGap.value = lsGet("ra.gap", "1.5");
optTr.checked = lsGet("ra.tr", "1") === "1";
document.body.classList.toggle("hide-tr", !optTr.checked);
optAuto.addEventListener("change", () => lsSet("ra.autorec", optAuto.checked ? "1" : "0"));
optGap.addEventListener("change", () => lsSet("ra.gap", optGap.value));
optTr.addEventListener("change", () => { lsSet("ra.tr", optTr.checked ? "1" : "0"); document.body.classList.toggle("hide-tr", !optTr.checked); });

api("GET", `/api/lessons/${LESSON_ID}`).then(d => {
  D = d;
  lessonLang = d.lesson.language || "sv";
  document.title = `${d.lesson.title} · ${t("stage_read_aloud")}`;
  $("#title").textContent = d.lesson.title;
  $("#langBadge").innerHTML = langBadgeHtml(lessonLang);
  renderLessonNav(LESSON_ID, "read_aloud", d.progress);
  if (d.lesson.status !== "ready" || !d.transcript) {
    sentsEl.innerHTML = `<p class="muted" style="padding:0 16px">${esc(t("notReady"))}</p>`;
    return;
  }
  if (d.lesson.has_video) {
    media = videoEl;
    videoEl.hidden = false;
    $("#audioBox").hidden = true;
    videoEl.src = `/media/${LESSON_ID}/video.mp4`;
  } else {
    media = audioEl;
    videoEl.hidden = true;
    $("#audioBox").hidden = false;
    audioEl.src = `/media/${LESSON_ID}/audio`;
  }
  player = new RangePlayer(media);
  media.addEventListener("play", () => requestAnimationFrame(tickWords));
  media.addEventListener("pause", tickWords);
  sents = d.transcript.sentences;
  recs = d.recordings;
  render();
  renderDone();
}).catch(err => toast(err.message));

function recsOf(sid) { return recs.filter(r => r.sentence_id === sid); }

function sentHtml(s, i) {
  const keys = new Set(D.vocab.words.map(w => w.key));
  const trEntry = (D.translation?.sentences || []).find(x => x.id === s.id);
  const trText = trEntry ? (trEntry.text || trEntry.zh || "") : "";
  const src = joinTokensHtml(s.tokens, lessonLang, tk =>
    esc(tk.pre) + `<span class="w" data-tid="${tk.id}">${keys.has(tk.key) ? `<mark class="hl">${esc(tk.text)}</mark>` : esc(tk.text)}</span>` + esc(tk.post)
  );
  const rs = recsOf(s.id);
  return `<div class="s ${i === idx ? "cur" : ""} ${rs.length ? "has" : ""}" data-i="${i}">
    <div class="src"><span class="num">${i + 1}</span>${src}</div>
    ${trText ? `<div class="tr">${esc(trText)}</div>` : ""}
    <div class="btns">
      <button class="small" data-act="orig">${t("btnOrig")}</button>
      <button class="small rec-btn ${recSid === s.id ? "on" : ""}" data-act="rec">${recSid === s.id ? t("btnStopRec") : t("btnRec")}</button>
      <button class="small keep" data-act="mine" ${rs.length ? "" : "disabled"}>${t("btnMine")}</button>
      <button class="small" data-act="cmp" ${rs.length ? "" : "disabled"}>${t("btnCmp")}</button>
      ${rs.length ? `<button class="ghost small keep cnt" data-act="list">${t("recCount", { n: rs.length })}</button>` : ""}
    </div>
    <div class="gap" hidden><i></i></div>
    ${openList === s.id ? listHtml(rs) : ""}
  </div>`;
}

let openList = null;
function listHtml(rs) {
  return `<ul class="recs">${rs.slice().reverse().map((r, k) => `<li>
    <button class="ghost small" data-file="${esc(r.file)}" data-act="play-file">▶</button>
    <span>${fmtDate(r.at)}${k === 0 ? t("recLatest") : ""}</span>
    <button class="ghost small danger" data-file="${esc(r.file)}" data-act="del-file">🗑</button></li>`).join("")}</ul>`;
}

function render() { sentsEl.innerHTML = sents.map(sentHtml).join(""); }
function rerender(i) { const el = sentsEl.querySelector(`.s[data-i="${i}"]`); if (el) el.outerHTML = sentHtml(sents[i], i); }

function select(i, scroll = true) {
  if (i < 0 || i >= sents.length) return;
  const old = idx; idx = i;
  rerender(old); rerender(i);
  if (scroll) {
    const el = sentsEl.querySelector(`.s[data-i="${i}"]`);
    const r = el.getBoundingClientRect();
    if (r.top < 60 || r.bottom > innerHeight - 20) el.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

function renderDone() {
  const box = $("#done-box");
  const done = D.progress.stage === "done";
  const n = new Set(recs.map(r => r.sentence_id)).size;
  box.className = `card done-box ${done ? "fin" : ""}`;
  box.innerHTML = done
    ? `<span>${t("doneSummary", { d: fmtDate(D.progress.completed_at), a: n, b: sents.length })}</span>
       <span style="flex:1"></span><a class="btn" href="/lesson/${LESSON_ID}/record">${t("viewRecord")}</a><a class="btn" href="/">${t("backHome")}</a>`
    : `<span class="muted">${t("notDoneSummary", { a: n, b: sents.length })}</span>
       <span style="flex:1"></span><button class="primary" id="finish">${t("finishLesson")}</button>`;
  $("#finish")?.addEventListener("click", async () => {
    try {
      const r = await api("POST", `/api/lessons/${LESSON_ID}/progress`, { action: "advance", stage: "read_aloud" });
      D.progress = r.progress;
      renderLessonNav(LESSON_ID, "read_aloud", D.progress);
      renderDone();
      toast(t("lessonDone"));
    } catch (err) { toast(err.message); }
  });
}

async function playOrig(i) { stopMine(); await player.playSentence(sents[i]); }
function latestRec(sid) { const rs = recsOf(sid); return rs[rs.length - 1]; }
function playFile(file) {
  player.stop();
  return new Promise(res => {
    mine.src = `/media/${LESSON_ID}/recordings/${encodeURIComponent(file)}`;
    mine.onended = mine.onerror = mine.onpause = () => res();
    mine.play().catch(() => res());
  });
}
function stopMine() { if (!mine.paused) mine.pause(); }
async function playMine(i) { const r = latestRec(sents[i].id); if (r) await playFile(r.file); }
async function compare(i) { await playOrig(i); await new Promise(r => setTimeout(r, 300)); await playMine(i); }

let lastTid = null;
function tickWords() {
  const curT = media.currentTime;
  const s = sents[idx];
  const tok = s && !media.paused ? s.tokens.find(x => curT >= x.start && curT < x.end + 0.05) : null;
  const tid = tok?.id ?? null;
  if (tid !== lastTid) {
    sentsEl.querySelector(".w.now")?.classList.remove("now");
    if (tid) sentsEl.querySelector(`.w[data-tid="${tid}"]`)?.classList.add("now");
    lastTid = tid;
  }
  if (!media.paused) requestAnimationFrame(tickWords);
}

let stream = null, recorder = null, chunks = [], recSid = null;
async function ensureMic() {
  if (stream) return stream;
  const mic = $("#mic");
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    mic.textContent = t("micReady");
    mic.className = "mic on";
    return stream;
  } catch {
    mic.textContent = t("micErr");
    mic.className = "mic err";
    throw new Error(t("micErr"));
  }
}
function pickMime() {
  for (const m of ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/mp4"]) {
    if (window.MediaRecorder?.isTypeSupported?.(m)) return m;
  }
  return "";
}
async function startRec(i) {
  if (recorder) return;
  await ensureMic();
  player.stop(); stopMine();
  const mime = pickMime();
  chunks = [];
  recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
  recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
  recorder.start();
  recSid = sents[i].id;
  recorder._i = i;
  recorder._t0 = performance.now();
  rerender(i);
}
function stopRec() {
  if (!recorder) return Promise.resolve();
  const rec = recorder, i = rec._i, sid = recSid;
  return new Promise(res => {
    rec.onstop = async () => {
      recorder = null; recSid = null;
      const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
      const secs = (performance.now() - rec._t0) / 1000;
      rerender(i);
      if (secs < 0.4 || blob.size < 500) { res(); return; }
      try {
        const r = await fetch(`/api/lessons/${LESSON_ID}/recordings?sentence_id=${sid}`,
          { method: "POST", body: blob, headers: { "Content-Type": blob.type } });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || r.status);
        recs = data.recordings; rerender(i); renderDone();
      } catch (err) { toast(err.message); }
      res();
    };
    rec.stop();
  });
}
async function toggleRec(i) {
  if (recorder) { const same = recorder._i === i; await stopRec(); if (same) return; }
  try { await startRec(i); } catch (err) { toast(err.message); }
}

function wait(ms, gapEl) {
  return new Promise(res => {
    const t0 = performance.now();
    const bar = gapEl?.querySelector("i");
    if (gapEl) gapEl.hidden = false;
    let raf;
    const done = () => { cancelAnimationFrame(raf); clearTimeout(timer); cancelWait = null; if (gapEl) gapEl.hidden = true; res(); };
    const timer = setTimeout(done, ms);
    const step = () => { if (bar) bar.style.width = `${Math.min(100, 100 * (performance.now() - t0) / ms)}%`; raf = requestAnimationFrame(step); };
    step();
    cancelWait = done;
  });
}

async function follow() {
  if (following) { stopFollow(); return; }
  following = true;
  $("#follow").textContent = t("followStop");
  try {
    for (let i = idx; following && i < sents.length; i++) {
      select(i);
      await playOrig(i);
      if (!following) break;
      const s = sents[i];
      const ms = Math.max(1500, (s.end - s.start) * parseFloat(optGap.value) * 1000 + 600);
      if (optAuto.checked) { try { await startRec(i); } catch { optAuto.checked = false; } }
      await wait(ms, sentsEl.querySelector(`.s[data-i="${i}"] .gap`));
      if (recorder) await stopRec();
    }
  } finally {
    following = false;
    $("#follow").textContent = t("followStart");
  }
}
function stopFollow() { following = false; cancelWait?.(); player.stop(); if (recorder) stopRec(); }

$("#follow").addEventListener("click", follow);
sentsEl.addEventListener("click", async e => {
  const row = e.target.closest(".s");
  if (!row) return;
  const i = +row.dataset.i;
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (!act) { if (i !== idx) { if (following) stopFollow(); select(i, false); } return; }
  e.target.closest("button")?.blur();
  if (i !== idx && act !== "list" && act !== "play-file" && act !== "mine") select(i, false);
  if (act === "orig") playOrig(i);
  else if (act === "rec") toggleRec(i);
  else if (act === "mine") playMine(i);
  else if (act === "cmp") compare(i);
  else if (act === "list") { openList = openList === sents[i].id ? null : sents[i].id; rerender(i); }
  else if (act === "play-file") playFile(e.target.closest("[data-file]").dataset.file);
  else if (act === "del-file") {
    const f = e.target.closest("[data-file]").dataset.file;
    if (!confirm(t("confirmDelRec"))) return;
    try {
      const r = await api("DELETE", `/api/lessons/${LESSON_ID}/recordings/${encodeURIComponent(f)}`);
      recs = r.recordings; rerender(i); renderDone();
    } catch (err) { toast(err.message); }
  }
});

document.addEventListener("keydown", e => {
  if (!sents.length || e.ctrlKey || e.altKey || e.metaKey || e.target.closest?.("input, textarea, select")) return;
  const k = e.key.toLowerCase();
  if (k === " ") { e.preventDefault(); if (following) return; media.paused ? playOrig(idx) : player.stop(); }
  else if (k === "r" && !e.repeat) toggleRec(idx);
  else if (k === "m") playMine(idx);
  else if (k === "c") compare(idx);
  else if (k === "arrowdown" || k === "j") { e.preventDefault(); if (!following) select(idx + 1); }
  else if (k === "arrowup" || k === "k") { e.preventDefault(); if (!following) select(idx - 1); }
  else if (k === "escape") stopFollow();
});

addEventListener("beforeunload", e => { if (recorder) { e.preventDefault(); e.returnValue = ""; } });
