"""画面字幕校对：用 RapidOCR 读取视频硬字幕，并与语音识别结果逐词比对。

多语言支持（zh / es / en / sv）：
- 拉丁语系（sv / es / en）自动折叠变音符（å ä ö / á é í ó ú ü ñ）进行鲁棒对齐，
  并自动识别花体字体导致的变音符丢失（lossy）。
- 中文（zh）使用 CJK 友好分词，将连续汉字拆解后与语音识别词级结果精准比对。
- 仅给出「确认一致（ref=True）」与「人工核对建议（suggestions）」，绝不私自篡改原文。
"""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import languages as langs  # noqa: E402
import lesson_store as ls  # noqa: E402
from env_setup import FFMPEG, MODELS_DIR  # noqa: E402

ProgressFn = Callable[[float, str], None]

SUBS_FILE = "subtitles.json"            # OCR 帧级文本缓存（可删并重建）
CAPTIONS_FILE = "captions.json"         # 可选的平台字幕轨
OCR_MODELS = MODELS_DIR / "ocr"


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(lo, min(hi, float(raw)))
    except ValueError:
        return default


FPS = _env_float("CLIPLINGUA_OCR_FPS", 1.5, 0.5, 5.0)
FRAME_WIDTH = 480
DET_LIMIT_SIDE_LEN = 544
MIN_SCORE = 0.75
MERGE_GAP = 0.7
WINDOW = 0.8
MIN_COVER = 0.4
MAX_SPAN = 4
UNSURE = 0.5

# 过滤画面中常见的水印、链接、账号名
_JUNK = re.compile(r"@|https?:|www\.|\.com|\.org|\.net", re.I)
_HANDLE = re.compile(r"@([\w.]{3,})")
SAME_FRAME = 2.0

_engine = None
_engine_provider: str | None = None
_engine_lock = threading.Lock()


def _noop(_p: float, _m: str) -> None:
    pass


# ---------- 文本归一化 ----------

def norm(word: str) -> str:
    return ls.normalize_word(word)


def fold(word: str) -> str:
    """去除拉丁变音符号（å ä ö / á é í ó ú ü ñ 等），中文字符不受影响。"""
    return "".join(c for c in unicodedata.normalize("NFD", word) if not unicodedata.combining(c))


def _is_watermark(word: str, handles: set[str]) -> bool:
    w = word.lower()
    if _JUNK.search(w):
        return True
    core = norm(w)
    for h in handles:
        if h in core:
            return True
        for k in range(5, len(h)):
            suf = h[-k:]
            if core.endswith(suf) and len(core) > len(suf):
                return True
    return False


GAP = "gap0"
PARTIAL = "_p"


def strip_watermark(text: str, handles: set[str]) -> str:
    """移除混入字幕末尾的 @账号/URL 水印，保留被遮挡前的有效文字。"""
    words = text.split()
    for i, w in enumerate(words):
        if _is_watermark(w, handles):
            keep = words[:i]
            head = re.match(r"[^\W\d_]+", w.split("@")[0]) if "@" in w else None
            if head and len(head.group(0)) >= 2:
                keep.append(head.group(0) + PARTIAL)
            return " ".join(keep + [GAP]) if keep else ""
    return text


def _partial_same(word: str, part: str) -> bool:
    stem = fold(part[:-1] if len(part) > 3 else part)
    return fold(word).startswith(stem)


def words_of(text: str, lang: str = "sv") -> list[str]:
    """按语言将字幕文本拆为归一化词序列（支持中文无空格分词）。"""
    if langs.is_cjk_lang(lang) or langs.has_cjk(text):
        out = []
        for _pre, core, _post in langs.tokenize_text(text, "zh"):
            if core == GAP or core.endswith(PARTIAL):
                out.append(core)
            else:
                w = core.lower()
                if w:
                    out.append(w)
        return out
    return [w for w in (norm(x) for x in text.split()) if w]


def _clean_word(w: str) -> bool:
    return bool(w) and w != GAP and (w.isalpha() or w.isdigit()) and not _JUNK.search(w)


# ---------- OCR 引擎 ----------

