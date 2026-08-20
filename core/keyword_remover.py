import os
import re
import shutil

from core.ffmpeg_runner import run_ffmpeg_with_fallback
from utils.media_utils import VIDEO_EXTS, collect_videos, probe_video
from core.video_utils import get_video_duration


MATCH_MODE_SEGMENT = "segment"
MATCH_MODE_ESTIMATE = "estimate"
MATCH_IGNORE_CHARS = "\\s,\\uFF0C.\\u3002!\\uFF01?\\uFF1F;\\uFF1B:\\uFF1A\\u3001\\\"'\\u201C\\u201D\\u2018\\u2019\\uFF08\\uFF09()\\u3010\\u3011\\[\\]{}<>\\u300A\\u300B\\u2581"
CLAUSE_BREAK_CHARS = set(",\uFF0C.\u3002!\uFF01?\uFF1F;\uFF1B:\uFF1A\u3001\n\r\t ")
ESTIMATE_MIN_DURATION = 0.6
MAX_ESTIMATE_UNIT_CHARS = 10


def parse_keywords(text):
    parts = re.split(r"[\n,，;；、]+", text)
    return [part.strip() for part in parts if part.strip()]


def normalize_match_text(text):
    return re.sub(f"[{MATCH_IGNORE_CHARS}]+", "", text).lower()


def normalize_match_text_with_map(text):
    normalized = []
    index_map = []
    for index, char in enumerate(text):
        if re.match(f"[{MATCH_IGNORE_CHARS}]", char):
            continue
        normalized.append(char.lower())
        index_map.append(index)
    return "".join(normalized), index_map


