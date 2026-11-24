"""多语言翻译任务导出 + AI 返回 JSON 合并导入（零 API Key，兼容任意大模型）。

支持中文 (zh)、西班牙语 (es)、英语 (en)、瑞典语 (sv) 任意组合互译：
- 根据课程目标语言（target_lang）与译文母语（native_lang）动态生成精准提示词。
- 导入时兼容新字段（text / in_sentence）及旧版本字段（zh / zh_in_sentence），无缝迁移。
"""
from __future__ import annotations

import json
import re

import languages as langs
import lemma as lemma_rules
import lesson_store as ls

MAX_SUSPECT_WORDS = 4
_LIMITS = {"text": 300, "lemma": 80, "pos": 20, "note": 500, "in_sentence": 80}


def _prompt_templates(target_lang: str, native_lang: str) -> tuple[str, list[str], dict]:
    """按 (目标学习语言, 译文母语) 动态构建给 AI 的翻译指令、规则与返回格式。"""
    tcfg = langs.get_lang(target_lang)
    ncfg = langs.get_lang(native_lang)
    tname = tcfg["names"][native_lang]
    nname = ncfg["names"][native_lang]
    lemma_rule = tcfg["lemma_hint"][native_lang]
    note_rule = tcfg["note_hint"][native_lang]
    pos_codes = " / ".join(lemma_rules.POS_LIST)

    if native_lang == "zh":
        instruction = (
            f"你是专业的{tname}老师。请将下面的{tname}内容翻译成自然通顺的简体中文，用于语言精学。"
            "只返回一个 JSON 对象（可放在 ```json 代码块中），严格遵循 response_format，不要附加其他说明文字。"
        )
        rules = [
            "sentences：每句都要翻译，id 原样保留，text 写自然通顺的简体中文译文。原文由语音识别生成，个别词可能听错，请按最合理的语义翻译。",
            "words：每个生词都要返回，key 必须原样照抄（不可更改大小写或替换为原形）。",
            "words.text：该词在 context_sentence 句中的简短中文释义（几个字）。",
            f"words.lemma：该词的词典原形（只写词本身，不加括号或多余说明）。规则：{lemma_rule}。",
            f"words.pos：词性代码，必须从以下标准代码中选一个：{pos_codes}。",
            f"words.note：简短中文用法说明（{note_rule}）；无特殊说明则填空字符串。",
            "words.in_sentence：该词在你给出的那句译文里对应的片段，必须是译文中原样出现的子串；无直接对应则填空字符串。",
            "suspects：若原文某处明显是语音识别听错（换成发音相近的词整句才合理），列出：sentence_id、heard（原文照抄）、should_be（推测原词）、reason（一句中文理由）。口语省略、俚语或说话人口误不算错；无则返 []。",
            "无论内容多少都必须一次性完整返回，禁止省略或使用「……」。",
        ]
        fmt = {
            "sentences": [{"id": 1, "text": "中文译文"}],
            "words": [{"key": "原样照抄", "text": "简短中文释义", "lemma": "词典原形", "pos": "verb",
                       "note": "简短用法说明", "in_sentence": "译文里对应的字"}],
            "suspects": [{"sentence_id": 1, "heard": "听错的词", "should_be": "实际说的词", "reason": "一句理由"}],
        }
    elif native_lang == "es":
        instruction = (
            f"Eres un profesor experto de {tname}. Traduce el siguiente contenido de {tname} al {nname} natural para estudiantes de idiomas. "
            "Devuelve ÚNICAMENTE un objeto JSON válido (puede ir en un bloque ```json), siguiendo exactamente response_format, sin texto extra."
        )
        rules = [
            f"sentences: traduce cada oración conservando id exactamente; text debe ser {nname} natural. La transcripción es automática; traduce según el sentido más lógico.",
            "words: devuelve cada palabra conservando key exactamente (sin cambiar mayúsculas ni lematizar key).",
            f"words.text: significado breve en {nname} dentro de context_sentence.",
            f"words.lemma: forma base de diccionario (solo la palabra, sin paréntesis). Reglas: {lemma_rule}.",
            f"words.pos: elige UNO de estos códigos estándar: {pos_codes}.",
            f"words.note: breve nota gramatical en {nname} ({note_rule}); cadena vacía si no aplica.",
            "words.in_sentence: subcadena exacta dentro de tu traducción de la oración que corresponde a esta palabra; o cadena vacía.",
            f"suspects: posibles errores claros de ASR (palabras con sonido similar). Campos: sentence_id, heard (copiado exactamente), should_be, reason (en {nname}). No marques jerga ni coloquialismos reales; devuelve [] si no hay.",
            "Devuelve todos los elementos completos sin omitir ninguno.",
        ]
        fmt = {
            "sentences": [{"id": 1, "text": "Traducción en español"}],
            "words": [{"key": "copiar_exacto", "text": "significado breve", "lemma": "lema", "pos": "verb",
                       "note": "nota breve", "in_sentence": "fragmento en la traducción"}],
            "suspects": [{"sentence_id": 1, "heard": "palabra errónea", "should_be": "palabra probable", "reason": "motivo breve"}],
        }
    elif native_lang == "sv":
        instruction = (
            f"Du är en erfaren lärare i {tname}. Översätt följande {tname}-innehåll till naturlig {nname} för språkinlärare. "
            "Returnera ENDAST ett giltigt JSON-objekt (gärna i ett ```json-kodblock) enligt response_format, utan extra förklaringar."
        )
        rules = [
            f"sentences: översätt varje mening och behåll id oförändrat; text ska vara naturlig {nname}.",
            "words: returnera varje ord och kopiera key exakt som den står.",
            f"words.text: kort betydelse på {nname} i sammanhanget context_sentence.",
            f"words.lemma: uppslagsform (endast själva ordet, utan parenteser). Regler: {lemma_rule}.",
            f"words.pos: välj EN av dessa standardkoder: {pos_codes}.",
            f"words.note: kort grammatiknotis på {nname} ({note_rule}); tom sträng annars.",
            "words.in_sentence: exakt delsträng i din meningsöversättning som motsvarar ordet; annars tom sträng.",
            f"suspects: uppenbara ASR-fel (ljudlika ord). Fält: sentence_id, heard (exakt kopia), should_be, reason (på {nname}). Returnera [] om inga finns.",
            "Returnera allt fullständigt utan att utelämna poster.",
        ]
        fmt = {
            "sentences": [{"id": 1, "text": "Svensk översättning"}],
            "words": [{"key": "kopiera_exakt", "text": "kort betydelse", "lemma": "grundform", "pos": "verb",
                       "note": "kort notis", "in_sentence": "motsvarande del i översättningen"}],
            "suspects": [{"sentence_id": 1, "heard": "felhört ord", "should_be": "troligt ord", "reason": "kort förklaring"}],
        }
    else:  # en
        instruction = (
            f"You are an expert {tname} teacher. Translate the following {tname} transcript into natural {nname} for language learners. "
            "Return ONLY a single valid JSON object (inside a ```json code block is fine) matching response_format, with no extra prose."
        )
        rules = [
            f"sentences: translate every sentence keeping id unchanged; text should be natural {nname}. The transcript comes from ASR, so translate the most sensible meaning.",
            "words: return every word keeping key copied verbatim (do not alter case or replace with lemma).",
            f"words.text: concise {nname} gloss in the context of context_sentence.",
            f"words.lemma: dictionary base form (word only, no parentheses). Rules: {lemma_rule}.",
            f"words.pos: pick ONE standard POS code: {pos_codes}.",
            f"words.note: brief usage note in {nname} ({note_rule}); empty string if none.",
            "words.in_sentence: exact substring inside your sentence translation corresponding to this word; empty string if none.",
            f"suspects: clear ASR mishearings only (similar-sounding real words). Fields: sentence_id, heard (verbatim), should_be, reason (in {nname}). Do not flag authentic slang or casual speech; return [] if none.",
            "Return every sentence and word in full without ellipsis or omission.",
        ]
        fmt = {
            "sentences": [{"id": 1, "text": "English translation"}],
            "words": [{"key": "copy_verbatim", "text": "concise gloss", "lemma": "dictionary_form", "pos": "verb",
                       "note": "brief usage note", "in_sentence": "matching substring in translation"}],
            "suspects": [{"sentence_id": 1, "heard": "misheard text", "should_be": "intended text", "reason": "short reason"}],
        }
    return instruction, rules, fmt


