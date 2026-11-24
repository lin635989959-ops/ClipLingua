"""课程数据存取：每个音视频课程 = lessons/ 下的一个文件夹，内含标准 JSON 与媒体文件。

所有写入均采用「先写临时文件再原子替换」策略，避免异常中断导致数据损坏。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from env_setup import LESSONS_DIR as _DEFAULT_LESSONS_DIR
import languages as langs

SCHEMA_VERSION = 2

# 测试或自定义部署时可通过环境变量重定向课程存储目录
LESSONS_DIR = Path(
    os.environ.get("CLIPLINGUA_LESSONS_DIR")
    or os.environ.get("SV_LESSONS_DIR")
    or _DEFAULT_LESSONS_DIR
)

# 课程文件夹内的标准文件名
LESSON_FILE = "lesson.json"            # 课程元数据（标题、语言、处理状态等）
TRANSCRIPT_FILE = "transcript.json"    # 原文逐词时间戳与分句
VOCAB_FILE = "vocab.json"              # 点选的本课生词
TRANSLATION_FILE = "translation.json"  # 导入的译文与词条解析
PROGRESS_FILE = "progress.json"        # 各学习阶段与练习记录
VIDEO_FILE = "video.mp4"               # 兼容浏览器的 H.264 视频（纯音频课程无此文件）
AUDIO_FILE = "audio.wav"               # 16kHz 单声道识别与回放音频
THUMB_FILE = "thumb.jpg"               # 首页缩略图（自动生成，可删并重建）
CLIPS_DIR = "clips"
RECORDINGS_DIR = "recordings"          # 用户跟读录音

# 处理状态（lesson.json 的 status）
STATUS_NEW = "new"
STATUS_DOWNLOADING = "downloading"
STATUS_DOWNLOADED = "downloaded"
STATUS_TRANSCRIBING = "transcribing"
STATUS_READY = "ready"
STATUS_ERROR = "error"

# 学习阶段（progress.json 的 stage），按顺序推进
STAGES = ["reading", "translate", "listen", "recall", "spelling", "read_aloud", "done"]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------- 原子 JSON 读写 ----------

def read_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------- 课程管理 ----------

_SAFE = re.compile(r"[^0-9A-Za-z_-]+")


def lesson_dir(lesson_id: str) -> Path:
    """返回课程目录路径，严格校验 ID 字符防止路径穿越。"""
    if not lesson_id or _SAFE.search(lesson_id):
        raise ValueError(f"Invalid lesson id: {lesson_id!r}")
    return LESSONS_DIR / lesson_id


def new_lesson_id(video_id: str | None = None) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = _SAFE.sub("", video_id or "")[:32]
    base = f"{stamp}_{suffix}" if suffix else stamp
    # 同秒并发创建时追加计数后缀避免目录冲突
    cand = base
    n = 1
    while (LESSONS_DIR / cand).exists():
        n += 1
        cand = f"{base}_{n}"
    return cand


def create_lesson(
    source_url: str,
    video_id: str | None = None,
    language: str = langs.DEFAULT_TARGET_LANG,
    native_lang: str = langs.DEFAULT_NATIVE_LANG,
) -> dict:
    """初始化课程目录及基础元数据文件。"""
    lang = langs.normalize_lang(language, langs.DEFAULT_TARGET_LANG)
    native = langs.normalize_lang(native_lang, langs.DEFAULT_NATIVE_LANG)
    lesson_id = new_lesson_id(video_id)
    d = lesson_dir(lesson_id)
    d.mkdir(parents=True, exist_ok=False)
    (d / RECORDINGS_DIR).mkdir()
    meta = {
        "schema_version": SCHEMA_VERSION,
        "id": lesson_id,
        "title": "",
        "author": "",
        "language": lang,
        "native_lang": native,
        "source_url": source_url,
        "video_id": video_id or "",
        "duration": None,
        "created_at": now_iso(),
        "status": STATUS_NEW,
        "error": None,
    }
    write_json(d / LESSON_FILE, meta)
    write_json(d / VOCAB_FILE, empty_vocab())
    write_json(d / PROGRESS_FILE, empty_progress())
    return meta


def lesson_language(meta: dict | None, transcript: dict | None = None) -> str:
    """解析课程目标学习语言（兼容无 language 字段的旧数据，默认瑞典语）。"""
    if meta and meta.get("language"):
        return langs.normalize_lang(meta["language"])
    if transcript and transcript.get("language"):
        return langs.normalize_lang(transcript["language"])
    return langs.DEFAULT_TARGET_LANG


def load(lesson_id: str, name: str, default=None):
    return read_json(lesson_dir(lesson_id) / name, default)


def save(lesson_id: str, name: str, data) -> None:
    write_json(lesson_dir(lesson_id) / name, data)


def update_meta(lesson_id: str, **fields) -> dict:
    meta = load(lesson_id, LESSON_FILE)
    if meta is None:
        raise FileNotFoundError(lesson_id)
    meta.update(fields)
    save(lesson_id, LESSON_FILE, meta)
    return meta


def list_lessons() -> list[dict]:
    """返回所有课程的元数据，按创建时间倒序排列。"""
    if not LESSONS_DIR.is_dir():
        return []
    out = []
    for d in LESSONS_DIR.iterdir():
        meta = read_json(d / LESSON_FILE) if d.is_dir() else None
        if meta:
            meta.setdefault("language", langs.DEFAULT_TARGET_LANG)
            meta.setdefault("native_lang", langs.DEFAULT_NATIVE_LANG)
            out.append(meta)
    return sorted(out, key=lambda m: m["id"], reverse=True)


# ---------- 默认空结构 ----------

def empty_vocab() -> dict:
    return {"schema_version": SCHEMA_VERSION, "words": []}


def empty_progress() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "reading",
        "stages": {s: {} for s in STAGES if s != "done"},
        "completed_at": None,
    }


# ---------- 单词切分与归一化 ----------

def split_token(raw: str) -> tuple[str, str, str]:
    return langs.split_token(raw)


def normalize_word(word: str) -> str:
    """生词统一 key：小写并剥离首尾标点。Hej, / HEJ! → hej；「学习」→ 学习。"""
    return split_token(word)[1].lower()


def join_tokens(tokens: list[dict], lang: str = "sv") -> str:
    return langs.join_tokens(tokens, lang)
