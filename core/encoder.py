# -*- coding: utf-8 -*-
"""编码策略公共模块：硬件探测（NVENC）+ 软件回退 + 默认并发数。

从 core/video_fission.py 上提（P1-1）。模块级缓存 + 带锁，进程内单例语义：
  - 首次探测（ffmpeg -encoders 含 h264_nvenc + 0.3s testsrc 试编码）后缓存结果，1s 内返回（AC-P1-1）；
  - 只探测 NVENC（本环境实测 QSV 慢于软件，裂变已注释排除），不做多编码器轮询；
  - fallback_to_software() 为进程内全局回退：NVENC 会话受限后同进程内其余模块自动转软件（R5）；
  - set_hardware_enabled() 为测试钩子（AC-P1-2）。
"""
import os
import subprocess
import threading

DEFAULT_CRF = 20                 # 裂变现状默认 crf
DEFAULT_PRESET = "ultrafast"     # 软件编码默认 preset
NVENC_PRESET = "p1"              # 本机实测最快档（裂变现状）
NVENC_WORKERS = 3                # 消费级卡 NVENC 同时会话 3~5，取 3
SOFTWARE_WORKERS_CAP = 8         # 软件编码并发上限
NVENC_CQ_OFFSET = 0              # OQ1：cq 相对 crf 的偏移，默认 0（cq = crf）；不达标时调整用
_DETECT_TIMEOUT = 30             # -encoders 探测超时
_TEST_DURATION = 0.3             # testsrc 试编码时长（裂变现状 0.3s）

# ── 模块级缓存（带锁，进程内单例）────────────────────────────
_state = {
    "nvenc_available": None,     # None=未知 / True / False（探测结果缓存）
    "forced": None,              # None=自动 / True=强制硬件 / False=强制软件（测试钩子）
}
_lock = threading.Lock()


def _probe_encoders_output() -> str:
    """ffmpeg -encoders 输出（含 stderr）；失败返回空串。"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, errors="ignore", timeout=_DETECT_TIMEOUT,
        )
        return (r.stdout or "") + (r.stderr or "")
    except Exception:
        return ""


def _test_hardware(codec: str) -> bool:
    """用 0.3s testsrc 试编码实测硬件编码器能否正常打开（对齐裂变 _test_hardware）。"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-f", "lavfi", "-i",
             "testsrc=duration=0.3:size=128x128:rate=10",
             "-c:v", codec, "-f", "null", "-"],
            capture_output=True, timeout=20,
        )
        return r.returncode == 0
    except Exception:
        return False


def is_nvenc_available() -> bool:
    """NVENC 是否可用（带缓存，首次探测后缓存结果；1s 内返回，AC-P1-1）。"""
    with _lock:
        forced = _state["forced"]
        if forced is not None:
            return forced
        cached = _state["nvenc_available"]
    if cached is not None:
        return cached
    # 双检锁：并发首调只探测一次
    with _lock:
        if _state["nvenc_available"] is None:
            ok = ("h264_nvenc" in _probe_encoders_output()
                  and _test_hardware("h264_nvenc"))
            _state["nvenc_available"] = ok
        return _state["nvenc_available"]


def get_encoder(crf: int = DEFAULT_CRF, preset: str = DEFAULT_PRESET):
    """获取编码器配置（带缓存）。

    Returns:
        (codec, preset, quality_args)
        NVENC:  ("h264_nvenc", "p1", ["-cq", "20"])
        软件:   ("libx264", "ultrafast", ["-crf", "20"])
    用法：cmd += ["-c:v", codec, "-preset", preset] + quality_args
    """
    if is_nvenc_available():
        cq = max(0, int(crf) + NVENC_CQ_OFFSET)
        return ("h264_nvenc", NVENC_PRESET, ["-cq", str(cq)])
    return ("libx264", preset, ["-crf", str(crf)])


def get_default_workers() -> int:
    """默认并发数：NVENC=3；软件=min(核数, 8)（AC-P1-1）。"""
    if is_nvenc_available():
        return NVENC_WORKERS
    return min(os.cpu_count() or 4, SOFTWARE_WORKERS_CAP)


def fallback_to_software() -> None:
    """硬件编码会话受限/失败时强制回退软件（进程内全局生效，避免其他模块再撞 NVENC 限制，R5）。"""
    with _lock:
        _state["nvenc_available"] = False


def set_hardware_enabled(enabled: bool | None) -> None:
    """测试钩子（AC-P1-2）：None=恢复自动探测；False=强制软件；True=强制硬件（跳过探测）。"""
    with _lock:
        _state["forced"] = enabled


def is_session_limit(stderr: str) -> bool:
    """判断 ffmpeg 报错是否为硬件编码会话受限（从 video_fission._is_session_limit 上提，行为不变）。"""
    e = (stderr or "").lower()
    return any(k in e for k in (
        "opencodingsession", "session", "concurrent", "too many",
        "failed to create", "insufficient device memory", "cuda error"))
