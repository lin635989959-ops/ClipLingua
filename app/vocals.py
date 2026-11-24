"""去背景音乐：用 UVR 的 MDX-Net 人声分离模型（Kim_Vocal_2，ONNX）把人声单独抽出来再识别。

只在「确实有明显背景音乐」时才用分离后的人声——没有音乐的视频，分离只会带来一点失真：
- 长音频采用两阶段策略：先抽样 2 段检测伴奏能量占比，明显无 BGM 时直接跳过剩余片段；
  若检测到 BGM，则复用已分离的抽样片段并补齐剩余部分。
- 自动检测可用的 GPU 加速后端（CUDA / DirectML），无 GPU 或驱动不匹配时静默回退 CPU。

命令行用法（只看检测结果，不改课程）：
    .venv\\Scripts\\python.exe app\\vocals.py <音频或视频文件> [输出人声.wav]
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from env_setup import FFMPEG, MODELS_DIR  # noqa: E402

MODEL = MODELS_DIR / "vocal" / "Kim_Vocal_2.onnx"

# Kim_Vocal_2 的参数（来自 UVR 的模型说明）
SR = 44100
N_FFT = 7680
HOP = 1024
DIM_F = 3072
DIM_T = 256
COMPENSATE = 1.009
CHUNK = HOP * (DIM_T - 1)          # 每段送进模型的采样数
TRIM = N_FFT // 2
GEN = CHUNK - 2 * TRIM             # 每段真正用到的中间部分

MUSIC_RATIO = 0.5        # 伴奏能量 / 原声能量 超过这个，就认为有明显背景音乐
                         # （实测：0.8 时分离后错词从 30 多个降到 3 个；0.45 时两者差不多；没音乐的视频约 0.02）
PROBE_CHUNKS = 2         # 长音频预检抽样段数（每段约 5.75 秒）
PROBE_SKIP_RATIO = 0.38  # 预检伴奏占比低于此保守阈值时直接跳过剩余片段

_session = None
_provider: str | None = None
_lock = threading.Lock()
_WINDOW = np.hanning(N_FFT + 1)[:-1].astype(np.float32)   # 周期汉宁窗（和 torch.hann_window 一样）


def _build_istft_norm() -> np.ndarray:
    """预计算 _istft 固定窗能量归一化因子，避免每段重复累加。"""
    length = N_FFT + HOP * (DIM_T - 1)
    norm = np.zeros(length, dtype=np.float64)
    w2 = _WINDOW.astype(np.float64) ** 2
    for t in range(DIM_T):
        s = t * HOP
        norm[s:s + N_FFT] += w2
    pad = N_FFT // 2
    return np.maximum(norm[pad:pad + CHUNK], 1e-8)


_ISTFT_NORM = _build_istft_norm()


def available() -> bool:
    return MODEL.is_file()


def select_ort_providers() -> list[str]:
    """按优先级选择当前环境可用的 ONNX Runtime 执行后端。"""
    import onnxruntime as ort
    avail = set(ort.get_available_providers())
    preferred = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]
    chosen = [p for p in preferred if p in avail]
    return chosen or ["CPUExecutionProvider"]


def provider_name() -> str:
    """返回已初始化会话的实际后端名称，未加载时返回首选可用后端。"""
    if _provider:
        return _provider
    try:
        return select_ort_providers()[0]
    except Exception:  # noqa: BLE001
        return "CPUExecutionProvider"


def _get_session():
    global _session, _provider
    with _lock:
        if _session is None:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.enable_cpu_mem_arena = True
            opts.intra_op_num_threads = min(os.cpu_count() or 4, 8)
            providers = select_ort_providers()
            try:
                _session = ort.InferenceSession(str(MODEL), sess_options=opts, providers=providers)
            except Exception:  # noqa: BLE001
                _session = ort.InferenceSession(str(MODEL), sess_options=opts, providers=["CPUExecutionProvider"])
            used = _session.get_providers()
            _provider = used[0] if used else "CPUExecutionProvider"
        return _session


# ---------- STFT（和 torch.stft(center=True) 对齐） ----------

def _stft(x: np.ndarray) -> np.ndarray:
    """x: [n, CHUNK] → 复数 [n, N_FFT//2+1, DIM_T]"""
    pad = N_FFT // 2
    xp = np.pad(x, ((0, 0), (pad, pad)), mode="reflect")
    idx = np.arange(DIM_T)[:, None] * HOP + np.arange(N_FFT)[None, :]
    frames = xp[:, idx] * _WINDOW                          # [n, T, N_FFT]
    return np.fft.rfft(frames, axis=-1).transpose(0, 2, 1)  # [n, F, T]


def _istft(spec: np.ndarray) -> np.ndarray:
    """复数 [n, F, T] → [n, CHUNK]"""
    frames = np.fft.irfft(spec.transpose(0, 2, 1), n=N_FFT, axis=-1) * _WINDOW   # [n, T, N_FFT]
    n = spec.shape[0]
    length = N_FFT + HOP * (DIM_T - 1)
    out = np.zeros((n, length), dtype=np.float64)
    for t in range(DIM_T):
        s = t * HOP
        out[:, s:s + N_FFT] += frames[:, t]
    pad = N_FFT // 2
    return out[:, pad:pad + CHUNK] / _ISTFT_NORM


def _pad_mix(mix: np.ndarray) -> tuple[np.ndarray, list[int]]:
    """对输入立体声音频做边界填充并生成分段起点列表。"""
    n = mix.shape[1]
    pad = GEN - n % GEN
    mix_p = np.concatenate([np.zeros((2, TRIM)), mix, np.zeros((2, pad)), np.zeros((2, TRIM))], axis=1)
    starts = list(range(0, n + pad, GEN))
    return mix_p, starts


def _separate_chunk(sess, mix_p: np.ndarray, start: int) -> np.ndarray:
    """对单段 [2, CHUNK] 执行模型推理并返回去除边界后的 [2, GEN] 人声。"""
    wave = mix_p[:, start:start + CHUNK].astype(np.float32)       # [2, CHUNK]
    spec = _stft(wave)                                             # [2, F, T]
    x = np.stack([spec.real, spec.imag], axis=1)                   # [2ch, 2, F, T]
    x = x.reshape(1, 4, -1, DIM_T)[:, :, :DIM_F].astype(np.float32)
    y = sess.run(None, {"input": x})[0]                            # [1, 4, DIM_F, T]
    full = np.zeros((1, 4, N_FFT // 2 + 1, DIM_T), dtype=np.float32)
    full[:, :, :DIM_F] = y
    full = full.reshape(2, 2, N_FFT // 2 + 1, DIM_T)
    wav = _istft(full[:, 0] + 1j * full[:, 1])                     # [2, CHUNK]
    return wav[:, TRIM:-TRIM]


def _pick_probe_indices(total: int) -> list[int]:
    """从总段数中均匀挑选代表性预检分段（避开极短的首尾静默段）。"""
    if total <= PROBE_CHUNKS:
        return list(range(total))
    a = max(0, total // 3)
    b = min(total - 1, (2 * total) // 3)
    return [a] if a == b else [a, b]


def separate(mix: np.ndarray, progress=None) -> np.ndarray:
    """mix: [2, n] 44.1kHz 立体声 → 人声 [2, n]"""
    sess = _get_session()
    n = mix.shape[1]
    mix_p, starts = _pad_mix(mix)
    outs = []
    for k, i in enumerate(starts):
        outs.append(_separate_chunk(sess, mix_p, i))
        if progress:
            progress((k + 1) / len(starts))
    return np.concatenate(outs, axis=1)[:, :n] * COMPENSATE


def separate_if_music(mix: np.ndarray, progress=None) -> tuple[np.ndarray | None, float, bool]:
    """两阶段人声分离：长音频先抽样检测伴奏占比，确认有明显 BGM 再补齐其余片段。

    返回 (完整人声或 None, 伴奏占比, 是否因预检跳过)。
    """
    sess = _get_session()
    n = mix.shape[1]
    mix_p, starts = _pad_mix(mix)
    total = len(starts)
    cache: dict[int, np.ndarray] = {}

    if total > PROBE_CHUNKS:
        probe_ids = _pick_probe_indices(total)
        orig_parts: list[np.ndarray] = []
        voc_parts: list[np.ndarray] = []
        for done_cnt, idx in enumerate(probe_ids, start=1):
            st = starts[idx]
            voc_chunk = _separate_chunk(sess, mix_p, st)
            cache[idx] = voc_chunk
            valid = max(0, min(GEN, n - st))
            if valid > 0:
                orig_parts.append(mix_p[:, st + TRIM:st + TRIM + valid])
                voc_parts.append(voc_chunk[:, :valid] * COMPENSATE)
            if progress:
                progress(done_cnt / total)
        if orig_parts:
            orig_cat = np.concatenate(orig_parts, axis=1)
            voc_cat = np.concatenate(voc_parts, axis=1)
            probe_ratio = _rms(orig_cat - voc_cat) / max(_rms(orig_cat), 1e-9)
            if probe_ratio < PROBE_SKIP_RATIO:
                if progress:
                    progress(1.0)
                return None, probe_ratio, True

    done_cnt = len(cache)
    outs: list[np.ndarray] = []
    for idx, st in enumerate(starts):
        if idx in cache:
            outs.append(cache[idx])
            continue
        outs.append(_separate_chunk(sess, mix_p, st))
        done_cnt += 1
        if progress:
            progress(done_cnt / total)

    voc = np.concatenate(outs, axis=1)[:, :n] * COMPENSATE
    ratio = _rms(mix - voc) / max(_rms(mix), 1e-9)
    return voc, ratio, False


# ---------- 读写音频（用 ffmpeg，免装别的库） ----------

def _read(path: Path, sr: int, ch: int) -> np.ndarray:
    cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-i", str(path),
           "-f", "f32le", "-ac", str(ch), "-ar", str(sr), "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 读音频出错：{r.stderr.decode('utf-8', 'replace')[-300:]}")
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, ch).T.copy()


def _write_16k_mono(wave: np.ndarray, sr: int, out: Path) -> None:
    """[2, n] → 16kHz 单声道 wav（交给 ffmpeg 重采样）"""
    data = np.clip(wave.T, -1, 1).astype(np.float32).tobytes()
    cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-f", "f32le", "-ac", "2",
           "-ar", str(sr), "-i", "-", "-ac", "1", "-ar", "16000", str(out)]
    r = subprocess.run(cmd, input=data, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 写音频出错：{r.stderr.decode('utf-8', 'replace')[-300:]}")


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)) + 1e-12))