def _ocr_base_params() -> dict:
    """构建跨平台通用的 RapidOCR 基础参数，开启 CPU 内存池与合理线程数以提速。"""
    return {
        "Global.model_root_dir": str(OCR_MODELS),
        "Global.use_cls": False,
        "Global.log_level": "error",
        "Det.limit_type": "max",
        "Det.limit_side_len": DET_LIMIT_SIDE_LEN,
        "EngineConfig.onnxruntime.enable_cpu_mem_arena": True,
        "EngineConfig.onnxruntime.intra_op_num_threads": min(os.cpu_count() or 4, 8),
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    }


def _detect_ort_gpu() -> str | None:
    """检测当前 onnxruntime 是否具备可用的 GPU 加速后端。"""
    try:
        import onnxruntime as ort
        avail = set(ort.get_available_providers())
        if "CUDAExecutionProvider" in avail:
            return "cuda"
        if "DmlExecutionProvider" in avail:
            return "dml"
    except Exception:  # noqa: BLE001
        pass
    return None


def engine_provider() -> str:
    """返回当前 OCR 引擎实际运行的后端标识（cuda / dml / cpu）。"""
    if _engine_provider:
        return _engine_provider
    return _detect_ort_gpu() or "cpu"


def get_engine():
    global _engine, _engine_provider
    with _engine_lock:
        if _engine is None:
            from rapidocr import RapidOCR
            OCR_MODELS.mkdir(parents=True, exist_ok=True)
            base = _ocr_base_params()
            gpu = _detect_ort_gpu()
            if gpu == "cuda":
                try:
                    _engine = RapidOCR(params={**base, "EngineConfig.onnxruntime.use_cuda": True})
                    _engine_provider = "cuda"
                except Exception:  # noqa: BLE001
                    _engine = RapidOCR(params=base)
                    _engine_provider = "cpu"
            elif gpu == "dml":
                try:
                    _engine = RapidOCR(params={**base, "EngineConfig.onnxruntime.use_dml": True})
                    _engine_provider = "dml"
                except Exception:  # noqa: BLE001
                    _engine = RapidOCR(params=base)
                    _engine_provider = "cpu"
            else:
                _engine = RapidOCR(params=base)
                _engine_provider = "cpu"
        return _engine


def _extract_frames(video: Path, out_dir: Path) -> list[tuple[float, Path]]:
    cmd = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
        "-vf", f"fps={FPS},scale={FRAME_WIDTH}:-2", "-q:v", "3", str(out_dir / "f%05d.jpg"),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {r.stderr.strip()[-300:]}")
    frames = sorted(out_dir.glob("f*.jpg"))
    return [((i + 0.5) / FPS, p) for i, p in enumerate(frames)]


def ocr_video(video: Path, progress: ProgressFn = _noop) -> list[dict]:
    """抽取视频帧并运行 OCR，相邻相似帧自动跳过以提速。"""
    engine = get_engine()
    tmp = Path(tempfile.mkdtemp(prefix="cl_frames_"))
    try:
        progress(2, "Extracting video frames…")
        frames = _extract_frames(video, tmp)
        import cv2
        import numpy as np
        out = []
        prev_small, lines = None, []
        for n, (t, path) in enumerate(frames):
            img = cv2.imread(str(path))
            small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (90, 160)).astype(np.int16)
            if prev_small is None or np.abs(small - prev_small).mean() >= SAME_FRAME:
                r = engine(img)
                lines = []
                for text, score, box in zip(
                    r.txts or (),
                    r.scores or (),
                    r.boxes if r.boxes is not None else (),
                ):
                    ys = [float(p[1]) for p in box]
                    lines.append({
                        "text": str(text).strip(),
                        "score": round(float(score), 3),
                        "y0": min(ys),
                        "y1": max(ys),
                    })
                prev_small = small
            out.append({"t": round(t, 2), "lines": lines})
            if n % 5 == 0 and frames:
                progress(5 + 90 * (n + 1) / len(frames), f"OCR scanning {n + 1}/{len(frames)}…")
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 帧文本合并为字幕事件 ----------

