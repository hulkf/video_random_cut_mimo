import os

from utils.media_utils import VIDEO_EXTS, collect_videos, probe_video
from utils.path_utils import build_output_path

SIZE_PRESETS = {
    "9:16": (1080, 1920),
    "3:4": (1080, 1440),
    "1:1": (1080, 1080),
}

PIPELINE_9X16_TO_3X4_TO_9X16 = "9:16->3:4->9:16"
BLUR_BG_SCALE = 0.25
DEFAULT_BLUR_STRENGTH = 6


def matches_target_size(video_path, target_ratio):
    if target_ratio == PIPELINE_9X16_TO_3X4_TO_9X16:
        return False
    target_width, target_height = SIZE_PRESETS[target_ratio]
    info = probe_video(video_path)
    return info["width"] == target_width and info["height"] == target_height


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

        src_info = probe_video(video_path)
        src_width, src_height = src_info["width"], src_info["height"]
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

    def resize_video(self, video_path, output_path, *, threads=None):
        from core.encoder import get_encoder
        from core.ffmpeg_runner import run_ffmpeg_with_fallback

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        filter_type, filter_value = self.build_filter(video_path)

        def build_cmd(enc_params):
            codec, enc_preset, quality_args = enc_params
            cmd = ["ffmpeg", "-i", video_path]
            if threads is not None:
                cmd = ["ffmpeg", "-threads", str(threads), "-i", video_path,
                       "-filter_threads", "2", "-filter_complex_threads", "2"]
            if filter_type == "complex":
                cmd.extend(["-filter_complex", filter_value, "-map", "[v]", "-map", "0:a?"])
            else:
                cmd.extend(["-vf", filter_value])
            cmd.extend([
                "-c:v", codec, "-preset", enc_preset,
                *quality_args,
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                "-y", output_path,
            ])
            if threads is not None:
                cmd[-2:-2] = ["-threads", str(threads)]
            return cmd

        # crf=23 保持现状（不因迁移改变质量档）；硬件失败自动回退软件重试一次（R5/R6）
        run_ffmpeg_with_fallback(build_cmd, crf=23, timeout=3600,
                                 output_path=output_path, error_message="resize failed")
        return output_path

    def resize_folder(self, input_folder, output_folder, callback=None, *,
                      process_mode="all", max_workers=None, on_result=None):
        from core.concat_batch import concat_workers, ordered_batch, reserve_output
        from utils.media_utils import collect_videos
        from utils.path_utils import build_output_path, strip_quotes

        input_folder = strip_quotes(input_folder)
        videos = collect_videos(input_folder)
        if not videos:
            raise ValueError("输入路径中没有找到视频文件")
        if process_mode == "mismatched":
            videos = [video for video in videos if not matches_target_size(video, self.target_ratio)]
        root = input_folder if os.path.isdir(input_folder) else os.path.dirname(input_folder)
        suffix = ("9x16_to_3x4_to_9x16" if self.target_ratio == PIPELINE_9X16_TO_3X4_TO_9X16
                  else self.target_ratio.replace(":", "x"))
        jobs, reserved = [], set()
        for video in videos:
            rel_base = os.path.splitext(os.path.relpath(video, root))[0]
            output = reserve_output(build_output_path(output_folder, rel_base, suffix), reserved)
            jobs.append((video, output))

        def process(job):
            video, output = job
            self.resize_video(video, output)
            return {"input": video, "output": output, "ratio": self.target_ratio}

        workers = concat_workers(max_workers)
        return ordered_batch(jobs, process, workers=workers,
            callback=(lambda cur, total, msg, sub: callback(cur, total, msg)) if callback else None,
            on_result=on_result, message="批量调整视频尺寸",
            ffmpeg_threads=max(1, min(4, (os.cpu_count() or 1) // workers)))
