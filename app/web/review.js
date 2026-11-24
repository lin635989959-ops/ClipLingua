// 多语言无痛记忆曲线复习 + 自由练习
"use strict";

mountUiLangPicker();
applyStaticI18n();
document.title = `${t("appName")} · ${t("navReview")}`;

const $ = sel => document.querySelector(sel);
const main = $("#main");
const audio = $("#audio");
const player = new RangePlayer(audio);

const GOOD = new Set(["known", "correct"]);
let INTERVALS = [0, 1, 3, 7, 15, 30];
let TAB = new URLSearchParams(location.search).get("free") ? "free" : "due";
let FILTER_LANG = lsGet("rv.lang", "all");
let BOOK = null;
let R = null;

function glossOf(it) { return it?.text || it?.zh || ""; }
function modeName(m) { return t(`mode_${m}`); }

function levelDots(lv) {
  if (lv == null) return `<span class="lv new">${t("newWord")}</span>`;
  return `<span class="lv" title="${lv} / 5">${"●".repeat(lv)}${"○".repeat(5 - lv)}</span>`;
}
function inDays(n) { return n <= 0 ? t("today") : n === 1 ? t("tomorrow") : t("inDays", { n }); }
function fmtDay(iso) {
  const days = Math.round((new Date(iso + "T00:00") - new Date(new Date().toDateString())) / 86400000);
  return `${iso.slice(5)} (${inDays(days)})`;
}
function nextLevel(lv, good) {
  const nl = good ? Math.min(5, lv + 1) : Math.max(0, lv - 2);
  return { nl, days: good ? INTERVALS[nl] : 1 };
}
function changeText(lv, good) {
  const p = nextLevel(lv, good);
  return t("lvChange", { a: lv, b: p.nl, when: inDays(p.days) });
}
function markForm(sentence, form, blank, lang) {
  if (!form) return esc(sentence);
  const escaped = form.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = langMeta(lang).isCjk
    ? new RegExp(`(${escaped})`, "giu")
    : new RegExp(`(^|[^\\p{L}\\p{N}])(${escaped})(?=[^\\p{L}\\p{N}]|$)`, "giu");
  return esc(sentence).replace(re, (...args) => {
    if (langMeta(lang).isCjk) return blank ? `<span class="blank">＿＿</span>` : `<mark class="hl">${args[1]}</mark>`;
    return args[1] + (blank ? `<span class="blank">＿＿＿</span>` : `<mark class="hl">${args[2]}</mark>`);
  });
}

function playRange(lid, start, end, slow = false) {
  stopSpeaker();
  const src = `/media/${lid}/audio`;
  if (!audio.src.endsWith(src)) audio.src = src;
  audio.defaultPlaybackRate = audio.playbackRate = slow ? 0.7 : 1;
  return player.play(start, end);
}
function origWord(it) { if (it.orig) return playRange(it.orig.lesson_id, it.orig.start - 0.08, it.orig.end + 0.15); }
function origSent(it, slow) {
  if (it.mode === "cloze") { const c = it.cloze; return playRange(c.lesson_id, c.start - 0.05, c.end + 0.3, slow); }
  const o = it.orig;
  if (o && o.sent_start != null) return playRange(o.lesson_id, o.sent_start - 0.05, o.sent_end + 0.3, slow);
}
function ttsSent(it) {
  player.stop();
  const lang = it.language || "sv";
  const text = it.mode === "cloze"
    ? joinTokensHtml(it.cloze.tokens, lang, tk => tk.pre + tk.text + tk.post)
    : it.orig?.sentence;
  if (!text) return;
  const v = wordVoice(lang);
  SPEAKER.say(text, { lang, voice: v === "orig" ? langMeta(lang).defaultVoice : v });
}
function sayWord(it, slow = false) {
  const btn = main.querySelector(".speaker[data-act=say]");
  btn?.classList.add("playing");
  Promise.resolve(speakWord(it.lemma, { lang: it.language || "sv", orig: () => origWord(it), stop: () => player.stop(), slow }))
    .finally(() => btn?.classList.remove("playing"));
}
function playCloze(it, slow = false) {
  const btn = main.querySelector(".speaker[data-act=cplay]");
  btn?.classList.add("playing");
  Promise.resolve(origSent(it, slow)).finally(() => btn?.classList.remove("playing"));
}

