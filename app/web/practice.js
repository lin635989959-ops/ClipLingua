// 练习 1（听音释义）/ 练习 2（看义说词）/ 练习 3（拼写）— 多语言卡片队列引擎
"use strict";

const LESSON_ID = lessonIdFromPath();
const VIEW = location.pathname.split("/")[3];
const $ = sel => document.querySelector(sel);
const main = $("#main");
const audio = $("#audio");
const player = new RangePlayer(audio);

mountUiLangPicker();
applyStaticI18n();

const CFG = {
  listen: {
    name: t("stage_listen"), good: "known",
    desc: t("pracListenDesc"),
    next: "recall", nextLabel: t("nextToRecall"),
  },
  recall: {
    name: t("stage_recall"), good: "known",
    desc: t("pracRecallDesc"),
    next: "spelling", nextLabel: t("nextToSpell"),
  },
  spelling: {
    name: t("stage_spelling"), good: "correct",
    desc: t("pracSpellDesc"),
    next: "read_aloud", nextLabel: t("nextToRead"),
  },
}[VIEW];

let D = null;
let lessonLang = "sv";
let items = [];
let excluded = 0;
let R = null;

function glossOf(tr) { return tr?.text || tr?.zh || ""; }
function inSentOf(tr) { return tr?.in_sentence || tr?.zh_in_sentence || ""; }

api("GET", `/api/lessons/${LESSON_ID}`).then(d => {
  D = d;
  lessonLang = d.lesson.language || "sv";
  document.title = `${d.lesson.title} · ${CFG.name}`;
  $("#title").textContent = d.lesson.title;
  $("#langBadge").innerHTML = langBadgeHtml(lessonLang);
  audio.src = `/media/${LESSON_ID}/audio`;
  renderLessonNav(LESSON_ID, VIEW, d.progress);
  buildItems();
  intro();
}).catch(err => toast(err.message));

function buildItems() {
  const tok = new Map(), sentOf = new Map();
  for (const s of D.transcript?.sentences || []) for (const tk of s.tokens) { tok.set(tk.id, tk); sentOf.set(tk.id, s); }
  const tw = new Map((D.translation?.words || []).map(w => [w.key, w]));
  const ts = new Map((D.translation?.sentences || []).map(s => [s.id, s.text || s.zh || ""]));
  const all = D.vocab.words.filter(w => w.token_ids.length && tok.has(w.token_ids[0])).map(w => {
    const tk = tok.get(w.token_ids[0]), s = sentOf.get(w.token_ids[0]);
    const tr = tw.get(w.key) || null;
    return { key: w.key, text: w.text, tok: tk, sent: s, tr: tr && glossOf(tr) ? tr : null, sentTr: ts.get(s.id) || "" };
  });
  const needTr = VIEW === "recall" || (VIEW === "spelling" && spellMode() === "zh");
  items = needTr ? all.filter(x => x.tr) : all;
  excluded = all.length - items.length;
}

function spellMode() { return lsGet("spell.mode", "listen"); }
function stageState() { return D.progress.stages[VIEW] || {}; }
function lastRound() { const r = stageState().rounds || []; return r[r.length - 1] || null; }

