// 阅读选词页：音视频同步高亮 + 选生词 + 手动修正 + OCR/AI 建议核对（多语言）
"use strict";

const LESSON_ID = location.pathname.split("/").pop();
const UNSURE = 0.3;
const $ = sel => document.querySelector(sel);
const videoEl = $("#video");
const audioEl = $("#audioPlayer");
let media = videoEl;
const textEl = $("#text");

let lessonLang = "sv";
let sentences = [];
let vocab = new Map();
let tokById = new Map();
let mode = "pick";
let sel = null;
let progress = null;

mountUiLangPicker();
applyStaticI18n();

const opt = { stop: $("#opt-stop"), follow: $("#opt-follow") };
for (const [k, el] of Object.entries(opt)) {
  const v = lsGet("reader." + k, null);
  if (v !== null) el.checked = v === "1";
  el.addEventListener("change", () => lsSet("reader." + k, el.checked ? "1" : "0"));
}

const RATES = [0.25, 0.4, 0.5, 0.75, 1];
const speedBtns = [...document.querySelectorAll(".seg.speed button")];
function setRate(r) {
  if (!RATES.includes(r)) r = 1;
  media.defaultPlaybackRate = media.playbackRate = r;
  media.preservesPitch = true;
  for (const b of speedBtns) b.classList.toggle("on", Number(b.dataset.rate) === r);
  lsSet("reader.rate", String(r));
}
for (const b of speedBtns) b.addEventListener("click", () => setRate(Number(b.dataset.rate)));

document.addEventListener("keydown", e => {
  if ((e.key !== "[" && e.key !== "]") || e.target.closest("input, textarea, select")) return;
  e.preventDefault();
  const i = RATES.indexOf(media.playbackRate);
  const cur = i < 0 ? RATES.indexOf(1) : i;
  const next = Math.min(RATES.length - 1, Math.max(0, cur + (e.key === "]" ? 1 : -1)));
  setRate(RATES[next]);
  toast(t("speedToast", { r: RATES[next] }), 1200);
});

function bindMediaEvents(el) {
  el.addEventListener("ratechange", () => {
    const r = el.playbackRate;
    for (const b of speedBtns) b.classList.toggle("on", Number(b.dataset.rate) === r);
  });
  el.addEventListener("seeked", () => {
    if (stopAt !== null && Math.abs(el.currentTime - stopFrom) > 0.3) stopAt = null;
    highlight();
  });
  el.addEventListener("play", tick);
  el.addEventListener("play", stopSpeaker);
  el.addEventListener("pause", () => { stopAt = null; });
  el.addEventListener("timeupdate", () => {
    if (stopAt !== null && el.currentTime >= stopAt) el.pause();
    highlight();
  });
}

api("GET", `/api/lessons/${LESSON_ID}`).then(({ lesson, transcript, vocab: v, progress: p }) => {
  progress = p;
  lessonLang = lesson.language || "sv";
  renderLessonNav(LESSON_ID, "reading", p);
  document.title = `${lesson.title} · ${t("appName")}`;
  $("#title").textContent = lesson.title;
  $("#langBadge").innerHTML = langBadgeHtml(lessonLang);

  if (lesson.has_video) {
    media = videoEl;
    videoEl.hidden = false;
    $("#audioBox").hidden = true;
    videoEl.src = `/media/${LESSON_ID}/video.mp4`;
  } else {
    media = audioEl;
    videoEl.hidden = true;
    $("#audioBox").hidden = false;
    $("#subcheck-btn").hidden = true;
    audioEl.src = `/media/${LESSON_ID}/audio`;
  }
  bindMediaEvents(media);
  setRate(Number(lsGet("reader.rate", "1")));

  if (lesson.status !== "ready" || !transcript) {
    textEl.innerHTML = `<p class="muted" style="padding:0 16px">${esc(t("notReady"))}</p>`;
    return;
  }
  setTranscript(transcript);
  setVocab(v);
  render();
  jumpToToken(new URLSearchParams(location.search).get("tok"));
  subcheckState(transcript, lesson);
  watchJob();
}).catch(err => toast(err.message));

function jumpToToken(tid) {
  const hit = tid && tokById.get(tid);
  if (!hit) return;
  const el = textEl.querySelector(`.tok[data-tid="${tid}"]`);
  el?.scrollIntoView({ block: "center" });
  el?.classList.add("flash");
  const seek = () => { media.currentTime = Math.max(0, hit.s.start); };
  if (media.readyState > 0) seek(); else media.addEventListener("loadedmetadata", seek, { once: true });
}