function renderTabs() {
  const langOpts = [["all", t("allLangs")], ...["sv", "en", "es", "zh"].map(c => [c, `${langName(c)} (${LANG_META[c].short})`])]
    .map(([v, lbl]) => `<option value="${v}" ${v === FILTER_LANG ? "selected" : ""}>${esc(lbl)}</option>`).join("");
  $("#tabs").innerHTML = `<a href="/review" data-tab="due" class="${TAB === "due" ? "on" : ""}">${t("tabDue")}</a>
    <a href="/review?free=1" data-tab="free" class="${TAB === "free" ? "on" : ""}">${t("tabFree")}</a>
    <span style="margin-left:auto"><select id="rv-lang" style="font-size:13px;padding:4px 8px">${langOpts}</select></span>`;
  $("#rv-lang").addEventListener("change", e => {
    FILTER_LANG = e.target.value;
    lsSet("rv.lang", FILTER_LANG);
    R = null; stopAll();
    (TAB === "free" ? introFree : introDue)();
  });
}

$("#tabs").addEventListener("click", e => {
  const a = e.target.closest("[data-tab]");
  if (!a) return;
  e.preventDefault();
  R = null; stopAll();
  (a.dataset.tab === "free" ? introFree : introDue)();
});
function stopAll() { player.stop(); stopSpeaker(); }