function intro() {
  if (D.lesson.status !== "ready" || !D.transcript) {
    main.innerHTML = `<div class="card notice"><p>${esc(t("notReady"))}</p><a class="btn" href="/">${esc(t("backHome"))}</a></div>`;
    return;
  }
  if (!D.vocab.words.length) {
    main.innerHTML = `<div class="card notice"><p>${esc(t("noVocabNotice"))}</p>
      <a class="btn" href="/lesson/${LESSON_ID}">${esc(t("goPickVocab"))}</a></div>`;
    return;
  }
  const noTr = !D.translation || !D.translation.words.length;
  if (noTr && VIEW !== "spelling") {
    main.innerHTML = `<div class="card notice"><p>${esc(t("noTransNotice"))}</p>
      <a class="btn primary" href="/lesson/${LESSON_ID}/translate">${esc(t("goTrans"))}</a></div>`;
    return;
  }
  const last = lastRound();
  const unknownLast = last ? Object.entries(last.results).filter(([, v]) => v !== CFG.good).map(([k]) => k) : [];
  const retry = items.filter(x => unknownLast.includes(x.key));
  const st = stageState();
  const hasLoose = langMeta(lessonLang).hasLoose;

  main.innerHTML = `<div class="card intro">
    <h2>${esc(CFG.name)}</h2>
    <p>${CFG.desc}</p>
    ${last ? `<div class="last">${t("lastRoundInfo", { d: fmtDate(last.finished_at), g: countGood(last), t: Object.keys(last.results).length, r: (st.rounds || []).length })}</div>` : ""}
    <div class="opts">
      ${voicePickerHtml(lessonLang)}
      <label><input type="checkbox" id="opt-shuffle" ${lsGet("prac.shuffle", "1") === "1" ? "checked" : ""}> ${t("optShuffle")}</label>
      ${VIEW === "spelling" ? `
        <label><input type="radio" name="sm" value="listen" ${spellMode() === "listen" ? "checked" : ""}> ${t("optSpellListen")}</label>
        <label><input type="radio" name="sm" value="zh" ${spellMode() === "zh" ? "checked" : ""}> ${t("optSpellTr")}</label>
        <label><input type="radio" name="sm" value="both" ${spellMode() === "both" ? "checked" : ""}> ${t("optSpellBoth")}</label>
        ${hasLoose ? `<label><input type="checkbox" id="opt-loose" ${lsGet("spell.loose", "0") === "1" ? "checked" : ""}> ${t("optLoose")}</label>` : ""}` : ""}
    </div>
    <div class="actions">
      <button class="primary big" id="start" ${items.length ? "" : "disabled"}>${t("startBtn", { n: items.length })}</button>
      ${retry.length && retry.length < items.length ? `<button id="start-retry">${t("startRetry", { n: retry.length })}</button>` : ""}
      <span class="sp"></span>
      ${VIEW === "spelling" ? `<button class="ghost" id="skip-stage">${t("skipSpell")}</button>` : ""}
      ${VIEW === "recall" && (st.rounds || []).length ? `<button id="pass">${t("passNow")}</button>` : ""}
      ${VIEW === "listen" && (st.rounds || []).length ? `<button id="pass">${CFG.nextLabel}</button>` : ""}
    </div></div>`;
  $("#opt-shuffle").addEventListener("change", e => lsSet("prac.shuffle", e.target.checked ? "1" : "0"));
  bindVoicePicker(main);
  $("#opt-loose")?.addEventListener("change", e => lsSet("spell.loose", e.target.checked ? "1" : "0"));
  main.querySelectorAll("input[name=sm]").forEach(r => r.addEventListener("change", () => {
    lsSet("spell.mode", r.value); buildItems(); intro();
  }));
  $("#start").addEventListener("click", () => startRound(items));
  $("#start-retry")?.addEventListener("click", () => startRound(retry));
  $("#skip-stage")?.addEventListener("click", skipStage);
  $("#pass")?.addEventListener("click", () => finishStage());
}

function countGood(rnd) { return Object.values(rnd.results).filter(v => v === CFG.good).length; }

function startRound(list) {
  const order = lsGet("prac.shuffle", "1") === "1" ? shuffle(list) : list.slice();
  R = {
    list, queue: order, first: {}, tries: {}, started_at: new Date().toISOString(),
    mode: VIEW === "spelling" ? spellMode() + (lsGet("spell.loose", "0") === "1" ? "+loose" : "") : undefined,
    revealed: false, n: 0,
  };
  ttsPrefetch(order.map(x => x.text), lessonLang);
  showCard();
}

function cur() { return R.queue[0]; }

