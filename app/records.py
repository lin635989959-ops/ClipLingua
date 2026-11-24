"""朗读录音文件管理、每课学习记录汇总与多语言跨课程总生词本。

总生词本不单独冗余存储，每次实时从各课程的 vocab.json + translation.json + progress.json 聚合，
并按「语言 + 词典原形 + 词性」自动去重合并。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import languages as langs
import lemma as lemma_rules
import lesson_store as ls
import progress as prog

# ---------- 录音 ----------

AUDIO_EXT = {
    "audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a",
    "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "video/webm": "webm",
}
REC_NAME = re.compile(r"^s(\d{1,4})_(\d{8}-\d{6})(?:-\d+)?\.(webm|ogg|m4a|mp3|wav)$")
MAX_REC_BYTES = 30 * 1024 * 1024


def rec_dir(lesson_id: str) -> Path:
    return ls.lesson_dir(lesson_id) / ls.RECORDINGS_DIR


def list_recordings(lesson_id: str) -> list[dict]:
    d = rec_dir(lesson_id)
    out = []
    if d.is_dir():
        for f in d.iterdir():
            m = REC_NAME.match(f.name)
            if m and f.is_file():
                at = datetime.strptime(m[2], "%Y%m%d-%H%M%S").astimezone().isoformat(timespec="seconds")
                out.append({"sentence_id": int(m[1]), "file": f.name, "at": at, "size": f.stat().st_size})
    return sorted(out, key=lambda r: (r["sentence_id"], r["file"]))


def save_recording(lesson_id: str, sentence_id: int, data: bytes, mimetype: str) -> dict:
    ext = AUDIO_EXT.get((mimetype or "").split(";")[0].strip().lower())
    if not ext:
        raise ValueError(f"Unsupported audio format: {mimetype}")
    if not data:
        raise ValueError("Recording is empty")
    if len(data) > MAX_REC_BYTES:
        raise ValueError("Recording exceeds size limit")
    d = rec_dir(lesson_id)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"s{sentence_id:03d}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    name, n = f"{stem}.{ext}", 1
    while (d / name).exists():
        n += 1
        name = f"{stem}-{n}.{ext}"
    (d / name).write_bytes(data)
    return next(r for r in list_recordings(lesson_id) if r["file"] == name)


def recording_path(lesson_id: str, name: str) -> Path | None:
    if not REC_NAME.match(name or ""):
        return None
    p = rec_dir(lesson_id) / name
    return p if p.is_file() else None


# ---------- 每课记录 ----------

def _round_stats(stage: str, rnd: dict) -> dict:
    res = rnd["results"]
    good = "correct" if stage == "spelling" else "known"
    return {
        "finished_at": rnd["finished_at"],
        "total": len(res),
        "good": sum(1 for v in res.values() if v == good),
        "partial": bool(rnd.get("partial")),
    }


def lesson_record(lesson_id: str) -> dict:
    p = prog.load(lesson_id)
    tr = ls.load(lesson_id, ls.TRANSCRIPT_FILE) or {"sentences": []}
    recs = list_recordings(lesson_id)
    out = {"stage": p.get("stage", "reading"), "completed_at": p.get("completed_at")}
    for st in prog.PRACTICE:
        s = p["stages"].get(st, {})
        rounds = s.get("rounds", [])
        out[st] = {
            "rounds": len(rounds),
            "done_at": s.get("done_at"),
            "skipped": bool(s.get("skipped")),
            "last": _round_stats(st, rounds[-1]) if rounds else None,
            "first": _round_stats(st, rounds[0]) if rounds else None,
        }
    out["read_aloud"] = {
        "done_at": p["stages"].get("read_aloud", {}).get("done_at"),
        "recordings": len(recs),
        "sentences_recorded": len({r["sentence_id"] for r in recs}),
        "sentences_total": len(tr["sentences"]),
    }
    return out


# ---------- 多语言总生词本 ----------

def word_results(p: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for st in ("listen", "recall"):
        for rnd in p["stages"].get(st, {}).get("rounds", []):
            for k, v in rnd["results"].items():
                cur = out.get(k)
                if cur is None or rnd["finished_at"] >= cur["at"]:
                    out[k] = {"result": v, "at": rnd["finished_at"], "stage": st}
    return out


def _collect(lang: str | None = None) -> list[dict]:
    filter_lang = langs.normalize_lang(lang) if lang and lang != "all" else None
    items = []
    for meta in ls.list_lessons():
        lid = meta["id"]
        if meta.get("status") != ls.STATUS_READY:
            continue
        tr = ls.load(lid, ls.TRANSCRIPT_FILE) or {"sentences": []}
        lesson_lang = ls.lesson_language(meta, tr)
        if filter_lang and lesson_lang != filter_lang:
            continue
        vocab = ls.load(lid, ls.VOCAB_FILE) or ls.empty_vocab()
        if not vocab["words"]:
            continue
        trans = ls.load(lid, ls.TRANSLATION_FILE) or {"sentences": [], "words": []}
        tw = {w["key"]: w for w in trans.get("words", [])}
        ts = {s["id"]: (s.get("text") or s.get("zh") or "") for s in trans.get("sentences", [])}
        tok_at = {t["id"]: (s, t) for s in tr["sentences"] for t in s["tokens"]}
        results = word_results(prog.load(lid))
        for w in vocab["words"]:
            if not w["token_ids"] or w["token_ids"][0] not in tok_at:
                continue
            s, t = tok_at[w["token_ids"][0]]
            info = tw.get(w["key"]) or {}
            gloss = str(info.get("text") or info.get("zh") or "")
            pos = lemma_rules.normalize_pos(info.get("pos"))
            items.append({
                "lesson_id": lid,
                "language": lesson_lang,
                "key": w["key"],
                "text": w["text"],
                "added_at": w["added_at"],
                "gloss": gloss,
                "note": info.get("note", ""),
                "pos": pos,
                "lemma": lemma_rules.normalize_lemma(info.get("lemma"), pos, lesson_lang) if gloss else "",
                "result": results.get(w["key"]),
                "source": {
                    "lesson_id": lid,
                    "language": lesson_lang,
                    "title": meta.get("title", ""),
                    "key": w["key"],
                    "form": w["text"],
                    "token_id": t["id"],
                    "start": t["start"],
                    "end": t["end"],
                    "sentence": s["text"],
                    "sentence_tr": ts.get(s["id"], ""),
                    "sentence_zh": ts.get(s["id"], ""),
                    "count": len(w["token_ids"]),
                },
            })
    return items


def vocab_book(lang: str | None = None) -> list[dict]:
    """按「语言 + 原形 + 词性」实时归并全部课程生词，可选按学习语言过滤。"""
    items = _collect(lang)
    book: dict[str, dict] = {}

    def entry_for(eid: str, language: str, lemma: str, pos: str, pending: bool) -> dict:
        return book.setdefault(eid, {
            "id": eid,
            "language": language,
            "lemma": lemma,
            "pos": pos,
            "pending": pending,
            "forms": [],
            "meanings": [],
            "text": "",
            "zh": "",
            "note": "",
            "added_at": None,
            "result": None,
            "result_at": None,
            "sources": [],
        })

    def add(e: dict, it: dict) -> None:
        if it["key"] not in e["forms"]:
            e["forms"].append(it["key"])
        if it["gloss"] and it["gloss"] not in e["meanings"]:
            e["meanings"].append(it["gloss"])
        e["note"] = e["note"] or it["note"]
        e["added_at"] = min(filter(None, [e["added_at"], it["added_at"]]))
        r = it["result"]
        if r and (e["result_at"] is None or r["at"] >= e["result_at"]):
            e["result"], e["result_at"] = r["result"], r["at"]
        e["sources"].append(it["source"])

    for it in sorted(items, key=lambda i: i["added_at"]):
        if it["lemma"]:
            eid = lemma_rules.entry_id(it["lemma"], it["pos"], it["language"])
            add(entry_for(eid, it["language"], it["lemma"], it["pos"], False), it)

    by_form: dict[tuple[str, str], set[str]] = {}
    for e in book.values():
        for f in [e["lemma"].lower(), *e["forms"]]:
            by_form.setdefault((e["language"], f), set()).add(e["id"])

    for it in sorted(items, key=lambda i: i["added_at"]):
        if it["lemma"]:
            continue
        hits = by_form.get((it["language"], it["key"]), set())
        if len(hits) == 1:
            add(book[next(iter(hits))], it)
        else:
            add(entry_for(f"?{it['language']}|{it['key']}", it["language"], it["text"], "", True), it)

    for e in book.values():
        joined = "; ".join(e["meanings"])
        e["text"] = joined
        e["zh"] = joined
        e["sources"].sort(key=lambda s: s["lesson_id"], reverse=True)
    return sorted(book.values(), key=lambda e: e["added_at"], reverse=True)


def set_entry_lemma(entry_id: str, lemma: str, pos: str) -> dict:
    """修改总生词本中词条的原形/词性，并回写到对应课程的 translation.json。"""
    import translation

    e = next((x for x in vocab_book() if x["id"] == entry_id), None)
    if e is None:
        raise ValueError("Vocabulary entry not found")
    lang = e["language"]
    npos = lemma_rules.normalize_pos(pos)
    nlem = lemma_rules.normalize_lemma(lemma, npos, lang)
    if not nlem:
        raise ValueError("Lemma cannot be empty")
    if npos and npos not in lemma_rules.POS_LIST:
        raise ValueError("Invalid POS code: " + " / ".join(lemma_rules.POS_LIST))

    done = 0
    for s in e["sources"]:
        trans = ls.load(s["lesson_id"], ls.TRANSLATION_FILE) or {}
        if any(w["key"] == s["key"] and (w.get("text") or w.get("zh")) for w in trans.get("words", [])):
            translation.set_entry(s["lesson_id"], "word", s["key"], {"lemma": nlem, "pos": npos})
            done += 1
    if not done:
        raise ValueError("This entry has no translation yet. Import or add a translation first.")
    return {"updated": done, "id": lemma_rules.entry_id(nlem, npos, lang)}
