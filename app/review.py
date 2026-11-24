"""无痛记忆曲线生词复习 + 自由练习（支持多语言独立过滤与调度）。

熟练度 0–5 级，复习间隔 INTERVALS = [0, 1, 3, 7, 15, 30] 天：
- 答对升 1 级，答错降 2 级并次日重练；
- 题型随熟练度递进：0–1 听音释义 → 2–3 看义说词 → 4 拼写 → 5 原句挖空；
- 不设连续打卡惩罚，每次限定短清单（10/20/30 词），断练也不会雪崩堆积。
"""
from __future__ import annotations

import threading
from datetime import date, timedelta

import lesson_store as ls
import records

REVIEW_FILE = "review.json"
INTERVALS = [0, 1, 3, 7, 15, 30]
MAX_LEVEL = len(INTERVALS) - 1
MODES = ("listen", "recall", "spelling", "cloze")
MODE_BY_LEVEL = ["listen", "listen", "recall", "recall", "spelling", "cloze"]
GOOD = {"known", "correct"}
RESULTS = {
    "listen": {"known", "unknown"},
    "recall": {"known", "unknown"},
    "spelling": {"correct", "wrong"},
    "cloze": {"correct", "wrong"},
}
MAX_HISTORY = 20
DEFAULT_SETTINGS = {"session_size": 20, "new_per_session": 5}
SESSION_SIZES = (10, 20, 30)

_lock = threading.Lock()


# ---------- 存取 ----------

def _path():
    return ls.LESSONS_DIR / REVIEW_FILE


def load() -> dict:
    d = ls.read_json(_path()) or {}
    d.setdefault("schema_version", 2)
    d["settings"] = {**DEFAULT_SETTINGS, **(d.get("settings") or {})}
    d.setdefault("entries", {})
    return d


def _save(d: dict) -> None:
    ls.write_json(_path(), d)


def today() -> date:
    return date.today()


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


# ---------- 调度规则 ----------

def initial_level(entry: dict) -> int:
    return 1 if entry.get("lesson_result") == "known" else 0


def apply_answer(level: int, good: bool, on: date) -> tuple[int, str]:
    if good:
        nl = min(MAX_LEVEL, level + 1)
        return nl, (on + timedelta(days=INTERVALS[nl])).isoformat()
    nl = max(0, level - 2)
    return nl, (on + timedelta(days=1)).isoformat()


def priority(st: dict, on: date) -> tuple:
    lv = st["level"]
    overdue = (on - _d(st["due"])).days
    return (-overdue / max(1, INTERVALS[lv]), lv, st["due"])


def eligible(e: dict) -> bool:
    return bool(e.get("text") or e.get("zh")) and not e.get("pending")


def introduced(st: dict | None) -> bool:
    return bool(st) and st.get("level") is not None and bool(st.get("due"))


# ---------- 总生词本 + 复习状态 ----------

def annotate(book: list[dict], d: dict | None = None) -> list[dict]:
    d = d or load()
    for e in book:
        st = d["entries"].get(e["id"])
        e["lesson_result"] = e.get("result")
        e["level"] = st["level"] if introduced(st) else None
        e["due"] = st["due"] if introduced(st) else None
        e["reviewable"] = eligible(e)
        last = (st or {}).get("history", [])[-1:] or [None]
        last = last[0]
        e["review_last"] = last
        if last and (e.get("result_at") is None or last["at"] >= e["result_at"]):
            e["result"] = "known" if last["result"] in GOOD else "unknown"
            e["result_at"] = last["at"]
    return book


def book(lang: str | None = None) -> list[dict]:
    return annotate(records.vocab_book(lang))


