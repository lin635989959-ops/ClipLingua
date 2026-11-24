"""手动修正语音识别错误的单词（阅读页「修正识别」与命令行共用）。

支持中文、西班牙语、英语、瑞典语的分词与自然拼接。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import languages as langs  # noqa: E402
import lesson_store as ls  # noqa: E402


def _renumber(sentence: dict, lang: str = "sv") -> None:
    for i, t in enumerate(sentence["tokens"], start=1):
        t["id"] = f"{sentence['id']}-{i}"
    sentence["text"] = langs.join_tokens(sentence["tokens"], lang)
    if sentence["tokens"]:
        sentence["start"] = sentence["tokens"][0]["start"]
        sentence["end"] = sentence["tokens"][-1]["end"]


def _shift_suggestions(sentence: dict, first: int, last: int, delta: int) -> None:
    """修改第 first..last 个词后，作废重叠建议并平移后续建议的位置。"""
    keep = []
    for g in sentence.get("suggestions", []):
        if g["last"] < first:
            keep.append(g)
        elif g["first"] > last:
            keep.append({**g, "first": g["first"] + delta, "last": g["last"] + delta})
    if keep:
        sentence["suggestions"] = keep
    else:
        sentence.pop("suggestions", None)


def _count_suggestions(tr: dict) -> None:
    if "subtitle_check" in tr:
        tr["subtitle_check"]["suggestions"] = sum(
            1 for s in tr["sentences"]
            for g in s.get("suggestions", []) if g.get("src") != "ai"
        )


def dismiss_suggestion(lesson_id: str, sentence_id: int, first: int, last: int) -> dict:
    """忽略一条画面字幕或 AI 建议，记录后在重新校对/导入时不再重复出现。"""
    tr = ls.load(lesson_id, ls.TRANSCRIPT_FILE)
    if tr is None:
        raise FileNotFoundError("Transcript not found")
    sent = next((s for s in tr["sentences"] if s["id"] == sentence_id), None)
    if sent is None:
        raise ValueError(f"Sentence {sentence_id} not found")
    hit = next((g for g in sent.get("suggestions", []) if g["first"] == first and g["last"] == last), None)
    if hit is None:
        raise ValueError("Suggestion no longer exists")
    sent["suggestions"].remove(hit)
    if not sent["suggestions"]:
        del sent["suggestions"]
    sent.setdefault("dismissed", []).append({"first": first, "last": last, "text": hit["text"]})
    _count_suggestions(tr)
    ls.save(lesson_id, ls.TRANSCRIPT_FILE, tr)
    return tr


def resync_vocab(lesson_id: str, transcript: dict) -> None:
    """原文编辑导致 token 编号变化后，按 key 重新同步每个生词在文中的所有出现位置。"""
    vocab = ls.load(lesson_id, ls.VOCAB_FILE) or ls.empty_vocab()
    where: dict[str, list[str]] = {}
    for s in transcript["sentences"]:
        for t in s["tokens"]:
            where.setdefault(t["key"], []).append(t["id"])
    for w in vocab["words"]:
        w["token_ids"] = where.get(w["key"], [])
    ls.save(lesson_id, ls.VOCAB_FILE, vocab)


def replace_tokens(
    lesson_id: str,
    sentence_id: int,
    first: int,
    last: int,
    new_text: str,
    keep_post: bool = True,
) -> dict:
    """将第 sentence_id 句的第 first..last 个词（1-based，含两端）替换为 new_text。"""
    tr = ls.load(lesson_id, ls.TRANSCRIPT_FILE)
    if tr is None:
        raise FileNotFoundError("Transcript not found")
    meta = ls.load(lesson_id, ls.LESSON_FILE)
    lang = ls.lesson_language(meta, tr)
    sent = next((s for s in tr["sentences"] if s["id"] == sentence_id), None)
    if sent is None:
        raise ValueError(f"Sentence {sentence_id} not found")
    toks = sent["tokens"]
    if not (1 <= first <= last <= len(toks)):
        raise ValueError(f"Token range out of bounds (sentence has {len(toks)} tokens)")

    old = toks[first - 1:last]
    t0, t1 = old[0]["start"], old[-1]["end"]
    words = langs.tokenize_text(new_text, lang)
    total = sum(len(w[1]) for w in words) or 1
    new_toks, cur = [], t0
    for pre, core, post in words:
        dur = (t1 - t0) * len(core) / total
        new_toks.append({
            "id": "", "pre": pre, "text": core, "post": post, "key": core.lower(),
            "start": round(cur, 2), "end": round(cur + dur, 2),
            "prob": 1.0, "edited": True,
        })
        cur += dur
    if keep_post and new_toks and not new_toks[-1]["post"]:
        new_toks[-1]["post"] = old[-1]["post"]

    sent["tokens"] = toks[:first - 1] + new_toks + toks[last:]
    _shift_suggestions(sent, first, last, len(new_toks) - (last - first + 1))
    _renumber(sent, lang)
    if not sent["tokens"]:
        tr["sentences"] = [s for s in tr["sentences"] if s["tokens"]]
    tr["edited_at"] = ls.now_iso()
    _count_suggestions(tr)
    ls.save(lesson_id, ls.TRANSCRIPT_FILE, tr)
    resync_vocab(lesson_id, tr)
    return sent


def _cli() -> None:
    ap = argparse.ArgumentParser(description="修正识别错的词")
    ap.add_argument("lesson_id")
    ap.add_argument("sentence", type=int, help="第几句")
    ap.add_argument("first", type=int, help="从第几个词")
    ap.add_argument("last", type=int, help="到第几个词（含）")
    ap.add_argument("text", help="改成的文字（空字符串 = 删除）")
    a = ap.parse_args()
    s = replace_tokens(a.lesson_id, a.sentence, a.first, a.last, a.text)
    print(f"✅ 第 {s['id']} 句：{s['text']}")


if __name__ == "__main__":
    _cli()
