"""生词（vocab.json）的增删。阅读页点单词时调用。

同一个词（相同 key）在文中出现多次时，token_ids 记录全部出现位置，一起高亮。
"""
from __future__ import annotations

import lesson_store as ls


def token_ids_for(transcript: dict | None, key: str) -> list[str]:
    if not transcript:
        return []
    return [t["id"] for s in transcript["sentences"] for t in s["tokens"] if t["key"] == key]


def set_word(lesson_id: str, key: str, text: str, on: bool) -> dict:
    """on=True 加入生词（已在就不变），on=False 删除。返回新的 vocab。"""
    key = ls.normalize_word(key)
    if not key:
        raise ValueError("不是有效的单词")
    vocab = ls.load(lesson_id, ls.VOCAB_FILE) or ls.empty_vocab()
    words = vocab["words"]
    exists = any(w["key"] == key for w in words)
    if on and not exists:
        tr = ls.load(lesson_id, ls.TRANSCRIPT_FILE)
        words.append({"key": key, "text": (text or key).strip()[:80] or key,
                      "token_ids": token_ids_for(tr, key), "added_at": ls.now_iso()})
    elif not on and exists:
        vocab["words"] = [w for w in words if w["key"] != key]
    else:
        return vocab
    ls.save(lesson_id, ls.VOCAB_FILE, vocab)
    return vocab