async function introDue() {
  TAB = "due"; R = null; renderTabs();
  history.replaceState(null, "", "/review");
  let s;
  try { s = await api("GET", `/api/review/summary?lang=${encodeURIComponent(FILTER_LANG)}`); } catch (err) { toast(err.message); return; }
  INTERVALS = s.intervals;
  const st = s.settings;
  if (!s.reviewable) {
    main.innerHTML = `<div class="card notice"><p>${esc(t("vocabEmptyAll"))}</p><a class="btn" href="/vocab">${t("navVocab")}</a></div>`;
    return;
  }
  const only = lsGet("rv.only", "0") === "1";
  const nDue = Math.min(st.session_size, s.due);
  const nNew = only ? 0 : Math.max(0, Math.min(st.new_per_session, s.new_available, st.session_size - nDue));
  const total = nDue + nNew;
  const maxLv = Math.max(1, s.new_available, ...s.levels);
  const voiceLang = FILTER_LANG === "all" ? "sv" : FILTER_LANG;

  main.innerHTML = `<div class="card intro">
    <h2>${t("dueTitle")}</h2>
    <div class="stats">
      <div><div class="big-num">${s.due}</div><div class="muted">${t("dueCountLbl")}</div></div>
      <div><div class="big-num" style="color:var(--muted)">${s.new_available}</div><div class="muted">${t("newCountLbl")}</div></div>
    </div>
    ${s.due > nDue ? `<p class="muted" style="font-size:13px">${t("dueCapHint", { n: nDue })}</p>` : ""}
    ${s.due ? "" : `<p>${t("reviewAllDone")}${s.next_due ? ` ${t("reviewNext", { d: fmtDay(s.next_due), n: s.next_due_count })}` : ""}</p>`}
    <div class="levels">
      <div class="col new"><span>${s.new_available}</span><i style="height:${70 * s.new_available / maxLv}%"></i></div>
      ${s.levels.map(n => `<div class="col"><span>${n}</span><i style="height:${70 * n / maxLv}%"></i></div>`).join("")}
    </div>
    <div class="levels-label"><span>${t("newWord")}</span>${s.levels.map((_, i) => `<span>L${i}</span>`).join("")}</div>
    <div class="opts">
      <label>${t("optSessionSize")} <select id="opt-size">${[10, 20, 30].map(n => `<option ${n === st.session_size ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      <label>${t("optNewPer")} <input type="number" id="opt-new" min="0" max="20" value="${st.new_per_session}"></label>
      <label><input type="checkbox" id="opt-only" ${only ? "checked" : ""}> ${t("optOnlyDue")}</label>
      ${voicePickerHtml(voiceLang)}
      <label><input type="checkbox" id="opt-loose" ${lsGet("spell.loose", "0") === "1" ? "checked" : ""}> ${t("optLoose")}</label>
    </div>
    <div class="actions">
      <button class="primary big" id="start" ${total ? "" : "disabled"}>${t("startReviewCount", { t: total, n: nNew ? t("withNew", { n: nNew }) : "" })}</button>
      <span class="sp"></span>
      <button id="to-free">${t("freePracticeBtn")}</button>
    </div>
    <details class="howto"><summary>${t("howtoTitle")}</summary>
      <table><tr><th>${t("howtoLv")}</th>${INTERVALS.map((_, i) => `<th>${i}</th>`).join("")}</tr>
        <tr><th>${t("howtoGap")}</th>${INTERVALS.map(d => `<td>${d ? t("howtoDay", { d }) : t("howtoSameDay")}</td>`).join("")}</tr>
        <tr><th>${t("howtoMode")}</th><td colspan="2">${modeName("listen")}</td><td colspan="2">${modeName("recall")}</td><td>${modeName("spelling")}</td><td>${modeName("cloze")}</td></tr></table>
      ${t("howtoDesc")}
    </details>
  </div>`;
  bindVoicePicker(main);
  $("#opt-loose").addEventListener("change", e => lsSet("spell.loose", e.target.checked ? "1" : "0"));
  $("#opt-only").addEventListener("change", e => { lsSet("rv.only", e.target.checked ? "1" : "0"); introDue(); });
  $("#opt-size").addEventListener("change", e => saveSettings({ session_size: +e.target.value }));
  $("#opt-new").addEventListener("change", e => {
    const n = Math.round(+e.target.value);
    if (!(n >= 0 && n <= 20)) { e.target.value = st.new_per_session; return; }
    saveSettings({ new_per_session: n });
  });
  $("#to-free").addEventListener("click", introFree);
  $("#start").addEventListener("click", async () => {
    $("#start").disabled = true;
    try {
      const d = await api("GET", `/api/review/session?lang=${encodeURIComponent(FILTER_LANG)}${only ? "&new=0" : ""}`);
      if (!d.items.length) { introDue(); return; }
      startRound(d.items, false);
    } catch (err) { toast(err.message); $("#start").disabled = false; }
  });
}

async function saveSettings(body) {
  try { await api("POST", "/api/review/settings", body); } catch (err) { toast(err.message); }
  introDue();
}

function fval(k, def) { return lsGet("rv.f." + k, def); }

async function introFree() {
  TAB = "free"; R = null; renderTabs();
  history.replaceState(null, "", "/review?free=1");
  try {
    const d = await api("GET", `/api/vocabbook?lang=${encodeURIComponent(FILTER_LANG)}`);
    INTERVALS = d.intervals || INTERVALS;
    BOOK = (d.words || []).filter(w => w.reviewable);
  } catch (err) { toast(err.message); return; }
  if (!BOOK.length) {
    main.innerHTML = `<div class="card notice"><p>${esc(t("vocabEmptyAll"))}</p><a class="btn" href="/vocab">${t("navVocab")}</a></div>`;
    return;
  }
  const lessons = new Map(), poses = new Set();
  for (const w of BOOK) { if (w.pos) poses.add(w.pos); for (const s of w.sources) lessons.set(s.lesson_id, s.title || s.lesson_id); }
  const sel = (id, opts, def) => `<select id="f-${id}">${opts.map(([v, n]) => `<option value="${esc(v)}" ${v === fval(id, def) ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>`;
  const scopeOpts = [["all", t("fltAll")], ["unknown", t("fltUnknown")], ["none", t("fltNone")], ["due", t("fltDue")]];
  const lvOpts = [["all", t("allLevels")], ["new", t("newWord")], ["0-1", t("fltLv01")], ["2-3", t("fltLv23")], ["4-5", t("fltLv45")]];
  const cntOpts = [["10", "10"], ["20", "20"], ["all", t("fltAll")]];
  const modeOpts = [["auto", t("modeAuto")], ["listen", modeName("listen")], ["recall", modeName("recall")], ["spelling", modeName("spelling")], ["cloze", modeName("cloze")]];

  main.innerHTML = `<div class="card intro">
    <h2>${t("freeTitle")}</h2>
    <p class="muted" style="font-size:14px">${t("freeDesc")}</p>
    <div class="free-grid">
      <span>${t("lblScope")}</span>${sel("scope", scopeOpts, "all")}
      <span>${t("lblLesson")}</span>${sel("lesson", [["all", t("allLessons")], ...lessons], "all")}
      <span>${t("lblPos")}</span>${sel("pos", [["all", t("allPos")], ...[...poses].map(p => [p, posName(p)])], "all")}
      <span>${t("lblLevel")}</span>${sel("level", lvOpts, "all")}
      <span>${t("lblCount")}</span>${sel("count", cntOpts, "20")}
      <span>${t("lblMode")}</span>${sel("mode", modeOpts, "auto")}
    </div>
    <div class="opts">${voicePickerHtml(FILTER_LANG === "all" ? (BOOK[0]?.language || "sv") : FILTER_LANG)}
      <label><input type="checkbox" id="opt-loose" ${lsGet("spell.loose", "0") === "1" ? "checked" : ""}> ${t("optLoose")}</label></div>
    <div class="actions"><button class="primary big" id="start"></button><span class="sp"></span><span class="muted" id="f-matches" style="font-size:13px"></span></div>
  </div>`;
  bindVoicePicker(main);
  $("#opt-loose").addEventListener("change", e => lsSet("spell.loose", e.target.checked ? "1" : "0"));
  main.querySelectorAll(".free-grid select").forEach(s => s.addEventListener("change", () => {
    lsSet("rv.f." + s.id.slice(2), s.value); updateFreeCount();
  }));
  $("#start").addEventListener("click", startFree);
  updateFreeCount();
}

function freeMatches() {
  const v = id => $("#f-" + id).value;
  const today = new Date().toLocaleDateString("sv-SE");
  return BOOK.filter(w => {
    const sc = v("scope"), lv = v("level"), ls = v("lesson"), pos = v("pos");
    if (sc === "unknown" && w.result !== "unknown") return false;
    if (sc === "none" && w.result) return false;
    if (sc === "due" && !(w.due && w.due <= today)) return false;
    if (ls !== "all" && !w.sources.some(s => s.lesson_id === ls)) return false;
    if (pos !== "all" && w.pos !== pos) return false;
    if (lv === "new") return w.level == null;
    if (lv !== "all") { const [a, b] = lv.split("-").map(Number); return w.level != null && w.level >= a && w.level <= b; }
    return true;
  });
}
function updateFreeCount() {
  const m = freeMatches(), c = $("#f-count").value;
  const n = c === "all" ? m.length : Math.min(+c, m.length);
  $("#f-matches").textContent = t("matchCount", { n: m.length });
  $("#start").textContent = t("startBtn", { n });
  $("#start").disabled = !n;
}
async function startFree() {
  const m = shuffle(freeMatches()), c = $("#f-count").value;
  const ids = (c === "all" ? m : m.slice(0, +c)).map(w => w.id);
  $("#start").disabled = true;
  try {
    const d = await api("POST", "/api/review/free", { ids, mode: $("#f-mode").value, lang: FILTER_LANG });
    startRound(d.items, true);
  } catch (err) { toast(err.message); $("#start").disabled = false; }
}

function startRound(items, free) {
  R = { items, free, queue: shuffle(items), first: {}, revealed: false, lastOk: false };
  for (const x of items) ttsPrefetch([x.lemma], x.language || "sv");
  showCard();
}
function cur() { return R.queue[0]; }

function record(it, result) {
  if (it.id in R.first) return;
  R.first[it.id] = result;
  api("POST", "/api/review/answer", { id: it.id, mode: it.mode, result, free: R.free })
    .catch(err => toast(err.message));
}

function headerHtml(it) {
  const done = R.items.length - new Set(R.queue.map(x => x.id)).size;
  return `<div class="bar-row">
    <span>${t("passedProg", { d: done, t: R.items.length })}</span>
    <div class="bar"><i style="width:${100 * done / R.items.length}%"></i></div>
    <span class="mode-tag">${langBadgeHtml(it.language || "sv")} ${esc(modeName(it.mode))} ${levelDots(it.new ? null : it.level)}</span>
    <button class="ghost small" id="quit">${t("quitRound")}</button>
  </div>`;
}

function wordToolsHtml(lang) {
  return wordVoice(lang) === "orig" ? "" : `<button class="small" data-act="slow">${t("btnSlow")}</button>
    <button class="small" data-act="orig">${t("origBtn")}</button>`;
}

function showCard() {
  stopAll();
  if (!R.queue.length) { endRound(); return; }
  R.revealed = false; R.lastOk = false;
  const it = cur();
  const lang = it.language || "sv";
  const self = it.mode === "listen" || it.mode === "recall";
  let cue = "";
  if (it.mode === "listen") {
    cue = `<button class="speaker" data-act="say">🔊</button>
      <div class="tools">${wordToolsHtml(lang)}<button class="small ghost" data-act="hint-spell">${t("hintSpell")}</button></div>
      <p class="prompt">${t("promptListen")}</p>`;
  } else if (it.mode === "recall") {
    cue = `<div class="zh-big">${esc(glossOf(it))}</div><div class="pos">${esc(posName(it.pos || ""))}</div>
      <div class="tools">${it.orig ? `<button class="small ghost" data-act="hint-ctx">${t("hintCtx")}</button>` : ""}</div>
      <p class="prompt">${t("promptRecall", { lang: langName(lang) })}</p>`;
  } else if (it.mode === "spelling") {
    cue = `<button class="speaker" data-act="say">🔊</button>
      <div class="zh-big">${esc(glossOf(it))}</div><div class="pos">${esc(posName(it.pos || ""))}</div>
      <div class="tools">${wordToolsHtml(lang)}<button class="small ghost" data-act="hint-first">${t("hintFirst")}</button></div>
      <p class="prompt">${t("promptSpellLemma")}</p>`;
  } else {
    const c = it.cloze;
    cue = `<button class="speaker" data-act="cplay">🔊</button>
      <div class="tools"><button class="small" data-act="cslow">${t("btnSlow")}</button>
        <button class="small" data-act="senttts">${t("btnTtsSent")}</button>
        <button class="small ghost" data-act="hint-zh">${t("hintGloss")}</button>
        <button class="small ghost" data-act="hint-first">${t("hintFirst")}</button></div>
      <div class="cloze-sent">${joinTokensHtml(c.tokens, lang, tk => esc(tk.pre) + (tk.blank ? `<span class="blank">＿＿＿</span>` : esc(tk.text)) + esc(tk.post))}
        <span class="src">📹 ${esc(c.title)}</span></div>
      <p class="prompt">${t("promptCloze")}</p>`;
  }
  const shortcutHint = langMeta(lang).hasSvShortcut ? ` · <span class="kbd">[</span> å <span class="kbd">'</span> ä <span class="kbd">;</span> ö` : "";
  main.innerHTML = headerHtml(it) + `<div class="card q">
    <div class="cue">${cue}</div>
    ${self ? "" : `<div class="spell">
      <input type="text" id="spell" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="${esc(it.mode === "cloze" ? t("clozePlaceholder") : t("spellPlaceholder"))}">
      <button class="primary" id="check">${t("btnCheck")}</button></div>
      ${specialLettersHtml(lang)}`}
    <div id="ans"></div>
    <div class="judge" id="judge">${self
      ? `<button class="primary big" id="reveal">${t("revealBtn")}</button>`
      : `<button class="ghost" id="giveup">${t("btnGiveUp")}</button>`}</div>
    <div class="keys">${self
      ? `${it.mode === "listen" ? `<span class="kbd">R</span> 🔊 ` : ""}<span class="kbd">Space</span> ${t("revealBtn")} · <span class="kbd">1</span> ${t("btnKnow")} · <span class="kbd">2</span> ${t("btnDontKnow")}`
      : `<span class="kbd">Enter</span> ${t("btnCheck")} / ${t("btnNext")} · <span class="kbd">Ctrl</span>+<span class="kbd">Space</span> 🔊${shortcutHint}`}</div>
  </div>`;
  $("#quit").addEventListener("click", quitRound);
  if (self) {
    $("#reveal").addEventListener("click", reveal);
  } else {
    const input = $("#spell");
    bindSpecialKeys(input, lang);
    main.querySelectorAll("[data-ch]").forEach(b => b.addEventListener("click", () => insertAtCaret(input, b.dataset.ch)));
    $("#check").addEventListener("click", () => check(false));
    $("#giveup").addEventListener("click", () => check(true));
    input.focus();
  }
  if (it.mode === "listen" || it.mode === "spelling") sayWord(it);
  else if (it.mode === "cloze") playCloze(it);
}

function ctxHtml(it, blank) {
  const o = it.orig;
  if (!o) return "";
  const strTr = o.sentence_tr || o.sentence_zh || "";
  return `<div class="ctx"><div>${markForm(o.sentence, o.form, blank, it.language || "sv")}</div>${!blank && strTr ? `<div class="z">${esc(strTr)}</div>` : ""}</div>`;
}

function answerHtml(it) {
  const lang = it.language || "sv";
  const others = it.forms.filter(f => f !== it.lemma.toLowerCase());
  const meta = [esc(posName(it.pos || "")), others.length ? `${t("seenForms")}${others.map(esc).join(" · ")}` : ""].filter(Boolean).join(" · ");
  const src = `<div class="sv-big">${esc(it.lemma)}</div>
    <div class="tools" style="margin:0"><button class="small" data-act="say">🔊</button>${wordToolsHtml(lang)}</div>`;
  const gloss = `<div class="zh-big" style="font-size:24px">${esc(glossOf(it))}</div>`;
  let ctx;
  if (it.mode === "cloze") {
    const c = it.cloze;
    const strTr = c.sentence_tr || c.sentence_zh || "";
    ctx = `<div class="ctx"><div>${joinTokensHtml(c.tokens, lang, tk => esc(tk.pre) + (tk.blank ? `<mark class="hl">${esc(tk.text)}</mark>` : esc(tk.text)) + esc(tk.post))}</div>
      ${strTr ? `<div class="z">${esc(strTr)}</div>` : ""}</div>`;
  } else ctx = ctxHtml(it, false);
  const canSent = it.mode === "cloze" || it.orig?.sent_start != null;
  return `<div class="answer">
    ${it.mode === "listen" ? gloss + src : src + gloss}
    ${meta ? `<div class="pos">${meta}</div>` : ""}
    ${it.note ? `<div class="note">${esc(it.note)}</div>` : ""}
    ${ctx}
    <div class="tools" style="margin:8px 0 0">${canSent ? `<button class="small" data-act="sent">▶ ${t("btnListenSent")}</button>` : ""}
      <button class="small" data-act="senttts">${t("btnTtsSent")}</button></div>
  </div>`;
}

function reveal() {
  if (R.revealed) return;
  R.revealed = true;
  const it = cur();
  const first = !R.free && !(it.id in R.first);
  $("#ans").innerHTML = answerHtml(it);
  $("#judge").innerHTML = `<button class="bad-btn big" id="no">${t("btnDontKnow")}${first ? `<small>L${nextLevel(it.level, false).nl} · ${t("tomorrow")}</small>` : ""}</button>
    <button class="ok-btn big" id="yes">${t("btnKnow")}${first ? `<small>L${nextLevel(it.level, true).nl} · ${inDays(nextLevel(it.level, true).days)}</small>` : ""}</button>`;
  $("#yes").addEventListener("click", () => judge(true));
  $("#no").addEventListener("click", () => judge(false));
  if (it.mode === "recall") sayWord(it);
}

function judge(ok) {
  const it = R.queue.shift();
  record(it, ok ? "known" : "unknown");
  if (!ok) R.queue.push(it);
  showCard();
}

function target(it) { return it.mode === "cloze" ? it.cloze.answer : it.lemma; }

function check(giveUp) {
  if (R.revealed) { nextSpell(); return; }
  const it = cur();
  const lang = it.language || "sv";
  const input = $("#spell");
  const typed = input.value;
  if (!giveUp && !typed.trim()) { input.focus(); return; }
  const loose = langMeta(lang).hasLoose && lsGet("spell.loose", "0") === "1";
  const tgt = normSpell(target(it), false, lang);
  const exact = !giveUp && normSpell(typed, false, lang) === tgt;
  const ok = exact || (!giveUp && loose && normSpell(typed, true, lang) === normSpell(target(it), true, lang));
  R.revealed = true; R.lastOk = ok;
  input.readOnly = true;
  input.classList.add(ok ? "right" : "wrong");
  const first = !R.free && !(it.id in R.first);
  $("#ans").innerHTML = `<div id="verdict">${ok
    ? `<div class="verdict right">${t("verdictRight")}${exact ? "" : t("verdictLoose", { w: esc(target(it)) })}</div>`
    : `<div class="verdict wrong">${giveUp ? t("verdictAns") : t("verdictWrong")}</div>
       ${giveUp ? `<div class="diff">${esc(tgt)}</div>` : diffHtml(normSpell(typed, false, lang), tgt)}
       ${giveUp ? "" : `<button class="small typo" id="typo">${t("verdictTypo")}</button>`}
       <p class="muted" style="font-size:13px;margin:6px 0 0">${t("requeueHint")}</p>`}
    ${first ? `<div class="change">${changeText(it.level, ok)}</div>` : ""}</div>` + answerHtml(it);
  $("#typo")?.addEventListener("click", () => {
    R.lastOk = true;
    input.classList.replace("wrong", "right");
    $("#verdict").innerHTML = `<div class="verdict right">${t("verdictTypoOk")}</div>${first ? `<div class="change">${changeText(it.level, true)}</div>` : ""}`;
    $("#next").focus();
  });
  $("#judge").innerHTML = `<button class="primary big" id="next">${t("btnNext")}</button>`;
  $("#next").addEventListener("click", nextSpell);
  $("#check").disabled = true;
  $("#next").focus();
  sayWord(it);
}

function nextSpell() {
  const it = R.queue.shift();
  record(it, R.lastOk ? "correct" : "wrong");
  if (!R.lastOk) R.queue.push(it);
  showCard();
}

function quitRound() {
  const it = cur();
  if (R.revealed && (it.mode === "spelling" || it.mode === "cloze")) record(it, R.lastOk ? "correct" : "wrong");
  endRound();
}

async function endRound() {
  stopAll();
  const round = R; R = null;
  const done = round.items.filter(x => x.id in round.first);
  const bad = done.filter(x => !GOOD.has(round.first[x.id]));
  const good = done.length - bad.length;
  let more = null;
  if (!round.free) { try { more = await api("GET", `/api/review/summary?lang=${encodeURIComponent(FILTER_LANG)}`); } catch {} }
  const rows = [...bad, ...done.filter(x => GOOD.has(round.first[x.id]))].map(x => {
    const ok = GOOD.has(round.first[x.id]);
    const p = nextLevel(x.level, ok);
    return `<li><button class="ghost small" data-say="${esc(x.id)}">🔊</button>
      ${langBadgeHtml(x.language || "sv")}
      <span class="w">${esc(x.lemma)}</span><span class="z">${esc(glossOf(x))}</span>
      <span class="chg">${ok ? "✓" : "✗"}${round.free ? "" : ` · L${x.level}→L${p.nl} · ${inDays(p.days)}`}</span></li>`;
  }).join("");
  const left = more ? more.due : 0;
  main.innerHTML = `<div class="card intro">
    <h2>${t("roundDone")}</h2>
    ${done.length ? `<p>${t("firstPass")}</p>
      <div class="big-num">${good} <span class="muted" style="font-size:20px">/ ${done.length}</span></div>
      <ul class="wlist one">${rows}</ul>` : ""}
    <div class="actions">
      ${bad.length ? `<button id="again-bad">${t("againBad", { n: bad.length })}</button>` : ""}
      ${round.free ? `<button id="again">${t("againAll")}</button>` : ""}
      <span class="sp"></span>
      ${round.free ? `<button class="primary" id="back">${t("changeBatch")}</button>`
        : `<button class="primary" id="back">${left || (more && more.new_available) ? t("contReview") : t("backStart")}</button>`}
    </div></div>`;
  main.querySelectorAll("[data-say]").forEach(b => b.addEventListener("click", () => {
    const it = round.items.find(x => x.id === b.dataset.say);
    if (it) sayWord(it);
  }));
  $("#again-bad")?.addEventListener("click", () => startRound(bad, true));
  $("#again")?.addEventListener("click", () => startRound(round.items, true));
  $("#back").addEventListener("click", () => (round.free ? introFree : introDue)());
}

main.addEventListener("click", e => {
  const b = e.target.closest("[data-act]");
  if (!b || !R || !R.queue.length) return;
  const it = cur();
  const act = b.dataset.act;
  if (act === "say") sayWord(it);
  else if (act === "slow") sayWord(it, true);
  else if (act === "orig") { stopSpeaker(); origWord(it); }
  else if (act === "sent") origSent(it);
  else if (act === "senttts") ttsSent(it);
  else if (act === "cplay") playCloze(it);
  else if (act === "cslow") playCloze(it, true);
  else if (act === "hint-spell") b.outerHTML = `<span class="sv-big" style="font-size:24px">${esc(it.lemma)}</span>`;
  else if (act === "hint-ctx") b.outerHTML = ctxHtml(it, true);
  else if (act === "hint-zh") b.outerHTML = `<span class="badge gold" style="font-size:14px">${esc(glossOf(it))}</span>`;
  else if (act === "hint-first") {
    const w = [...normSpell(target(it), false, it.language || "sv")];
    b.outerHTML = `<span class="hintline">${esc(w[0] || "")}${"_".repeat(Math.max(0, w.length - 1))}</span>`;
  }
  const input = $("#spell");
  if (input && !input.readOnly) input.focus();
});

main.addEventListener("click", e => {
  const b = e.target.closest("button");
  if (b && R && R.queue.length && !$("#spell")) b.blur();
});

document.addEventListener("keydown", e => {
  if (!R || !R.queue.length || e.altKey || e.metaKey) return;
  const it = cur();
  if (it.mode === "spelling" || it.mode === "cloze") {
    if (e.key === "Enter" && !e.repeat) { e.preventDefault(); check(false); }
    else if (e.key === " " && e.ctrlKey) { e.preventDefault(); it.mode === "cloze" ? playCloze(it) : sayWord(it); }
    return;
  }
  if (e.ctrlKey || e.target.closest?.("input, textarea, select")) return;
  const k = e.key.toLowerCase();
  if (k === "r") sayWord(it);
  else if (k === "o" && wordVoice(it.language || "sv") !== "orig") { stopSpeaker(); origWord(it); }
  else if ((k === " " || k === "enter") && !R.revealed) { e.preventDefault(); reveal(); }
  else if (k === " " && R.revealed) { e.preventDefault(); sayWord(it); }
  else if (R.revealed && (k === "1" || k === "j" || k === "arrowright") && !e.repeat) judge(true);
  else if (R.revealed && (k === "2" || k === "f" || k === "arrowleft") && !e.repeat) judge(false);
});

window.addEventListener("pagehide", () => {
  if (!R || !R.queue.length || !R.revealed) return;
  const it = cur();
  if ((it.mode === "spelling" || it.mode === "cloze") && !(it.id in R.first)) {
    navigator.sendBeacon?.("/api/review/answer", new Blob([JSON.stringify({ id: it.id, mode: it.mode,
      result: R.lastOk ? "correct" : "wrong", free: R.free })], { type: "application/json" }));
  }
});

(TAB === "free" ? introFree : introDue)();