def _norm_word(entry: dict, target_lang: str) -> None:
    entry["pos"] = lemma_rules.normalize_pos(entry.get("pos"))
    entry["lemma"] = lemma_rules.normalize_lemma(entry.get("lemma"), entry["pos"], target_lang)


def empty_translation(native_lang: str = langs.DEFAULT_NATIVE_LANG) -> dict:
    return {
        "schema_version": ls.SCHEMA_VERSION,
        "native_lang": langs.normalize_lang(native_lang, langs.DEFAULT_NATIVE_LANG),
        "imported_at": None,
        "sentences": [],
        "words": [],
    }


def _sent_text(s: dict) -> str:
    return str(s.get("text") or s.get("zh") or "")


def _word_text(w: dict) -> str:
    return str(w.get("text") or w.get("zh") or "")


def _word_in_sent(w: dict) -> str:
    return str(w.get("in_sentence") or w.get("zh_in_sentence") or "")


def _first_sentence_of(transcript: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for s in transcript["sentences"]:
        for t in s["tokens"]:
            out.setdefault(t["key"], s)
    return out


def build_task(lesson_id: str, only_missing: bool = False, native_lang: str | None = None) -> dict:
    """生成导出给外部 AI 的翻译任务 JSON。"""
    tr = ls.load(lesson_id, ls.TRANSCRIPT_FILE)
    if not tr:
        raise ValueError("Transcript not ready yet")
    meta = ls.load(lesson_id, ls.LESSON_FILE) or {}
    target_lang = ls.lesson_language(meta, tr)
    nlang = langs.normalize_lang(native_lang or meta.get("native_lang"), langs.DEFAULT_NATIVE_LANG)

    vocab = ls.load(lesson_id, ls.VOCAB_FILE) or ls.empty_vocab()
    old = ls.load(lesson_id, ls.TRANSLATION_FILE) or empty_translation(nlang)
    gap = missing(lesson_id, old)
    need_s = set(gap["missing_sentences"]) | set(gap["stale_sentences"])
    need_w = set(gap["missing_words"])
    first = _first_sentence_of(tr)

    sentences = [
        {"id": s["id"], "source": s["text"]}
        for s in tr["sentences"]
        if not only_missing or s["id"] in need_s
    ]
    words = []
    for w in vocab["words"]:
        s = first.get(w["key"])
        if s is None or (only_missing and w["key"] not in need_w):
            continue
        words.append({
            "key": w["key"],
            "word": w["text"],
            "context_sentence_id": s["id"],
            "context_sentence": s["text"],
        })

    instruction, rules, fmt = _prompt_templates(target_lang, nlang)
    return {
        "instruction": instruction,
        "source_language": target_lang,
        "target_translation_language": nlang,
        "rules": rules,
        "lesson": meta.get("title", ""),
        "sentences": sentences,
        "words": words,
        "response_format": fmt,
    }


# ---------- 导入 ----------

def extract_json(text: str):
    """从 AI 回复中鲁棒提取 JSON 对象（自动去除代码块、说明文字和尾部多余逗号）。"""
    s = (text or "").strip().lstrip("\ufeff")
    if not s:
        raise ValueError("Paste area is empty")
    fence = re.search(r"```(?:json|JSON)?\s*(.*?)```", s, re.S)
    if fence and "{" in fence.group(1):
        s = fence.group(1)
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b < a:
        raise ValueError("No JSON object found in the pasted text")
    s = s[a:b + 1]
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", s))
        except json.JSONDecodeError:
            raise ValueError(
                f"Invalid JSON near line {e.lineno}, col {e.colno} ({e.msg}). "
                "The reply may have been truncated."
            ) from None


def _clean(v, limit: int) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()[:limit]


def _pick_translation_field(item: dict, native_lang: str, limit: int) -> str:
    """兼容 text / translation / zh / en / es / sv 等 AI 可能使用的译文字段名。"""
    for k in ("text", "translation", native_lang, "zh", "en", "es", "sv", "meaning", "gloss"):
        val = item.get(k)
        if val is not None and str(val).strip():
            return _clean(val, limit)
    return ""


def import_text(lesson_id: str, text: str, native_lang: str | None = None) -> tuple[dict, dict]:
    """校验并合并 AI 返回的 JSON，返回 (最新 translation, 导入报告)。"""
    data = extract_json(text)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object with 'sentences' and/or 'words'")
    if "sentences" not in data and "words" not in data:
        raise ValueError("Missing 'sentences' or 'words'. Make sure you pasted the AI reply, not the prompt.")
    tr = ls.load(lesson_id, ls.TRANSCRIPT_FILE)
    if not tr:
        raise ValueError("Transcript not ready yet")
    meta = ls.load(lesson_id, ls.LESSON_FILE) or {}
    target_lang = ls.lesson_language(meta, tr)
    nlang = langs.normalize_lang(native_lang or meta.get("native_lang"), langs.DEFAULT_NATIVE_LANG)

    vocab = ls.load(lesson_id, ls.VOCAB_FILE) or ls.empty_vocab()
    src_by_id = {s["id"]: s["text"] for s in tr["sentences"]}
    vocab_keys = [w["key"] for w in vocab["words"]]

    old = ls.load(lesson_id, ls.TRANSLATION_FILE) or empty_translation(nlang)
    sent_map = {s["id"]: s for s in old.get("sentences", [])}
    word_map = {w["key"]: w for w in old.get("words", [])}
    rep = {"sentences": 0, "words": 0, "unknown_sentences": [], "unknown_words": []}

    for item in data.get("sentences") or []:
        if not isinstance(item, dict):
            continue
        try:
            sid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        tr_text = _pick_translation_field(item, nlang, 2000)
        if sid not in src_by_id:
            rep["unknown_sentences"].append(sid)
        elif tr_text:
            sent_map[sid] = {"id": sid, "text": tr_text, "zh": tr_text, "src": src_by_id[sid]}
            rep["sentences"] += 1

    for item in data.get("words") or []:
        if not isinstance(item, dict):
            continue
        raw = item.get("key") or item.get("word") or ""
        key = ls.normalize_word(str(raw))
        tr_text = _pick_translation_field(item, nlang, _LIMITS["text"])
        if key not in vocab_keys:
            if raw:
                rep["unknown_words"].append(str(raw)[:40])
            continue
        if not tr_text:
            continue
        in_sent = _clean(item.get("in_sentence") or item.get("zh_in_sentence"), _LIMITS["in_sentence"])
        entry = {
            "key": key,
            "text": tr_text,
            "zh": tr_text,
            "lemma": _clean(item.get("lemma"), _LIMITS["lemma"]),
            "pos": _clean(item.get("pos"), _LIMITS["pos"]),
            "note": _clean(item.get("note"), _LIMITS["note"]),
            "in_sentence": in_sent,
            "zh_in_sentence": in_sent,
        }
        _norm_word(entry, target_lang)
        word_map[key] = entry
        rep["words"] += 1

    if rep["sentences"] == 0 and rep["words"] == 0:
        raise ValueError("Nothing imported: sentence IDs or word keys did not match this lesson.")

    _apply_suspects(lesson_id, tr, data.get("suspects"), rep, target_lang)
    ok_texts = {s["id"]: s.pop("_ok_texts") for s in tr["sentences"] if "_ok_texts" in s}
    for sid, e in sent_map.items():
        if sid in ok_texts and (e.get("src") or e.get("sv")) == src_by_id.get(sid):
            e["src_ok"] = ok_texts[sid]

    order = {sid: i for i, sid in enumerate(src_by_id)}
    worder = {k: i for i, k in enumerate(vocab_keys)}
    new = {
        "schema_version": ls.SCHEMA_VERSION,
        "native_lang": nlang,
        "imported_at": ls.now_iso(),
        "sentences": sorted(sent_map.values(), key=lambda s: order.get(s["id"], 1e9)),
        "words": sorted(word_map.values(), key=lambda w: worder.get(w["key"], 1e9)),
    }
    ls.save(lesson_id, ls.TRANSLATION_FILE, new)
    if meta.get("native_lang") != nlang:
        ls.update_meta(lesson_id, native_lang=nlang)
    rep.update(missing(lesson_id, new))
    return new, rep


def _apply_text(tokens: list[dict], first: int, last: int, text: str, lang: str) -> list[dict]:
    old = tokens[first - 1:last]
    new = [
        {"pre": a, "text": b, "post": c}
        for a, b, c in langs.tokenize_text(old[0]["pre"] + text + old[-1]["post"], lang)
    ]
    return tokens[:first - 1] + new + tokens[last:]


def _apply_suspects(lesson_id: str, tr: dict, items, rep: dict, lang: str) -> None:
    """将 AI 指出的疑似听错片段映射到 transcript.json 的 suggestions 中（不自动改写原文）。"""
    rep.update({"suspects": 0, "suspects_skipped": 0, "suspects_unmatched": []})
    if not isinstance(items, list) or not items:
        return
    by_id = {s["id"]: s for s in tr["sentences"]}
    changed = False
    for item in items[:200]:
        if not isinstance(item, dict):
            continue
        try:
            sid = int(item.get("sentence_id", item.get("id")))
        except (TypeError, ValueError):
            continue
        heard = [w[1].lower() for w in langs.tokenize_text(str(item.get("heard") or ""), lang)]
        should = [w[1] for w in langs.tokenize_text(str(item.get("should_be") or ""), lang)]
        s = by_id.get(sid)
        label = f"#{sid} '{_clean(item.get('heard'), 40)}'"
        if (
            s is None
            or not heard
            or not should
            or len(heard) > MAX_SUSPECT_WORDS
            or len(should) > MAX_SUSPECT_WORDS + 2
        ):
            rep["suspects_unmatched"].append(label)
            continue
        if [w.lower() for w in should] == heard:
            continue
        keys = [t["key"] for t in s["tokens"]]
        n = len(heard)
        pos = next((i for i in range(len(keys) - n + 1) if keys[i:i + n] == heard), None)
        if pos is None:
            rep["suspects_unmatched"].append(label)
            continue
        first, last = pos + 1, pos + n
        span = s["tokens"][pos:pos + n]
        if s["tokens"][pos]["text"][:1].isupper():
            should[0] = should[0][:1].upper() + should[0][1:]
        text = langs.join_tokens([{"pre": "", "text": w, "post": ""} for w in should], lang)
        busy = {k for g in s.get("suggestions", []) for k in range(g["first"], g["last"] + 1)}
        gone = {(d["first"], d["last"], d["text"]) for d in s.get("dismissed", [])}
        if (
            any(t.get("ref") or t.get("edited") for t in span)
            or busy & set(range(first, last + 1))
            or (first, last, text) in gone
        ):
            rep["suspects_skipped"] += 1
            continue
        s.setdefault("suggestions", []).append({
            "first": first,
            "last": last,
            "text": text,
            "src": "ai",
            "reason": _clean(item.get("reason"), 200),
        })
        s["suggestions"].sort(key=lambda g: g["first"])
        rep["suspects"] += 1
        changed = True

    if changed:
        ls.save(lesson_id, ls.TRANSCRIPT_FILE, tr)
    for s in tr["sentences"]:
        ai = [g for g in s.get("suggestions", []) if g.get("src") == "ai"]
        if not ai:
            continue
        ok = []
        for g in ai:
            ok.append(langs.join_tokens(_apply_text(s["tokens"], g["first"], g["last"], g["text"], lang), lang))
        if len(ai) > 1:
            toks = s["tokens"]
            for g in sorted(ai, key=lambda g: -g["first"]):
                toks = _apply_text(toks, g["first"], g["last"], g["text"], lang)
            ok.append(langs.join_tokens(toks, lang))
        s["_ok_texts"] = ok


def missing(lesson_id: str, translation: dict | None = None) -> dict:
    """计算尚未翻译的句子/单词以及因原文编辑而可能过时的句子 ID。"""
    tr = ls.load(lesson_id, ls.TRANSCRIPT_FILE) or {"sentences": []}
    vocab = ls.load(lesson_id, ls.VOCAB_FILE) or ls.empty_vocab()
    t = translation if translation is not None else (ls.load(lesson_id, ls.TRANSLATION_FILE) or empty_translation())
    smap = {s["id"]: s for s in t.get("sentences", [])}
    have_w = {w["key"] for w in t.get("words", []) if _word_text(w)}
    stale = []
    for s in tr["sentences"]:
        if s["id"] in smap:
            entry = smap[s["id"]]
            orig_src = entry.get("src") or entry.get("sv")
            ok_list = entry.get("src_ok") or entry.get("sv_ok") or []
            if orig_src not in (None, s["text"]) and s["text"] not in ok_list:
                stale.append(s["id"])
    return {
        "missing_sentences": [s["id"] for s in tr["sentences"] if s["id"] not in smap],
        "stale_sentences": stale,
        "missing_words": [w["key"] for w in vocab["words"] if w["key"] not in have_w and w["token_ids"]],
    }


def set_entry(lesson_id: str, kind: str, ident, fields: dict) -> dict:
    """手动编辑单条句子或生词翻译。"""
    meta = ls.load(lesson_id, ls.LESSON_FILE) or {}
    target_lang = ls.lesson_language(meta)
    nlang = langs.normalize_lang(meta.get("native_lang"), langs.DEFAULT_NATIVE_LANG)
    t = ls.load(lesson_id, ls.TRANSLATION_FILE) or empty_translation(nlang)

    if kind == "sentence":
        tr = ls.load(lesson_id, ls.TRANSCRIPT_FILE) or {"sentences": []}
        src = {s["id"]: s["text"] for s in tr["sentences"]}
        sid = int(ident)
        if sid not in src:
            raise ValueError("Sentence not found")
        tr_text = _clean(fields.get("text") if "text" in fields else fields.get("zh"), 2000)
        t["sentences"] = [s for s in t["sentences"] if s["id"] != sid]
        if tr_text:
            t["sentences"].append({"id": sid, "text": tr_text, "zh": tr_text, "src": src[sid]})
            order = {k: i for i, k in enumerate(src)}
            t["sentences"].sort(key=lambda s: order.get(s["id"], 1e9))
    elif kind == "word":
        key = ls.normalize_word(str(ident))
        if not key:
            raise ValueError("Invalid word key")
        cur = next((w for w in t["words"] if w["key"] == key), None)
        if cur is None:
            cur = {"key": key, "text": "", "zh": "", "lemma": "", "pos": "", "note": "", "in_sentence": "", "zh_in_sentence": ""}
            t["words"].append(cur)
        if "text" in fields or "zh" in fields:
            val = _clean(fields.get("text") if "text" in fields else fields.get("zh"), _LIMITS["text"])
            cur["text"] = cur["zh"] = val
        if "in_sentence" in fields or "zh_in_sentence" in fields:
            val = _clean(
                fields.get("in_sentence") if "in_sentence" in fields else fields.get("zh_in_sentence"),
                _LIMITS["in_sentence"],
            )
            cur["in_sentence"] = cur["zh_in_sentence"] = val
        for f in ("lemma", "pos", "note"):
            if f in fields:
                cur[f] = _clean(fields[f], _LIMITS[f])
        _norm_word(cur, target_lang)
        if not _word_text(cur):
            t["words"] = [w for w in t["words"] if w["key"] != key]
    else:
        raise ValueError("Invalid kind")
    ls.save(lesson_id, ls.TRANSLATION_FILE, t)
    return t
