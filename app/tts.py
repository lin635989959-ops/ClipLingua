"""多语言单词/例句语音合成（TTS）：支持中文、西班牙语、英语、瑞典语。

- 在线神经语音：使用 edge-tts（按需联网合成一次后写入 tts_cache/，后续复用本地缓存）。
- 离线备用语音：使用 piper-tts（读取 models/tts/ 下对应语言的 .onnx 模型，完全离线运行）。
- 响度均衡：统一通过 ffmpeg speechnorm 处理，保证不同声音、单词和句子响度一致。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from env_setup import FFMPEG, MODELS_DIR, ROOT  # noqa: E402
import languages as langs  # noqa: E402

CACHE_DIR = ROOT / "tts_cache"
TTS_MODELS_DIR = MODELS_DIR / "tts"

# 构建所有语言的语音注册表：voice_id -> (lang, engine, target)
VOICES: dict[str, tuple[str, str, str]] = {}
for _code, _cfg in langs.LANG_CONFIG.items():
    for _v in _cfg["voices"]:
        _target = _v["voice"] if _v["engine"] == "edge" else _v["model"]
        VOICES[_v["id"]] = (_code, _v["engine"], _target)

DEFAULT_VOICE = langs.LANG_CONFIG[langs.DEFAULT_TARGET_LANG]["default_voice"]
MAX_CHARS = 400
EDGE_SLOW_RATE = "-30%"
PIPER_SLOW_SCALE = 1.4
NORMALIZE_FILTER = "speechnorm=e=6.25:r=0.00001:l=1,adelay=60:all=1"
EDGE_VOLUME = "+50%"
EDGE_RETRY_AFTER = 60


class TTSError(Exception):
    pass


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_edge_failed_at = 0.0
_piper_voices: dict[str, object] = {}
_piper_lock = threading.Lock()


def _key_lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def resolve_voice(voice: str | None, lang: str | None = None) -> str:
    """将传入的 voice ID 或旧短名解析为有效的标准 voice ID。"""
    if voice:
        v = langs.LEGACY_VOICE_ALIAS.get(voice, voice)
        if v in VOICES:
            return v
    cfg = langs.get_lang(lang)
    return cfg["default_voice"]


def _clean(text: str) -> str:
    text = " ".join(str(text or "").split())
    if not text:
        raise TTSError("Text is empty")
    if len(text) > MAX_CHARS:
        raise TTSError(f"Text exceeds {MAX_CHARS} characters")
    return text


def cache_path(text: str, voice: str, slow: bool) -> Path:
    h = hashlib.sha1(f"{voice}|{int(slow)}|{text}".encode("utf-8")).hexdigest()[:20]
    return CACHE_DIR / f"{voice}{'_slow' if slow else ''}_{h}.mp3"


def _normalize(raw: Path, out: Path) -> None:
    """响度均衡并统一输出为 24kHz 单声道 MP3。"""
    if not FFMPEG.is_file():
        os.replace(raw, out)
        return
    r = subprocess.run(
        [
            str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
            "-af", NORMALIZE_FILTER, "-ar", "24000", "-ac", "1",
            "-c:a", "libmp3lame", "-q:a", "4", "-f", "mp3", str(out),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r.returncode != 0 or not out.is_file():
        out.unlink(missing_ok=True)
        os.replace(raw, out)


# ---------- Edge 在线神经语音 ----------

def _edge(text: str, voice_name: str, slow: bool, out: Path) -> None:
    import edge_tts

    async def go():
        com = edge_tts.Communicate(
            text, voice_name,
            rate=EDGE_SLOW_RATE if slow else "+0%",
            volume=EDGE_VOLUME,
            connect_timeout=5, receive_timeout=10,
        )
        await com.save(str(out))

    asyncio.run(go())
    if not out.is_file() or out.stat().st_size < 500:
        raise TTSError("Online TTS returned empty audio")


# ---------- Piper 离线语音 ----------

def _espeak_dir() -> Path:
    """当项目目录含非 ASCII 字符时，将 espeak-ng-data 复制到纯 ASCII 本地缓存路径。"""
    import piper
    src = Path(piper.__file__).parent / "espeak-ng-data"
    try:
        str(src).encode("ascii")
        return src
    except UnicodeEncodeError:
        pass
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    try:
        from importlib.metadata import version
        ver = version("piper-tts")
    except Exception:  # noqa: BLE001
        ver = "x"
    dst = base / "ClipLingua" / f"espeak-ng-data-{ver}"
    try:
        str(dst).encode("ascii")
    except UnicodeEncodeError:
        raise TTSError("Offline TTS requires an ASCII-only cache directory") from None
    if not (dst / "phontab").is_file():
        tmp = dst.with_name(dst.name + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(src, tmp)
        shutil.rmtree(dst, ignore_errors=True)
        tmp.rename(dst)
    return dst


def piper_model_path(model_file: str) -> Path:
    return TTS_MODELS_DIR / model_file


def _piper(text: str, model_file: str, slow: bool, out: Path) -> None:
    model_path = piper_model_path(model_file)
    if not model_path.is_file():
        raise TTSError(f"Offline Piper model not found: models/tts/{model_file}")
    from piper import PiperVoice, SynthesisConfig
    with _piper_lock:
        pv = _piper_voices.get(model_file)
        if pv is None:
            pv = PiperVoice.load(str(model_path), espeak_data_dir=_espeak_dir())
            _piper_voices[model_file] = pv
        cfg = SynthesisConfig(length_scale=PIPER_SLOW_SCALE if slow else None)
        with wave.open(str(out), "wb") as f:
            pv.synthesize_wav(text, f, syn_config=cfg)


# ---------- 对外接口 ----------

def _has_offline_for(lang_code: str) -> str | None:
    off_id = langs.LANG_CONFIG[lang_code]["offline_voice"]
    _, _, model_file = VOICES[off_id]
    return off_id if piper_model_path(model_file).is_file() else None


def synth(
    text: str,
    voice: str | None = None,
    slow: bool = False,
    lang: str | None = None,
) -> tuple[Path, str]:
    """合成发音，返回 (缓存文件路径, 实际使用的 voice ID)。"""
    global _edge_failed_at
    text = _clean(text)
    vid = resolve_voice(voice, lang)
    v_lang, engine, target = VOICES[vid]
    path = cache_path(text, vid, slow)
    if path.is_file():
        return path, vid

    if engine == "edge" and time.time() - _edge_failed_at < EDGE_RETRY_AFTER:
        off = _has_offline_for(v_lang)
        if off:
            return synth(text, off, slow, v_lang)

    CACHE_DIR.mkdir(exist_ok=True)
    with _key_lock(path.name):
        if path.is_file():
            return path, vid
        tid = threading.get_ident()
        raw = path.with_name(f"{path.stem}.{tid}.raw.{'mp3' if engine == 'edge' else 'wav'}")
        tmp = path.with_name(f"{path.stem}.{tid}.tmp.mp3")
        try:
            if engine == "edge":
                _edge(text, target, slow, raw)
            else:
                _piper(text, target, slow, raw)
            _normalize(raw, tmp)
            os.replace(tmp, path)
            raw.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            raw.unlink(missing_ok=True)
            tmp.unlink(missing_ok=True)
            if engine == "edge":
                _edge_failed_at = time.time()
                off = _has_offline_for(v_lang)
                if off:
                    print(f"[TTS] Online voice unavailable, falling back to offline {off}: {e}")
                    return synth(text, off, slow, v_lang)
            raise e if isinstance(e, TTSError) else TTSError(f"TTS error: {e}") from e
    return path, vid


def status() -> dict:
    try:
        import edge_tts  # noqa: F401
        edge = True
    except ImportError:
        edge = False
    try:
        import piper  # noqa: F401
        has_piper = True
    except ImportError:
        has_piper = False
    offline_by_lang = {
        code: bool(has_piper and _has_offline_for(code))
        for code in langs.SUPPORTED_LANGS
    }
    return {
        "online": edge,
        "offline": any(offline_by_lang.values()),
        "offline_by_lang": offline_by_lang,
        "default": DEFAULT_VOICE,
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Synthesize word/sentence pronunciation")
    ap.add_argument("text")
    ap.add_argument("--lang", default="sv", choices=list(langs.SUPPORTED_LANGS))
    ap.add_argument("--voice", default=None)
    ap.add_argument("--slow", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    p, used = synth(a.text, a.voice, a.slow, a.lang)
    print(f"{used}  {p}  ({time.time() - t0:.2f}s)")
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "start", "", str(p)])