function headerHtml() {
  const done = R.list.length - new Set(R.queue.map(x => x.key)).size;
  return `<div class="bar-row">
    <span>${t("passedProg", { d: done, t: R.list.length })}</span>
    <div class="bar"><i style="width:${100 * done / R.list.length}%"></i></div>
    <button class="ghost small" id="quit">${t("quitRound")}</button>
    ${VIEW === "recall" ? `<button class="small" id="pass-now">${t("passNow")}</button>` : ""}
    ${VIEW === "spelling" ? `<button class="ghost small" id="skip-now">${t("skipSpell")}</button>` : ""}
  </div>`;
}

function bindHeader() {
  $("#quit").addEventListener("click", async () => { await saveRound(true); intro(); });
  $("#pass-now")?.addEventListener("click", async () => { await saveRound(true); finishStage(); });
  $("#skip-now")?.addEventListener("click", async () => { await saveRound(true); skipStage(); });
}

function showCard() {
  if (!R.queue.length) { endRound(); return; }
  R.revealed = false;
  R.n++;
  if (VIEW === "spelling") return showSpellCard();
  const it = cur();
  main.innerHTML = headerHtml() + `<div class="card q">
    <div class="cue">${VIEW === "listen" ? listenCue(it) : recallCue(it)}</div>
    <div id="ans"></div>
    <div class="judge" id="judge">
      <button class="primary big" id="reveal">${t("revealBtn")}</button>
    </div>
    <div class="keys">${VIEW === "listen" ? `<span class="kbd">R</span> 🔊 ` : ""}<span class="kbd">Space</span> ${t("revealBtn")} · <span class="kbd">1</span> ${t("btnKnow")} · <span class="kbd">2</span> ${t("btnDontKnow")}</div>
  </div>`;
  bindHeader();
  $("#reveal").addEventListener("click", reveal);
  main.querySelector("[data-act=say]")?.addEventListener("click", () => sayWord(it));
  main.querySelector("[data-act=sent]")?.addEventListener("click", () => saySent(it));
  main.querySelector("[data-act=hint]")?.addEventListener("click", e => {
    e.target.closest("button").outerHTML = VIEW === "listen"
      ? `<span class="sv-big" style="font-size:24px">${esc(it.text)}</span>` : contextHtml(it, true);
  });
  if (VIEW === "listen") sayWord(it);
}

function wordToolsHtml() {
  return wordVoice(lessonLang) === "orig" ? "" : `<button class="small" data-act="slow">${t("btnSlow")}</button>
    <button class="small" data-act="orig">${t("origBtn")}</button>`;
}

function listenCue(it) {
  return `<button class="speaker" data-act="say">🔊</button>
    <div class="tools">
      ${wordToolsHtml()}
      <button class="small" data-act="sent">${t("btnListenSent")}</button>
      <button class="small ghost" data-act="hint">${t("hintSpell")}</button>
    </div>
    <p class="prompt">${t("promptListen")}</p>`;
}

function recallCue(it) {
  return `<div class="zh-big">${esc(glossOf(it.tr))}</div>
    <div class="pos">${esc(posName(it.tr.pos || ""))}</div>
    <div class="tools"><button class="small ghost" data-act="hint">${t("hintCtx")}</button></div>
    <p class="prompt">${t("promptRecall", { lang: langName(lessonLang) })}</p>`;
}

function contextHtml(it, blank) {
  const src = joinTokensHtml(it.sent.tokens, lessonLang, tk => {
    const w = tk.key === it.key
      ? (blank ? `<span class="blank">${"＿".repeat(Math.min(6, Math.max(2, tk.text.length)))}</span>` : `<mark class="hl">${esc(tk.text)}</mark>`)
      : esc(tk.text);
    return esc(tk.pre) + w + esc(tk.post);
  });
  let trStr = esc(it.sentTr);
  const frag = inSentOf(it.tr);
  if (frag && it.sentTr.includes(frag)) trStr = esc(it.sentTr).replace(esc(frag), `<mark class="hl">${esc(frag)}</mark>`);
  return `<div class="ctx"><div>${src}</div>${it.sentTr ? `<div class="z">${trStr}</div>` : ""}</div>`;
}

