import json
import os
import subprocess


VIDEO_EXTS = (".mp4", ".avi", ".mov", ".mkv", ".flv")

SIZE_PRESETS = {
    "9:16": (1080, 1920),
    "3:4": (1080, 1440),
    "1:1": (1080, 1080),
}


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
    target_width, target_height = SIZE_PRESETS[target_ratio]
    width, height = probe_video(video_path)
    return width == target_width and height == target_height


class VideoResizer:
    def __init__(self, target_ratio="9:16"):
        if target_ratio not in SIZE_PRESETS:
            raise ValueError(f"Unsupported target ratio: {target_ratio}")
        self.target_ratio = target_ratio
        self.target_width, self.target_height = SIZE_PRESETS[target_ratio]

    def build_filter(self, video_path):
        src_width, src_height = probe_video(video_path)
        src_ratio = src_width / src_height
        target_ratio = self.target_width / self.target_height

        if abs(src_ratio - target_ratio) < 0.01:
            return "simple", f"scale={self.target_width}:{self.target_height},setsar=1"

        if src_ratio < target_ratio:
            return "simple", (
                f"scale={self.target_width}:{self.target_height}:force_original_aspect_ratio=increase,"
                f"crop={self.target_width}:{self.target_height},setsar=1"
            )

        return "complex", (
            "[0:v]scale="
            f"{self.target_width}:{self.target_height}:force_original_aspect_ratio=increase,"
            f"crop={self.target_width}:{self.target_height},boxblur=20:5[bg];"
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
