"""媒体导入模块：支持导入本地音视频文件（首选）及合法公开视频链接（通过标准 yt-dlp）。

合规说明：本工具仅用于用户拥有合法使用权或采用开放许可（如 Creative Commons、公有领域、
个人录制）的音视频素材进行离线个人语言学习，不提供或托管任何受版权保护的内容。
"""
from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import languages as langs  # noqa: E402
import lesson_store as ls  # noqa: E402
from env_setup import FFMPEG, ROOT  # noqa: E402

ProgressFn = Callable[[float, str], None]

# 可选的 Netscape 格式 cookies 文件（标准 yt-dlp 参数，仅供本机使用）
COOKIES_FILE = ROOT / "cookies.txt"

# 优先选择浏览器原生支持的 H.264 编码，不兼容时本地转码
FORMAT = "b[vcodec^=h264]/b[vcodec^=avc]/b"
H264_ARGS = [
    "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-movflags", "+faststart",
]
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus", ".wma"}


class DownloadError(Exception):
    pass


def _noop(_p: float, _m: str) -> None:
    pass


def _clean_error(e: Exception) -> str:
    msg = str(e).replace("ERROR: ", "").strip()
    for cut in ("; please report this issue", "; please report", ". Confirm you are on"):
        msg = msg.split(cut)[0]
    return msg[:300]


def cookies_status() -> dict:
    """检查本地是否放置了可选的 cookies.txt 文件（不解析账号私密字段）。"""
    return {"present": COOKIES_FILE.is_file()}


@contextlib.contextmanager
def _cookie_opts() -> Iterator[dict | None]:
    """若本地存在 cookies.txt，使用临时副本传给 yt-dlp 并在结束后立即清理。"""
    if not COOKIES_FILE.is_file():
        yield None
        return
    fd, tmp = tempfile.mkstemp(prefix="cl_cookies_", suffix=".txt")
    os.close(fd)
    try:
        shutil.copyfile(COOKIES_FILE, tmp)
        yield {"cookiefile": tmp}
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def explain_error(e: Exception) -> str:
    """将常见网络或格式错误整理为易读的提示信息。"""
    raw = _clean_error(e)
    low = raw.lower()
    has = lambda *keys: any(k in low for k in keys)  # noqa: E731

    if has("unsupported url"):
        tip = "Unsupported media URL. Please provide a direct public video/audio URL or import a local file."
    elif has("requested format is not available", "no video formats"):
        tip = "No playable video/audio stream found at this URL."
    elif has("http error 404", "not available", "has been removed"):
        tip = "The media resource is unavailable or has been removed."
    elif has("timed out", "getaddrinfo", "connection", "unable to download", "network", "ssl"):
        tip = "Network connection failed. Check your internet connection or import a local file instead."
    else:
        tip = "Import failed. You can download/export your own media file and import it locally."
    return f"{tip}\n({raw})"


class _SilentLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _run_ffmpeg(args: list[str]) -> None:
    cmd = [str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", *args]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise DownloadError(f"ffmpeg error: {r.stderr.strip()[-500:]}")


def _probe(media: Path) -> dict:
    """用 ffmpeg 解析媒体编码与时长（无需单独依赖 ffprobe）。"""
    r = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-i", str(media)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    info = {"vcodec": None, "duration": None}
    for line in r.stderr.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            hms = line.split(",")[0].split()[1]
            try:
                h, m, s = hms.split(":")
                info["duration"] = round(int(h) * 3600 + int(m) * 60 + float(s), 2)
            except ValueError:
                pass
        elif "Video:" in line and info["vcodec"] is None:
            info["vcodec"] = line.split("Video:")[1].split()[0].strip(",")
    return info


def _finish_video(d: Path, progress: ProgressFn) -> float | None:
    """确保 video.mp4 为浏览器通用 H.264 编码，并提取 16kHz 单声道 audio.wav。"""
    video = d / ls.VIDEO_FILE
    info = _probe(video)
    if info["vcodec"] not in ("h264", None):
        progress(85, f"Transcoding {info['vcodec']} to H.264…")
        tmp = d / "video_h264.mp4"
        _run_ffmpeg(["-i", str(video), *H264_ARGS, str(tmp)])
        tmp.replace(video)
    progress(92, "Extracting 16kHz audio…")
    _run_ffmpeg(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(d / ls.AUDIO_FILE)])
    return info["duration"]