function answerHtml(it) {
  const tr = it.tr;
  const meta = [tr?.lemma && tr.lemma.toLowerCase() !== it.key ? `${t("colLemma")} <b>${esc(tr.lemma)}</b>` : "", esc(posName(tr?.pos || ""))].filter(Boolean).join(" · ");
  return `<div class="answer">
    ${VIEW === "listen"
      ? `<div class="zh-big">${tr ? esc(glossOf(tr)) : `<span class="muted" style="font-size:16px">${t("noGloss")}</span>`}</div>
         <div class="sv-big" style="font-size:22px">${esc(it.text)}</div>`
      : `<div class="sv-big">${esc(it.text)}</div>
         <div class="tools" style="margin:0"><button class="small" data-act="say2">🔊</button>${wordToolsHtml()}</div>`}
    ${meta ? `<div class="pos">${meta}</div>` : ""}
    ${tr?.note ? `<div class="note">${esc(tr.note)}</div>` : ""}
    ${contextHtml(it, false)}
    <div class="tools" style="margin:8px 0 0"><button class="small" data-act="sent2">▶ ${t("btnListenSent")}</button>
      <button class="small" data-act="senttts">${t("btnTtsSent")}</button></div>
  </div>`;
}

function reveal() {
  if (R.revealed) return;
  R.revealed = true;
  const it = cur();
  $("#ans").innerHTML = answerHtml(it);
  $("#judge").innerHTML = `<button class="bad-btn big" id="no">${t("btnDontKnow")}</button><button class="ok-btn big" id="yes">${t("btnKnow")}</button>`;
  $("#yes").addEventListener("click", () => judge(true));
  $("#no").addEventListener("click", () => judge(false));
  main.querySelector("[data-act=say2]")?.addEventListener("click", () => sayWord(it));
  main.querySelector("[data-act=sent2]")?.addEventListener("click", () => saySent(it));
  if (VIEW === "recall") sayWord(it);
}

function judge(ok) {
  const it = R.queue.shift();
  R.tries[it.key] = (R.tries[it.key] || 0) + 1;
  if (!(it.key in R.first)) R.first[it.key] = ok ? "known" : "unknown";
  if (!ok) R.queue.push(it);
  showCard();
}

function sayWord(it, slow = false) {
  const btn = main.querySelector(".speaker");
  btn?.classList.add("playing");
  Promise.resolve(speakWord(it.text, { lang: lessonLang, orig: () => player.playToken(it.tok), stop: () => player.stop(), slow }))
    .finally(() => btn?.classList.remove("playing"));
}
function sayOrig(it) { stopSpeaker(); player.playToken(it.tok); }
function saySent(it) { stopSpeaker(); player.playSentence(it.sent); }
function saySentTts(it) {
  player.stop();
  const v = wordVoice(lessonLang);
  SPEAKER.say(it.sent.text, { lang: lessonLang, voice: v === "orig" ? langMeta(lessonLang).defaultVoice : v });
}

main.addEventListener("click", e => {
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (!R || !R.queue.length || !["slow", "orig", "senttts"].includes(act)) return;
  const it = cur();
  if (act === "slow") sayWord(it, true);
  else if (act === "orig") sayOrig(it);
  else saySentTts(it);
  const input = $("#spell");
  if (input && !input.readOnly) input.focus();
});