def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def find_handles(frames: list[dict], extra: list[str] = ()) -> set[str]:
    handles = {norm(h) for h in extra if h and len(norm(h)) >= 5}
    for fr in frames:
        for ln in fr["lines"]:
            for m in _HANDLE.finditer(ln["text"]):
                h = norm(m.group(1).replace(".", ""))
                if len(h) >= 5:
                    handles.add(h)
    return handles


def build_events(
    frames: list[dict],
    duration: float | None,
    handles: set[str] = frozenset(),
    lang: str = "sv",
    fps: float | None = None,
) -> list[dict]:
    eff_fps = float(fps) if fps and fps > 0 else FPS
    step = 1 / eff_fps
    merge_gap = max(MERGE_GAP, 2.2 * step)
    open_ev: list[dict] = []
    done: list[dict] = []
    is_zh = langs.is_cjk_lang(lang)
    for fr in frames:
        t = fr["t"]
        still = []
        for ev in open_ev:
            (still if t - ev["last"] <= merge_gap else done).append(ev)
        open_ev = still
        for ln in fr["lines"]:
            text = strip_watermark(" ".join(ln["text"].split()), handles)
            key = text.lower()
            if ln["score"] < MIN_SCORE or len(key) < (1 if is_zh else 2):
                continue
            yc = (ln["y0"] + ln["y1"]) / 2
            hit = None
            for ev in open_ev:
                if ev["last"] < t and abs(ev["yc"] - yc) < 40 and _similar(ev["key"], key) >= 0.75:
                    hit = ev
                    break
            if hit is None:
                hit = {"start": t - step / 2, "last": t, "yc": yc, "key": key, "variants": {}}
                open_ev.append(hit)
            hit["last"] = t
            hit["yc"] = yc
            v = hit["variants"].setdefault(text, [0, 0.0])
            v[0] += 1
            v[1] = max(v[1], ln["score"])
    done.extend(open_ev)

    events = []
    for ev in done:
        text, (_cnt, score) = max(
            ev["variants"].items(),
            key=lambda kv: (not _JUNK.search(kv[0]), kv[1][0], kv[1][1]),
        )
        events.append({
            "start": round(max(0.0, ev["start"]), 2),
            "end": round(ev["last"] + step / 2, 2),
            "y": round(ev["yc"]),
            "text": text,
            "score": score,
            "frames": sum(c for c, _ in ev["variants"].values()),
        })

    if duration:
        events = [e for e in events if e["end"] - e["start"] < max(12.0, duration * 0.5)]
    min_wlen = 1 if is_zh else 2
    events = [
        e for e in events
        if any(_clean_word(w) and len(w) >= min_wlen for w in words_of(e["text"], lang))
    ]
    events.sort(key=lambda e: (e["start"], e["y"]))
    return events


# ---------- 字幕轨解析 ----------

_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def parse_srt(data: str) -> list[dict]:
    """解析 SRT / WebVTT 字幕文本。"""
    out = []
    for block in re.split(r"\n\s*\n", data.replace("\r", "")):
        lines = block.strip().split("\n")
        for i, line in enumerate(lines):
            m = _SRT_TIME.search(line)
            if m:
                g = [int(x) for x in m.groups()]
                start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                text = " ".join(re.sub(r"<[^>]+>", "", x).strip() for x in lines[i + 1:]).strip()
                if text:
                    out.append({"start": round(start, 2), "end": round(end, 2), "text": text})
                break
    return out


def save_caption_track(lesson_id: str, info: dict, ydl, lang: str = "sv") -> dict | None:
    """从媒体信息中提取与目标语言匹配的字幕轨，存为 captions.json。"""
    subs = info.get("subtitles") or {}
    prefixes = langs.get_lang(lang)["caption_prefixes"]
    matched_lang = next((k for k in subs if k.lower().split("-")[0] in prefixes), None)
    if not matched_lang:
        return None
    for entry in subs[matched_lang]:
        data = entry.get("data")
        if data is None and entry.get("url") and entry.get("ext") in ("srt", "vtt"):
            with contextlib.suppress(Exception):
                data = ydl.urlopen(entry["url"]).read().decode("utf-8", "replace")
        if data:
            cues = parse_srt(data)
            if cues:
                track = {
                    "schema_version": ls.SCHEMA_VERSION,
                    "lang": matched_lang,
                    "cues": cues,
                    "created_at": ls.now_iso(),
                }
                ls.save(lesson_id, CAPTIONS_FILE, track)
                return track
    return None


