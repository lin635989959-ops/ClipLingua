// 翻译对照页：多语言 AI Prompt 导出 + JSON 导入 + 双语对照高亮 + 生词表
"use strict";

const LESSON_ID = lessonIdFromPath();
const $ = sel => document.querySelector(sel);
const main = $("#main");
const audio = $("#audio");
const player = new RangePlayer(audio);

let D = null;
let lessonLang = "sv";
let nativeLang = uiLang();
let lastReport = null;
let llmCfg = { configured: false };
let autoRunning = false;

mountUiLangPicker();
applyStaticI18n();

window.addEventListener("cliplingua:llm-changed", e => {
  llmCfg = e.detail || { configured: false };
  if (D) render();
});

Promise.all([api("GET", `/api/lessons/${LESSON_ID}`), fetchLlmConfig()]).then(([d, cfg]) => {
  D = d;
  llmCfg = cfg || { configured: false };
  lessonLang = d.lesson.language || "sv";
  nativeLang = d.translation?.native_lang || d.lesson.native_lang || uiLang();
  document.title = `${d.lesson.title} · ${t("stage_translate")}`;
  $("#title").textContent = d.lesson.title;
  $("#langBadge").innerHTML = langBadgeHtml(lessonLang);
  audio.src = `/media/${LESSON_ID}/audio`;
  render();
}).catch(err => toast(err.message));

function sentTr(s) { return s?.text || s?.zh || ""; }
function wordTr(w) { return w?.text || w?.zh || ""; }
function wordInSent(w) { return w?.in_sentence || w?.zh_in_sentence || ""; }

function hasTranslation() {
  return !!(D.translation && (D.translation.sentences.length || D.translation.words.length));
}

function render() {
  renderLessonNav(LESSON_ID, "translate", D.progress);
  if (D.lesson.status !== "ready" || !D.transcript) {
    main.innerHTML = `<div class="card notice"><p>${esc(t("notReady"))}</p><a class="btn" href="/">${esc(t("backHome"))}</a></div>`;
    return;
  }
  const has = hasTranslation();
  const m = D.missing || { missing_sentences: [], missing_words: [], stale_sentences: [] };
  const gapN = m.missing_sentences.length + m.stale_sentences.length + m.missing_words.length;
  const exchange = exchangeHtml(has, m, gapN);
  main.innerHTML = has
    ? `${gapN ? `<div class="card panel report warn" style="margin-bottom:14px">${gapHtml(m)}</div>` : ""}
       ${pairsHtml()}${wordsHtml()}
       <details class="card panel redo" ${gapN || lastReport ? "open" : ""}>
         <summary>${gapN ? t("redoMissing", { n: gapN }) : t("redoAll")}</summary>${exchange}</details>
       <div class="card foot">
         <span class="muted">${t("footReady")}</span>
         <span style="flex:1"></span>
         <button class="primary" id="go">${t("goPractice1")}</button></div>`
    : exchange;
  bind();
}