function showSpellCard() {
  const it = cur();
  const mode = spellMode();
  const showTr = (mode === "zh" || mode === "both") && it.tr;
  const listen = mode !== "zh" || !it.tr;
  const shortcutHint = langMeta(lessonLang).hasSvShortcut
    ? ` · <span class="kbd">[</span> å <span class="kbd">'</span> ä <span class="kbd">;</span> ö` : "";
  main.innerHTML = headerHtml() + `<div class="card q">
    <div class="cue">
      ${listen ? `<button class="speaker" data-act="say">🔊</button>` : ""}
      ${showTr ? `<div class="zh-big">${esc(glossOf(it.tr))}</div><div class="pos">${esc(posName(it.tr.pos || ""))}</div>` : ""}
      <div class="tools">
        ${listen ? wordToolsHtml() : ""}
        ${listen ? `<button class="small" data-act="sent">${t("btnListenSent")}</button>` : ""}
        <button class="small ghost" data-act="hint">${t("hintFirst")}</button>
      </div>
    </div>
    <div class="spell">
      <input type="text" id="spell" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="${esc(t("spellPlaceholder"))}">
      <button class="primary" id="check">${t("btnCheck")}</button>
    </div>
    ${specialLettersHtml(lessonLang)}
    <div id="ans"></div>
    <div class="judge" id="judge">
      <button class="ghost" id="giveup">${t("btnGiveUp")}</button>
      <button class="ghost" id="skipword">${t("btnSkipWord")}</button>
    </div>
    <div class="keys"><span class="kbd">Enter</span> ${t("btnCheck")} / ${t("btnNext")} · <span class="kbd">Ctrl</span>+<span class="kbd">Space</span> 🔊${shortcutHint}</div>
  </div>`;
  bindHeader();
  const input = $("#spell");
  input.focus();
  main.querySelector("[data-act=say]")?.addEventListener("click", () => { sayWord(it); input.focus(); });
  main.querySelector("[data-act=sent]")?.addEventListener("click", () => { saySent(it); input.focus(); });
  main.querySelector("[data-act=hint]").addEventListener("click", e => {
    const w = [...normSpell(it.key, false, lessonLang)];
    e.target.outerHTML = `<span class="hintline">${esc(w[0] || "")}${"_".repeat(Math.max(0, w.length - 1))}</span>`;
    input.focus();
  });
  main.querySelectorAll("[data-ch]").forEach(b => b.addEventListener("click", () => insertAtCaret(input, b.dataset.ch)));
  $("#check").addEventListener("click", () => checkSpell(false));
  $("#giveup").addEventListener("click", () => checkSpell(true));
  $("#skipword").addEventListener("click", skipWord);
  bindSpecialKeys(input, lessonLang);
  if (listen) sayWord(it);
}

function checkSpell(giveUp) {
  if (R.revealed) { nextSpell(); return; }
  const it = cur();
  const input = $("#spell");
  const typed = input.value;
  if (!giveUp && !typed.trim()) { input.focus(); return; }
  const loose = langMeta(lessonLang).hasLoose && lsGet("spell.loose", "0") === "1";
  const target = normSpell(it.key, false, lessonLang);
  const exact = !giveUp && normSpell(typed, false, lessonLang) === target;
  const ok = exact || (!giveUp && loose && normSpell(typed, true, lessonLang) === normSpell(it.key, true, lessonLang));
  R.revealed = true;
  R.lastOk = ok;
  R.tries[it.key] = (R.tries[it.key] || 0) + 1;
  if (!(it.key in R.first)) R.first[it.key] = ok ? "correct" : "wrong";
  input.readOnly = true;
  input.classList.add(ok ? "right" : "wrong");
  $("#ans").innerHTML = (ok
    ? `<div class="verdict right">${t("verdictRight")}${exact ? "" : t("verdictLoose", { w: esc(it.text) })}</div>`
    : `<div class="verdict wrong">${giveUp ? t("verdictAns") : t("verdictWrong")}</div>
       ${giveUp ? `<div class="diff">${esc(target)}</div>` : diffHtml(normSpell(typed, false, lessonLang), target)}
       <p class="muted" style="font-size:13px;margin:6px 0 0">${t("requeueHint")}</p>`) + answerHtml(it);
  $("#judge").innerHTML = `<button class="primary big" id="next">${t("btnNext")}</button>`;
  $("#next").addEventListener("click", nextSpell);
  main.querySelector("[data-act=say2]")?.addEventListener("click", () => sayWord(it));
  main.querySelector("[data-act=sent2]")?.addEventListener("click", () => saySent(it));
  $("#check").disabled = true;
  $("#next").focus();
  sayWord(it);
}

function nextSpell() {
  const it = R.queue.shift();
  if (!R.lastOk) R.queue.push(it);
  showCard();
}