def fetch_caption_track(lesson_id: str, url: str, lang: str = "sv") -> dict | None:
    import yt_dlp
    import downloader
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "logger": downloader._SilentLogger()}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return save_caption_track(lesson_id, info, ydl, lang=lang)


# ---------- 逐词比对 ----------

BAND_GAP = 45


def _bands(events: list[dict], t0: float, t1: float) -> list[list[dict]]:
    win = sorted((e for e in events if not (e["end"] < t0 or e["start"] > t1)), key=lambda e: e["y"])
    bands: list[list[dict]] = []
    for e in win:
        if bands and e["y"] - bands[-1][-1]["y"] < BAND_GAP:
            bands[-1].append(e)
        else:
            bands.append([e])
    return [sorted(b, key=lambda e: (e["start"], e["y"])) for b in bands]


def _ref_words(band: list[dict], hyp: list[str], lang: str = "sv") -> list[str]:
    have = {fold(w) for w in hyp}
    words: list[str] = []
    last = None
    for e in band:
        if last is not None and _similar(last, e["text"].lower()) > 0.9:
            continue
        ws = words_of(e["text"], lang)
        real = [w for w in ws if w != GAP and not w.endswith(PARTIAL)]
        if len(real) >= 3 and sum(fold(w) in have for w in real) / len(real) < 0.34:
            continue
        last = e["text"].lower()
        words.extend(ws)
    real = [w for w in words if w != GAP and not w.endswith(PARTIAL)]
    if not real or sum(fold(w) in have for w in real) / len(real) < 0.34:
        return []
    return words


def _mark_lossy(events: list[dict], all_words: set[str], lang: str = "sv") -> None:
    """检测花体字幕是否大面积丢失拉丁变音符号（如 å ä ö / á é ñ）。"""
    if langs.is_cjk_lang(lang):
        for e in events:
            e["lossy"] = False
        return
    folded = {}
    for w in all_words:
        folded.setdefault(fold(w), set()).add(w)
    rows = sorted(events, key=lambda e: e["y"])
    bands: list[list[dict]] = []
    for e in rows:
        if bands and e["y"] - bands[-1][-1]["y"] < BAND_GAP:
            bands[-1].append(e)
        else:
            bands.append([e])
    for band in bands:
        kept = lost = 0
        for e in band:
            for w in words_of(e["text"], lang):
                if not any(h != fold(h) for h in folded.get(fold(w), ())):
                    continue
                if w in all_words:
                    kept += 1
                else:
                    lost += 1
        bad = lost > kept
        for e in band:
            e["lossy"] = bad


def _join_sugg(words: list[str], lang: str) -> str:
    if langs.is_cjk_lang(lang):
        return langs.join_tokens([{"pre": "", "text": w, "post": ""} for w in words], lang)
    return " ".join(words)


