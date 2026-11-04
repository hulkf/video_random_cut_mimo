# -*- coding: utf-8 -*-
"""媒体工具公共模块：格式常量 / 视频探测 / 时长 / 收集。只依赖标准库。

单点化（P1-3）：
  - VIDEO_EXTS / collect_videos / probe_video / get_video_duration 从
    core/video_resizer.py、core/keyword_remover.py、core/video_concatenator.py、
    utils/video_utils.py 上提合并；
  - utils/video_utils.get_video_duration 保留为一行转发（兼容未迁移调用方）。
"""
import json
import os
import subprocess

VIDEO_EXTS: tuple = (".mp4", ".avi", ".mov", ".mkv", ".flv")  # P1 保持 5 元组（OQ3）


def collect_videos(path: str, exts=VIDEO_EXTS) -> list:
    """收集视频文件（递归）。
    - path 为目录：os.walk 递归，返回排序列表（兼容 video_resizer/keyword_remover 现状）；
    - path 为单文件：匹配 exts 返回 [path]，否则 []（兼容 video_concatenator.get_videos 现状）；
    - 大小写不敏感（.endswith(exts) 前 lower()）。
    """
    path = (path or "").strip()
    if not path:
        return []
    if os.path.isfile(path):
        return [path] if path.lower().endswith(exts) else []
    if not os.path.isdir(path):
        return []
    videos = []
    for root, _dirs, files in os.walk(path):
        for file_name in files:
            if file_name.lower().endswith(exts):
                videos.append(os.path.join(root, file_name))
    return sorted(videos)


def collect_files(path: str, exts) -> list:
    """通用收集（目录或单文件，兼容用户"输入目录/输入单文件"两种填法）。

    - path 为目录：os.walk 递归收集匹配 exts 的文件，排序返回；
    - path 为单文件：匹配 exts 返回 [path]，否则 []；
    - 大小写不敏感。exts 为元组（如 VIDEO_EXTS / 图片扩展名集合）。
    """
    path = (path or "").strip()
    if not path:
        return []
    if os.path.isfile(path):
        return [path] if path.lower().endswith(tuple(exts)) else []
    if not os.path.isdir(path):
        return []
    found = []
    for root, _dirs, files in os.walk(path):
        for file_name in files:
            if file_name.lower().endswith(tuple(exts)):
                found.append(os.path.join(root, file_name))
    return sorted(found)


def _parse_frame_rate(r_frame_rate) -> float:
    """r_frame_rate（如 "30000/1001" / "30/1" / 缺失）→ float。"""
    if not r_frame_rate:
        return 0.0
    try:
        if "/" in str(r_frame_rate):
            num, den = str(r_frame_rate).split("/", 1)
            den = float(den)
            if den == 0:
                return 0.0
            return float(num) / den
        return float(r_frame_rate)
    except (TypeError, ValueError):
        return 0.0


def _parse_duration(*values) -> float:
    """取第一个可解析的时长（秒）。"""
    for v in values:
        if not v:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def probe_video(video_path: str, *, timeout: float = 10.0) -> dict:
    """一次 ffprobe 全量探测（-show_format -show_streams，本地解析）。

    Returns:
        {
            "codec_name": str,           # 视频编码器，如 "h264"
            "width": int, "height": int,
            "pix_fmt": str,              # 如 "yuv420p"（缺失为 ""）
            "r_frame_rate": float,       # 平均帧率（如 30.0）
            "fps": float,                # 同 r_frame_rate（兼容 concatenator 旧调用点）
            "duration": float,           # 秒
            "sample_aspect_ratio": str,  # 如 "1:1"；未知为 "0:1"
            "display_aspect_ratio": str, # 如 "9:16"
            "has_audio": bool,           # 是否存在音频流
        }
    单次超时只对身份未变化的本地文件再探测一次；持续超时明确失败。
    不重试格式错误、不重新执行编码，默认最多两次各10秒的只读探测。
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_format", "-show_streams",
        "-of", "json", video_path,
    ]
    def identity():
        try:
            info = os.stat(video_path)
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        except OSError:
            return None

    original = identity()
    for attempt in range(2):
        try:
            # run() kills and reaps the timed-out process before raising: the
            # second read never overlaps a surviving first ffprobe process.
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="ignore", timeout=timeout,
            )
            if attempt and identity() != original:
                raise RuntimeError("VIDEO_PROBE_SOURCE_CHANGED：探测期间输入视频已变化，请核查原文件。")
            break
        except subprocess.TimeoutExpired as error:
            if original is None or identity() != original:
                raise RuntimeError("VIDEO_PROBE_SOURCE_CHANGED：探测期间输入视频已变化或不可用，请核查原文件。") from error
            if attempt:
                raise RuntimeError("VIDEO_PROBE_TIMEOUT：读取视频信息连续两次超时（每次{:g}秒）；请检查文件读取与本机负载。".format(timeout)) from error
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError("ffprobe failed: {}".format(result.stderr))

    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    vstream = None
    for s in streams:
        if s.get("codec_type") == "video":
            vstream = s
            break
    if vstream is None:
        raise RuntimeError("No video stream found: {}".format(video_path))

    width = int(vstream.get("width", 0) or 0)
    height = int(vstream.get("height", 0) or 0)
    rotation = 0
    rotation_values = [(vstream.get("tags") or {}).get("rotate")]
    rotation_values.extend(
        side_data.get("rotation")
        for side_data in (vstream.get("side_data_list") or [])
        if isinstance(side_data, dict)
    )
    for value in rotation_values:
        if value is None or value == "":
            continue
        try:
            rotation = int(round(float(value))) % 360
            break
        except (TypeError, ValueError):
            continue
    display_width, display_height = width, height
    if rotation % 180 == 90:
        display_width, display_height = height, width
    fps = _parse_frame_rate(vstream.get("r_frame_rate", ""))
    duration = _parse_duration(
        (data.get("format", {}) or {}).get("duration", ""),
        vstream.get("duration", ""),
    )
    return {
        "codec_name": vstream.get("codec_name", "") or "",
        "width": width,
        "height": height,
        "rotation": rotation,
        "display_width": display_width,
        "display_height": display_height,
        "pix_fmt": vstream.get("pix_fmt", "") or "",
        "r_frame_rate": fps,
        "fps": fps,
        "duration": duration,
        "sample_aspect_ratio": vstream.get("sample_aspect_ratio", "") or "0:1",
        "display_aspect_ratio": vstream.get("display_aspect_ratio", "") or "0:1",
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def get_video_duration(video_path: str) -> float:
    """视频时长（秒）。实现与现状 utils/video_utils.get_video_duration 等价
    （ffprobe -show_format duration），不加 timeout，保持行为完全一致。
    """
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="ignore")
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError("ffprobe failed: {}".format(result.stderr))
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])