def find_existing(video_id: str, language: str) -> dict | None:
    """同一视频且同学习语言已导入过（非错误状态）则直接复用。"""
    code = langs.normalize_lang(language)
    for meta in ls.list_lessons():
        if (
            video_id
            and meta.get("video_id") == video_id
            and ls.lesson_language(meta) == code
            and meta.get("status") != ls.STATUS_ERROR
        ):
            return meta
    return None


def import_file(
    path: str | Path,
    progress: ProgressFn = _noop,
    on_lesson: Callable[[str], None] | None = None,
    language: str = langs.DEFAULT_TARGET_LANG,
    native_lang: str = langs.DEFAULT_NATIVE_LANG,
) -> dict:
    """导入本地视频或音频文件（推荐方式，支持 MP4/MOV/WEBM/MKV/MP3/WAV/M4A/OGG/FLAC）。"""
    src = Path(path)
    if not src.is_file():
        raise DownloadError(f"File not found: {src}")
    meta = ls.create_lesson(f"file:{src.name}", None, language=language, native_lang=native_lang)
    lid = meta["id"]
    if on_lesson:
        on_lesson(lid)
    d = ls.lesson_dir(lid)
    ls.update_meta(lid, status=ls.STATUS_DOWNLOADING, title=src.stem[:200])
    try:
        is_audio_only = src.suffix.lower() in AUDIO_EXTS
        if is_audio_only:
            progress(40, "Converting audio…")
            _run_ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(d / ls.AUDIO_FILE)])
            duration = _probe(d / ls.AUDIO_FILE)["duration"]
        else:
            progress(15, "Preparing video…")
            if src.suffix.lower() == ".mp4":
                shutil.copyfile(src, d / ls.VIDEO_FILE)
            else:
                _run_ffmpeg(["-i", str(src), *H264_ARGS, str(d / ls.VIDEO_FILE)])
            duration = _finish_video(d, progress)
    except Exception as e:  # noqa: BLE001
        ls.update_meta(lid, status=ls.STATUS_ERROR, error=str(e)[:1000])
        raise
    meta = ls.update_meta(lid, status=ls.STATUS_DOWNLOADED, duration=duration, error=None)
    progress(100, "Import complete")
    return meta


def download(
    url: str,
    progress: ProgressFn = _noop,
    reuse: bool = True,
    on_lesson: Callable[[str], None] | None = None,
    language: str = langs.DEFAULT_TARGET_LANG,
    native_lang: str = langs.DEFAULT_NATIVE_LANG,
) -> dict:
    """从公开媒体链接导入课程（需确保对该内容拥有合法学习使用权）。"""
    try:
        import yt_dlp
    except ImportError as e:
        raise DownloadError("yt-dlp is not installed. Import a local media file or install yt-dlp.") from e

    url = url.strip()
    progress(2, "Fetching media metadata…")
    base_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ffmpeg_location": str(FFMPEG.parent),
        "logger": _SilentLogger(),
    }
    with _cookie_opts() as cookie_opts:
        return _download(yt_dlp, url, base_opts, cookie_opts, progress, reuse, on_lesson, language, native_lang)


