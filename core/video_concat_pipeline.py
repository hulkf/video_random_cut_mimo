"""模板001的千川视频预处理、拼接和成品尺寸收口。"""

import os
import shutil
import tempfile
import uuid

from core.video_concatenator import VideoConcatenatorEngine
from core.video_resizer import VideoResizer
from utils.media_utils import collect_videos, probe_video


OUTPUT_RESOLUTION_LIMIT = 2000


def _is_9x16(width, height):
    return width > 0 and height > 0 and width * 16 == height * 9


def normalize_folder_9x16(input_folder, output_folder, blur_strength=6):
    """保留已是9:16的素材，其余素材复用视频尺寸引擎转为1080x1920。"""
    videos = collect_videos(input_folder)
    if not videos:
        raise ValueError("输入文件夹中没有视频: {}".format(input_folder))

    os.makedirs(output_folder, exist_ok=True)
    resizer = VideoResizer("9:16", blur_strength)
    converted = 0
    copied = 0
    used_names = set()

    for index, video_path in enumerate(videos):
        stem, extension = os.path.splitext(os.path.basename(video_path))
        info = probe_video(video_path)
        display_width = info.get("display_width", info.get("width", 0))
        display_height = info.get("display_height", info.get("height", 0))
        already_9x16 = _is_9x16(display_width, display_height)
        output_name = stem + (extension.lower() if already_9x16 else ".mp4")
        if output_name.lower() in used_names:
            output_name = "{:06d}_{}".format(index, output_name)
        used_names.add(output_name.lower())
        output_path = os.path.join(output_folder, output_name)

        # 带旋转元数据的竖屏仍需物理转正，否则拼接引擎按编码宽高处理会出错。
        if already_9x16 and not info.get("rotation", 0):
            shutil.copy2(video_path, output_path)
            copied += 1
        else:
            resizer.resize_video(video_path, output_path)
            converted += 1

    return {"input_count": len(videos), "converted": converted, "copied": copied}


def limit_output_resolution(outputs, blur_strength=6):
    """将任一边像素值超过2000的成品原位降至1080x1920。"""
    resizer = VideoResizer("9:16", blur_strength)
    downscaled = 0
    kept = 0

    for output_path in outputs:
        info = probe_video(output_path)
        width = info.get("display_width", info.get("width", 0))
        height = info.get("display_height", info.get("height", 0))
        if max(width, height) <= OUTPUT_RESOLUTION_LIMIT:
            kept += 1
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

    return {"downscaled": downscaled, "kept": kept}


def run_template_001_concat(config, callback=None):
    """执行模板001：A/B归一化为9:16，拼接，再限制成品最高尺寸。"""
    blur_strength = int(config.get("blur_strength", 6))
    with tempfile.TemporaryDirectory(prefix="video_template_001_") as work_folder:
        folder_a = os.path.join(work_folder, "a_9x16")
        folder_b = os.path.join(work_folder, "b_9x16")
        normalize_folder_9x16(config["folder_a"], folder_a, blur_strength)
        normalize_folder_9x16(config["folder_b"], folder_b, blur_strength)

        effective_config = dict(config)
        effective_config["folder_a"] = folder_a
        effective_config["folder_b"] = folder_b
        outputs = VideoConcatenatorEngine(effective_config).run(callback)
        limit_output_resolution(outputs, blur_strength)
        return outputs
