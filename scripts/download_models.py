"""ClipLingua 模型按需下载助手（均从官方开源仓库拉取，不随代码仓库分发大文件）。

用法示例：
  python scripts/download_models.py --vocal                  # 下载 Kim_Vocal_2 人声分离模型
  python scripts/download_models.py --whisper multi          # 下载多语言 Whisper (large-v3-turbo)
  python scripts/download_models.py --whisper sv             # 下载瑞典语 KBLab strict 模型
  python scripts/download_models.py --piper sv en es zh      # 下载指定语言的 Piper 离线语音模型
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"

PIPER_MODELS = {
    "sv": (
        "sv_SE-lisa-medium.onnx",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/sv/sv_SE/lisa/medium/sv_SE-lisa-medium.onnx",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/sv/sv_SE/lisa/medium/sv_SE-lisa-medium.onnx.json",
    ),
    "en": (
        "en_US-lessac-medium.onnx",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
    ),
    "es": (
        "es_ES-davefx-medium.onnx",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx.json",
    ),
    "zh": (
        "zh_CN-huayan-medium.onnx",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json",
    ),
}

VOCAL_URL = "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx"


def _download_file(url: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_file():
        print(f"  [skip] already exists: {dst.relative_to(ROOT)}")
        return
    print(f"  [get]  {url}\n      -> {dst.relative_to(ROOT)}")
    tmp = dst.with_name(dst.name + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dst)


def download_vocal() -> None:
    print("Downloading vocal separation model (UVR Kim_Vocal_2, MIT)...")
    _download_file(VOCAL_URL, MODELS_DIR / "vocal" / "Kim_Vocal_2.onnx")


def download_whisper(kind: str) -> None:
    from huggingface_hub import snapshot_download

    if kind == "sv":
        repo, folder = "KBLab/kb-whisper-large", MODELS_DIR / "kb-whisper-large-strict"
        print(f"Downloading Swedish KBLab strict model ({repo}, Apache-2.0)...")
        snapshot_download(repo_id=repo, revision="strict", local_dir=str(folder))
    else:
        repo, folder = "deepdml/faster-whisper-large-v3-turbo-ct2", MODELS_DIR / "whisper-large-v3-turbo"
        print(f"Downloading multilingual Whisper model ({repo}, MIT)...")
        snapshot_download(repo_id=repo, local_dir=str(folder))


def download_piper(langs: list[str]) -> None:
    for code in langs:
        name, onnx_url, json_url = PIPER_MODELS[code]
        print(f"Downloading Piper offline voice [{code}] ({name})...")
        _download_file(onnx_url, MODELS_DIR / "tts" / name)
        _download_file(json_url, MODELS_DIR / "tts" / f"{name}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description="Download optional ClipLingua models on demand")
    ap.add_argument("--vocal", action="store_true", help="Download Kim_Vocal_2 vocal separator")
    ap.add_argument("--whisper", choices=["multi", "sv"], help="Download Whisper ASR model")
    ap.add_argument("--piper", nargs="+", choices=list(PIPER_MODELS), help="Download Piper offline TTS models")
    a = ap.parse_args()
    if not (a.vocal or a.whisper or a.piper):
        ap.print_help()
        sys.exit(0)
    if a.vocal:
        download_vocal()
    if a.whisper:
        download_whisper(a.whisper)
    if a.piper:
        download_piper(a.piper)
    print("\nDone.")


if __name__ == "__main__":
    main()
