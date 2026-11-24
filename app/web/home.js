// 首页：多语言课程列表 + 本地媒体导入 / 公开链接导入 + 复习入口
"use strict";

const $ = id => document.getElementById(id);
let pollTimer = null;
let lastState = { lessons: [], jobs: [] };
let filterLang = lsGet("home.filterLang", "all");

mountUiLangPicker();
applyStaticI18n();
document.title = `${t("appName")} · ${t("appSubtitle")}`;

// 初始化学习语言与译文语言选择器
function initLangSelectors() {
  const langs = ["sv", "en", "es", "zh"];
  const savedTarget = lsGet("home.targetLang", uiLang() === "sv" ? "en" : "sv");
  const savedNative = lsGet("home.nativeLang", uiLang());
  const opts = langs.map(c => `<option value="${c}">${esc(langName(c))} (${LANG_META[c].native})</option>`).join("");
  $("targetLang").innerHTML = opts;
  $("nativeLang").innerHTML = opts;
  $("targetLang").value = langs.includes(savedTarget) ? savedTarget : "sv";
  $("nativeLang").value = langs.includes(savedNative) ? savedNative : uiLang();
  $("targetLang").addEventListener("change", () => lsSet("home.targetLang", $("targetLang").value));
  $("nativeLang").addEventListener("change", () => lsSet("home.nativeLang", $("nativeLang").value));
}
initLangSelectors();

function renderLangFilter() {
  const langs = ["all", "sv", "en", "es", "zh"];
  $("langFilter").innerHTML = langs.map(c =>
    `<button class="small ${filterLang === c ? "on" : ""}" data-flang="${c}">${c === "all" ? esc(t("allLangs")) : esc(langName(c))}</button>`
  ).join("");
}
renderLangFilter();
$("langFilter").addEventListener("click", e => {
  const b = e.target.closest("[data-flang]");
  if (!b) return;
  filterLang = b.dataset.flang;
  lsSet("home.filterLang", filterLang);
  renderLangFilter();
  render(lastState);
});

// ---------- 新建课程 ----------

async function uploadFile(file) {
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  fd.append("language", $("targetLang").value);
  fd.append("native_lang", $("nativeLang").value);
  toast(t("toastUploading", { name: file.name }), 60000);
  try {
    await api("POST", "/api/lessons/file", fd);
    toast(t("toastFileStarted"));
    refresh();
  } catch (err) {
    toast(err.message);
  }
}

$("fileInput").addEventListener("change", e => {
  uploadFile(e.target.files[0]);
  e.target.value = "";
});

const dz = $("dropzone");
dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("drop"); });
dz.addEventListener("dragleave", () => dz.classList.remove("drop"));
dz.addEventListener("drop", e => {
  e.preventDefault();
  dz.classList.remove("drop");
  uploadFile(e.dataTransfer.files[0]);
});

$("urlForm").addEventListener("submit", async e => {
  e.preventDefault();
  const text = $("urlInput").value.trim();
  if (!text) { $("urlInput").focus(); return; }
  $("urlBtn").disabled = true;
  try {
    await api("POST", "/api/lessons/url", {
      text,
      language: $("targetLang").value,
      native_lang: $("nativeLang").value,
    });
    $("urlInput").value = "";
    toast(t("toastUrlStarted"));
    refresh();
  } catch (err) {
    toast(err.message);
  } finally {
    $("urlBtn").disabled = false;
  }
});

// ---------- 课程列表 ----------

