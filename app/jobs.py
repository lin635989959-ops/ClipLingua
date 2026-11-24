"""后台任务队列：主队列执行「媒体导入 → 人声分离与转写」，独立后台队列异步执行「画面字幕 OCR 校对」。"""
from __future__ import annotations

import itertools
import os
import queue
import re
import shutil
import threading
import traceback
from pathlib import Path

import downloader
import languages as langs
import lesson_store as ls
import subtitles
import transcriber

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "error"

_URL_RE = re.compile(r"https?://[^\s<>\"'，。、）)]+")


def _auto_ocr_enabled() -> bool:
    """是否允许在转写完成后自动派发后台字幕校对（可通过 CLIPLINGUA_AUTO_OCR=0 关闭）。"""
    return os.environ.get("CLIPLINGUA_AUTO_OCR", "1").strip().lower() not in ("0", "false", "no", "off")


def extract_url(text: str) -> str | None:
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(".,;!?") if m else None


class Job:
    def __init__(
        self,
        kind: str,
        label: str,
        arg: str,
        lesson_id: str | None = None,
        language: str = langs.DEFAULT_TARGET_LANG,
        native_lang: str = langs.DEFAULT_NATIVE_LANG,
        fetch_captions: bool = True,
    ):
        self.id = next(_ids)
        self.kind = kind            # file / url / retry / subs
        self.label = label
        self.arg = arg
        self.lesson_id = lesson_id
        self.language = langs.normalize_lang(language)
        self.native_lang = langs.normalize_lang(native_lang, langs.DEFAULT_NATIVE_LANG)
        self.fetch_captions = fetch_captions
        self.state = QUEUED
        self.progress = 0.0
        self.message = "Queued…"
        self.error: str | None = None
        self.dismissed = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "lesson_id": self.lesson_id,
            "language": self.language,
            "state": self.state,
            "progress": round(self.progress, 1),
            "message": self.message,
            "error": self.error,
        }


_ids = itertools.count(1)
_jobs: dict[int, Job] = {}
_queue: queue.Queue[Job] = queue.Queue()
_sub_queue: queue.Queue[Job] = queue.Queue()
_lock = threading.Lock()
_worker: threading.Thread | None = None
_sub_worker: threading.Thread | None = None


def _submit(job: Job) -> Job:
    global _worker, _sub_worker
    with _lock:
        _jobs[job.id] = job
        if job.kind == "subs":
            if _sub_worker is None or not _sub_worker.is_alive():
                _sub_worker = threading.Thread(target=_work_loop, args=(_sub_queue,), name="subcheck-worker", daemon=True)
                _sub_worker.start()
            target_q = _sub_queue
        else:
            if _worker is None or not _worker.is_alive():
                _worker = threading.Thread(target=_work_loop, args=(_queue,), name="lesson-worker", daemon=True)
                _worker.start()
            target_q = _queue
    target_q.put(job)
    return job


def submit_url(
    text: str,
    language: str = langs.DEFAULT_TARGET_LANG,
    native_lang: str = langs.DEFAULT_NATIVE_LANG,
) -> Job:
    url = extract_url(text)
    if not url:
        raise ValueError("Please enter a valid http(s):// public media URL")
    return _submit(Job("url", url, url, language=language, native_lang=native_lang))


def submit_file(
    path: Path,
    label: str,
    language: str = langs.DEFAULT_TARGET_LANG,
    native_lang: str = langs.DEFAULT_NATIVE_LANG,
) -> Job:
    return _submit(Job("file", label, str(path), language=language, native_lang=native_lang))


def submit_retry(lesson_id: str) -> Job:
    meta = ls.load(lesson_id, ls.LESSON_FILE)
    if meta is None:
        raise FileNotFoundError(lesson_id)
    if active_job_for(lesson_id):
        raise ValueError("Lesson is already processing")
    has_audio = (ls.lesson_dir(lesson_id) / ls.AUDIO_FILE).is_file()
    if not has_audio and not str(meta.get("source_url", "")).startswith("http"):
        raise ValueError("Incomplete local media file; please delete and re-import")
    return _submit(
        Job(
            "retry",
            meta.get("title") or lesson_id,
            lesson_id,
            lesson_id,
            language=ls.lesson_language(meta),
            native_lang=meta.get("native_lang", langs.DEFAULT_NATIVE_LANG),
        )
    )


def submit_subcheck(lesson_id: str) -> Job:
    meta = ls.load(lesson_id, ls.LESSON_FILE)
    if meta is None:
        raise FileNotFoundError(lesson_id)
    if meta.get("status") != ls.STATUS_READY:
        raise ValueError("Lesson is not ready yet")
    if active_job_for(lesson_id):
        raise ValueError("Lesson is already processing")
    return _submit(Job("subs", meta.get("title") or lesson_id, lesson_id, lesson_id, language=ls.lesson_language(meta)))