def summary(on: date | None = None, lang: str | None = None) -> dict:
    on = on or today()
    d = load()
    entries = annotate(records.vocab_book(lang), d)
    ok = [e for e in entries if e["reviewable"]]
    due = [e for e in ok if e["due"] and _d(e["due"]) <= on]
    new = [e for e in ok if e["level"] is None]
    future = sorted(e["due"] for e in ok if e["due"] and _d(e["due"]) > on)
    nxt = future[0] if future else None
    levels = [0] * (MAX_LEVEL + 1)
    for e in ok:
        if e["level"] is not None:
            levels[e["level"]] += 1
    return {
        "today": on.isoformat(),
        "language": lang or "all",
        "settings": d["settings"],
        "intervals": INTERVALS,
        "due": len(due),
        "new_available": len(new),
        "new_today": min(len(new), d["settings"]["new_per_session"]),
        "next_due": nxt,
        "next_due_count": future.count(nxt) if nxt else 0,
        "reviewable": len(ok),
        "not_reviewable": len(entries) - len(ok),
        "levels": levels,
    }


# ---------- 出题 ----------

def _sentence(src: dict, cache: dict) -> dict | None:
    lid = src["lesson_id"]
    if lid not in cache:
        tr = ls.load(lid, ls.TRANSCRIPT_FILE) or {"sentences": []}
        dd = ls.lesson_dir(lid)
        cache[lid] = (tr, (dd / ls.AUDIO_FILE).is_file() or (dd / ls.VIDEO_FILE).is_file())
    tr, has_audio = cache[lid]
    if not has_audio:
        return None
    sent = next((x for x in tr["sentences"] if any(t["id"] == src["token_id"] for t in x["tokens"])), None)
    return sent if sent and sent.get("end") is not None else None


def _cloze(sources: list[dict], n_done: int, cache: dict) -> dict | None:
    usable = [(s, sent) for s in sources if (sent := _sentence(s, cache))]
    if not usable:
        return None
    s, sent = usable[n_done % len(usable)]
    tok = next(t for t in sent["tokens"] if t["id"] == s["token_id"])
    str_tr = s.get("sentence_tr") or s.get("sentence_zh") or ""
    return {
        "lesson_id": s["lesson_id"],
        "language": s.get("language", "sv"),
        "title": s.get("title", ""),
        "start": sent["start"],
        "end": sent["end"],
        "tokens": [
            {"pre": t["pre"], "text": t["text"], "post": t["post"], "blank": t["key"] == tok["key"]}
            for t in sent["tokens"]
        ],
        "answer": tok["text"],
        "sentence_tr": str_tr,
        "sentence_zh": str_tr,
        "n_sources": len(usable),
    }


def build_items(entries: list[dict], d: dict, mode: str | None = None) -> list[dict]:
    cache: dict = {}
    out = []
    for e in entries:
        st = d["entries"].get(e["id"]) or {}
        lv = st["level"] if introduced(st) else initial_level(e)
        m = mode if mode in MODES else MODE_BY_LEVEL[lv]
        cloze = None
        if m == "cloze":
            n_done = sum(1 for h in st.get("history", []) if h["mode"] == "cloze")
            cloze = _cloze(e["sources"], n_done, cache)
            if cloze is None:
                m = "spelling"
        src = e["sources"][0] if e["sources"] else None
        sent = _sentence(src, cache) if src else None
        gloss = e.get("text") or e.get("zh") or ""
        out.append({
            "id": e["id"],
            "language": e["language"],
            "lemma": e["lemma"],
            "pos": e["pos"],
            "text": gloss,
            "zh": gloss,
            "note": e.get("note", ""),
            "forms": e["forms"],
            "level": lv,
            "new": not introduced(st),
            "mode": m,
            "cloze": cloze,
            "orig": {
                "lesson_id": src["lesson_id"],
                "start": src["start"],
                "end": src["end"],
                "form": src["form"],
                "sentence": src["sentence"],
                "sentence_tr": src.get("sentence_tr") or src.get("sentence_zh") or "",
                "sentence_zh": src.get("sentence_tr") or src.get("sentence_zh") or "",
                "sent_start": sent["start"] if sent else None,
                "sent_end": sent["end"] if sent else None,
                "title": src.get("title", ""),
            } if src else None,
        })
    return out