def prepare(media: Path, out_wav: Path, progress=None) -> dict:
    """从视频里分离人声。有明显背景音乐时写出 out_wav（16kHz 单声道人声）。

    返回 {"music_ratio": 伴奏能量占比, "used": 是否写出了人声版, "sampled": 是否预检跳过, "provider": 推理后端}。"""
    mix = _read(media, SR, 2)
    if mix.shape[1] < SR // 2:
        return {"music_ratio": 0.0, "used": False, "sampled": False, "provider": provider_name()}
    voc, ratio, sampled = separate_if_music(mix, progress)
    used = voc is not None and ratio >= MUSIC_RATIO
    if used:
        _write_16k_mono(voc, SR, out_wav)
    return {
        "music_ratio": round(ratio, 3),
        "used": used,
        "sampled": sampled,
        "provider": provider_name(),
    }


def _cli() -> None:
    import time
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    t = time.time()
    mix = _read(src, SR, 2)
    voc, ratio, sampled = separate_if_music(mix)
    tag = "（预检跳过全量分离）" if sampled else ""
    print(
        f"伴奏能量占比 {ratio:.3f}（≥ {MUSIC_RATIO} 才会用人声版识别）{tag}，"
        f"后端 {provider_name()}，用时 {time.time() - t:.1f} 秒"
    )
    if out:
        if voc is None:
            voc = separate(mix)
        _write_16k_mono(voc, SR, out)
        print(f"人声已写到 {out}")


if __name__ == "__main__":
    _cli()
