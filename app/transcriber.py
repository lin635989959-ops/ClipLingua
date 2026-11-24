"""多语言语音识别：生成词级时间戳 transcript.json（支持中文、西班牙语、英语、瑞典语）。

核心流程：
1. 若存在人声分离模型（Kim_Vocal_2），先检测背景音乐占比，有明显 BGM 时自动提取纯净人声。
2. 按目标语言路由最优 Whisper 模型（瑞典语优先 KBLab strict，中/英/西使用多语言 large-v3-turbo）。
3. 将词级输出智能断句并组装为语言适配的 token 列表与自然拼接文本。
"""
from __future__ import annotations

import argparse
import gc
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import languages as langs  # noqa: E402
import lesson_store as ls  # noqa: E402
import vocals  # noqa: E402
from env_setup import MODELS_DIR  # noqa: E402

ProgressFn = Callable[[float, str], None]

MAX_WORDS_LATIN = 28     # 拉丁语系长句切分阈值
MAX_TOKENS_CJK = 36      # 中文长句切分阈值
PAUSE_SPLIT = 2.0        # 词间停顿超过该秒数视为新句子
LOW_PROB = 0.3           # 置信度低于此值标为可能识别错误

_model = None
_model_key: str | None = None
_model_lock = threading.Lock()
_device: str | None = None


def _noop(_p: float, _m: str) -> None:
    pass


def get_model(lang: str = "sv") -> tuple[object, str]:
    """按语言加载 Whisper 模型（单槽缓存，切换不同模型时释放旧显存）。"""
    global _model, _model_key, _device
    model_ref, display_name = langs.resolve_whisper_model(lang, MODELS_DIR)
    with _model_lock:
        if _model is None or _model_key != model_ref:
            if _model is not None:
                _model = None
                gc.collect()
            from faster_whisper import WhisperModel
            try:
                _model = WhisperModel(model_ref, device="cuda", compute_type="float16")
                _device = "cuda"
            except Exception:  # noqa: BLE001
                _model = WhisperModel(model_ref, device="cpu", compute_type="int8")
                _device = "cpu"
            _model_key = model_ref
        return _model, display_name


# ---------- 词级结果整理与分句 ----------

def _words_to_tokens(words, lang: str = "sv") -> list[dict]:
    """将 faster-whisper 的 Word 列表转为标准 token 列表，纯标点自动并到相邻词。"""
    tokens: list[dict] = []
    is_zh = langs.is_cjk_lang(lang)
    for w in words:
        raw = getattr(w, "word", "") or ""
        start = round(float(getattr(w, "start", 0.0)), 2)
        end = round(float(getattr(w, "end", start)), 2)
        prob = round(float(getattr(w, "probability", 1.0)), 3)

        if is_zh:
            # 中文 Whisper 片段可能自带标点或混合多个词，先按中文分词器安全拆分
            parts = langs.tokenize_text(raw, "zh")
            if not parts:
                s = raw.strip()
                if s and tokens:
                    tokens[-1]["post"] += s
                continue
            total = sum(len(p[1]) for p in parts) or 1
            cur = start
            for pre, core, post in parts:
                dur = (end - start) * len(core) / total
                tokens.append({
                    "pre": pre, "text": core, "post": post, "key": core.lower(),
                    "start": round(cur, 2), "end": round(cur + dur, 2), "prob": prob,
                })
                cur += dur
        else:
            pre, core, post = ls.split_token(raw)
            if not core:
                if tokens:
                    tokens[-1]["post"] += pre
                continue
            tokens.append({
                "pre": pre, "text": core, "post": post, "key": core.lower(),
                "start": start, "end": end, "prob": prob,
            })
    return tokens


def _split_long(toks: list[dict], max_len: int) -> list[list[dict]]:
    """过长的句子优先在逗号/分号处、其次在最大停顿处递归切分。"""
    if len(toks) <= max_len:
        return [toks]
    lo, hi = len(toks) // 4, len(toks) * 3 // 4
    best, best_score = lo, -1.0
    for i in range(lo, hi):
        gap = toks[i + 1]["start"] - toks[i]["end"]
        has_pause = any(p in toks[i]["post"] for p in langs.PAUSE_PUNCT)
        score = gap + (10 if has_pause else 0)
        if score > best_score:
            best, best_score = i, score
    cut = best + 1
    return _split_long(toks[:cut], max_len) + _split_long(toks[cut:], max_len)


def _group_sentences(tokens: list[dict], lang: str = "sv") -> list[list[dict]]:
    groups: list[list[dict]] = []
    cur: list[dict] = []
    is_zh = langs.is_cjk_lang(lang)
    for i, t in enumerate(tokens):
        if cur and t["start"] - cur[-1]["end"] > PAUSE_SPLIT:
            groups.append(cur)
            cur = []
        cur.append(t)
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        ends = t["post"].rstrip("\"'»”)」』】》").endswith(langs.SENTENCE_END)
        # 拉丁语缩写如 "t.ex." / "e.g." 后面跟小写字母时不断句
        if not is_zh and ends and nxt and nxt["text"][:1].islower() and "." in t["text"]:
            ends = False
        if ends:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    max_len = MAX_TOKENS_CJK if is_zh else MAX_WORDS_LATIN
    out: list[list[dict]] = []
    for g in groups:
        out.extend(_split_long(g, max_len))
    return out