$("#next-btn").addEventListener("click", async () => {
  if (!vocab.size && !confirm(t("confirmNoVocab"))) return;
  try {
    await saveQueue;
    if (!progress || progress.stage === "reading") {
      await api("POST", `/api/lessons/${LESSON_ID}/progress`, { action: "advance", stage: "reading" });
    }
    location.href = `/lesson/${LESSON_ID}/translate`;
  } catch (err) {
    toast(err.message);
  }
});

function setTranscript(tr) {
  sentences = tr.sentences;
  tokById = new Map();
  for (const s of sentences) s.tokens.forEach((t, i) => tokById.set(t.id, { s, t, i }));
}
function setVocab(v) { vocab = new Map(v.words.map(w => [w.key, w])); }

function render() {
  textEl.innerHTML = sentences.map(sentenceHtml).join("")
    || `<p class="muted" style="padding:0 16px">${esc(t("noTranscript"))}</p>`;
  renderVocab();
  renderStats();
  lastSent = lastTok = null;
}

const isUnsure = tk => tk.prob < UNSURE && !tk.ref;

function sentenceHtml(s) {
  const sugg = s.suggestions || [];
  const diffIdx = new Map(sugg.flatMap(g => Array.from({ length: g.last - g.first + 1 }, (_, k) => [g.first - 1 + k, g.src])));
  const toks = joinTokensHtml(s.tokens, lessonLang, (tk, i) => {
    const cls = ["tok"];
    if (vocab.has(tk.key)) cls.push("picked");
    if (isUnsure(tk)) cls.push("unsure");
    if (diffIdx.has(i)) cls.push("diff");
    if (sel && sel.sid === s.id && i >= sel.a && i <= sel.b) cls.push("sel");
    const p = x => x ? `<span class="punct">${esc(x)}</span>` : "";
    return `<span class="wd">${p(tk.pre)}<span class="${cls.join(" ")}" data-tid="${tk.id}">${esc(tk.text)}</span>${p(tk.post)}</span>`;
  });
  const editor = sel && sel.sid === s.id ? editorHtml(s) : "";
  return `<div class="sent" data-sid="${s.id}">
    <button class="play" data-sid="${s.id}">▶ ${fmtDuration(s.start)}</button>
    <div class="body"><div class="toks">${toks}</div>${sugg.map((g, k) => suggHtml(s, g, k)).join("")}${editor}</div></div>`;
}

function suggHtml(s, g, k) {
  const old = joinTokensHtml(s.tokens.slice(g.first - 1, g.last), lessonLang, tk => tk.text);
  const src = g.src === "ai"
    ? `<span class="src ai">${t("srcAi")}</span>`
    : g.src === "caption"
    ? `<span class="src">${t("srcCap")}</span>`
    : `<span class="src" title="${esc(s.ref_text || "")}">${t("srcOcr")}</span>`;
  const tip = g.partial ? t("tipPartial") : g.nodia ? t("tipNodia")
    : g.src === "ai" && g.reason ? ` (${esc(g.reason)})` : "";
  return `<div class="sugg" data-sid="${s.id}" data-k="${k}">
    ${src}<span class="old">${esc(old)}</span>→<span class="new">${esc(g.text)}</span><span class="lbl">${tip}</span>
    <button class="ghost" data-sact="listen">${t("btnListen")}</button>
    ${g.partial || g.nodia ? "" : `<button data-sact="accept">${t("btnAccept")}</button>`}
    <button class="ghost" data-sact="edit">${t("btnEdit")}</button>
    <button class="ghost" data-sact="dismiss">${t("btnDismiss")}</button>
  </div>`;
}

function rerenderSentence(sid) {
  const s = sentences.find(x => x.id === sid);
  const el = textEl.querySelector(`.sent[data-sid="${sid}"]`);
  if (!s || !el) return;
  el.outerHTML = sentenceHtml(s);
  lastSent = lastTok = null;
}