def _download(
    yt_dlp,
    url: str,
    base_opts: dict,
    cookie_opts: dict | None,
    progress: ProgressFn,
    reuse: bool,
    on_lesson: Callable[[str], None] | None,
    language: str,
    native_lang: str,
) -> dict:
    try:
        with yt_dlp.YoutubeDL(base_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        if cookie_opts is None:
            raise DownloadError(explain_error(e)) from e
        progress(3, "Retrying with local cookies.txt…")
        base_opts = {**base_opts, **cookie_opts}
        try:
            with yt_dlp.YoutubeDL(base_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e2:  # noqa: BLE001
            raise DownloadError(explain_error(e2)) from e2

    video_id = str(info.get("id") or "")
    if reuse and (old := find_existing(video_id, language)):
        progress(100, "Lesson already exists")
        return old

    meta = ls.create_lesson(url, video_id, language=language, native_lang=native_lang)
    lid = meta["id"]
    if on_lesson:
        on_lesson(lid)
    d = ls.lesson_dir(lid)
    title = (info.get("title") or info.get("description") or "").strip()
    ls.update_meta(
        lid,
        status=ls.STATUS_DOWNLOADING,
        title=title[:200],
        author=info.get("uploader") or info.get("channel") or "",
        source_url=info.get("webpage_url") or url,
    )

    def hook(s: dict) -> None:
        if s.get("status") == "downloading":
            total = s.get("total_bytes") or s.get("total_bytes_estimate") or 0
            if total:
                progress(5 + 75 * s.get("downloaded_bytes", 0) / total, "Downloading media…")

    try:
        opts = {
            **base_opts,
            "format": FORMAT,
            "outtmpl": str(d / "video.%(ext)s"),
            "merge_output_format": "mp4",
            "progress_hooks": [hook],
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        got = sorted(d.glob("video.*"))
        if not got:
            raise DownloadError("Download finished but no media file was produced")
        if got[0].suffix.lower() in AUDIO_EXTS:
            _run_ffmpeg(["-i", str(got[0]), "-vn", "-ac", "1", "-ar", "16000", str(d / ls.AUDIO_FILE)])
            got[0].unlink()
            duration = _probe(d / ls.AUDIO_FILE)["duration"] or info.get("duration")
        else:
            if got[0].name != ls.VIDEO_FILE:
                _run_ffmpeg(["-i", str(got[0]), "-c", "copy", str(d / ls.VIDEO_FILE)])
                got[0].unlink()
            duration = _finish_video(d, progress) or info.get("duration")
    except Exception as e:  # noqa: BLE001
        msg = e.args[0] if isinstance(e, DownloadError) else explain_error(e)
        ls.update_meta(lid, status=ls.STATUS_ERROR, error=msg[:1000])
        raise DownloadError(msg) from e

    # 可选提取对应目标语言的平台字幕轨
    progress(96, "Checking caption tracks…")
    try:
        import subtitles
        with yt_dlp.YoutubeDL(base_opts) as ydl:
            subtitles.save_caption_track(lid, info, ydl, lang=language)
    except Exception:  # noqa: BLE001
        pass

    meta = ls.update_meta(lid, status=ls.STATUS_DOWNLOADED, duration=duration, error=None)
    progress(100, "Download complete")
    return meta


def _cli() -> None:
    ap = argparse.ArgumentParser(description="Import local media or public URL into ClipLingua")
    ap.add_argument("url", nargs="?", help="Public media URL")
    ap.add_argument("--file", help="Local video or audio file path (recommended)")
    ap.add_argument("--lang", default="sv", choices=list(langs.SUPPORTED_LANGS), help="Target learning language")
    ap.add_argument("--native", default="zh", choices=list(langs.SUPPORTED_LANGS), help="Native/translation language")
    ap.add_argument("--no-reuse", action="store_true", help="Do not reuse existing lesson")
    a = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            s.reconfigure(encoding="utf-8", errors="replace")
    if not a.url and not a.file:
        ap.error("Provide --file <path> or a public URL")

    def show(p: float, msg: str) -> None:
        print(f"  [{p:5.1f}%] {msg}")

    try:
        meta = (
            import_file(a.file, show, language=a.lang, native_lang=a.native)
            if a.file
            else download(a.url, show, reuse=not a.no_reuse, language=a.lang, native_lang=a.native)
        )
    except DownloadError as e:
        print(f"Error: {e}")
        sys.exit(1)
    print(f"\nCreated lesson {meta['id']} [{meta['language']}] - {meta['title']}")


if __name__ == "__main__":
    _cli()
