import os
import re
import shutil
import subprocess

from utils.video_utils import get_video_duration


VIDEO_EXTS = (".mp4", ".avi", ".mov", ".mkv", ".flv")
MATCH_MODE_SEGMENT = "segment"
MATCH_MODE_ESTIMATE = "estimate"


def collect_videos(folder_path):
    videos = []
    for root, dirs, files in os.walk(folder_path):
        for file_name in files:
            if file_name.lower().endswith(VIDEO_EXTS):
                videos.append(os.path.join(root, file_name))
    return sorted(videos)


def parse_keywords(text):
    parts = re.split(r"[\n,，;；、]+", text)
    return [part.strip() for part in parts if part.strip()]


def merge_ranges(ranges, max_duration=None):
    if not ranges:
        return []

    sorted_ranges = sorted(ranges, key=lambda item: item[0])
    merged = []
    for start, end in sorted_ranges:
        if max_duration is not None:
            start = max(0.0, min(start, max_duration))
            end = max(0.0, min(end, max_duration))
        if end <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(round(start, 3), round(end, 3)) for start, end in merged]


def build_keep_ranges(duration, delete_ranges, min_duration=0.08):
    keep_ranges = []
    cursor = 0.0
    for start, end in delete_ranges:
        if start - cursor >= min_duration:
            keep_ranges.append((cursor, start))
        cursor = max(cursor, end)
    if duration - cursor >= min_duration:
        keep_ranges.append((cursor, duration))
    return keep_ranges


def has_audio_stream(video_path):
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=index", "-of", "csv=p=0", video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="ignore")
    return result.returncode == 0 and bool(result.stdout.strip())


class KeywordRemover:
    def __init__(self, keywords, padding=0.15, match_mode=MATCH_MODE_SEGMENT):
        self.keywords = keywords
        self.padding = max(0.0, float(padding))
        self.match_mode = match_mode

    def _matched_keywords(self, text):
        lower_text = text.lower()
        return [
            keyword.strip()
            for keyword in self.keywords
            if keyword.strip() and keyword.strip().lower() in lower_text
        ]

    def find_delete_ranges(self, segments, duration):
        ranges = []
        for seg in segments:
            text = str(seg.get("text", "")).strip()
            if not text:
                continue

            text_len = max(1, len(text))
            seg_start = float(seg.get("start", 0.0))
            seg_end = float(seg.get("end", seg_start))
            seg_duration = max(0.0, seg_end - seg_start)
            matched_keywords = self._matched_keywords(text)
            if not matched_keywords:
                continue

            if self.match_mode == MATCH_MODE_SEGMENT:
                ranges.append((
                    seg_start - self.padding,
                    seg_end + self.padding
                ))
                continue

            lower_text = text.lower()

            for key in matched_keywords:
                search_text = lower_text
                search_key = key.lower()
                pos = search_text.find(search_key)
                while pos >= 0:
                    match_start = seg_start + seg_duration * (pos / text_len)
                    match_end = seg_start + seg_duration * ((pos + len(key)) / text_len)
                    ranges.append((
                        match_start - self.padding,
                        match_end + self.padding
                    ))
                    pos = search_text.find(search_key, pos + len(search_key))

        return merge_ranges(ranges, duration)

    def remove_ranges(self, video_path, output_path, delete_ranges):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        duration = get_video_duration(video_path)
        delete_ranges = merge_ranges(delete_ranges, duration)

        if not delete_ranges:
            shutil.copy2(video_path, output_path)
            return output_path, []

        keep_ranges = build_keep_ranges(duration, delete_ranges)
        if not keep_ranges:
            raise RuntimeError("关键词覆盖了整段视频，无法生成空视频")

        audio_enabled = has_audio_stream(video_path)
        filter_parts = []
        concat_inputs = []

        for index, (start, end) in enumerate(keep_ranges):
            filter_parts.append(
                f"[0:v]trim=start={start:.3f}:end={end:.3f},"
                f"setpts=PTS-STARTPTS[v{index}]"
            )
            concat_inputs.append(f"[v{index}]")
            if audio_enabled:
                filter_parts.append(
                    f"[0:a]atrim=start={start:.3f}:end={end:.3f},"
                    f"asetpts=PTS-STARTPTS[a{index}]"
                )
                concat_inputs.append(f"[a{index}]")

        if audio_enabled:
            filter_parts.append(
                "".join(concat_inputs) +
                f"concat=n={len(keep_ranges)}:v=1:a=1[outv][outa]"
            )
            map_args = ["-map", "[outv]", "-map", "[outa]"]
        else:
            filter_parts.append(
                "".join(concat_inputs) +
                f"concat=n={len(keep_ranges)}:v=1:a=0[outv]"
            )
            map_args = ["-map", "[outv]"]

        cmd = [
            "ffmpeg", "-i", video_path,
            "-filter_complex", ";".join(filter_parts),
            *map_args,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            "-y", output_path
        ]
        result = subprocess.run(cmd, capture_output=True,
                                encoding="utf-8", errors="ignore", timeout=7200)
        if result.returncode != 0 or not os.path.exists(output_path):
            raise RuntimeError(f"remove keyword ranges failed: {result.stderr}")

        return output_path, delete_ranges
