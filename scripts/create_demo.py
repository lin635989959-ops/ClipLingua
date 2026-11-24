"""生成 100% 原创无版权的四语示例课程（用于体验完整闭环或录制演示截图）。

用法：
  python scripts/create_demo.py                 # 在 lessons/ 中生成 4 门示范课程（sv/es/en/zh）
  python scripts/create_demo.py --clean         # 清除由本脚本创建的 demo_ 示范课程
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import languages as langs  # noqa: E402
import lesson_store as ls  # noqa: E402
import review  # noqa: E402
from env_setup import FFMPEG  # noqa: E402


DEMO_LESSONS = [
    {
        "id": "demo_sv_coffee",
        "language": "sv",
        "native_lang": "en",
        "title": "Fika i Stockholm (Original Demo)",
        "author": "ClipLingua Sample",
        "duration": 10.0,
        "sentences": [
            {
                "id": 1, "start": 0.4, "end": 4.5,
                "text": "Ibland erbjuder ett litet kafé nybakade kanelbullar på morgonen.",
                "tr": "Sometimes a small café offers freshly baked cinnamon buns in the morning.",
                "tokens": [
                    ("Ibland", 0.4, 0.9, 0.95, True),
                    ("bjuder", 0.95, 1.55, 0.42, False),
                    ("ett", 1.6, 1.85, 0.98, True),
                    ("litet", 1.9, 2.25, 0.96, True),
                    ("kafé", 2.3, 2.75, 0.89, True),
                    ("nybakade", 2.8, 3.4, 0.94, True),
                    ("kanelbullar", 3.45, 4.05, 0.97, True),
                    ("på", 4.1, 4.25, 0.99, True),
                    ("morgonen.", 4.28, 4.5, 0.95, True),
                ],
                "suggestions": [
                    {"first": 2, "last": 2, "text": "erbjuder", "src": "ocr"},
                ],
            },
            {
                "id": 2, "start": 5.0, "end": 9.2,
                "text": "Vi brukade prata länge medan kaffet svalnade långsamt.",
                "tr": "We used to talk for a long time while the coffee cooled down slowly.",
                "tokens": [
                    ("Vi", 5.0, 5.2, 0.99, True),
                    ("brukade", 5.25, 5.8, 0.93, True),
                    ("prata", 5.85, 6.25, 0.97, True),
                    ("länge", 6.3, 6.75, 0.94, True),
                    ("medan", 6.8, 7.25, 0.96, True),
                    ("kaffet", 7.3, 7.8, 0.98, True),
                    ("svallade", 7.85, 8.5, 0.24, False),
                    ("långsamt.", 8.55, 9.2, 0.95, True),
                ],
                "suggestions": [
                    {"first": 7, "last": 7, "text": "svalnade", "src": "ai", "reason": "Matches 'coffee cooling down'"},
                ],
            },
        ],
        "vocab": [
            ("kanelbullar", "kanelbullar", "cinnamon buns", "kanelbulle", "noun", "en kanelbulle / kanelbullar", "cinnamon buns"),
            ("nybakade", "nybakade", "freshly baked", "nybakad", "adj", "nybakad / nybakat / nybakade", "freshly baked"),
            ("brukade", "brukade", "used to", "bruka", "verb", "brukar / brukade / brukat", "used to"),
        ],
    },
    {
        "id": "demo_es_plaza",
        "language": "es",
        "native_lang": "en",
        "title": "Una tarde en la plaza (Original Demo)",
        "author": "ClipLingua Sample",
        "duration": 8.0,
        "sentences": [
            {
                "id": 1, "start": 0.5, "end": 4.2,
                "text": "¿Cuándo empezamos a aprender idiomas con vídeos cortos?",
                "tr": "When did we start learning languages with short videos?",
                "tokens": [
                    ("¿Cuándo", 0.5, 1.0, 0.96, True),
                    ("empezamos", 1.05, 1.75, 0.94, True),
                    ("a", 1.8, 1.9, 0.99, True),
                    ("aprender", 1.95, 2.55, 0.97, True),
                    ("idiomas", 2.6, 3.15, 0.95, True),
                    ("con", 3.2, 3.4, 0.99, True),
                    ("vídeos", 3.45, 3.85, 0.91, True),
                    ("cortos?", 3.9, 4.2, 0.96, True),
                ],
            },
            {
                "id": 2, "start": 4.6, "end": 7.6,
                "text": "Escuchar cada frase despacio mejora la pronunciación.",
                "tr": "Listening to each sentence slowly improves pronunciation.",
                "tokens": [
                    ("Escuchar", 4.6, 5.15, 0.95, True),
                    ("cada", 5.2, 5.5, 0.98, True),
                    ("frase", 5.55, 5.95, 0.96, True),
                    ("despacio", 6.0, 6.55, 0.92, True),
                    ("mejora", 6.6, 7.0, 0.97, True),
                    ("la", 7.05, 7.15, 0.99, True),
                    ("pronunciación.", 7.2, 7.6, 0.94, True),
                ],
            },
        ],
        "vocab": [
            ("empezamos", "empezamos", "we started", "empezar", "verb", "e→ie stem change (empiezo)", "started"),
            ("despacio", "despacio", "slowly", "despacio", "adv", "common everyday adverb", "slowly"),
            ("mejora", "mejora", "improves", "mejorar", "verb", "regular -ar verb", "improves"),
        ],
    },
    {
        "id": "demo_zh_tea",
        "language": "zh",
        "native_lang": "en",
        "title": "清晨泡茶日记 (Original Demo)",
        "author": "ClipLingua Sample",
        "duration": 7.5,
        "sentences": [
            {
                "id": 1, "start": 0.4, "end": 3.8,
                "text": "清晨泡一壶绿茶，慢慢听一段对话。",
                "tr": "Brew a pot of green tea in the early morning and slowly listen to a short dialogue.",
                "tokens": [
                    ("清晨", 0.4, 0.9, 0.96, True),
                    ("泡", 0.95, 1.2, 0.94, True),
                    ("一壶", 1.25, 1.7, 0.92, True),
                    ("绿茶，", 1.75, 2.25, 0.97, True),
                    ("慢慢", 2.35, 2.75, 0.95, True),
                    ("听", 2.8, 3.0, 0.98, True),
                    ("一段", 3.05, 3.35, 0.94, True),
                    ("对话。", 3.4, 3.8, 0.97, True),
                ],
            },
            {
                "id": 2, "start": 4.2, "end": 7.2,
                "text": "每天坚持十分钟，语感就会自然提升。",
                "tr": "Stick to ten minutes a day, and your language intuition will naturally improve.",
                "tokens": [
                    ("每天", 4.2, 4.6, 0.98, True),
                    ("坚持", 4.65, 5.1, 0.95, True),
                    ("十分钟，", 5.15, 5.7, 0.96, True),
                    ("语感", 5.8, 6.2, 0.91, True),
                    ("就会", 6.25, 6.55, 0.97, True),
                    ("自然", 6.6, 6.9, 0.95, True),
                    ("提升。", 6.92, 7.2, 0.96, True),
                ],
            },
        ],
        "vocab": [
            ("清晨", "清晨", "early morning", "清晨", "noun", "qīngchén — crisp early morning", "early morning"),
            ("语感", "语感", "language intuition", "语感", "noun", "yǔgǎn — feel for a language", "language intuition"),
            ("提升", "提升", "to improve / elevate", "提升", "verb", "tíshēng — upgrade or improve", "improve"),
        ],
    },
    {
        "id": "demo_en_podcast",
        "language": "en",
        "native_lang": "zh",
        "title": "Building Daily Habits (Original Demo)",
        "author": "ClipLingua Sample",
        "duration": 8.0,
        "sentences": [
            {
                "id": 1, "start": 0.4, "end": 4.0,
                "text": "Consistent shadowing turns passive vocabulary into active fluency.",
                "tr": "持续的影子跟读能把被动词汇转化为流利的主动表达。",
                "tokens": [
                    ("Consistent", 0.4, 0.95, 0.97, True),
                    ("shadowing", 1.0, 1.6, 0.94, True),
                    ("turns", 1.65, 1.95, 0.98, True),
                    ("passive", 2.0, 2.45, 0.96, True),
                    ("vocabulary", 2.5, 3.1, 0.95, True),
                    ("into", 3.15, 3.35, 0.99, True),
                    ("active", 3.4, 3.7, 0.97, True),
                    ("fluency.", 3.72, 4.0, 0.96, True),
                ],
            },
        ],
        "vocab": [
            ("consistent", "Consistent", "持续一致的", "consistent", "adj", "staying steady over time", "持续的"),
            ("fluency", "fluency", "流利度", "fluency", "noun", "smooth, natural speech", "流利"),
        ],
    },
]


def _make_sample_media(d: Path, duration: float, color: str, subtitle_text: str) -> None:
    """用 FFmpeg 生成 100% 合成的无版权静音/纯音演示视频与音频。"""
    if not FFMPEG.is_file():
        return
    video = d / ls.VIDEO_FILE
    audio = d / ls.AUDIO_FILE
    thumb = d / ls.THUMB_FILE
    # 生成柔和的 440Hz 极低音量演示音频 + 竖屏纯色渐变演示视频
    subprocess.run(
        [
            str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=360x640:d={duration}:r=24",
            "-f", "lavfi", "-i", f"sine=frequency=330:duration={duration}",
            "-af", "volume=0.04",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(video),
        ],
        capture_output=True,
    )
    subprocess.run(
        [
            str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(audio),
        ],
        capture_output=True,
    )
    subprocess.run(
        [
            str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-ss", "0.5", "-i", str(video), "-frames:v", "1", "-vf", "scale=240:-2", str(thumb),
        ],
        capture_output=True,
    )


def create_demos() -> list[str]:
    colors = {"sv": "#0b4f8a", "es": "#9a3412", "zh": "#9d174d", "en": "#312e81"}
    created = []
    for item in DEMO_LESSONS:
        lid = item["id"]
        d = ls.lesson_dir(lid)
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        (d / ls.RECORDINGS_DIR).mkdir()

        meta = {
            "schema_version": ls.SCHEMA_VERSION,
            "id": lid,
            "title": item["title"],
            "author": item["author"],
            "language": item["language"],
            "native_lang": item["native_lang"],
            "source_url": f"demo:{lid}",
            "video_id": lid,
            "duration": item["duration"],
            "created_at": ls.now_iso(),
            "status": ls.STATUS_READY,
            "error": None,
        }
        ls.save(lid, ls.LESSON_FILE, meta)

        sents = []
        tr_sents = []
        tok_pos: dict[str, list[str]] = {}
        for s in item["sentences"]:
            toks = []
            for idx, (raw, st, en, prob, ref) in enumerate(s["tokens"], start=1):
                pre, core, post = langs.split_token(raw)
                tid = f"{s['id']}-{idx}"
                key = core.lower()
                tok_pos.setdefault(key, []).append(tid)
                tk = {"id": tid, "pre": pre, "text": core, "post": post, "key": key, "start": st, "end": en, "prob": prob}
                if ref:
                    tk["ref"] = True
                toks.append(tk)
            sobj = {"id": s["id"], "start": s["start"], "end": s["end"], "text": s["text"], "tokens": toks}
            if s.get("suggestions"):
                sobj["suggestions"] = s["suggestions"]
            sents.append(sobj)
            tr_sents.append({"id": s["id"], "text": s["tr"], "zh": s["tr"], "src": s["text"]})

        ls.save(lid, ls.TRANSCRIPT_FILE, {
            "schema_version": ls.SCHEMA_VERSION,
            "language": item["language"],
            "model": "demo-synthetic",
            "device": "cpu",
            "seconds": 0.1,
            "vocal_separation": {"music_ratio": 0.62, "used": True},
            "subtitle_check": {"words": sum(len(s["tokens"]) for s in sents), "confirmed": sum(1 for s in sents for t in s["tokens"] if t.get("ref")), "suggestions": 1, "ocr_events": 4, "caption_cues": 0, "checked_at": ls.now_iso()},
            "created_at": ls.now_iso(),
            "sentences": sents,
        })

        vocab_words = []
        tr_words = []
        for key, text, gloss, lem, pos, note, frag in item["vocab"]:
            vocab_words.append({"key": key, "text": text, "token_ids": tok_pos.get(key, []), "added_at": ls.now_iso()})
            tr_words.append({
                "key": key, "text": gloss, "zh": gloss, "lemma": lem,
                "pos": pos, "note": note, "in_sentence": frag, "zh_in_sentence": frag,
            })

        ls.save(lid, ls.VOCAB_FILE, {"schema_version": ls.SCHEMA_VERSION, "words": vocab_words})
        ls.save(lid, ls.TRANSLATION_FILE, {
            "schema_version": ls.SCHEMA_VERSION,
            "native_lang": item["native_lang"],
            "imported_at": ls.now_iso(),
            "sentences": tr_sents,
            "words": tr_words,
        })
        prog = ls.empty_progress()
        prog["stage"] = "listen"
        prog["stages"]["reading"] = {"done_at": ls.now_iso()}
        prog["stages"]["translate"] = {"done_at": ls.now_iso()}
        ls.save(lid, ls.PROGRESS_FILE, prog)

        _make_sample_media(d, item["duration"], colors[item["language"]], item["title"])
        created.append(lid)

    # 为示例词条初始化部分记忆曲线状态
    review.answer("sv|bruka|verb", "listen", "known")
    review.answer("es|empezar|verb", "listen", "known")
    review.answer("zh|语感|noun", "listen", "unknown")
    return created


def clean_demos() -> None:
    for item in DEMO_LESSONS:
        d = ls.lesson_dir(item["id"])
        if d.exists():
            shutil.rmtree(d)
    rv = ls.LESSONS_DIR / review.REVIEW_FILE
    if rv.exists():
        rv.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description="Create or clean copyright-free ClipLingua demo lessons")
    ap.add_argument("--clean", action="store_true", help="Remove demo lessons")
    a = ap.parse_args()
    if a.clean:
        clean_demos()
        print("Removed demo lessons.")
    else:
        ids = create_demos()
        print("Created demo lessons:", ", ".join(ids))


if __name__ == "__main__":
    main()