function renderStats() {
  const nw = sentences.reduce((n, s) => n + s.tokens.length, 0);
  const nu = sentences.reduce((n, s) => n + s.tokens.filter(isUnsure).length, 0);
  const nSrc = ai => sentences.reduce((n, s) => n + (s.suggestions || []).filter(g => (g.src === "ai") === ai).length, 0);
  const ns = nSrc(false), na = nSrc(true);
  const nr = sentences.reduce((n, s) => n + s.tokens.filter(tk => tk.ref).length, 0);
  $("#stats").textContent = t("statsBase", { s: sentences.length, w: nw })
    + (ns ? t("statsDiff", { n: ns }) : "")
    + (na ? t("statsAi", { n: na }) : "")
    + (nu ? t("statsUnsure", { n: nu }) : "")
    + (nr ? t("statsRef", { n: nr }) : "");
  $("#next-text").textContent = vocab.size ? t("nextPicked", { n: vocab.size }) : t("nextNone");
  $("#hint").innerHTML = mode === "pick" ? t("hintPick") : t("hintEdit");
}

function renderVocab() {
  const words = [...vocab.values()];
  $("#vocab-n").textContent = words.length ? `${words.length}` : "";
  $("#vocab-empty").hidden = words.length > 0;
  $("#vocab-list").innerHTML = words.map(w => {
    const n = w.token_ids.length;
    const gone = n === 0;
    return `<li class="${gone ? "gone" : ""}" data-key="${esc(w.key)}">
      <button class="ghost small" data-act="say" ${gone ? "disabled" : ""}>🔊</button>
      <span class="w" data-act="find">${esc(w.text)}</span>
      ${n > 1 ? `<span class="cnt">×${n}</span>` : ""}
      <button class="ghost small" data-act="remove">✕</button></li>`;
  }).join("");
}

$("#vocab-list").addEventListener("click", e => {
  const li = e.target.closest("li");
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (!li || !act) return;
  const w = vocab.get(li.dataset.key);
  if (!w) return;
  const first = tokById.get(w.token_ids[0]);
  if (act === "remove") toggleWord(w.key, w.text, false);
  else if (act === "say" && first) speakWord(w.text, { lang: lessonLang, orig: () => playWord(first.t), stop: () => media.pause() });
  else if (act === "find" && first) {
    const el = textEl.querySelector(`.tok[data-tid="${first.t.id}"]`);
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
    el?.classList.remove("flash"); void el?.offsetWidth; el?.classList.add("flash");
  }
});

let saveQueue = Promise.resolve();
let pendingSaves = 0;

function toggleWord(key, text, on) {
  if (on) {
    const ids = sentences.flatMap(s => s.tokens.filter(tk => tk.key === key).map(tk => tk.id));
    vocab.set(key, { key, text, token_ids: ids, added_at: new Date().toISOString() });
  } else {
    vocab.delete(key);
  }
  for (const el of textEl.querySelectorAll(".tok")) {
    if (tokById.get(el.dataset.tid)?.t.key === key) el.classList.toggle("picked", on);
  }
  renderVocab();
  renderStats();

  pendingSaves++;
  saveQueue = saveQueue
    .then(() => api("POST", `/api/lessons/${LESSON_ID}/vocab`, { key, text, on }))
    .then(r => { if (--pendingSaves === 0) { setVocab(r.vocab); renderVocab(); renderStats(); } })
    .catch(err => { pendingSaves--; toast(err.message); reload(); });
}

function reload() {
  api("GET", `/api/lessons/${LESSON_ID}`).then(({ transcript, vocab: v }) => {
    setTranscript(transcript); setVocab(v); render();
  }).catch(err => toast(err.message));
}

textEl.addEventListener("click", e => {
  const play = e.target.closest(".play");
  if (play) {
    const s = sentences.find(x => x.id === +play.dataset.sid);
    if (s) playRange(s.start, opt.stop.checked ? s.end + 0.25 : null);
    return;
  }
  if (e.target.closest(".editor")) return;
  const tokEl = e.target.closest(".tok");
  if (!tokEl) return;
  const hit = tokById.get(tokEl.dataset.tid);
  if (!hit) return;
  if (mode === "pick") toggleWord(hit.t.key, hit.t.text, !vocab.has(hit.t.key));
  else selectForEdit(hit, e.shiftKey);
});

document.querySelector(".seg").addEventListener("click", e => {
  const b = e.target.closest("button[data-mode]");
  if (!b || b.dataset.mode === mode) return;
  mode = b.dataset.mode;
  document.querySelectorAll(".seg button[data-mode]").forEach(x => x.classList.toggle("on", x === b));
  document.body.className = "mode-" + mode;
  const old = sel; sel = null;
  if (old) rerenderSentence(old.sid);
  renderStats();
});