def build_sentences(words, lang: str = "sv") -> list[dict]:
    """将词级识别结果整理为带编号与精确时间区间的句子列表。"""
    sentences = []
    for sid, toks in enumerate(_group_sentences(_words_to_tokens(words, lang), lang), start=1):
        for wid, t in enumerate(toks, start=1):
            t["id"] = f"{sid}-{wid}"
        text = langs.join_tokens(toks, lang)
        sentences.append({
            "id": sid,
            "start": toks[0]["start"],
            "end": toks[-1]["end"],
            "text": text,
            "tokens": [
                {k: t[k] for k in ("id", "pre", "text", "post", "key", "start", "end", "prob")}
                for t in toks
            ],
        })
    return sentences


# ---------- 主流程 ----------

def transcribe_lesson(lesson_id: str, progress: ProgressFn = _noop) -> dict:
    """对指定课程执行人声分离（按需）+ 多语言 Whisper 词级转写。"""
    d = ls.lesson_dir(lesson_id)
    audio = d / ls.AUDIO_FILE
    if not audio.is_file():
        raise FileNotFoundError(f"Audio file missing: {audio}")
    meta = ls.load(lesson_id, ls.LESSON_FILE) or {}
    duration = meta.get("duration") or 0
    lang = ls.lesson_language(meta)

    ls.update_meta(lesson_id, status=ls.STATUS_TRANSCRIBING, error=None)
    vocal_wav = d / "audio_vocals.tmp.wav"
    try:
        src, sep = audio, None
        media = (d / ls.VIDEO_FILE) if (d / ls.VIDEO_FILE).is_file() else audio
        if vocals.available() and media.is_file():
            progress(1, "Checking background music…")
            try:
                sep = vocals.prepare(media, vocal_wav, lambda f: progress(1 + 8 * f, "Separating vocals…"))
                if sep["used"]:
                    src = vocal_wav
            except Exception as e:  # noqa: BLE001
                sep = {"error": str(e)[:200], "used": False}

        progress(10, "Loading Whisper model…")
        model, model_name = get_model(lang)
        progress(
            12,
            f"Transcribing [{lang.upper()}] ({_device}"
            f"{', vocals isolated' if sep and sep.get('used') else ''})…",
        )
        t0 = time.time()
        try:
            beam_size = max(1, min(5, int(os.environ.get("CLIPLINGUA_WHISPER_BEAM", "2"))))
        except ValueError:
            beam_size = 2
        kwargs = dict(
            language=lang,
            task="transcribe",
            beam_size=beam_size,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
        )
        # 中文转写提示使用简体中文输出，避免简繁混杂
        if lang == "zh":
            kwargs["initial_prompt"] = "以下是简体中文口语对话，请使用简体中文和规范标点。"

        segments, _info = model.transcribe(str(src), **kwargs)
        words = []
        for seg in segments:
            words.extend(seg.words or [])
            if duration:
                progress(12 + 83 * min(seg.end / duration, 1), "Transcribing…")

        sentences = build_sentences(words, lang)
        transcript = {
            "schema_version": ls.SCHEMA_VERSION,
            "language": lang,
            "model": model_name,
            "device": _device,
            "seconds": round(time.time() - t0, 1),
            "vocal_separation": sep,
            "created_at": ls.now_iso(),
            "sentences": sentences,
        }
        ls.save(lesson_id, ls.TRANSCRIPT_FILE, transcript)
    except Exception as e:  # noqa: BLE001
        ls.update_meta(lesson_id, status=ls.STATUS_ERROR, error=f"Transcription failed: {e}"[:1000])
        raise
    finally:
        vocal_wav.unlink(missing_ok=True)
    ls.update_meta(lesson_id, status=ls.STATUS_READY, error=None)
    progress(100, "Transcription complete")
    return transcript


def _cli() -> None:
    ap = argparse.ArgumentParser(description="Transcribe lesson audio into transcript.json")
    ap.add_argument("lesson_id", nargs="?", help="Lesson ID (defaults to latest)")
    a = ap.parse_args()
    lid = a.lesson_id
    if not lid:
        lessons = ls.list_lessons()
        if not lessons:
            sys.exit("No lessons found.")
        lid = lessons[0]["id"]

    tr = transcribe_lesson(lid, lambda p, m: print(f"  [{p:5.1f}%] {m}", flush=True))
    n_tok = sum(len(s["tokens"]) for s in tr["sentences"])
    print(f"\nDone: {lid} [{tr['language']}] - {len(tr['sentences'])} sentences, {n_tok} tokens ({tr['seconds']}s)")


if __name__ == "__main__":
    _cli()
