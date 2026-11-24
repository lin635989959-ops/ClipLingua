// 全站公共工具：API、播放器、多语言合成发音、多语言拼写与分词拼接
"use strict";

async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body instanceof FormData) {
    opt.body = body;
  } else if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, opt);
  } catch {
    throw new Error(t("errConnect"));
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Error (${res.status})`);
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

let _toastTimer;
function toast(msg, ms = 3500) {
  let el = document.querySelector(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

function fmtDuration(sec) {
  if (sec == null) return "";
  sec = Math.round(sec);
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function stageName(stage) { return t(`stage_${stage}`); }

function lessonIdFromPath() { return location.pathname.split("/")[2]; }

function getLessonSteps() {
  return [
    ["reading", "", t("step_reading")],
    ["translate", "translate", t("step_translate")],
    ["listen", "listen", t("step_listen")],
    ["recall", "recall", t("step_recall")],
    ["spelling", "spelling", t("step_spelling")],
    ["read_aloud", "read_aloud", t("step_read_aloud")],
  ];
}

function renderLessonNav(lessonId, active, progress) {
  const nav = document.getElementById("steps");
  if (!nav) return;
  const steps = getLessonSteps();
  const stage = progress?.stage || "reading";
  const order = steps.map(s => s[0]).concat("done");
  const cur = order.indexOf(stage);
  nav.innerHTML = steps.map(([key, view, label], i) => {
    const st = progress?.stages?.[key] || {};
    const cls = [key === active ? "on" : "", (st.done_at || i < cur) ? "done" : "", i === cur ? "cur" : ""];
    const href = `/lesson/${lessonId}${view ? "/" + view : ""}`;
    return `<a class="${cls.join(" ").trim()}" href="${href}">${label}${st.skipped ? t("skipped") : ""}</a>`;
  }).join("") +
    `<a class="${active === "record" ? "on" : ""} rec" href="/lesson/${lessonId}/record">${t("step_record")}</a>` +
    (stage === "done" ? `<span class="badge ok">${t("lessonDone")}</span>` : "");
}

function langBadgeHtml(code) {
  const m = langMeta(code);
  return `<span class="lang-tag ${m.code}" title="${esc(langName(m.code))}">${m.short}</span>`;
}

// 中/拉丁自然拼接：中文汉字间不强插多余空格
function joinTokensHtml(items, lang, renderItem) {
  const sep = langMeta(lang).isCjk ? "" : " ";
  return items.map(renderItem).join(sep);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

class RangePlayer {
  constructor(el) {
    this.el = el;
    this.stopAt = null;
    this._done = null;
    el.addEventListener("pause", () => { if (el.paused) this._finish(); });
    el.addEventListener("timeupdate", () => this._check());
  }
  async play(start, end) {
    this._finish();
    const el = this.el;
    if (el.readyState === 0) {
      const ready = new Promise(res => el.addEventListener("loadedmetadata", res, { once: true }));
      if (el.preload !== "auto") { el.preload = "auto"; el.load(); }
      await ready;
    }
    el.currentTime = Math.max(0, start);
    this.stopAt = end;
    const p = new Promise(res => { this._done = res; });
    try { await el.play(); } catch { this._finish(); }
    clearTimeout(this._timer);
    const ms = (end - el.currentTime) * 1000 / (el.playbackRate || 1);
    if (end != null && ms > 0) this._timer = setTimeout(() => {
      if (this.stopAt === end) { this.stopAt = null; el.pause(); }
    }, ms);
    const loop = () => {
      if (this._check() || el.paused) return;
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
    return p;
  }
  playToken(t) { return this.play(t.start - 0.08, t.end + 0.15); }
  playSentence(s) { return this.play(s.start - 0.05, s.end + 0.3); }
  stop() { this.el.pause(); this._finish(); }
  _check() {
    if (this.stopAt !== null && this.el.currentTime >= this.stopAt) {
      this.stopAt = null;
      this.el.pause();
      return true;
    }
    return false;
  }
  _finish() {
    this.stopAt = null;
    const d = this._done;
    this._done = null;
    d?.();
  }
}

function shuffle(a) {
  a = a.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function lsGet(k, def) { try { const v = localStorage.getItem(k); return v === null ? def : v; } catch { return def; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch {} }

// ---------- 多语言 TTS 发音 ----------

function voicesForLang(lang) {
  const m = langMeta(lang);
  return m.voices.map(([id, lbl]) => [id, lbl === "__orig__" ? t("voiceOrig") : lbl]);
}

function voiceTip(id) {
  if (id === "orig") return t("voiceTipOrig");
  if (id.endsWith("lisa") || id.endsWith("lessac") || id.endsWith("davefx") || id.endsWith("huayan")) return t("voiceTipPiper");
  return t("voiceTipEdge");
}

function wordVoice(lang = "sv") {
  const m = langMeta(lang);
  const v = lsGet(`tts.voice.${m.code}`, m.defaultVoice);
  return m.voices.some(x => x[0] === v) ? v : m.defaultVoice;
}

function ttsUrl(text, voice, slow, lang = "sv") {
  return `/tts?lang=${encodeURIComponent(lang)}&voice=${encodeURIComponent(voice)}&slow=${slow ? 1 : 0}&text=${encodeURIComponent(text)}`;
}

const _ttsMem = new Map();
let _ttsFallbackTold = false;
function ttsFetch(text, voice, slow, lang = "sv") {
  const u = ttsUrl(text, voice, slow, lang);
  if (!_ttsMem.has(u)) {
    _ttsMem.set(u, fetch(u).then(async r => {
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `TTS Error (${r.status})`);
      const blob = await r.blob();
      const used = r.headers.get("X-TTS-Voice") || voice;
      if (used !== voice) _ttsMem.delete(u);
      return { src: URL.createObjectURL(blob), voice: used };
    }).catch(e => { _ttsMem.delete(u); throw e; }));
  }
  return _ttsMem.get(u);
}

let _prefetchChain = Promise.resolve();
function ttsPrefetch(texts, lang = "sv", voice = null) {
  const v = voice || wordVoice(lang);
  if (v === "orig") return;
  for (const txt of texts) if (txt) _prefetchChain = _prefetchChain.then(() => ttsFetch(txt, v, false, lang).catch(() => {}));
}

class Speaker {
  constructor() {
    this.el = new Audio();
    this._done = null;
    this._seq = 0;
    this.el.addEventListener("ended", () => this._finish());
    this.el.addEventListener("error", () => this._finish());
    this.el.addEventListener("pause", () => { if (this.el.paused) this._finish(); });
  }
  async say(text, { lang = "sv", voice = null, slow = false } = {}) {
    this.stop();
    const v = voice || wordVoice(lang);
    const seq = ++this._seq;
    let r;
    try { r = await ttsFetch(text, v, slow, lang); } catch (e) { toast(e.message); return false; }
    if (seq !== this._seq) return true;
    if (r.voice !== v && !_ttsFallbackTold) {
      _ttsFallbackTold = true;
      toast(t("voiceFallback"));
    }
    const p = new Promise(res => { this._done = res; });
    this.el.src = r.src;
    try { await this.el.play(); } catch { this._finish(); }
    await p;
    return true;
  }
  stop() { this._seq++; if (!this.el.paused) this.el.pause(); this._finish(); }
  _finish() { const d = this._done; this._done = null; d?.(true); }
}
const SPEAKER = new Speaker();

async function speakWord(text, { lang = "sv", orig, stop, slow = false, voice = null } = {}) {
  const v = voice || wordVoice(lang);
  if (v === "orig" || !text) { SPEAKER.stop(); return orig?.(); }
  stop?.();
  const ok = await SPEAKER.say(text, { lang, voice: v, slow });
  if (!ok) return orig?.();
}
function stopSpeaker() { SPEAKER.stop(); }

function voicePickerHtml(lang = "sv") {
  const m = langMeta(lang);
  const v = wordVoice(m.code);
  const list = voicesForLang(m.code);
  return `<label class="voice-pick" title="${esc(voiceTip(v))}">${t("wordVoice")}
    <select data-voice-pick data-voice-lang="${m.code}">
      ${list.map(([k, n]) => `<option value="${k}" ${k === v ? "selected" : ""}>${esc(n)}</option>`).join("")}
    </select></label>`;
}

function bindVoicePicker(root, onChange) {
  root.querySelectorAll("[data-voice-pick]").forEach(sel => sel.addEventListener("change", () => {
    const lc = sel.dataset.voiceLang || "sv";
    lsSet(`tts.voice.${lc}`, sel.value);
    sel.closest(".voice-pick").title = voiceTip(sel.value);
    onChange?.(sel.value, lc);
  }));
}

// ---------- 多语言拼写工具 ----------

function normSpell(s, loose, lang = "sv") {
  s = String(s || "").trim().toLowerCase()
    .replace(/[’`´]/g, "'")
    .replace(/\s+/g, " ")
    .replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
  if (!loose) return s;
  const map = langMeta(lang).looseMap || {};
  return [...s].map(c => map[c] || c).join("");
}