def _align_sentence(
    s: dict,
    ref: list[str],
    src: str,
    only_unsure: bool,
    lossy: bool = False,
    lang: str = "sv",
) -> tuple[int, list[dict]]:
    toks = s["tokens"]
    hyp = [t["key"] for t in toks]
    if not hyp or not ref:
        return 0, []
    sm = SequenceMatcher(None, [fold(w) for w in hyp], [fold(w) for w in ref], autojunk=False)
    ops = sm.get_opcodes()
    matched = sum(i2 - i1 for tag, i1, i2, _, _ in ops if tag == "equal")
    if matched < 2 or matched / len(hyp) < MIN_COVER:
        return matched, []

    for tag, i1, i2, j1, _ in ops:
        if tag == "equal":
            for k, t in enumerate(toks[i1:i2]):
                t["ref"] = True
                if hyp[i1 + k] != ref[j1 + k]:
                    lossy = True

    sugg = []
    for k, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag not in ("replace", "insert"):
            continue
        before = k > 0 and ops[k - 1][0] == "equal"
        after = k + 1 < len(ops) and ops[k + 1][0] == "equal"
        new = ref[j1:j2]
        if tag == "replace" and before and new[0].endswith(PARTIAL):
            part = new[0][:-len(PARTIAL)]
            t = toks[i1]
            if _partial_same(hyp[i1], part):
                t["ref"] = True
            elif len(part) >= 4 and _clean_word(part) and not t.get("edited") and not only_unsure:
                txt = part[:1].upper() + part[1:] if t["text"][:1].isupper() else part
                sugg.append({"first": i1 + 1, "last": i1 + 1, "text": txt + "…", "src": src, "partial": True})
            continue
        if tag == "replace" and fold("".join(hyp[i1:i2])) == fold("".join(new)):
            for t in toks[i1:i2]:
                t["ref"] = True
            continue
        if not all(_clean_word(w) for w in new) or len(new) > MAX_SPAN:
            continue
        if tag == "replace":
            if not (before and after):
                if not (before or after):
                    continue
                if i2 - i1 != j2 - j1 and _similar(fold("".join(hyp[i1:i2])), fold("".join(new))) < 0.6:
                    continue
            if i2 - i1 > MAX_SPAN:
                continue
            first, last = i1, i2 - 1
            text_words = new
        else:
            if not (before and after) or len(new) > 2:
                continue
            first = last = i1 - 1
            text_words = [hyp[i1 - 1]] + new
        span = toks[first:last + 1]
        if any(t.get("edited") for t in span):
            continue
        if only_unsure and min(t["prob"] for t in span) >= UNSURE:
            continue
        out = list(text_words)
        if span[0]["text"][:1].isupper():
            out[0] = out[0][:1].upper() + out[0][1:]
        if tag == "insert":
            out[0] = span[0]["text"]
        g = {"first": first + 1, "last": last + 1, "text": _join_sugg(out, lang), "src": src}
        if lossy and tag == "replace" and (
            i2 - i1 != j2 - j1 or _similar(fold("".join(hyp[i1:i2])), fold("".join(new))) >= 0.75
        ):
            continue
        if lossy and not langs.is_cjk_lang(lang) and fold(g["text"]) == g["text"].lower():
            g["nodia"] = True
        sugg.append(g)
    return matched, sugg


def align(lesson_id: str) -> dict:
    """用画面 OCR 字幕或字幕轨对 transcript.json 打标并生成建议。"""
    tr = ls.load(lesson_id, ls.TRANSCRIPT_FILE)
    if tr is None:
        raise FileNotFoundError("Transcript not found")
    meta = ls.load(lesson_id, ls.LESSON_FILE) or {}
    lang = ls.lesson_language(meta, tr)
    subs = ls.load(lesson_id, SUBS_FILE) or {}
    cap = ls.load(lesson_id, CAPTIONS_FILE) or {}
    frames = subs.get("frames") or []
    ocr_events = build_events(
        frames,
        meta.get("duration"),
        find_handles(frames, [meta.get("author", "")]),
        lang=lang,
        fps=subs.get("fps"),
    )
    _mark_lossy(ocr_events, {t["key"] for s in tr["sentences"] for t in s["tokens"]}, lang=lang)
    sources = [("ocr", ocr_events, False), ("caption", cap.get("cues") or [], True)]

    n_words = n_ref = n_sugg = 0
    dismissed_all = {}
    ai_sugg = {}
    for s in tr["sentences"]:
        ai_sugg[s["id"]] = [g for g in s.get("suggestions", []) if g.get("src") == "ai"]
        dismissed_all[s["id"]] = {(d["first"], d["last"], d["text"]) for d in s.get("dismissed", [])}
        for t in s["tokens"]:
            t.pop("ref", None)
        s.pop("suggestions", None)
        s.pop("ref_text", None)

    for s in tr["sentences"]:
        n_words += len(s["tokens"])
        taken: set[int] = set()
        out = []
        hyp = [t["key"] for t in s["tokens"]]
        t0, t1 = s["start"] - WINDOW, s["end"] + WINDOW
        found = []
        for src, events, only_unsure in sources:
            bands = (
                _bands(events, t0, t1)
                if src == "ocr"
                else [[{**c, "y": 0} for c in events if not (c["end"] < t0 or c["start"] > t1)]]
            )
            for band in bands:
                ref = _ref_words(band, hyp, lang=lang)
                if ref:
                    matched, sugg = _align_sentence(
                        s, ref, src, only_unsure,
                        lossy=any(e.get("lossy") for e in band),
                        lang=lang,
                    )
                    found.append((matched, src, ref, sugg))
        found.sort(key=lambda x: (x[1] != "ocr", -x[0]))
        for matched, src, ref, sugg in found:
            if src == "ocr" and "ref_text" not in s and matched / max(1, len(hyp)) >= MIN_COVER:
                clean_ref = [w.replace(PARTIAL, "…") for w in ref if w != GAP]
                s["ref_text"] = _join_sugg(clean_ref, lang)[:500]
            for g in sugg:
                rng = set(range(g["first"], g["last"] + 1))
                if rng & taken or (g["first"], g["last"], g["text"]) in dismissed_all[s["id"]]:
                    continue
                taken |= rng
                out.append(g)
        n_sugg += len(out)
        for g in ai_sugg[s["id"]]:
            rng = set(range(g["first"], g["last"] + 1))
            if not rng & taken and not any(s["tokens"][k - 1].get("ref") for k in rng):
                taken |= rng
                out.append(g)
        if out:
            s["suggestions"] = sorted(out, key=lambda g: g["first"])
        n_ref += sum(1 for t in s["tokens"] if t.get("ref"))

    stats = {
        "words": n_words,
        "confirmed": n_ref,
        "suggestions": n_sugg,
        "ocr_events": len(ocr_events),
        "caption_cues": len(cap.get("cues") or []),
        "checked_at": ls.now_iso(),
    }
    tr["subtitle_check"] = stats
    ls.save(lesson_id, ls.TRANSCRIPT_FILE, tr)
    return stats