function statusHtml(l, job) {
  if (job && (job.state === "queued" || job.state === "running")) {
    return `<div class="status">
      <span class="badge busy">${job.state === "queued" ? t("queued") : t("processing")}</span>
      <div class="bar"><i style="width:${job.progress}%"></i></div>
      <span class="msg">${esc(job.message)} ${Math.round(job.progress)}%</span></div>`;
  }
  switch (l.status) {
    case "ready": {
      const stage = l.stage || "reading";
      const cls = stage === "done" ? "ok" : stage === "reading" ? "" : "gold";
      return `<div class="status">
        <span class="badge ${cls}">${esc(stageName(stage))}</span>
        ${l.n_vocab ? `<span class="msg">${t("vocabCount", { n: l.n_vocab })}</span>` : ""}
        ${l.has_translation ? `<span class="msg">${t("hasTrans")}</span>` : ""}
        ${recordLine(l.record)}</div>`;
    }
    case "downloaded":
      return `<div class="status"><span class="badge">${t("downloadedOnly")}</span>
        <button class="small" data-act="retry">${t("startTranscribe")}</button></div>`;
    case "error":
      return `<div class="status"><span class="badge bad">${t("errorBadge")}</span>
        <button class="small" data-act="retry">${t("retry")}</button>
        <button class="small ghost danger" data-act="delete">${t("del")}</button>
        <span class="err">${esc(l.error || "")}</span></div>`;
    default:
      return `<div class="status"><span class="badge busy">${t("processing")}</span>
        <span class="msg">${t("waiting")}</span></div>`;
  }
}

function recordLine(r) {
  if (!r) return "";
  const parts = [];
  const pr = (name, x) => {
    if (x.skipped) parts.push(`${name} ${t("recSkip")}`);
    else if (x.last) parts.push(`${name} ${x.last.good}/${x.last.total}${x.rounds > 1 ? t("recRounds", { n: x.rounds }) : ""}`);
  };
  pr(t("recListen"), r.listen);
  pr(t("recRecall"), r.recall);
  pr(t("recSpell"), r.spelling);
  if (r.read_aloud.recordings) parts.push(t("recAudio", { a: r.read_aloud.sentences_recorded, b: r.read_aloud.sentences_total }));
  if (r.completed_at) parts.push(t("recDoneOn", { d: fmtDate(r.completed_at).slice(0, 10) }));
  return parts.length ? `<span class="rec">📊 ${parts.join(" · ")}</span>` : "";
}

function lessonHtml(l, job) {
  // 课程转写就绪后即可进入学习，后台字幕校对（subs）不阻塞点击进入
  const ready = l.status === "ready" && !(job && job.kind !== "subs" && job.state !== "error");
  const meta = [
    fmtDate(l.created_at),
    l.duration ? t("duration", { t: fmtDuration(l.duration) }) : "",
    l.author ? `@${esc(l.author)}` : "",
    l.n_sentences ? t("sentWordCount", { s: l.n_sentences, w: l.n_words }) : "",
  ].filter(Boolean).map(s => `<span>${s}</span>`).join("");
  const thumb = l.has_video && l.status !== "downloading"
    ? `<img src="/media/${l.id}/thumb.jpg" alt="" loading="lazy" onerror="this.replaceWith('🎬')">`
    : (l.has_video ? "🎬" : "🎧");
  return `<article class="card lesson ${ready ? "clickable" : ""}" data-id="${l.id}">
    <div class="thumb">${thumb}</div>
    <div class="info">
      <div class="title-row">
        ${langBadgeHtml(l.language || "sv")}
        <span class="title">${esc(l.title || t("untitled"))}</span>
        <button class="ghost rename" data-act="rename" title="${esc(t("renameTitle"))}">✎</button>
      </div>
      <div class="meta">${meta}</div>
      ${l.preview ? `<div class="preview">${esc(l.preview)}</div>` : ""}
      ${statusHtml(l, job)}
    </div>
  </article>`;
}

function pendingJobHtml(j) {
  if (j.state === "error") {
    return `<article class="card lesson" data-job="${j.id}">
      <div class="thumb">⚠️</div>
      <div class="info">
        <div class="title-row">${langBadgeHtml(j.language || "sv")}<span class="title">${esc(j.label)}</span></div>
        <div class="status"><span class="badge bad">${t("failed")}</span>
          <button class="small ghost" data-act="dismiss">${t("gotIt")}</button>
          <span class="err">${esc(j.error)}</span></div>
      </div></article>`;
  }
  return `<article class="card lesson" data-job="${j.id}">
    <div class="thumb">⏳</div>
    <div class="info">
      <div class="title-row">${langBadgeHtml(j.language || "sv")}<span class="title">${esc(j.label)}</span></div>
      <div class="status"><span class="badge busy">${j.state === "queued" ? t("queued") : t("processing")}</span>
        <div class="bar"><i style="width:${j.progress}%"></i></div>
        <span class="msg">${esc(j.message)}</span></div>
    </div></article>`;
}

