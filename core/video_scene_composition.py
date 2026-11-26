"""Render optional Workbench composition steps using the pinned ffmpeg-skill backend."""

from bisect import bisect_left
import json
import os
from pathlib import Path
import sys
import tempfile

from core.video_scene_edit import _run, _snapshot, export


def valid_intervals(spans, limit):
    if not isinstance(spans, list) or not 1 <= len(spans) <= limit:
        return False
    previous = 0
    for span in spans:
        if not isinstance(span, (list, tuple)) or len(span) != 2:
            return False
        start, end = span
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not previous <= start < end <= 240):
            return False
        previous = end
    return True


def validate_composition(composition):
    if composition.get("format", "original") not in ("original", "vertical"):
        raise ValueError("无效的画幅")
    if composition.get("audio", "original") not in ("original", "mute"):
        raise ValueError("无效的音频选项")
    if "keep_seconds" in composition and not valid_intervals(composition["keep_seconds"], 20):
        raise ValueError("无效的保留区间")
    if "append" in composition:
        append = composition["append"]
        if (not isinstance(append, dict) or set(append) != {"path", "size", "mtime_ns"}
                or not isinstance(append["path"], str)
                or type(append["size"]) is not int
                or not isinstance(append["mtime_ns"], str) or not append["mtime_ns"].isdigit()):
            raise ValueError("无效的拼接素材")
    if "captions" in composition:
        cues = composition["captions"]
        if (not isinstance(cues, list) or not 1 <= len(cues) <= 50
                or any(not isinstance(cue, dict) or set(cue) != {"start", "end", "text"}
                       or not isinstance(cue["text"], str) or not 1 <= len(cue["text"].strip()) <= 160
                       or any(ord(ch) < 32 or ch in "{}<>" for ch in cue["text"])
                       for cue in cues)
                or not valid_intervals([(cue["start"], cue["end"]) for cue in cues], 50)):
            raise ValueError("无效的字幕")


def source_path(entry):
    file = Path(entry["path"])
    if not file.is_absolute() or file.resolve(strict=True) != file or file.suffix.lower() != ".mp4":
        raise ValueError("请选择已绑定的 MP4 素材")
    stamp = _snapshot(file)
    if stamp[:2] != (entry["size"], int(entry["mtime_ns"])):
        raise ValueError("工作台绑定素材已改变")
    return file, stamp


def caption_time(seconds):
    hours, ms = divmod(round(seconds * 1000), 3600000)
    minutes, ms = divmod(ms, 60000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def skill_render(script, inputs, output, options):
    root = Path(os.environ.get(
        "JR_FFMPEG_SKILL_DIR",
        "D:/JR_project/JR_workbench/third-party/ffmpeg-skill-4.7.0/package",
    ))
    handler = root / "scripts" / (script + ".py")
    if not handler.is_file():
        raise RuntimeError("工作台未安装固定版本的 ffmpeg-skill")
    raw = _run(sys.executable, [
        str(handler), *map(str, inputs), "-o", str(output), *options, "--json-brief",
    ], timeout=230)
    result = json.loads(raw)
    if not output.is_file() or output.stat().st_size == 0 or result.get("verified") is False:
        raise RuntimeError("ffmpeg-skill 未返回通过校验的成片")


def compose(source, target, ranges, times, duration, audio, composition, check):
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".scene-compose-") as temp:
        scratch = Path(temp)
        first = source
        operations = []
        if ranges != [[0, len(times)]]:
            first = scratch / "selected.mp4"
            export(source, first, ranges, times, duration, audio)
            operations.append("cut")
        append = composition.get("append")
        other, other_stamp = source_path(append) if append else (None, None)
        width, height = (1080, 1920) if composition.get("format") == "vertical" else (None, None)
        passed = first
        check()
        if other:
            passed = scratch / "joined.mp4"
            options = ["--transition", "none", "--fit", "pad", "--on-silent", "warn",
                       "--codec", "h264", "--preset", "fast", "--no-hw"]
            if width:
                options += ["--width", str(width), "--height", str(height)]
            skill_render("join", [first, other], passed, options)
            operations.append("join")
        elif width:
            passed = scratch / "vertical.mp4"
            skill_render("fit", [first], passed, [
                "--aspect", "9:16", "--width", str(width), "--height", str(height),
                "--fit", "pad", "--codec", "h264", "--preset", "fast", "--no-hw",
            ])
            operations.append("fit")
        if composition.get("audio") == "mute":
            muted = scratch / "muted.mp4"
            check()
            skill_render("audio", [passed], muted, ["--mute"])
            passed = muted
            operations.append("mute")
        if cues := composition.get("captions"):
            output_duration = float(_run("ffprobe", [
                "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(passed),
            ]).strip())
            if any(cue["end"] > output_duration + 0.001 for cue in cues):
                raise ValueError("字幕超出剪辑后成片时长")
            srt = scratch / "captions.srt"
            srt.write_text("\n".join(
                f'{i}\n{caption_time(cue["start"])} --> {caption_time(cue["end"])}\n{cue["text"]}\n'
                for i, cue in enumerate(cues, 1)
            ), encoding="utf-8")
            captioned = scratch / "captioned.mp4"
            check()
            skill_render("caption", [passed], captioned, [
                "--mode", "mux", "--srt", str(srt), "--min-duration", "0",
            ])
            passed = captioned
            operations.append("captions")
        check()
        verified = json.loads(_run("ffprobe", [
            "-v", "error", "-show_streams", "-show_entries",
            "stream=codec_type,width,height,duration:stream_disposition=:stream_tags=", "-of", "json", str(passed),
        ]))["streams"]
        streams = {stream["codec_type"]: stream for stream in verified}
        video = streams.get("video", {})
        if width and (video.get("width"), video.get("height")) != (width, height):
            raise RuntimeError("成片尺寸与竖版要求不符")
        if composition.get("audio") == "mute" and "audio" in streams:
            raise RuntimeError("成片未完成静音")
        if composition.get("audio") != "mute" and audio and "audio" not in streams:
            raise RuntimeError("原声未保留")
        if composition.get("captions") and "subtitle" not in streams:
            raise RuntimeError("成片未包含字幕")
        if other and _snapshot(other) != other_stamp:
            raise ValueError("拼接素材在渲染期间发生变化")
        _run("ffmpeg", ["-nostdin", "-v", "error", "-xerror", "-i", str(passed),
                        "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"], timeout=180)
        check()
        os.link(passed, target)
        return operations, streams


def time_ranges(spans, times, duration):
    result = []
    for start, end in spans:
        if end > duration + .001:
            raise ValueError("保留区间超出原片范围")
        lo, hi = bisect_left(times, start), bisect_left(times, end)
        if lo == hi:
            raise ValueError("保留区间不含原始帧")
        result.append([lo, hi])
    return result