function exchangeHtml(has, m, gapN) {
  const nV = D.vocab.words.filter(w => w.token_ids.length).length;
  const langOpts = ["zh", "en", "es", "sv"].map(c =>
    `<option value="${c}" ${c === nativeLang ? "selected" : ""}>${esc(langName(c))} (${LANG_META[c].native})</option>`
  ).join("");
  const autoBlock = llmCfg.configured
    ? `<div class="row" style="margin:8px 0 12px;padding:12px;border-radius:10px;background:var(--accent-soft)">
        <label class="voice-pick">${t("transLangLabel")} <select id="trLang">${langOpts}</select></label>
        <button class="primary" data-act="auto" data-only="0" ${autoRunning ? "disabled" : ""}>${autoRunning ? esc(t("autoTransRunning")) : esc(t("autoTransBtn", { s: D.transcript.sentences.length, w: nV }))}</button>
        ${has && gapN ? `<button data-act="auto" data-only="1" ${autoRunning ? "disabled" : ""}>${esc(t("autoTransMissing", { n: gapN }))}</button>` : ""}
        <span class="muted" style="font-size:12px">${esc(llmCfg.model || "")}</span>
      </div>`
    : `<div class="row" style="margin-bottom:10px">
        <label class="voice-pick">${t("transLangLabel")} <select id="trLang">${langOpts}</select></label>
        <span class="muted" style="font-size:13px">${esc(t("autoTransHint"))}</span>
        <button class="small" type="button" data-act="open-api">${esc(t("autoTransSetup"))}</button>
      </div>`;
  const manualBody = `<ol class="how">
      <li>${t("transHow1")}</li>
      <li>${t("transHow2")}</li>
      <li>${t("transHow3")}</li>
    </ol>
    <div class="row" style="margin-bottom:8px">
      <button class="${llmCfg.configured ? "" : "primary"}" data-act="copy" data-only="0">${t("copyAll", { s: D.transcript.sentences.length, w: nV })}</button>
      ${has && gapN ? `<button data-act="copy" data-only="1">${t("copyMissing", { n: gapN })}</button>` : ""}
      <span class="muted" style="font-size:13px" id="copied"></span>
    </div>
    <textarea id="task" readonly spellcheck="false"></textarea>
    <h3>${t("pasteTitle")}</h3>
    <textarea id="reply" spellcheck="false" placeholder="${esc(t("pastePlaceholder"))}"></textarea>
    <div class="row">
      <button class="${llmCfg.configured ? "" : "primary"}" data-act="import">${t("importBtn")}</button>
      <span class="muted" style="font-size:13px">${t("importHint")}</span>
    </div>`;
  return `<section class="${has ? "" : "card panel"}">
    ${has ? "" : `<h2>${t("transTitle")}</h2>`}
    ${autoBlock}
    ${llmCfg.configured
      ? `<details class="redo"><summary style="font-size:14px;font-weight:500" class="muted">${esc(t("manualFallback"))}</summary><div style="margin-top:10px">${manualBody}</div></details>`
      : manualBody}
    <div id="report">${lastReport ? reportHtml(lastReport) : ""}</div>
  </section>`;
}

function gapHtml(m) {
  const parts = [];
  if (m.missing_sentences.length) parts.push(t("gapMissingSent", { n: m.missing_sentences.length }));
  if (m.stale_sentences.length) parts.push(t("gapStaleSent", { n: m.stale_sentences.length }));
  if (m.missing_words.length) parts.push(t("gapMissingWord", { n: m.missing_words.length }));
  return t("gapPrefix", { items: parts.join(" · ") });
}

function reportHtml(r) {
  if (r.error) return `<div class="report err">${t("importErr", { e: esc(r.error) })}</div>`;
  const notes = [];
  if (r.suspects) notes.push(t("importSuspects", { n: r.suspects, id: LESSON_ID }));
  if (r.missing_sentences?.length) notes.push(t("gapMissingSent", { n: r.missing_sentences.length }));
  if (r.missing_words?.length) notes.push(t("gapMissingWord", { n: r.missing_words.length }));
  const warn = (r.missing_sentences?.length || r.missing_words?.length || r.unknown_words?.length);
  return `<div class="report ${warn ? "warn" : "ok"}">${t("importOk", { s: r.sentences, w: r.words })}
    ${notes.length ? `<ul>${notes.map(x => `<li>${x}</li>`).join("")}</ul>` : ""}</div>`;
}

async function copyTask(only) {
  try {
    const task = await api("GET", `/api/lessons/${LESSON_ID}/translation/task?native_lang=${encodeURIComponent(nativeLang)}${only ? "&only_missing=1" : ""}`);
    const text = JSON.stringify(task, null, 1);
    $("#task").value = text;
    await copyText(text);
    $("#copied").textContent = t("copiedMsg", { s: task.sentences.length, w: task.words.length });
  } catch (err) { toast(err.message); }
}

async function doImport() {
  const text = $("#reply").value;
  if (!text.trim()) { $("#reply").focus(); return; }
  try {
    const r = await api("POST", `/api/lessons/${LESSON_ID}/translation`, { text, native_lang: nativeLang });
    lastReport = r.report;
    D.translation = r.translation;
    D.missing = r.missing;
    if (D.progress.stage === "reading") D.progress.stage = "translate";
    render();
    toast(t("importOk", { s: r.report.sentences, w: r.report.words }));
    if (r.report.sentences || r.report.words) document.querySelector(".pairs")?.scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    $("#report").innerHTML = reportHtml({ error: err.message });
  }
}

