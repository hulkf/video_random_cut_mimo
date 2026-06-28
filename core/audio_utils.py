# -*- coding: utf-8 -*-
"""
音频处理公共模块
提供人声分离、降噪等共享功能
"""
import os
import sys
import subprocess
import tempfile
import wave
import numpy as np


def separate_vocals(audio, sample_rate=16000):
    """使用 demucs (htdemucs) 从音频中分离人声

    Args:
        audio: float32 numpy array, mono
        sample_rate: 采样率

    Returns:
        float32 numpy array (人声)
    """
    tmp_dir = tempfile.mkdtemp(prefix="vocal_sep_")
    input_wav = os.path.join(tmp_dir, "input.wav")
    with wave.open(input_wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes((audio * 32767).astype(np.int16).tobytes())

    cmd = [
        sys.executable, "-m", "demucs",
        "-n", "htdemucs",
        "-o", tmp_dir,
        "--two-stems", "vocals",
        input_wav
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        return audio

    vocals_dir = os.path.join(tmp_dir, "htdemucs", "input")
    vocals_path = None
    for name in ["vocals.wav", "no_vocals.wav"]:
        p = os.path.join(vocals_dir, name)
        if os.path.exists(p):
            vocals_path = p
            break

    if vocals_path is None:
        return audio

    with wave.open(vocals_path, "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        vocals = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)

    peak = np.abs(vocals).max()
    if peak > 1e-6:
        vocals = vocals / peak * 0.9
    else:
        return audio

    return vocals.astype(np.float32)