function render(state) {
  lastState = state;
  const jobByLesson = {};
  const pending = [];
  for (const j of state.jobs) {
    if (j.lesson_id && state.lessons.some(l => l.id === j.lesson_id)) {
      const cur = jobByLesson[j.lesson_id];
      if (!cur || j.state !== "error") jobByLesson[j.lesson_id] = j;
    } else {
      pending.push(j);
    }
  }
  const filtered = filterLang === "all"
    ? state.lessons
    : state.lessons.filter(l => (l.language || "sv") === filterLang);
  const html = pending.map(pendingJobHtml).join("") +
    filtered.map(l => lessonHtml(l, jobByLesson[l.id])).join("");
  $("lessons").innerHTML = html || `<div class="card empty">${esc(t("emptyLessons"))}</div>`;
  $("count").textContent = filtered.length ? t("lessonCount", { n: filtered.length }) : "";
}

async function refresh() {
  clearTimeout(pollTimer);
  try {
    render(await api("GET", "/api/state"));
  } catch (err) {
    toast(err.message);
  }
  const busy = lastState.jobs.some(j => j.state === "queued" || j.state === "running");
  pollTimer = setTimeout(refresh, busy ? 1000 : 5000);
}

function lessonUrl(id) {
  const l = lastState.lessons.find(x => x.id === id);
  const view = { translate: "translate", listen: "listen", recall: "recall", spelling: "spelling",
                 read_aloud: "read_aloud", done: "record" }[l?.stage];
  return `/lesson/${id}${view ? "/" + view : ""}`;
}

$("lessons").addEventListener("click", async e => {
  const card = e.target.closest(".lesson");
  if (!card) return;
  const btn = e.target.closest("button[data-act]");
  const id = card.dataset.id;
  if (!btn) {
    if (card.classList.contains("clickable")) location.href = lessonUrl(id);
    return;
  }
  e.stopPropagation();
  const act = btn.dataset.act;
  try {
    if (act === "retry") {
      await api("POST", `/api/lessons/${id}/retry`);
    } else if (act === "delete") {
      if (!confirm(t("confirmDelLesson"))) return;
      await api("DELETE", `/api/lessons/${id}`);
    } else if (act === "dismiss") {
      await api("POST", `/api/jobs/${card.dataset.job}/dismiss`);
    } else if (act === "rename") {
      const l = lastState.lessons.find(x => x.id === id);
      const title = prompt(t("promptTitle"), l ? l.title : "");
      if (title === null || !title.trim()) return;
      await api("PATCH", `/api/lessons/${id}`, { title });
    }
    refresh();
  } catch (err) {
    toast(err.message);
  }
});

async function loadReview() {
  const box = $("reviewBox");
  let s;
  try { s = await api("GET", "/api/review/summary"); } catch { return; }
  if (!s.reviewable) return;
  const parts = [];
  if (s.due) parts.push(t("reviewDue", { n: s.due }));
  if (s.new_today) parts.push(t("reviewNew", { n: s.new_today }));
  const next = s.next_due ? t("reviewNext", { d: s.next_due.slice(5), n: s.next_due_count }) : "";
  box.innerHTML = `<span style="font-size:22px">🔁</span>
    <span class="t">${parts.length ? `<b>${t("reviewBannerTitle")}</b>　${parts.join(" · ")}` : `<b>${t("reviewBannerTitle")}</b>　${t("reviewAllDone")}`}
      <div class="s">${parts.length ? t("reviewSub", { n: s.settings.session_size }) : next || t("reviewCanFree")}</div></span>
    <span class="btn">${parts.length ? t("startReviewBtn") : t("freePracticeBtn")}</span>`;
  if (!parts.length) box.href = "/review?free=1";
  box.hidden = false;
}

refresh();
loadReview();