function vocabKeys() { return new Set(D.vocab.words.map(w => w.key)); }

function trRanges(str, cands) {
  const ranges = [];
  const low = str.toLowerCase();
  for (const c of [...new Set(cands)].filter(Boolean).sort((a, b) => b.length - a.length)) {
    const cl = c.toLowerCase();
    let i = low.indexOf(cl);
    while (i >= 0) {
      const end = i + c.length;
      if (!ranges.some(r => i < r[1] && end > r[0])) ranges.push([i, end]);
      i = low.indexOf(cl, end);
    }
  }
  return ranges.sort((a, b) => a[0] - b[0]);
}

function highlightTr(str, s) {
  const keys = vocabKeys();
  const tw = new Map((D.translation?.words || []).map(w => [w.key, w]));
  const firstSid = new Map();
  for (const x of D.transcript.sentences) for (const tk of x.tokens) if (!firstSid.has(tk.key)) firstSid.set(tk.key, x.id);
  const cands = [];
  for (const k of new Set(s.tokens.map(tk => tk.key))) {
    const w = tw.get(k);
    if (!keys.has(k) || !w) continue;
    const frag = wordInSent(w);
    if (firstSid.get(k) === s.id && frag) cands.push(frag);
    const guess = [frag, ...wordTr(w).split(/[；;，,、/（）()…]+/)].map(x => x.trim()).filter(x => x.length >= 2);
    cands.push(...guess);
  }
  let out = "", pos = 0;
  for (const [a, b] of trRanges(str, cands)) {
    out += esc(str.slice(pos, a)) + `<mark class="hl">${esc(str.slice(a, b))}</mark>`;
    pos = b;
  }
  return out + esc(str.slice(pos));
}

function srcHtml(s) {
  const keys = vocabKeys();
  const tw = new Map((D.translation?.words || []).map(w => [w.key, w]));
  return joinTokensHtml(s.tokens, lessonLang, tk => {
    const word = keys.has(tk.key)
      ? `<mark class="hl" title="${esc(wordTr(tw.get(tk.key)) || t("noGloss"))}">${esc(tk.text)}</mark>`
      : esc(tk.text);
    return esc(tk.pre) + word + esc(tk.post);
  });
}

function pairsHtml() {
  const trMap = new Map((D.translation?.sentences || []).map(s => [s.id, s]));
  const stale = new Set(D.missing?.stale_sentences || []);
  const rows = D.transcript.sentences.map(s => {
    const z = trMap.get(s.id);
    const zText = sentTr(z);
    return `<div class="pair">
      <button class="play" data-play="${s.id}">▶ ${fmtDuration(s.start)}</button>
      <div class="src">${srcHtml(s)}</div>
      <div class="tr ${zText ? "" : "none"}">${zText ? highlightTr(zText, s) : esc(t("noTransYet"))}
        <button class="ghost edit" data-edit-s="${s.id}">✎</button>
        ${stale.has(s.id) ? `<div class="stale">${esc(t("staleTag"))}</div>` : ""}</div>
    </div>`;
  }).join("");
  return `<div class="card pairs">${rows}</div>`;
}

