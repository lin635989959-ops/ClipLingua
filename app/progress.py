"""学习进度（progress.json）：阶段推进 + 每轮练习结果。

阶段只会往前走（stage = 走到过的最远阶段）；已经做过的练习随时可以回去再练，记录会追加。
"""
from __future__ import annotations

import lesson_store as ls

PRACTICE = ("listen", "recall", "spelling")
RESULT_VALUES = {
    "listen": {"known", "unknown"},
    "recall": {"known", "unknown"},
    "spelling": {"correct", "wrong", "skipped"},
}
MAX_ROUNDS = 100     # 每个练习最多保留多少轮记录


def load(lesson_id: str) -> dict:
    p = ls.load(lesson_id, ls.PROGRESS_FILE) or ls.empty_progress()
    for s in ls.STAGES[:-1]:
        p["stages"].setdefault(s, {})
    p.setdefault("completed_at", None)
    return p


def _idx(stage: str) -> int:
    return ls.STAGES.index(stage)


def advance(lesson_id: str, stage: str, skipped: bool = False) -> dict:
    """标记 stage 完成（或跳过），当前阶段推进到它的下一步。"""
    if stage not in ls.STAGES[:-1]:
        raise ValueError(f"未知阶段: {stage}")
    if skipped and stage != "spelling":
        raise ValueError("只有拼写练习可以跳过")
    p = load(lesson_id)
    st = p["stages"][stage]
    st["done_at"] = ls.now_iso()
    if skipped:
        st["skipped"] = True
    else:
        st.pop("skipped", None)
    nxt = ls.STAGES[_idx(stage) + 1]
    if _idx(p.get("stage", "reading")) < _idx(nxt):
        p["stage"] = nxt
    if nxt == "done" and not p.get("completed_at"):
        p["completed_at"] = st["done_at"]
    ls.save(lesson_id, ls.PROGRESS_FILE, p)
    return p


def add_round(lesson_id: str, stage: str, rnd: dict) -> dict:
    """追加一轮练习记录。rnd: {started_at, finished_at, mode?, partial?, results: {key: 结果}, tries: {key: 次数}}"""
    if stage not in PRACTICE:
        raise ValueError(f"这个阶段没有练习记录: {stage}")
    allowed = RESULT_VALUES[stage]
    results = {}
    for k, v in list((rnd.get("results") or {}).items())[:1000]:
        key = ls.normalize_word(str(k))
        if key and v in allowed:
            results[key] = v
    if not results:
        raise ValueError("这一轮没有结果")
    tries = {}
    for k, v in list((rnd.get("tries") or {}).items())[:1000]:
        key = ls.normalize_word(str(k))
        if key in results and isinstance(v, int) and 0 < v < 1000:
            tries[key] = v
    clean = {
        "started_at": str(rnd.get("started_at") or ls.now_iso())[:40],
        "finished_at": ls.now_iso(),
        "results": results,
    }
    if tries:
        clean["tries"] = tries
    if rnd.get("mode"):
        clean["mode"] = str(rnd["mode"])[:40]
    if rnd.get("partial"):
        clean["partial"] = True
    p = load(lesson_id)
    rounds = p["stages"][stage].setdefault("rounds", [])
    rounds.append(clean)
    del rounds[:-MAX_ROUNDS]
    ls.save(lesson_id, ls.PROGRESS_FILE, p)
    return p
