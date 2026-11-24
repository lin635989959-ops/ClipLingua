"""项目公共路径与运行环境设置（所有后端模块优先 import 本模块）。

支持 Windows / macOS / Linux：
- 自动定位 ffmpeg（优先 tools/ffmpeg，其次系统 PATH，再退回 imageio-ffmpeg 内置二进制）
- 在 Windows 下自动将 pip 安装的 NVIDIA cuBLAS / cuDNN 加入 DLL 搜索路径
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
MODELS_DIR = ROOT / "models"
LESSONS_DIR = ROOT / "lessons"

# 瑞典语专用本地模型目录（KBLab strict 优先，其次 standard）
WHISPER_MODEL_STANDARD = MODELS_DIR / "kb-whisper-large"
WHISPER_MODEL_STRICT = MODELS_DIR / "kb-whisper-large-strict"
WHISPER_MODEL = WHISPER_MODEL_STRICT if (WHISPER_MODEL_STRICT / "model.bin").is_file() else WHISPER_MODEL_STANDARD


def _find_ffmpeg() -> Path:
    """按优先级查找 ffmpeg 可执行文件路径。"""
    # 1. 环境变量显式指定
    env_ff = os.environ.get("CLIPLINGUA_FFMPEG")
    if env_ff and Path(env_ff).is_file():
        return Path(env_ff)
    # 2. 项目本地 tools/ 目录
    for name in ("ffmpeg.exe", "ffmpeg"):
        cand = TOOLS_DIR / name
        if cand.is_file():
            return cand
    # 3. 系统 PATH
    sys_ff = shutil.which("ffmpeg")
    if sys_ff:
        return Path(sys_ff)
    # 4. imageio-ffmpeg 自带二进制
    try:
        import imageio_ffmpeg
        return Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:  # noqa: BLE001
        return TOOLS_DIR / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")


FFMPEG = _find_ffmpeg()


def setup_cuda_dlls() -> list[str]:
    """在 Windows 下将 pip 安装的 NVIDIA cuBLAS / cuDNN 目录加入 DLL 搜索路径。"""
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return []
    site = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not site.is_dir():
        return []
    added = []
    for bin_dir in site.glob("*/bin"):
        os.add_dll_directory(str(bin_dir))
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
        added.append(bin_dir.parent.name)
    return added


# 将 ffmpeg 所在目录加入 PATH，确保 yt-dlp 等外部工具可直接调用
if FFMPEG.parent.is_dir():
    os.environ["PATH"] = str(FFMPEG.parent) + os.pathsep + os.environ.get("PATH", "")
setup_cuda_dlls()
