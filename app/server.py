"""ClipLingua 本地 Web 服务器（仅监听 127.0.0.1，数据完全留在本地）。

运行：python app/server.py --open
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from env_setup import FFMPEG, ROOT  # noqa: E402
import downloader  # noqa: E402
import jobs  # noqa: E402
import languages as langs  # noqa: E402
import lesson_store as ls  # noqa: E402
import llm  # noqa: E402
import progress as prog  # noqa: E402
import records  # noqa: E402
import review  # noqa: E402
import transcript_edit  # noqa: E402
import translation  # noqa: E402
import tts  # noqa: E402
import vocab as vocab_mod  # noqa: E402

from flask import Flask, abort, jsonify, request, send_file, send_from_directory  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent / "web"
UPLOAD_TMP = ROOT / "tmp"

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="/static")
app.json.ensure_ascii = False
app.json.sort_keys = False


def _err(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


def _lesson_or_404(lesson_id: str) -> dict:
    try:
        meta = ls.load(lesson_id, ls.LESSON_FILE)
    except ValueError:
        abort(404)
    if meta is None:
        abort(404)
    meta.setdefault("language", langs.DEFAULT_TARGET_LANG)
    meta.setdefault("native_lang", langs.DEFAULT_NATIVE_LANG)
    return meta


def _summary(meta: dict) -> dict:
    lid = meta["id"]
    tr = ls.load(lid, ls.TRANSCRIPT_FILE) or {}
    sents = tr.get("sentences", [])
    vocab = ls.load(lid, ls.VOCAB_FILE) or {}
    p = ls.load(lid, ls.PROGRESS_FILE) or {}
    d = ls.lesson_dir(lid)
    return {
        **meta,
        "language": ls.lesson_language(meta, tr),
        "has_video": (d / ls.VIDEO_FILE).is_file(),
        "n_sentences": len(sents),
        "n_words": sum(len(s["tokens"]) for s in sents),
        "preview": " ".join(s["text"] for s in sents[:2])[:160],
        "n_vocab": len(vocab.get("words", [])),
        "stage": p.get("stage", "reading"),
        "has_translation": (d / ls.TRANSLATION_FILE).is_file(),
        "record": records.lesson_record(lid) if meta.get("status") == ls.STATUS_READY else None,
    }


# ---------- 页面路由 ----------

@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/lesson/<lesson_id>")
def lesson_page(lesson_id: str):
    _lesson_or_404(lesson_id)
    return send_from_directory(WEB_DIR, "lesson.html")


VIEWS = {
    "translate": "translate.html",
    "listen": "practice.html",
    "recall": "practice.html",
    "spelling": "practice.html",
    "read_aloud": "readaloud.html",
    "record": "record.html",
}


@app.get("/lesson/<lesson_id>/<view>")
def lesson_view(lesson_id: str, view: str):
    _lesson_or_404(lesson_id)
    if view not in VIEWS:
        abort(404)
    return send_from_directory(WEB_DIR, VIEWS[view])


@app.get("/vocab")
def vocab_page():
    return send_from_directory(WEB_DIR, "vocab.html")


@app.get("/review")
def review_page():
    return send_from_directory(WEB_DIR, "review.html")


# ---------- 媒体与 TTS ----------

@app.get("/media/<lesson_id>/video.mp4")
def media_video(lesson_id: str):
    _lesson_or_404(lesson_id)
    p = ls.lesson_dir(lesson_id) / ls.VIDEO_FILE
    if not p.is_file():
        abort(404)
    return send_file(p, mimetype="video/mp4", conditional=True)


@app.get("/media/<lesson_id>/audio")
def media_audio(lesson_id: str):
    _lesson_or_404(lesson_id)
    d = ls.lesson_dir(lesson_id)
    if (d / ls.AUDIO_FILE).is_file():
        return send_file(d / ls.AUDIO_FILE, mimetype="audio/wav", conditional=True)
    if (d / ls.VIDEO_FILE).is_file():
        return send_file(d / ls.VIDEO_FILE, mimetype="video/mp4", conditional=True)
    abort(404)


@app.get("/tts")
def tts_audio():
    """多语言合成发音：/tts?text=...&lang=sv|en|es|zh&voice=...&slow=0|1。"""
    lang = request.args.get("lang")
    voice = request.args.get("voice")
    slow = request.args.get("slow") == "1"
    requested = tts.resolve_voice(voice, lang)
    try:
        path, used = tts.synth(request.args.get("text", ""), requested, slow, lang=lang)
    except tts.TTSError as e:
        return _err(str(e), 503)
    resp = send_file(path, mimetype="audio/mpeg", conditional=True)
    resp.headers["X-TTS-Voice"] = used
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable" if used == requested else "no-store"
    return resp


@app.get("/api/tts/status")
def tts_status():
    return jsonify(tts.status())


@app.get("/api/languages")
def api_languages():
    return jsonify({**langs.public_languages_info(), "tts": tts.status()})


@app.get("/media/<lesson_id>/recordings/<name>")
def media_recording(lesson_id: str, name: str):
    _lesson_or_404(lesson_id)
    p = records.recording_path(lesson_id, name)
    if p is None:
        abort(404)
    mime = {
        "webm": "audio/webm", "ogg": "audio/ogg", "m4a": "audio/mp4",
        "mp3": "audio/mpeg", "wav": "audio/wav",
    }[p.suffix[1:]]
    return send_file(p, mimetype=mime, conditional=True)


_thumb_lock = threading.Lock()


@app.get("/media/<lesson_id>/thumb.jpg")
def media_thumb(lesson_id: str):
    meta = _lesson_or_404(lesson_id)
    d = ls.lesson_dir(lesson_id)
    thumb, video = d / ls.THUMB_FILE, d / ls.VIDEO_FILE
    if not thumb.is_file():
        if not video.is_file() or meta.get("status") == ls.STATUS_DOWNLOADING:
            abort(404)
        with _thumb_lock:
            if not thumb.is_file():
                at = "1" if (meta.get("duration") or 0) > 2 else "0"
                subprocess.run(
                    [
                        str(FFMPEG), "-y", "-loglevel", "error", "-ss", at, "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=240:-2", "-q:v", "4", str(thumb),
                    ],
                    capture_output=True,
                )
        if not thumb.is_file():
            abort(404)
    return send_file(thumb, mimetype="image/jpeg", max_age=3600)


# ---------- 课程 API ----------

@app.get("/api/state")
def api_state():
    return jsonify({
        "lessons": [_summary(m) for m in ls.list_lessons()],
        "jobs": jobs.visible_jobs(),
        "cookies": downloader.cookies_status(),
    })


@app.post("/api/lessons/url")
def api_add_url():
    body = request.get_json(silent=True) or {}
    text = body.get("text", "")
    language = langs.normalize_lang(body.get("language"))
    native_lang = langs.normalize_lang(body.get("native_lang"), langs.DEFAULT_NATIVE_LANG)
    try:
        job = jobs.submit_url(text, language=language, native_lang=native_lang)
    except ValueError as e:
        return _err(str(e))
    return jsonify(job.to_dict())


_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@app.post("/api/lessons/file")
def api_add_file():
    f = request.files.get("file")
    if f is None or not f.filename:
        return _err("No media file received")
    language = langs.normalize_lang(request.form.get("language"))
    native_lang = langs.normalize_lang(request.form.get("native_lang"), langs.DEFAULT_NATIVE_LANG)
    name = _BAD_NAME.sub("_", Path(f.filename).name).strip(" .") or "media.mp4"
    UPLOAD_TMP.mkdir(exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=UPLOAD_TMP))
    path = tmp_dir / name
    f.save(path)
    return jsonify(jobs.submit_file(path, name, language=language, native_lang=native_lang).to_dict())


@app.post("/api/lessons/<lesson_id>/retry")
def api_retry(lesson_id: str):
    _lesson_or_404(lesson_id)
    try:
        job = jobs.submit_retry(lesson_id)
    except ValueError as e:
        return _err(str(e))
    return jsonify(job.to_dict())


@app.patch("/api/lessons/<lesson_id>")
def api_rename(lesson_id: str):
    _lesson_or_404(lesson_id)
    title = str((request.get_json(silent=True) or {}).get("title", "")).strip()
    if not title:
        return _err("Title cannot be empty")
    return jsonify(ls.update_meta(lesson_id, title=title[:200]))


@app.delete("/api/lessons/<lesson_id>")
def api_delete(lesson_id: str):
    meta = _lesson_or_404(lesson_id)
    if meta.get("status") != ls.STATUS_ERROR and request.args.get("force") != "1":
        return _err("Only errored lessons can be deleted without force=1")
    if jobs.active_job_for(lesson_id):
        return _err("Lesson is currently processing")
    shutil.rmtree(ls.lesson_dir(lesson_id))
    return jsonify({"ok": True})


@app.get("/api/lessons/<lesson_id>")
def api_lesson(lesson_id: str):
    meta = _lesson_or_404(lesson_id)
    ready = meta.get("status") == ls.STATUS_READY
    d = ls.lesson_dir(lesson_id)
    tr = ls.load(lesson_id, ls.TRANSCRIPT_FILE)
    meta = {**meta, "language": ls.lesson_language(meta, tr), "has_video": (d / ls.VIDEO_FILE).is_file()}
    return jsonify({
        "lesson": meta,
        "transcript": tr,
        "vocab": ls.load(lesson_id, ls.VOCAB_FILE) or ls.empty_vocab(),
        "translation": ls.load(lesson_id, ls.TRANSLATION_FILE),
        "missing": translation.missing(lesson_id) if ready else None,
        "progress": prog.load(lesson_id),
        "recordings": records.list_recordings(lesson_id),
        "record": records.lesson_record(lesson_id) if ready else None,
    })


_edit_lock = threading.Lock()


def _ready_or_err(meta: dict):
    if meta.get("status") != ls.STATUS_READY:
        return _err("Lesson is not ready yet")
    return None


@app.post("/api/lessons/<lesson_id>/vocab")
def api_vocab(lesson_id: str):
    meta = _lesson_or_404(lesson_id)
    if (e := _ready_or_err(meta)):
        return e
    body = request.get_json(silent=True) or {}
    try:
        with _edit_lock:
            v = vocab_mod.set_word(
                lesson_id, str(body.get("key", "")), str(body.get("text", "")), bool(body.get("on", True))
            )
    except ValueError as e:
        return _err(str(e))
    return jsonify({"vocab": v})


@app.post("/api/lessons/<lesson_id>/edit")
def api_edit(lesson_id: str):
    meta = _lesson_or_404(lesson_id)
    if (e := _ready_or_err(meta)):
        return e
    body = request.get_json(silent=True) or {}
    try:
        sid, first, last = int(body["sentence_id"]), int(body["first"]), int(body["last"])
        text = str(body.get("text", ""))[:2000]
    except (KeyError, TypeError, ValueError):
        return _err("Invalid parameters")
    try:
        with _edit_lock:
            transcript_edit.replace_tokens(lesson_id, sid, first, last, text, keep_post=False)
    except (ValueError, FileNotFoundError) as e:
        return _err(str(e))
    return jsonify({
        "transcript": ls.load(lesson_id, ls.TRANSCRIPT_FILE),
        "vocab": ls.load(lesson_id, ls.VOCAB_FILE) or ls.empty_vocab(),
    })


@app.post("/api/lessons/<lesson_id>/subcheck")
def api_subcheck(lesson_id: str):
    _lesson_or_404(lesson_id)
    try:
        job = jobs.submit_subcheck(lesson_id)
    except ValueError as e:
        return _err(str(e))
    return jsonify(job.to_dict())


@app.get("/api/lessons/<lesson_id>/job")
def api_lesson_job(lesson_id: str):
    _lesson_or_404(lesson_id)
    j = jobs.active_job_for(lesson_id) or jobs.last_job_for(lesson_id)
    return jsonify({"job": j.to_dict() if j else None})


@app.post("/api/lessons/<lesson_id>/suggestion/dismiss")
def api_suggestion_dismiss(lesson_id: str):
    meta = _lesson_or_404(lesson_id)
    if (e := _ready_or_err(meta)):
        return e
    body = request.get_json(silent=True) or {}
    try:
        sid, first, last = int(body["sentence_id"]), int(body["first"]), int(body["last"])
    except (KeyError, TypeError, ValueError):
        return _err("Invalid parameters")
    try:
        with _edit_lock:
            tr = transcript_edit.dismiss_suggestion(lesson_id, sid, first, last)
    except (ValueError, FileNotFoundError) as e:
        return _err(str(e))
    return jsonify({"transcript": tr})


# ---------- 翻译导出 / 导入 ----------

@app.get("/api/lessons/<lesson_id>/translation/task")
def api_translation_task(lesson_id: str):
    meta = _lesson_or_404(lesson_id)
    if (e := _ready_or_err(meta)):
        return e
    native_lang = request.args.get("native_lang")
    try:
        return jsonify(
            translation.build_task(
                lesson_id,
                only_missing=request.args.get("only_missing") == "1",
                native_lang=native_lang,
            )
        )
    except ValueError as e:
        return _err(str(e))


@app.post("/api/lessons/<lesson_id>/translation")
def api_translation_import(lesson_id: str):
    meta = _lesson_or_404(lesson_id)
    if (e := _ready_or_err(meta)):
        return e
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", ""))
    native_lang = body.get("native_lang")
    if len(text) > 2_000_000:
        return _err("Payload too large")
    try:
        with _edit_lock:
            t, report = translation.import_text(lesson_id, text, native_lang=native_lang)
            if prog.load(lesson_id)["stage"] == "reading":
                prog.advance(lesson_id, "reading")
    except ValueError as e:
        return _err(str(e))
    return jsonify({"translation": t, "report": report, "missing": translation.missing(lesson_id, t)})


@app.post("/api/lessons/<lesson_id>/translation/entry")
def api_translation_entry(lesson_id: str):
    meta = _lesson_or_404(lesson_id)
    if (e := _ready_or_err(meta)):
        return e
    body = request.get_json(silent=True) or {}
    try:
        with _edit_lock:
            t = translation.set_entry(
                lesson_id, str(body.get("kind")), body.get("id"), body.get("fields") or {}
            )
    except (ValueError, TypeError) as e:
        return _err(str(e))
    return jsonify({"translation": t, "missing": translation.missing(lesson_id, t)})


@app.post("/api/lessons/<lesson_id>/translation/auto")
def api_translation_auto(lesson_id: str):
    """使用用户配置的 AI API 一键翻译并自动导入。"""
    meta = _lesson_or_404(lesson_id)
    if (e := _ready_or_err(meta)):
        return e
    body = request.get_json(silent=True) or {}
    only_missing = bool(body.get("only_missing"))
    native_lang = body.get("native_lang")
    try:
        with _edit_lock:
            t, report = llm.translate_lesson(lesson_id, only_missing=only_missing, native_lang=native_lang)
    except llm.LLMError as e:
        return _err(str(e), 502)
    except ValueError as e:
        return _err(str(e), 400)
    return jsonify({"translation": t, "report": report, "missing": translation.missing(lesson_id, t)})


# ---------- 可选 AI API 设置 ----------

@app.get("/api/llm/config")
def api_llm_get():
    return jsonify(llm.public_config())


@app.post("/api/llm/config")
def api_llm_save():
    body = request.get_json(silent=True) or {}
    try:
        cfg = llm.save_config(
            str(body.get("provider", "deepseek")),
            body.get("api_key"),
            body.get("model"),
            body.get("base_url"),
        )
    except ValueError as e:
        return _err(str(e))
    return jsonify(cfg)


@app.delete("/api/llm/config")
def api_llm_clear():
    return jsonify(llm.clear_config())


@app.post("/api/llm/test")
def api_llm_test():
    body = request.get_json(silent=True) or {}
    try:
        r = llm.test_connection(
            body.get("provider"), body.get("api_key"), body.get("model"), body.get("base_url")
        )
    except (llm.LLMError, ValueError) as e:
        return _err(str(e), 502)
    return jsonify(r)


@app.post("/api/llm/models")
def api_llm_models():
    """动态拉取当前服务商账号可用的模型 ID 列表。"""
    body = request.get_json(silent=True) or {}
    try:
        models = llm.list_models(body.get("provider"), body.get("api_key"), body.get("base_url"))
    except (llm.LLMError, ValueError) as e:
        return _err(str(e), 502)
    return jsonify({"models": models})


# ---------- 练习进度 / 录音 ----------

@app.post("/api/lessons/<lesson_id>/progress")
def api_progress(lesson_id: str):
    meta = _lesson_or_404(lesson_id)
    if (e := _ready_or_err(meta)):
        return e
    body = request.get_json(silent=True) or {}
    action, stage = body.get("action"), str(body.get("stage", ""))
    try:
        with _edit_lock:
            if action == "advance":
                p = prog.advance(lesson_id, stage)
            elif action == "skip":
                p = prog.advance(lesson_id, stage, skipped=True)
            elif action == "round":
                p = prog.add_round(lesson_id, stage, body.get("round") or {})
            else:
                return _err("Invalid action")
    except ValueError as e:
        return _err(str(e))
    return jsonify({"progress": p, "record": records.lesson_record(lesson_id)})


@app.post("/api/lessons/<lesson_id>/recordings")
def api_recording_add(lesson_id: str):
    meta = _lesson_or_404(lesson_id)
    if (e := _ready_or_err(meta)):
        return e
    try:
        sid = int(request.args.get("sentence_id", ""))
    except ValueError:
        return _err("Missing sentence_id")
    if (request.content_length or 0) > records.MAX_REC_BYTES:
        return _err("Recording too large")
    try:
        rec = records.save_recording(lesson_id, sid, request.get_data(), request.mimetype)
    except ValueError as e:
        return _err(str(e))
    return jsonify({"recording": rec, "recordings": records.list_recordings(lesson_id)})


@app.delete("/api/lessons/<lesson_id>/recordings/<name>")
def api_recording_delete(lesson_id: str, name: str):
    _lesson_or_404(lesson_id)
    p = records.recording_path(lesson_id, name)
    if p is None:
        return _err("Recording not found", 404)
    p.unlink()
    return jsonify({"recordings": records.list_recordings(lesson_id)})


# ---------- 多语言总生词本 ----------

@app.get("/api/vocabbook")
def api_vocabbook():
    lang = request.args.get("lang")
    return jsonify({"words": review.book(lang), "intervals": review.INTERVALS})


@app.post("/api/vocabbook/entry")
def api_vocabbook_entry():
    body = request.get_json(silent=True) or {}
    lang = body.get("lang")
    try:
        with _edit_lock:
            old_id = str(body.get("id", ""))
            r = records.set_entry_lemma(old_id, str(body.get("lemma", "")), str(body.get("pos", "")))
            review.migrate(old_id, r["id"])
    except ValueError as e:
        return _err(str(e))
    return jsonify({**r, "words": review.book(lang)})


# ---------- 生词复习 ----------

@app.get("/api/review/summary")
def api_review_summary():
    lang = request.args.get("lang")
    return jsonify(review.summary(lang=lang))


@app.get("/api/review/session")
def api_review_session():
    lang = request.args.get("lang")
    try:
        size = int(request.args["size"]) if request.args.get("size") else None
        new = int(request.args["new"]) if request.args.get("new") else None
    except ValueError:
        return _err("Invalid parameters")
    return jsonify(review.session(size, new, lang=lang))


@app.post("/api/review/free")
def api_review_free():
    body = request.get_json(silent=True) or {}
    ids = body.get("ids")
    if not isinstance(ids, list):
        return _err("Invalid parameters")
    try:
        return jsonify(review.free_items(ids, body.get("mode"), lang=body.get("lang")))
    except ValueError as e:
        return _err(str(e))


@app.post("/api/review/answer")
def api_review_answer():
    body = request.get_json(silent=True) or {}
    try:
        r = review.answer(
            str(body.get("id", "")),
            str(body.get("mode", "")),
            str(body.get("result", "")),
            bool(body.get("free")),
        )
    except ValueError as e:
        return _err(str(e))
    return jsonify(r)


@app.post("/api/review/settings")
def api_review_settings():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({
            "settings": review.set_settings(body.get("session_size"), body.get("new_per_session"))
        })
    except (ValueError, TypeError) as e:
        return _err(str(e))


@app.post("/api/jobs/<int:job_id>/dismiss")
def api_dismiss(job_id: int):
    jobs.dismiss(job_id)
    return jsonify({"ok": True})


# ---------- 启动 ----------

def _port_owner(port: int) -> str | None:
    import json
    import socket
    import urllib.request
    with socket.socket() as sock:
        sock.settimeout(0.5)
        if sock.connect_ex(("127.0.0.1", port)) != 0:
            return None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as r:
            return "self" if "lessons" in json.loads(r.read().decode("utf-8")) else "other"
    except Exception:  # noqa: BLE001
        return "other"


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Start ClipLingua local server")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--open", action="store_true", help="Open browser automatically")
    a = ap.parse_args()

    url = f"http://localhost:{a.port}"
    owner = _port_owner(a.port)
    if owner == "self":
        print(f"  ClipLingua is already running at {url}")
        webbrowser.open(url)
        return
    if owner == "other":
        print(f"  Port {a.port} is already in use. Try: python app/server.py --open --port {a.port + 1}")
        sys.exit(1)

    shutil.rmtree(UPLOAD_TMP, ignore_errors=True)
    n = jobs.recover_interrupted()
    if n:
        print(f"  Recovered {n} interrupted lesson(s).")
    print(f"\n  ClipLingua is running at: {url}\n  Press Ctrl+C or close this window to stop.\n")
    if a.open:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    import logging
    import flask.cli
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    flask.cli.show_server_banner = lambda *args, **kwargs: None
    app.run(host="127.0.0.1", port=a.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