def clean_token_text(text):
    text = str(text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("@@", "").replace("\u2581", " ")


def coerce_token_item(token):
    if isinstance(token, dict):
        text = token.get("text", token.get("token", ""))
        start = token.get("start")
        end = token.get("end")
    elif isinstance(token, (list, tuple)):
        if len(token) >= 3:
            text, start, end = token[0], token[1], token[2]
        elif len(token) == 2 and isinstance(token[1], (list, tuple)):
            text = token[0]
            start, end = token[1][0], token[1][1]
        else:
            return None
    else:
        return None

    try:
        start = float(start)
        end = float(end)
    except (TypeError, ValueError):
        return None
    if end <= start:
        return None
    return {"text": clean_token_text(text), "start": start, "end": end}


def normalized_token_stream(tokens):
    stream = []
    index_map = []
    usable_tokens = []

    for raw_token in tokens or []:
        token = coerce_token_item(raw_token)
        if not token:
            continue
        normalized = normalize_match_text(token["text"])
        if not normalized:
            continue

        token_index = len(usable_tokens)
        usable_tokens.append(token)
        for char in normalized:
            stream.append(char)
            index_map.append(token_index)

    return "".join(stream), index_map, usable_tokens


def split_text_units(text, max_chars=MAX_ESTIMATE_UNIT_CHARS):
    units = []
    start = None
    chars_in_unit = 0

    for index, char in enumerate(text):
        if char in CLAUSE_BREAK_CHARS:
            if start is not None and start < index:
                units.append((start, index))
            start = None
            chars_in_unit = 0
            continue

        if start is None:
            start = index
            chars_in_unit = 0

        chars_in_unit += 1
        if chars_in_unit >= max_chars:
            units.append((start, index + 1))
            start = None
            chars_in_unit = 0

    if start is not None and start < len(text):
        units.append((start, len(text)))

    return units or [(0, len(text))]


def find_text_unit(units, raw_start, raw_end):
    for unit_start, unit_end in units:
        if unit_start <= raw_start < unit_end:
            return unit_start, max(unit_end, raw_end)
    return 0, max(raw_end, 1)


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
    """判断视频是否含音频流（委托 utils.media_utils.probe_video）。"""
    try:
        return bool(probe_video(video_path)["has_audio"])
    except Exception:
        return False


class KeywordRemover:
    def __init__(self, keywords, padding=0.15, match_mode=MATCH_MODE_SEGMENT,
                 estimate_min_duration=ESTIMATE_MIN_DURATION):
        self.keywords = keywords
        self.padding = max(0.0, float(padding))
        self.match_mode = match_mode
        self.estimate_min_duration = max(0.1, float(estimate_min_duration))

    def _matched_keywords(self, text):
        lower_text = text.lower()
        normalized_text = normalize_match_text(text)
        return [
            keyword.strip()
            for keyword in self.keywords
            if keyword.strip() and (
                keyword.strip().lower() in lower_text
                or normalize_match_text(keyword) in normalized_text
            )
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

            token_ranges = self._find_token_delete_ranges(
                seg.get("tokens"), matched_keywords, seg_start, seg_end
            )
            if token_ranges:
                ranges.extend(token_ranges)
                continue

            search_text, index_map = normalize_match_text_with_map(text)
            if not search_text:
                continue
            text_units = split_text_units(text)
            for key in matched_keywords:
                search_key = normalize_match_text(key)
                if not search_key:
                    continue
                pos = search_text.find(search_key)
                while pos >= 0:
                    raw_start = index_map[pos]
                    raw_end = index_map[min(pos + len(search_key) - 1, len(index_map) - 1)] + 1
                    unit_start, unit_end = find_text_unit(text_units, raw_start, raw_end)
                    unit_len = max(1, unit_end - unit_start)
                    unit_duration = seg_duration * (unit_len / text_len)
                    unit_time_start = seg_start + seg_duration * (unit_start / text_len)
                    match_start = unit_time_start + unit_duration * (
                        (raw_start - unit_start) / unit_len
                    )
                    match_end = unit_time_start + unit_duration * (
                        (raw_end - unit_start) / unit_len
                    )
                    ranges.append(self._pad_estimate_range(
                        match_start, match_end, seg_start, seg_end
                    ))
                    pos = search_text.find(search_key, pos + len(search_key))

        return merge_ranges(ranges, duration)

    def _pad_estimate_range(self, match_start, match_end, bound_start, bound_end):
        max_duration = max(0.0, bound_end - bound_start)
        min_duration = min(self.estimate_min_duration, max_duration)
        match_start = max(bound_start, min(match_start, bound_end))
        match_end = max(bound_start, min(match_end, bound_end))
        if match_end - match_start < min_duration:
            center = (match_start + match_end) / 2
            match_start = center - min_duration / 2
            match_end = center + min_duration / 2
            if match_start < bound_start:
                match_end = min(bound_end, match_end + (bound_start - match_start))
                match_start = bound_start
            if match_end > bound_end:
                match_start = max(bound_start, match_start - (match_end - bound_end))
                match_end = bound_end
        return match_start - self.padding, match_end + self.padding

    def _find_token_delete_ranges(self, tokens, matched_keywords, seg_start, seg_end):
        search_text, index_map, usable_tokens = normalized_token_stream(tokens)
        if not search_text:
            return []

        ranges = []
        for key in matched_keywords:
            search_key = normalize_match_text(key)
            if not search_key:
                continue
            pos = search_text.find(search_key)
            while pos >= 0:
                start_token = usable_tokens[index_map[pos]]
                end_token = usable_tokens[
                    index_map[min(pos + len(search_key) - 1, len(index_map) - 1)]
                ]
                ranges.append(self._pad_estimate_range(
                    start_token["start"], end_token["end"], seg_start, seg_end
                ))
                pos = search_text.find(search_key, pos + len(search_key))
        return ranges

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

        def _build_remove_cmd(params):
            _codec, _enc_preset, _quality_args = params
            return [
                "ffmpeg", "-i", video_path,
                "-filter_complex", ";".join(filter_parts),
                *map_args,
                "-c:v", _codec, "-preset", _enc_preset, *_quality_args,
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                "-y", output_path
            ]

        run_ffmpeg_with_fallback(
            _build_remove_cmd, crf=23,
            timeout=7200, error_message="remove keyword ranges failed",
            output_path=output_path,
        )
        if not os.path.exists(output_path):
            raise RuntimeError("remove keyword ranges failed: output file not created")

        return output_path, delete_ranges
