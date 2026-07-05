import json
import os
import subprocess


VIDEO_EXTS = (".mp4", ".avi", ".mov", ".mkv", ".flv")

SIZE_PRESETS = {
    "9:16": (1080, 1920),
    "3:4": (1080, 1440),
    "1:1": (1080, 1080),
}

PIPELINE_9X16_TO_3X4_TO_9X16 = "9:16->3:4->9:16"
BLUR_BG_SCALE = 0.25
DEFAULT_BLUR_STRENGTH = 6


def collect_videos(folder_path):
    videos = []
    for root, dirs, files in os.walk(folder_path):
        for file_name in files:
            if file_name.lower().endswith(VIDEO_EXTS):
                videos.append(os.path.join(root, file_name))
    return sorted(videos)


def probe_video(video_path):
    cmd = [
        "ffprobe", "-v", "quiet", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json", video_path
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore"
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if not streams:
        raise RuntimeError(f"No video stream found: {video_path}")

    return int(streams[0]["width"]), int(streams[0]["height"])


def matches_target_size(video_path, target_ratio):
    if target_ratio == PIPELINE_9X16_TO_3X4_TO_9X16:
        return False
    target_width, target_height = SIZE_PRESETS[target_ratio]
    width, height = probe_video(video_path)
    return width == target_width and height == target_height


class VideoResizer:
    def __init__(self, target_ratio="9:16", blur_strength=DEFAULT_BLUR_STRENGTH):
        if target_ratio not in SIZE_PRESETS and target_ratio != PIPELINE_9X16_TO_3X4_TO_9X16:
            raise ValueError(f"Unsupported target ratio: {target_ratio}")
        self.target_ratio = target_ratio
        self.blur_strength = max(1, int(blur_strength))
        if target_ratio == PIPELINE_9X16_TO_3X4_TO_9X16:
            self.target_width, self.target_height = SIZE_PRESETS["9:16"]
        else:
            self.target_width, self.target_height = SIZE_PRESETS[target_ratio]

    def blur_filter(self):
        passes = 2 if self.blur_strength <= 10 else 3
        return f"boxblur={self.blur_strength}:{passes}"

    def build_pipeline_filter(self):
        bg_width = max(2, int(self.target_width * BLUR_BG_SCALE))
        bg_height = max(2, int(self.target_height * BLUR_BG_SCALE))
        mid_width, mid_height = SIZE_PRESETS["3:4"]
        return (
            "[0:v]"
            f"scale={mid_width}:{mid_height}:force_original_aspect_ratio=increase,"
            f"crop={mid_width}:{mid_height},split[bg_src][fg_src];"
            "[bg_src]"
            f"scale={bg_width}:{bg_height}:force_original_aspect_ratio=increase,"
            f"crop={bg_width}:{bg_height},{self.blur_filter()},"
            f"scale={self.target_width}:{self.target_height}[bg];"
            "[fg_src]"
            f"scale={self.target_width}:{self.target_height}:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
        )

    def build_filter(self, video_path):
        if self.target_ratio == PIPELINE_9X16_TO_3X4_TO_9X16:
            return "complex", self.build_pipeline_filter()

        src_width, src_height = probe_video(video_path)
        src_ratio = src_width / src_height
        target_ratio = self.target_width / self.target_height
        blur_width = max(2, int(self.target_width * BLUR_BG_SCALE))
        blur_height = max(2, int(self.target_height * BLUR_BG_SCALE))

        if abs(src_ratio - target_ratio) < 0.01:
            return "simple", f"scale={self.target_width}:{self.target_height},setsar=1"

        if src_ratio < target_ratio:
            return "simple", (
                f"scale={self.target_width}:{self.target_height}:force_original_aspect_ratio=increase,"
                f"crop={self.target_width}:{self.target_height},setsar=1"
            )

        return "complex", (
            "[0:v]scale="
            f"{blur_width}:{blur_height}:force_original_aspect_ratio=increase,"
            f"crop={blur_width}:{blur_height},{self.blur_filter()},"
            f"scale={self.target_width}:{self.target_height}[bg];"
            "[0:v]scale="
            f"{self.target_width}:{self.target_height}:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
        )

    def resize_video(self, video_path, output_path):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        filter_type, filter_value = self.build_filter(video_path)
        cmd = ["ffmpeg", "-i", video_path]
        if filter_type == "complex":
            cmd.extend(["-filter_complex", filter_value, "-map", "[v]", "-map", "0:a?"])
        else:
            cmd.extend(["-vf", filter_value])
        cmd.extend([
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            "-y", output_path
        ])
        result = subprocess.run(
            cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=3600
        )
        if result.returncode != 0 or not os.path.exists(output_path):
            raise RuntimeError(f"resize failed: {result.stderr}")
        return output_path

    def resize_folder(self, input_folder, output_folder, callback=None):
        videos = collect_videos(input_folder)
        results = []
        total = len(videos)

        for index, video_path in enumerate(videos):
            rel_path = os.path.relpath(video_path, input_folder)
            rel_base, _ = os.path.splitext(rel_path)
            output_path = os.path.join(output_folder, f"{rel_base}_{self.target_ratio.replace(':', 'x')}.mp4")

            if callback:
                callback(index, total, rel_path)

            result_path = self.resize_video(video_path, output_path)
            results.append({
                "input": video_path,
                "output": result_path,
                "ratio": self.target_ratio,
            })

            if callback:
                callback(index + 1, total, rel_path)

        return results