// 瑞典语快捷键：[ ' ; → å ä ö
const SV_KEYS = {
  "[": "å", "{": "Å", "【": "å", "'": "ä", '"': "Ä", "‘": "ä", "’": "ä", "“": "Ä", "”": "Ä",
  ";": "ö", ":": "Ö", "；": "ö", "：": "Ö",
};
function bindSpecialKeys(input, lang = "sv") {
  if (!langMeta(lang).hasSvShortcut) return;
  input.addEventListener("input", () => {
    const v = input.value, pos = input.selectionStart;
    const nv = v.replace(/[\[{【'"‘’“”;:；：]/g, c => SV_KEYS[c]);
    if (nv !== v) { input.value = nv; input.setSelectionRange(pos, pos); }
  });
}

function specialLettersHtml(lang = "sv") {
  const letters = langMeta(lang).specialLetters || [];
  if (!letters.length) return "";
  return `<div class="letters">${letters.map(c => `<button data-ch="${c}" tabindex="-1">${c}</button>`).join("")}</div>`;
}

function insertAtCaret(input, ch) {
  if (input.readOnly) return;
  const a = input.selectionStart ?? input.value.length, b = input.selectionEnd ?? a;
  input.value = input.value.slice(0, a) + ch + input.value.slice(b);
  input.setSelectionRange(a + ch.length, a + ch.length);
  input.focus();
}

function diffHtml(typed, target) {
  const a = [...typed], b = [...target];
  const n = a.length, m = b.length;
  const L = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  let i = 0, j = 0, yours = "", right = "";
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) { yours += esc(a[i]); right += esc(b[j]); i++; j++; }
    else if (j < m && (i >= n || L[i][j + 1] >= L[i + 1][j])) { right += `<span class="miss">${esc(b[j])}</span>`; j++; }
    else { yours += `<span class="extra">${esc(a[i])}</span>`; i++; }
  }
  return `<div class="diff-label">${t("diffYours")}</div><div class="diff">${yours || "—"}</div>
    <div class="diff-label">${t("diffRight")}</div><div class="diff">${right}</div>`;
}

// ---------- 右上角可选 AI API 设置弹窗 ----------
let _llmCache = null;

async function fetchLlmConfig(force = false) {
  if (_llmCache && !force) return _llmCache;
  try {
    _llmCache = await api("GET", "/api/llm/config");
  } catch {
    _llmCache = { configured: false, provider: "deepseek", base_url: "", model: "", api_key_masked: "", presets: [] };
  }
  document.querySelectorAll("[data-api-btn]").forEach(b => b.classList.toggle("on", !!_llmCache.configured));
  return _llmCache;
}

function openApiSettingsModal() {
  fetchLlmConfig(true).then(cfg => {
    let mask = document.getElementById("apiModalMask");
    if (mask) mask.remove();
    const presets = cfg.presets || [];
    mask = document.createElement("div");
    mask.id = "apiModalMask";
    mask.className = "modal-mask";
    mask.innerHTML = `<div class="modal" role="dialog" aria-modal="true">
      <h2><span>${esc(t("apiTitle"))}</span><button class="ghost small" id="mClose">✕</button></h2>
      <p class="desc">${esc(t("apiIntro"))}</p>
      <div class="field">
        <label>${esc(t("apiProvider"))}</label>
        <select id="mProvider">${presets.map(p => `<option value="${esc(p.id)}" ${p.id === cfg.provider ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select>
      </div>
      <div class="field">
        <label>${esc(t("apiKey"))}</label>
        <input type="password" id="mKey" autocomplete="off" placeholder="${esc(t("apiKeyPh"))}">
        ${cfg.api_key_masked ? `<div class="sub">${esc(t("apiKeySaved", { mask: cfg.api_key_masked }))}</div>` : ""}
      </div>
      <div class="field">
        <label style="display:flex;align-items:center;justify-content:space-between;gap:8px">
          <span>${esc(t("apiModel"))}</span>
          <button type="button" class="ghost small" id="mFetch">${esc(t("apiFetchModels"))}</button>
        </label>
        <select id="mModelSel"></select>
        <input type="text" id="mModelCustom" autocomplete="off" placeholder="${esc(t("apiModelPh"))}" style="margin-top:6px" hidden>
      </div>
      <div class="field">
        <label>${esc(t("apiBaseUrl"))}</label>
        <input type="text" id="mUrl" value="${esc(cfg.base_url)}" autocomplete="off" placeholder="https://api.deepseek.com/v1">
      </div>
      <div class="legal-note" style="margin:0">${esc(t("apiPrivacy"))}</div>
      <div id="mMsg" class="m-msg"></div>
      <div class="m-foot">
        <button id="mTest">${esc(t("apiTest"))}</button>
        ${cfg.configured ? `<button class="ghost danger" id="mClear">${esc(t("apiClear"))}</button>` : ""}
        <span class="sp"></span>
        <button class="primary" id="mSave">${esc(t("btnSave"))}</button>
      </div>
    </div>`;
    document.body.appendChild(mask);

    const byId = id => mask.querySelector("#" + id);
    const close = () => mask.remove();
    mask.addEventListener("click", e => { if (e.target === mask) close(); });
    byId("mClose").addEventListener("click", close);

    const CUSTOM_VAL = "__custom__";
    function populateModels(list, selected) {
      const sel = byId("mModelSel");
      const customIn = byId("mModelCustom");
      const items = [...new Set(list || [])].filter(Boolean);
      const inList = selected && items.includes(selected);
      sel.innerHTML = items.map(m => `<option value="${esc(m)}" ${m === selected ? "selected" : ""}>${esc(m)}</option>`).join("")
        + `<option value="${CUSTOM_VAL}" ${!inList ? "selected" : ""}>${esc(t("apiModelCustom"))}</option>`;
      customIn.hidden = !!inList;
      if (!inList) customIn.value = selected || "";
    }

    const curPreset = presets.find(x => x.id === cfg.provider) || presets[0];
    populateModels(curPreset?.models || [], cfg.model || curPreset?.model || "");

    byId("mModelSel").addEventListener("change", () => {
      const isCustom = byId("mModelSel").value === CUSTOM_VAL;
      byId("mModelCustom").hidden = !isCustom;
      if (isCustom) byId("mModelCustom").focus();
    });

    byId("mProvider").addEventListener("change", () => {
      const p = presets.find(x => x.id === byId("mProvider").value);
      if (p) {
        if (p.id !== "custom") byId("mUrl").value = p.base_url;
        populateModels(p.models || [], p.model || "");
      }
    });

    const currentModel = () => byId("mModelSel").value === CUSTOM_VAL
      ? byId("mModelCustom").value.trim()
      : byId("mModelSel").value.trim();

    const payload = () => ({
      provider: byId("mProvider").value,
      api_key: byId("mKey").value.trim() || null,
      model: currentModel(),
      base_url: byId("mUrl").value.trim(),
    });

    byId("mFetch").addEventListener("click", async () => {
      const msg = byId("mMsg");
      const btn = byId("mFetch");
      btn.disabled = true;
      msg.className = "m-msg muted";
      msg.textContent = t("apiTesting");
      try {
        const r = await api("POST", "/api/llm/models", payload());
        const keep = currentModel();
        populateModels(r.models, r.models.includes(keep) ? keep : r.models[0]);
        msg.className = "m-msg ok";
        msg.textContent = t("apiFetchOk", { n: r.models.length });
      } catch (err) {
        msg.className = "m-msg err";
        msg.textContent = err.message;
      } finally {
        btn.disabled = false;
      }
    });

    byId("mTest").addEventListener("click", async () => {
      const msg = byId("mMsg");
      byId("mTest").disabled = true;
      msg.className = "m-msg muted";
      msg.textContent = t("apiTesting");
      try {
        const r = await api("POST", "/api/llm/test", payload());
        msg.className = "m-msg ok";
        msg.textContent = t("apiTestOk", { model: r.model });
      } catch (err) {
        msg.className = "m-msg err";
        msg.textContent = err.message;
      } finally {
        byId("mTest").disabled = false;
      }
    });

    byId("mSave").addEventListener("click", async () => {
      const msg = byId("mMsg");
      try {
        _llmCache = await api("POST", "/api/llm/config", payload());
        document.querySelectorAll("[data-api-btn]").forEach(b => b.classList.toggle("on", !!_llmCache.configured));
        toast(t("apiSaved"));
        window.dispatchEvent(new CustomEvent("cliplingua:llm-changed", { detail: _llmCache }));
        close();
      } catch (err) {
        msg.className = "m-msg err";
        msg.textContent = err.message;
      }
    });

    byId("mClear")?.addEventListener("click", async () => {
      try {
        _llmCache = await api("DELETE", "/api/llm/config");
        document.querySelectorAll("[data-api-btn]").forEach(b => b.classList.toggle("on", false));
        toast(t("apiCleared"));
        window.dispatchEvent(new CustomEvent("cliplingua:llm-changed", { detail: _llmCache }));
        close();
      } catch (err) {
        toast(err.message);
      }
    });
  });
}

// 扩展右上角工具槽：自动在语言切换器旁挂载「⚙️ AI API」设置入口
(function enhanceTopSlot() {
  const origMount = window.mountUiLangPicker;
  if (typeof origMount !== "function") return;
  window.mountUiLangPicker = function () {
    document.querySelectorAll("[data-ui-lang-slot]").forEach(slot => {
      slot.classList.add("top-tools");
      slot.innerHTML = `<button class="api-btn" type="button" data-api-btn><span class="dot"></span>${esc(t("apiBtn"))}</button>` + uiLangPickerHtml();
      slot.querySelector("[data-api-btn]")?.addEventListener("click", openApiSettingsModal);
      slot.querySelector("[data-ui-lang]")?.addEventListener("change", e => setUiLang(e.target.value));
    });
    fetchLlmConfig();
  };
})();