function selectForEdit(hit, extend) {
  const old = sel;
  if (extend && sel && sel.sid === hit.s.id) {
    const anchor = sel.anchor;
    sel = { sid: hit.s.id, anchor, a: Math.min(anchor, hit.i), b: Math.max(anchor, hit.i) };
  } else {
    sel = { sid: hit.s.id, anchor: hit.i, a: hit.i, b: hit.i };
  }
  if (old && old.sid !== sel.sid) rerenderSentence(old.sid);
  rerenderSentence(sel.sid);
  const input = textEl.querySelector(".editor input");
  input?.focus(); input?.select();
}

function selectedTokens() {
  const s = sentences.find(x => x.id === sel.sid);
  return s ? s.tokens.slice(sel.a, sel.b + 1) : [];
}

function editorHtml(s) {
  const toks = s.tokens.slice(sel.a, sel.b + 1);
  const value = joinTokensHtml(toks, lessonLang, tk => tk.pre + tk.text + tk.post);
  const n = toks.length;
  return `<div class="editor">
    <div class="row">
      <input type="text" value="${esc(value)}" spellcheck="false" autocomplete="off">
      <button class="primary" data-act="save">${t("btnSave")}</button>
      <button data-act="listen">${t("btnListen")}</button>
      <button class="danger" data-act="delete">${n > 1 ? t("btnDelWords", { n }) : t("btnDelWord")}</button>
      <button class="ghost" data-act="cancel">${t("btnCancel")}</button>
    </div>
    <div class="tip">${t("editTip", { n })}</div>
  </div>`;
}

textEl.addEventListener("click", e => {
  const act = e.target.closest(".editor [data-act]")?.dataset.act;
  if (!act || !sel) return;
  if (act === "save") saveEdit(textEl.querySelector(".editor input").value);
  else if (act === "delete") saveEdit("");
  else if (act === "cancel") cancelEdit();
  else if (act === "listen") {
    const toks = selectedTokens();
    if (toks.length) playRange(Math.max(0, toks[0].start - 0.1), toks[toks.length - 1].end + 0.2);
  }
});

textEl.addEventListener("keydown", e => {
  if (!e.target.matches(".editor input")) return;
  if (e.key === "Enter") { e.preventDefault(); saveEdit(e.target.value); }
  else if (e.key === "Escape") cancelEdit();
});

function cancelEdit() { const old = sel; sel = null; if (old) rerenderSentence(old.sid); }

let editing = false;
async function saveEdit(text) {
  if (!sel || editing) return;
  text = text.trim();
  const toks = selectedTokens();
  const origStr = joinTokensHtml(toks, lessonLang, tk => tk.pre + tk.text + tk.post);
  if (text === origStr) { cancelEdit(); return; }
  const s = sentences.find(x => x.id === sel.sid);
  if (!text && toks.length === s.tokens.length && !confirm(t("confirmDelSent"))) return;
  editing = true;
  try {
    await saveQueue;
    const r = await api("POST", `/api/lessons/${LESSON_ID}/edit`,
      { sentence_id: sel.sid, first: sel.a + 1, last: sel.b + 1, text });
    sel = null;
    setTranscript(r.transcript); setVocab(r.vocab); render();
    toast(text ? t("saved") : t("deleted"));
  } catch (err) {
    toast(err.message);
  } finally {
    editing = false;
  }
}

let stopAt = null, stopFrom = null;
function playRange(start, end) {
  stopFrom = start; stopAt = end;
  media.currentTime = start;
  media.play().catch(() => {});
  tick();
}
function playWord(tk) { playRange(Math.max(0, tk.start - 0.08), tk.end + 0.15); }

let ticking = false;
function tick() {
  if (ticking) return;
  ticking = true;
  const loop = () => {
    if (stopAt !== null && media.currentTime >= stopAt) { media.pause(); stopAt = null; }
    highlight();
    if (!media.paused) requestAnimationFrame(loop); else ticking = false;
  };
  requestAnimationFrame(loop);
}