function skipWord() {
  if (R.revealed) return;
  const it = R.queue.shift();
  if (!(it.key in R.first)) R.first[it.key] = "skipped";
  showCard();
}

async function saveRound(partial) {
  if (!R || !Object.keys(R.first).length) { R = null; return; }
  const rnd = { started_at: R.started_at, results: R.first, tries: R.tries, mode: R.mode, partial };
  R = null;
  try {
    const r = await api("POST", `/api/lessons/${LESSON_ID}/progress`, { action: "round", stage: VIEW, round: rnd });
    D.progress = r.progress;
    renderLessonNav(LESSON_ID, VIEW, D.progress);
  } catch (err) { toast(err.message); }
}

async function endRound() {
  const list = R.list;
  const first = { ...R.first };
  await saveRound(false);
  const bad = list.filter(x => first[x.key] && first[x.key] !== CFG.good);
  const good = list.length - bad.length;
  main.innerHTML = `<div class="card intro">
    <h2>${t("roundDone")}</h2>
    <p>${t("firstPass")}</p>
    <div class="big-num">${good} <span class="muted" style="font-size:20px">/ ${list.length}</span></div>
    ${bad.length ? `<h3 style="margin:16px 0 0;font-size:15px">${t("needReviewWords")}</h3>
      <ul class="wlist">${bad.map(x => `<li><button class="ghost small" data-say="${esc(x.key)}">🔊</button>
        <span class="w">${esc(x.text)}</span><span class="z">${esc(glossOf(x.tr))}</span></li>`).join("")}</ul>` : `<p>${t("allGood")}</p>`}
    <div class="actions">
      <button id="again">${t("againAll")}</button>
      ${bad.length ? `<button id="again-bad">${t("againBad", { n: bad.length })}</button>` : ""}
      <span class="sp"></span>
      <button class="primary" id="next-stage">${CFG.nextLabel}</button>
    </div></div>`;
  main.querySelectorAll("[data-say]").forEach(b => b.addEventListener("click", () => {
    const it = list.find(x => x.key === b.dataset.say);
    if (it) sayWord(it);
  }));
  $("#again").addEventListener("click", () => startRound(items));
  $("#again-bad")?.addEventListener("click", () => startRound(bad));
  $("#next-stage").addEventListener("click", () => finishStage());
}

async function finishStage() {
  try {
    await api("POST", `/api/lessons/${LESSON_ID}/progress`, { action: "advance", stage: VIEW });
    location.href = `/lesson/${LESSON_ID}/${CFG.next}`;
  } catch (err) { toast(err.message); }
}

async function skipStage() {
  try {
    await api("POST", `/api/lessons/${LESSON_ID}/progress`, { action: "skip", stage: "spelling" });
    location.href = `/lesson/${LESSON_ID}/read_aloud`;
  } catch (err) { toast(err.message); }
}

main.addEventListener("click", e => {
  const b = e.target.closest("button");
  if (b && R && VIEW !== "spelling") b.blur();
});

document.addEventListener("keydown", e => {
  if (!R || e.altKey || e.metaKey) return;
  if (VIEW === "spelling") {
    if (e.key === "Enter" && !e.repeat) { e.preventDefault(); if (R.revealed) nextSpell(); else checkSpell(false); }
    else if (e.key === " " && e.ctrlKey) { e.preventDefault(); sayWord(cur()); }
    return;
  }
  if (e.ctrlKey || e.target.closest?.("input, textarea")) return;
  const k = e.key.toLowerCase();
  if (k === "r") sayWord(cur());
  else if (k === "o" && wordVoice(lessonLang) !== "orig") sayOrig(cur());
  else if ((k === " " || k === "enter") && !R.revealed) { e.preventDefault(); reveal(); }
  else if (k === " " && R.revealed) { e.preventDefault(); sayWord(cur()); }
  else if (R.revealed && (k === "1" || k === "j" || k === "arrowright") && !e.repeat) judge(true);
  else if (R.revealed && (k === "2" || k === "f" || k === "arrowleft") && !e.repeat) judge(false);
});