def session(
    size: int | None = None,
    new: int | None = None,
    on: date | None = None,
    lang: str | None = None,
) -> dict:
    on = on or today()
    d = load()
    size = size if size in SESSION_SIZES else d["settings"]["session_size"]
    new = d["settings"]["new_per_session"] if new is None else max(0, min(50, new))
    entries = [e for e in annotate(records.vocab_book(lang), d) if e["reviewable"]]
    due = sorted(
        (e for e in entries if e["due"] and _d(e["due"]) <= on),
        key=lambda e: priority(d["entries"][e["id"]], on),
    )
    fresh = sorted((e for e in entries if e["level"] is None), key=lambda e: e["added_at"] or "")
    picked = due[:size]
    picked += fresh[:max(0, min(new, size - len(picked)))]
    return {
        "items": build_items(picked, d),
        "due_total": len(due),
        "new_total": len(fresh),
        "intervals": INTERVALS,
        "today": on.isoformat(),
    }


def free_items(ids: list[str], mode: str | None, lang: str | None = None) -> dict:
    d = load()
    by_id = {e["id"]: e for e in annotate(records.vocab_book(lang), d) if e["reviewable"]}
    picked = [by_id[i] for i in dict.fromkeys(map(str, ids)) if i in by_id][:500]
    if not picked:
        raise ValueError("No reviewable entries selected")
    return {"items": build_items(picked, d, mode if mode in MODES else None), "intervals": INTERVALS}


# ---------- 记录作答 ----------

def answer(entry_id: str, mode: str, result: str, free: bool = False, on: date | None = None) -> dict:
    on = on or today()
    if mode not in MODES or result not in RESULTS[mode]:
        raise ValueError("Invalid mode or result")
    with _lock:
        d = load()
        st = d["entries"].get(entry_id)
        if st is None:
            e = next((x for x in records.vocab_book() if x["id"] == entry_id), None)
            if e is None or not eligible(e):
                raise ValueError("Entry not found or not reviewable")
            st = d["entries"][entry_id] = {"level": None, "due": None, "history": []}
            st["_init"] = initial_level({"lesson_result": e.get("result")})
        good = result in GOOD
        before = st["level"] if introduced(st) else None
        if not free:
            lv = before if before is not None else st.get("_init", 0)
            st["level"], st["due"] = apply_answer(lv, good, on)
            st.setdefault("introduced_at", ls.now_iso())
        st.pop("_init", None)
        st["history"].append({
            "at": ls.now_iso(), "mode": mode, "result": result,
            **({"free": True} if free else {}),
        })
        del st["history"][:-MAX_HISTORY]
        _save(d)
        return {"id": entry_id, "level": st["level"], "due": st["due"], "before": before}


def set_settings(session_size=None, new_per_session=None) -> dict:
    with _lock:
        d = load()
        if session_size is not None:
            if int(session_size) not in SESSION_SIZES:
                raise ValueError("Session size must be 10, 20, or 30")
            d["settings"]["session_size"] = int(session_size)
        if new_per_session is not None:
            n = int(new_per_session)
            if not 0 <= n <= 20:
                raise ValueError("new_per_session must be between 0 and 20")
            d["settings"]["new_per_session"] = n
        _save(d)
        return d["settings"]


# ---------- 词条迁移与合并 ----------

def _merge(a: dict, b: dict) -> dict:
    hist = sorted(a.get("history", []) + b.get("history", []), key=lambda h: h["at"])[-MAX_HISTORY:]
    ia, ib = introduced(a), introduced(b)
    out = {"level": None, "due": None, "history": hist}
    if ia or ib:
        out["level"] = min(x["level"] for x in (a, b) if introduced(x))
        out["due"] = min(x["due"] for x in (a, b) if introduced(x))
        ats = [x["introduced_at"] for x in (a, b) if x.get("introduced_at")]
        if ats:
            out["introduced_at"] = min(ats)
    return out


def migrate(old_id: str, new_id: str) -> None:
    if old_id == new_id:
        return
    with _lock:
        d = load()
        st = d["entries"].pop(old_id, None)
        if st is None:
            return
        cur = d["entries"].get(new_id)
        d["entries"][new_id] = _merge(cur, st) if cur else st
        _save(d)
