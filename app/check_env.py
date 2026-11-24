"""ClipLingua 运行环境自检脚本。运行：python app/check_env.py"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from env_setup import FFMPEG, MODELS_DIR  # noqa: E402
import languages as langs  # noqa: E402

results = []


def check(name, fn):
    try:
        info = fn()
        results.append((True, name, info))
    except Exception as e:  # noqa: BLE001
        results.append((False, name, f"{type(e).__name__}: {e}"))


def py_version():
    v = sys.version_info
    assert v >= (3, 10), f"Python {v.major}.{v.minor} < 3.10"
    return sys.version.split()[0]


def ffmpeg():
    assert FFMPEG.is_file(), f"ffmpeg not found ({FFMPEG})"
    out = subprocess.run([str(FFMPEG), "-version"], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()[0]


def flask():
    import importlib.metadata as md
    return md.version("flask")


def whisper_check():
    import ctranslate2
    import faster_whisper
    n_gpu = ctranslate2.get_cuda_device_count()
    sv_ref, sv_name = langs.resolve_whisper_model("sv", MODELS_DIR)
    en_ref, en_name = langs.resolve_whisper_model("en", MODELS_DIR)
    return f"faster-whisper {faster_whisper.__version__}, CUDA GPUs={n_gpu}, sv={sv_name}, multi={en_name}"


def ocr_check():
    import cv2
    import numpy as np
    import subtitles
    img = np.full((120, 600, 3), 255, np.uint8)
    cv2.putText(img, "ClipLingua OCR", (20, 75), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 3)
    r = subtitles.get_engine()(img)
    text = " ".join(r.txts or ())
    assert text, "OCR returned empty result"
    return f"RapidOCR ready [{subtitles.engine_provider()}] ({text})"


def vocal_check():
    import vocals
    if not vocals.available():
        return f"Optional model not downloaded yet (ORT={vocals.provider_name()}; run scripts/download_models.py --vocal)"
    import onnxruntime  # noqa: F401
    return f"Kim_Vocal_2 ({vocals.MODEL.stat().st_size / 1e6:.0f} MB, ORT={vocals.provider_name()})"


def tts_check():
    import tts
    st = tts.status()
    off = [k for k, v in st["offline_by_lang"].items() if v]
    return f"edge-tts={'on' if st['online'] else 'off'}, offline Piper={off or 'none'}"


def ytdlp_check():
    try:
        import yt_dlp
        return f"yt-dlp {yt_dlp.version.__version__} (optional URL import ready)"
    except ImportError:
        return "Not installed (local file import works without yt-dlp)"


check("Python", py_version)
check("FFmpeg", ffmpeg)
check("Flask", flask)
check("Whisper / CUDA", whisper_check)
check("RapidOCR", ocr_check)
check("Vocal Separation", vocal_check)
check("TTS Voices", tts_check)
check("yt-dlp (Optional)", ytdlp_check)

print()
for ok, name, info in results:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}: {info}")
print()
if not all(ok for ok, *_ in results):
    sys.exit(1)
print("All core checks passed.")