def _enqueue_auto_subcheck(meta: dict) -> Job | None:
    """转写完成后在独立后台队列派发字幕校对，不阻塞课程进入学习与后续视频导入。"""
    if not _auto_ocr_enabled():
        return None
    lid = meta["id"]
    d = ls.lesson_dir(lid)
    if not (d / ls.VIDEO_FILE).is_file() and not (d / subtitles.CAPTIONS_FILE).is_file():
        return None
    if active_job_for(lid):
        return None
    # 下载阶段已尝试提取平台字幕轨，自动派发时不再重复发起 yt-dlp 网络请求
    return _submit(
        Job(
            "subs",
            meta.get("title") or lid,
            lid,
            lid,
            language=ls.lesson_language(meta),
            fetch_captions=False,
        )
    )


def _subcheck(lesson_id: str, progress, fetch_captions: bool = True) -> None:
    d = ls.lesson_dir(lesson_id)
    if not (d / ls.VIDEO_FILE).is_file() and not (d / subtitles.CAPTIONS_FILE).is_file():
        progress(100, "Audio-only lesson ready")
        return
    try:
        subtitles.check_lesson(lesson_id, progress, fetch_captions=fetch_captions)
        ls.update_meta(lesson_id, subcheck_error=None)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ls.update_meta(lesson_id, subcheck_error=f"Subtitle check failed: {e}"[:500])


def active_job_for(lesson_id: str) -> Job | None:
    with _lock:
        return next(
            (j for j in _jobs.values() if j.lesson_id == lesson_id and j.state in (QUEUED, RUNNING)),
            None,
        )


def last_job_for(lesson_id: str) -> Job | None:
    with _lock:
        return max((j for j in _jobs.values() if j.lesson_id == lesson_id), key=lambda j: j.id, default=None)


def visible_jobs() -> list[dict]:
    with _lock:
        return [
            j.to_dict() for j in _jobs.values()
            if j.state in (QUEUED, RUNNING) or (j.state == FAILED and not j.dismissed)
        ]


def dismiss(job_id: int) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].dismissed = True


def recover_interrupted() -> int:
    n = 0
    for meta in ls.list_lessons():
        if meta.get("status") in (ls.STATUS_NEW, ls.STATUS_DOWNLOADING, ls.STATUS_TRANSCRIBING):
            ls.update_meta(
                meta["id"],
                status=ls.STATUS_ERROR,
                error="Interrupted when server stopped. Click Retry to resume.",
            )
            n += 1
    return n


def _work_loop(q: queue.Queue[Job]) -> None:
    while True:
        job = q.get()
        job.state = RUNNING
        try:
            _run(job)
            job.state, job.progress, job.message = DONE, 100, "Done"
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            job.state, job.error = FAILED, str(e)[:500]
            job.message = "Error"


def _run(job: Job) -> None:
    def set_lesson(lid: str) -> None:
        job.lesson_id = lid

    def dl(p: float, m: str) -> None:
        job.progress, job.message = p * 0.35, m

    def tr(p: float, m: str) -> None:
        job.progress, job.message = 35 + p * 0.65, m

    def only(p: float, m: str) -> None:
        job.progress, job.message = p, m

    if job.kind == "subs":
        _subcheck(job.arg, only, fetch_captions=job.fetch_captions)
        return

    if job.kind == "url":
        meta = downloader.download(
            job.arg, dl, on_lesson=set_lesson, language=job.language, native_lang=job.native_lang
        )
    elif job.kind == "file":
        try:
            meta = downloader.import_file(
                job.arg, dl, on_lesson=set_lesson, language=job.language, native_lang=job.native_lang
            )
        finally:
            shutil.rmtree(Path(job.arg).parent, ignore_errors=True)
    else:  # retry
        old = ls.load(job.arg, ls.LESSON_FILE)
        if (ls.lesson_dir(job.arg) / ls.AUDIO_FILE).is_file():
            meta = old
        else:
            meta = downloader.download(
                old["source_url"], dl, reuse=False, on_lesson=set_lesson,
                language=ls.lesson_language(old),
                native_lang=old.get("native_lang", langs.DEFAULT_NATIVE_LANG),
            )
            shutil.rmtree(ls.lesson_dir(job.arg), ignore_errors=True)

    job.lesson_id = meta["id"]
    if meta.get("status") != ls.STATUS_READY:
        transcriber.transcribe_lesson(meta["id"], tr)
        job.state, job.progress, job.message = DONE, 100, "Done"
        latest = ls.load(meta["id"], ls.LESSON_FILE) or meta
        _enqueue_auto_subcheck(latest)
