"""模板001的千川视频预处理、拼接和成品尺寸收口。"""

import os
import shutil
import tempfile
import uuid
import time

from core.concat_batch import concat_workers, ordered_batch
from core.video_concatenator import VideoConcatenatorEngine
from core.video_resizer import VideoResizer
from utils.media_utils import collect_videos, probe_video


OUTPUT_RESOLUTION_LIMIT = 2000


def _is_9x16(width, height):
    return width > 0 and height > 0 and width * 16 == height * 9


def normalize_folder_9x16(input_folder, output_folder, blur_strength=6, callback=None,
                         *, workers=1, reuse_sources=False):
    """保留已是9:16的素材，其余素材复用视频尺寸引擎转为1080x1920。"""
    videos = collect_videos(input_folder)
    if not videos:
        raise ValueError("输入文件夹中没有视频: {}".format(input_folder))

    os.makedirs(output_folder, exist_ok=True)
    resizer = VideoResizer("9:16", blur_strength)
    used_names = set()
    jobs = []

    for index, video_path in enumerate(videos):
        if callback:
            callback(index, len(videos), "检查9:16素材", 0)
        stem, extension = os.path.splitext(os.path.basename(video_path))
        info = probe_video(video_path)
        display_width = info.get("display_width", info.get("width", 0))
        display_height = info.get("display_height", info.get("height", 0))
        already_9x16 = _is_9x16(display_width, display_height)
        output_name = stem + (extension.lower() if already_9x16 else ".mp4")
        renamed = output_name.lower() in used_names
        while output_name.lower() in used_names:
            output_name = "{:06d}_{}".format(index, output_name)
        used_names.add(output_name.lower())
        output_path = os.path.join(output_folder, output_name)
        jobs.append((video_path, output_path, already_9x16 and not info.get("rotation", 0), renamed))

    def process(job):
        video_path, output_path, already_9x16, renamed = job
        # 带旋转元数据的竖屏仍需物理转正，否则拼接引擎按编码宽高处理会出错。
        if already_9x16 and reuse_sources and not renamed:
            return output_path, video_path, "reused"
        if already_9x16:
            shutil.copy2(video_path, output_path)
            kind = "copied"
        else:
            if reuse_sources:
                resizer.resize_video(video_path, output_path,
                                     threads=max(1, min(4, (os.cpu_count() or 1) // workers)))
            else:
                resizer.resize_video(video_path, output_path)
            kind = "converted"
        return output_path, output_path, kind

    results = ordered_batch(jobs, process, workers=workers, callback=callback,
                            message="统一9:16素材")
    summary = {"input_count": len(videos),
               "converted": sum(row[2] == "converted" for row in results),
               "copied": sum(row[2] == "copied" for row in results)}
    if reuse_sources:
        summary["reused"] = sum(row[2] == "reused" for row in results)
        # Match collect_videos(staging_folder)'s ordering, including renamed collisions.
        summary["_videos"] = [row[1] for row in sorted(results)]
    return summary


def limit_output_resolution(outputs, blur_strength=6, callback=None):
    """将任一边像素值超过2000的成品原位降至1080x1920。"""
    resizer = VideoResizer("9:16", blur_strength)
    downscaled = 0
    kept = 0

    for index, output_path in enumerate(outputs):
        if callback:
            callback(index, len(outputs), "检查成品分辨率", 0)
        info = probe_video(output_path)
        width = info.get("display_width", info.get("width", 0))
        height = info.get("display_height", info.get("height", 0))
        if max(width, height) <= OUTPUT_RESOLUTION_LIMIT:
            kept += 1
            if callback:
                callback(index + 1, len(outputs), "成品分辨率无需调整", 100)
            continue

        stem, _extension = os.path.splitext(output_path)
        temporary_output = "{}.template001-{}.mp4".format(stem, uuid.uuid4().hex)
        try:
            resizer.resize_video(output_path, temporary_output)
            os.replace(temporary_output, output_path)
        finally:
            if os.path.exists(temporary_output):
                os.remove(temporary_output)
        downscaled += 1
        if callback:
            callback(index + 1, len(outputs), "已限制成品分辨率", 100)

    return {"downscaled": downscaled, "kept": kept}


def run_template_001_concat(config, callback=None):
    """执行模板001：A/B归一化为9:16，拼接，再限制成品最高尺寸。"""
    blur_strength = int(config.get("blur_strength", 6))
    workers = concat_workers(config.get("batch_workers"))
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="video_template_001_") as work_folder:
        folder_a = os.path.join(work_folder, "a_9x16")
        folder_b = os.path.join(work_folder, "b_9x16")
        normalized_a = normalize_folder_9x16(config["folder_a"], folder_a, blur_strength, callback,
                                             workers=workers, reuse_sources=True)
        normalized_b = normalize_folder_9x16(config["folder_b"], folder_b, blur_strength, callback,
                                             workers=workers, reuse_sources=True)
        normalized_at = time.perf_counter()

        effective_config = dict(config)
        effective_config["folder_a"] = folder_a
        effective_config["folder_b"] = folder_b
        effective_config["prepared_videos_a"] = normalized_a.get("_videos")
        effective_config["prepared_videos_b"] = normalized_b.get("_videos")
        effective_config["limit_output_edge"] = OUTPUT_RESOLUTION_LIMIT
        outputs = VideoConcatenatorEngine(effective_config).run(callback)
        concatenated_at = time.perf_counter()
        limit_output_resolution(outputs, blur_strength, callback)
        if "_performance" in config:
            config["_performance"].update({
                "batch_workers": workers,
                "normalization_seconds": round(normalized_at - started, 3),
                "concat_seconds": round(concatenated_at - normalized_at, 3),
                "resolution_check_seconds": round(time.perf_counter() - concatenated_at, 3),
                "reused_inputs": normalized_a.get("reused", 0) + normalized_b.get("reused", 0),
            })
        return outputs