let lastSent = null, lastTok = null;
function highlight() {
  const curT = media.currentTime;
  let cur = null, curTok = null;
  for (let i = 0; i < sentences.length; i++) {
    const s = sentences[i];
    const until = sentences[i + 1]?.start ?? s.end + 0.5;
    if (curT >= s.start && curT < until) {
      cur = s;
      curTok = s.tokens.find(x => curT >= x.start && curT < x.end + 0.05) || null;
      break;
    }
  }
  const sid = cur?.id ?? null, tid = curTok?.id ?? null;
  if (sid !== lastSent) {
    textEl.querySelector(".sent.on")?.classList.remove("on");
    const el = sid !== null ? textEl.querySelector(`.sent[data-sid="${sid}"]`) : null;
    el?.classList.add("on");
    if (el && opt.follow.checked && !media.paused) {
      const r = el.getBoundingClientRect();
      if (r.top < 60 || r.bottom > innerHeight - 20) el.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    lastSent = sid;
  }
  if (tid !== lastTok) {
    textEl.querySelector(".tok.now")?.classList.remove("now");
    if (tid) textEl.querySelector(`.tok[data-tid="${tid}"]`)?.classList.add("now");
    lastTok = tid;
  }
}

document.addEventListener("keydown", e => {
  if (e.key !== " " || e.target.closest("input, textarea, button, video, audio, select")) return;
  e.preventDefault();
  if (media.paused) media.play().catch(() => {}); else media.pause();
});

const subBtn = $("#subcheck-btn");
function subcheckState(tr, lesson) {
  const st = tr && tr.subtitle_check;
  if (st) {
    subBtn.textContent = t("subcheckRedo");
    if (!st.ocr_events && !st.caption_cues) toast(t("noOcrFound"), 4000);
  }
  if (lesson && lesson.subcheck_error) toast(lesson.subcheck_error, 6000);
}

subBtn.addEventListener("click", async () => {
  try {
    await saveQueue;
    await api("POST", `/api/lessons/${LESSON_ID}/subcheck`);
    watchJob(true);
  } catch (err) { toast(err.message); }
});

let jobTimer = null;
async function watchJob(started = false) {
  clearTimeout(jobTimer);
  try {
    const { job } = await api("GET", `/api/lessons/${LESSON_ID}/job`);
    const running = job && (job.state === "queued" || job.state === "running");
    if (running) {
      subBtn.disabled = true;
      subBtn.textContent = `📺 ${Math.round(job.progress)}% ${job.message}`;
      jobTimer = setTimeout(() => watchJob(true), 1000);
      return;
    }
    subBtn.disabled = false;
    if (started) {
      if (job && job.state === "error") toast(job.error, 6000);
      const { transcript, vocab: v } = await api("GET", `/api/lessons/${LESSON_ID}`);
      setTranscript(transcript); setVocab(v); render();
      subcheckState(transcript, null);
      const st = transcript.subtitle_check;
      if (st && (st.ocr_events || st.caption_cues)) {
        toast(t("subcheckDone", { c: st.confirmed, s: st.suggestions }), 5000);
      }
    }
  } catch (err) {
    subBtn.disabled = false;
    if (started) toast(err.message);
  }
}

textEl.addEventListener("click", async e => {
  const b = e.target.closest(".sugg [data-sact]");
  if (!b) return;
  const box = b.closest(".sugg");
  const s = sentences.find(x => x.id === +box.dataset.sid);
  const g = s && (s.suggestions || [])[+box.dataset.k];
  if (!g) return;
  const toks = s.tokens.slice(g.first - 1, g.last);
  const act = b.dataset.sact;
  if (act === "listen") {
    playRange(Math.max(0, toks[0].start - 0.3), toks[toks.length - 1].end + 0.4);
  } else if (act === "edit") {
    if (mode !== "edit") document.querySelector('.seg button[data-mode="edit"]').click();
    sel = { sid: s.id, anchor: g.first - 1, a: g.first - 1, b: g.last - 1 };
    rerenderSentence(s.id);
    const input = textEl.querySelector(".editor input");
    if (input) {
      input.value = toks[0].pre + g.text.replace(/…$/, "") + toks[toks.length - 1].post;
      input.focus();
    }
  } else if (act === "accept") {
    if (editing) return;
    sel = { sid: s.id, anchor: g.first - 1, a: g.first - 1, b: g.last - 1 };
    await saveEdit(toks[0].pre + g.text + toks[toks.length - 1].post);
  } else if (act === "dismiss") {
    try {
      await saveQueue;
      const r = await api("POST", `/api/lessons/${LESSON_ID}/suggestion/dismiss`,
        { sentence_id: s.id, first: g.first, last: g.last });
      setTranscript(r.transcript); rerenderSentence(s.id); renderStats();
    } catch (err) { toast(err.message); }
  }
});