def check_lesson(
    lesson_id: str,
    progress: ProgressFn = _noop,
    re_ocr: bool = False,
    fetch_captions: bool = False,
) -> dict:
    """运行画面字幕 OCR（有缓存则复用）并执行逐词比对。"""
    d = ls.lesson_dir(lesson_id)
    meta = ls.load(lesson_id, ls.LESSON_FILE) or {}
    lang = ls.lesson_language(meta)
    video = d / ls.VIDEO_FILE
    if (
        fetch_captions
        and not (d / CAPTIONS_FILE).is_file()
        and str(meta.get("source_url", "")).startswith("http")
    ):
        progress(1, "Checking caption track…")
        with contextlib.suppress(Exception):
            fetch_caption_track(lesson_id, meta["source_url"], lang=lang)
    cap = ls.load(lesson_id, CAPTIONS_FILE) or {}
    has_captions = bool(cap.get("cues"))
    # 已有有效平台字幕轨且非强制重新 OCR 时，直接复用字幕轨，省去耗时硬字幕 OCR
    if video.is_file() and (re_ocr or (not has_captions and not (d / SUBS_FILE).is_file())):
        t0 = time.time()
        frames = ocr_video(video, lambda p, m: progress(2 + p * 0.93, m))
        ls.save(
            lesson_id,
            SUBS_FILE,
            {
                "schema_version": ls.SCHEMA_VERSION,
                "fps": FPS,
                "provider": engine_provider(),
                "seconds": round(time.time() - t0, 1),
                "created_at": ls.now_iso(),
                "frames": frames,
            },
        )
    progress(97, "Aligning subtitles…")
    stats = align(lesson_id)
    progress(100, "Subtitle check complete")
    return stats


def _cli() -> None:
    for s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Verify transcript against on-screen subtitles via OCR")
    ap.add_argument("lesson_id")
    ap.add_argument("--re-ocr", action="store_true")
    ap.add_argument("--captions", action="store_true")
    a = ap.parse_args()
    st = check_lesson(a.lesson_id, lambda p, m: print(f"  [{p:5.1f}%] {m}", flush=True), a.re_ocr, a.captions)
    print(f"\nDone: {st['confirmed']}/{st['words']} confirmed, {st['suggestions']} suggestions.")


if __name__ == "__main__":
    _cli()