function wordsHtml() {
  const tw = new Map((D.translation?.words || []).map(w => [w.key, w]));
  const words = D.vocab.words.filter(w => w.token_ids.length);
  if (!words.length) return "";
  const v = wordVoice(lessonLang);
  const rows = words.map(w => {
    const tr = tw.get(w.key);
    const gloss = wordTr(tr);
    return `<tr>
      <td class="say"><button class="ghost small" data-say="${esc(w.token_ids[0])}">🔊</button>${v === "orig" ? "" :
        `<button class="ghost small" data-orig="${esc(w.token_ids[0])}">${t("origBtn")}</button>`}</td>
      <td class="w">${esc(w.text)}</td>
      <td class="lemma muted">${esc(tr?.lemma || "")}</td>
      <td class="muted">${esc(posName(tr?.pos || ""))}</td>
      <td>${gloss ? esc(gloss) : `<span class="miss">${esc(t("noGloss"))}</span>`}
        <button class="ghost small edit" data-edit-w="${esc(w.key)}">✎</button></td>
      <td class="note">${esc(tr?.note || "")}</td>
    </tr>`;
  }).join("");
  ttsPrefetch(words.map(w => w.text), lessonLang);
  return `<div class="card panel"><h2 style="display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px">${t("wordTableTitle")} <span class="muted" style="font-weight:400;font-size:13px">${t("wordTableCount", { n: words.length })}</span>
    <span style="flex:1"></span><span style="font-weight:400">${voicePickerHtml(lessonLang)}</span></h2>
    <table class="words"><thead><tr><th></th><th>${t("colWord")}</th><th class="lemma">${t("colLemma")}</th><th>${t("colPos")}</th><th>${t("colGloss")}</th><th class="note">${t("colNote")}</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

async function doAutoTranslate(only) {
  if (autoRunning) return;
  autoRunning = true;
  render();
  try {
    const r = await api("POST", `/api/lessons/${LESSON_ID}/translation/auto`, {
      only_missing: only,
      native_lang: nativeLang,
    });
    lastReport = r.report;
    D.translation = r.translation;
    D.missing = r.missing;
    if (D.progress.stage === "reading") D.progress.stage = "translate";
    autoRunning = false;
    render();
    toast(t("importOk", { s: r.report.sentences, w: r.report.words }));
    if (r.report.sentences || r.report.words) document.querySelector(".pairs")?.scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    autoRunning = false;
    lastReport = { error: err.message };
    render();
    toast(err.message);
  }
}

function bind() {
  bindVoicePicker(main, () => render());
  $("#trLang")?.addEventListener("change", e => { nativeLang = e.target.value; });
  main.querySelectorAll("[data-act=auto]").forEach(b => b.addEventListener("click", () => doAutoTranslate(b.dataset.only === "1")));
  main.querySelector("[data-act=open-api]")?.addEventListener("click", openApiSettingsModal);
  main.querySelectorAll("[data-act=copy]").forEach(b => b.addEventListener("click", () => copyTask(b.dataset.only === "1")));
  main.querySelector("[data-act=import]")?.addEventListener("click", doImport);
  $("#go")?.addEventListener("click", async () => {
    const m = D.missing;
    if (m.missing_words.length && !confirm(t("confirmMissingWords", { n: m.missing_words.length }))) return;
    try {
      const st = D.progress.stage;
      if (st === "reading" || st === "translate") {
        await api("POST", `/api/lessons/${LESSON_ID}/progress`, { action: "advance", stage: "translate" });
      }
      location.href = `/lesson/${LESSON_ID}/listen`;
    } catch (err) { toast(err.message); }
  });
}

const tokById = () => {
  const m = new Map();
  for (const s of D.transcript.sentences) for (const tk of s.tokens) m.set(tk.id, tk);
  return m;
};

main.addEventListener("click", async e => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.play) {
    const s = D.transcript.sentences.find(x => x.id === +b.dataset.play);
    if (s) { stopSpeaker(); player.playSentence(s); }
  } else if (b.dataset.say) {
    const tk = tokById().get(b.dataset.say);
    if (tk) speakWord(tk.text, { lang: lessonLang, orig: () => player.playToken(tk), stop: () => player.stop() });
  } else if (b.dataset.orig) {
    const tk = tokById().get(b.dataset.orig);
    if (tk) { stopSpeaker(); player.playToken(tk); }
  } else if (b.dataset.editS) {
    const sid = +b.dataset.editS;
    const cur = sentTr((D.translation?.sentences || []).find(s => s.id === sid));
    const src = D.transcript.sentences.find(s => s.id === sid)?.text || "";
    const txt = prompt(`${t("promptEditSent")}\n\n${src}`, cur);
    if (txt === null) return;
    saveEntry("sentence", sid, { text: txt });
  } else if (b.dataset.editW) {
    const key = b.dataset.editW;
    const cur = wordTr((D.translation?.words || []).find(w => w.key === key));
    const txt = prompt(t("promptEditWord", { w: key }), cur);
    if (txt === null) return;
    saveEntry("word", key, { text: txt });
  }
});

async function saveEntry(kind, id, fields) {
  try {
    const r = await api("POST", `/api/lessons/${LESSON_ID}/translation/entry`, { kind, id, fields });
    D.translation = r.translation;
    D.missing = r.missing;
    render();
  } catch (err) { toast(err.message); }
}
