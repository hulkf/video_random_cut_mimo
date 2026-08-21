"""Headless adapters for every business tab in the desktop application.

The GUI remains the human-facing layer.  This module gives agents a stable,
JSON-friendly API while reusing the same core engines and workers as the tabs.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List


OPERATIONS: Dict[str, Dict[str, Any]] = {
    "video_slice": {"tab": "视频切片", "required": ["input_path", "output_folder"]},
    "video_screenshot": {"tab": "视频截图", "required": ["input_path", "output_folder"]},
    "text_recognition": {"tab": "文字识别", "required": ["input_path"]},
    "face_detection": {"tab": "人脸识别", "required": ["input_path"]},
    "audio_mix": {"tab": "音频混剪", "required": ["clips_folder", "media_folder", "output_folder"]},
    "video_mix": {"tab": "视频混剪", "required": ["video_folder", "clips_folder", "output_folder"]},
    "video_resize": {"tab": "视频尺寸", "required": ["input_path", "output_folder"]},
    "video_enhance": {"tab": "视频优化", "required": ["input_path", "output_folder"]},
    "keyword_remove": {"tab": "去关键词", "required": ["input_path", "output_folder", "keywords", "model_path"]},
    "subtitle_generate": {"tab": "视频字幕", "required": ["input_path", "output_folder", "model_path"]},
    "kaipai_process": {"tab": "开拍云端", "required": ["input_path", "task_name"]},
    "kaipai_download": {"tab": "开拍云端", "required": ["items", "output_folder"]},
    "kaipai_quota": {"tab": "开拍云端", "required": []},
    "video_fission": {"tab": "视频裂变", "required": ["input_sources", "output_folder"]},
    "voice_profile_list": {"tab": "音色复刻", "required": []},
    "voice_profile_create": {"tab": "音色复刻", "required": ["name", "reference_audio", "reference_text"]},
    "voice_profile_delete": {"tab": "音色复刻", "required": ["profile_id"]},
    "voice_clone_apply": {"tab": "音色复刻", "required": ["profile_id", "input_path", "output_folder"]},
    "voice_synthesize": {"tab": "音色复刻", "required": ["profile_id", "text", "output_path"]},
    "video_download": {"tab": "视频下载", "required": ["urls", "output_folder"]},
    "download_auth_status": {"tab": "视频下载", "required": []},
    "download_login": {"tab": "视频下载", "required": []},
    "settings_get": {"tab": "设置", "required": []},
    "settings_update": {"tab": "设置", "required": ["section", "values"]},
    "settings_secret_set": {"tab": "设置", "required": ["section", "key", "value"]},
}

OPERATION_OPTIONS = {
    "video_slice": ["min_duration", "max_duration", "detect_text", "separate_folders"],
    "video_screenshot": ["frame_count", "detect_faces", "delete_face_images", "delete_face_videos", "model_path", "separate_folders"],
    "text_recognition": ["frame_interval", "threshold", "max_workers"],
    "face_detection": ["min_face_ratio", "sample_count", "model_path", "score_threshold", "auto_delete", "max_workers"],
    "audio_mix": ["cover_enabled", "cover_folder", "cover_duration_min", "cover_duration_max"],
    "video_mix": ["head_tail", "head_min", "head_max", "tail_min", "tail_max", "slice_count_min", "slice_count_max", "slice_duration_min", "slice_duration_max", "mode", "mix_count", "cover_enabled", "cover_folder", "cover_duration_min", "cover_duration_max"],
    "video_resize": ["target_ratio", "process_mode", "blur_strength"],
    "video_enhance": ["level", "wink_exe", "include_images", "skip_existing", "retry", "timeout"],
    "keyword_remove": ["padding", "match_mode", "estimate_min_duration", "model_type"],
    "subtitle_generate": ["font_name", "font_size", "font_color", "outline_color", "outline_width", "position", "model_type", "enable_correction", "keep_srt"],
    "kaipai_process": ["params"],
    "video_fission": ["count", "intensity", "preset", "crf", "seed", "separate_folder", "max_workers"],
    "voice_profile_list": ["voices_dir"],
    "voice_profile_create": ["voices_dir"],
    "voice_profile_delete": ["voices_dir"],
    "voice_clone_apply": ["voices_dir", "model_dir", "conda_exe", "text_source", "asr_type", "asr_model_path", "speed"],
    "voice_synthesize": ["voices_dir", "model_dir", "conda_exe", "speed"],
}

for _operation_name, _option_names in OPERATION_OPTIONS.items():
    OPERATIONS[_operation_name]["options"] = _option_names

for _operation_name in (
    "video_enhance", "kaipai_process", "kaipai_download", "kaipai_quota",
    "video_download", "download_login",
):
    OPERATIONS[_operation_name]["external_service"] = True

for _operation_name in (
    "video_screenshot", "face_detection", "voice_profile_delete",
    "settings_update", "settings_secret_set",
):
    OPERATIONS[_operation_name]["supports_destructive_action"] = True

for _operation_name in (
    "video_enhance", "kaipai_process", "kaipai_download", "kaipai_quota",
    "video_download", "download_login", "voice_profile_delete",
    "settings_update", "settings_secret_set",
):
    OPERATIONS[_operation_name]["authorization_requirement"] = "always"

OPERATIONS["video_screenshot"]["authorization_requirement"] = {
    "when_any_option_true": ["delete_face_images", "delete_face_videos"]
}
OPERATIONS["face_detection"]["authorization_requirement"] = {
    "when_any_option_true": ["auto_delete"]
}


def operation_field_schema(name: str) -> Dict[str, Any]:
    """Return the machine-readable contract for a named input/option field."""
    boolean_fields = {
        "detect_text", "separate_folders", "detect_faces", "delete_face_images",
        "delete_face_videos", "auto_delete", "cover_enabled", "head_tail",
        "include_images", "skip_existing",
        "enable_correction", "keep_srt", "separate_folder",
    }
    integer_fields = {
        "frame_count", "max_workers", "sample_count", "level", "retry", "timeout",
        "font_size", "outline_width", "count", "crf", "seed", "blur_strength",
        "slice_count_min", "slice_count_max", "mode", "mix_count",
    }
    number_fields = {
        "min_duration", "max_duration", "frame_interval", "threshold",
        "min_face_ratio", "score_threshold", "cover_duration_min",
        "cover_duration_max", "head_min", "head_max", "tail_min", "tail_max",
        "slice_duration_min", "slice_duration_max", "padding", "speed",
        "estimate_min_duration",
    }
    array_fields = {"keywords", "items", "input_sources", "urls"}
    object_fields = {"params", "values"}
    enum_values = {
        "target_ratio": ["9:16", "3:4", "1:1", "9:16->3:4->9:16"],
        "process_mode": ["all", "mismatched"],
        "match_mode": ["segment", "keyword"],
        "position": ["上", "中", "下"],
        "model_type": ["FireRedASR", "FunASR"],
        "asr_type": ["FireRedASR", "FunASR"],
        "text_source": ["auto", "subtitle", "asr"],
        "intensity": ["mild", "medium", "strong"],
    }
    defaults = {
        "target_ratio": "9:16", "process_mode": "all", "blur_strength": 6,
        "frame_count": 5, "separate_folders": True, "detect_faces": False,
        "delete_face_images": False, "delete_face_videos": False,
        "auto_delete": False, "cover_enabled": False, "cover_duration_min": 0.5,
        "cover_duration_max": 1.0, "skip_existing": True, "speed": 1.0,
        "max_workers": 4,
    }
    schema: Dict[str, Any] = {"type": "string"}
    if name in boolean_fields:
        schema = {"type": "boolean"}
    elif name in integer_fields:
        schema = {"type": "integer"}
    elif name in number_fields:
        schema = {"type": "number"}
    elif name in array_fields:
        schema = {"type": "array"}
    elif name in object_fields:
        schema = {"type": "object"}
    if name in enum_values:
        schema = {"type": "string", "enum": enum_values[name]}
    if name in defaults:
        schema["default"] = defaults[name]
    return schema


for _operation_name, _spec in OPERATIONS.items():
    _spec["input_schema"] = {
        "type": "object",
        "required": list(_spec["required"]),
        "properties": {name: operation_field_schema(name) for name in _spec["required"]},
    }
    _spec["option_schema"] = {
        "type": "object",
        "properties": {name: operation_field_schema(name) for name in _spec.get("options", [])},
    }
    _spec["result_schema"] = {
        "type": "object",
        "properties": {
            "operation": {"type": "string"},
            "results": {"type": "array"},
            "outputs": {"type": "array", "items": {"type": "string"}},
        },
    }


def _inputs(request: Dict[str, Any]) -> Dict[str, Any]:
    return request.get("inputs") or request


def _options(request: Dict[str, Any]) -> Dict[str, Any]:
    return request.get("options") or {}


def _run_worker(worker, extra_signals: Iterable[str] = ()) -> Dict[str, Any]:
    completed: List[Any] = []
    errors: List[str] = []
    extras: Dict[str, List[Any]] = {name: [] for name in extra_signals}
    worker.finished.connect(lambda value: completed.append(value))
    worker.error.connect(lambda value: errors.append(str(value)))
    for name in extra_signals:
        signal = getattr(worker, name)
        signal.connect(lambda value, signal_name=name: extras[signal_name].append(value))
    worker.run()
    if errors:
        raise RuntimeError(errors[-1])
    return {"results": completed[-1] if completed else [], **extras}


def _collect_input_files(input_path: str, extensions: Iterable[str]) -> List[str]:
    if os.path.isfile(input_path):
        return [input_path]
    if not os.path.isdir(input_path):
        raise ValueError("输入路径不存在: {}".format(input_path))
    normalized = tuple(ext.lower() for ext in extensions)
    found = []
    for root, _dirs, files in os.walk(input_path):
        for name in files:
            if name.lower().endswith(normalized):
                found.append(os.path.join(root, name))
    return sorted(found)


def _video_slice(request):
    from core.slicer import VideoSlicer

    data, opts = _inputs(request), _options(request)
    engine = VideoSlicer(
        float(opts.get("min_duration", 3)),
        float(opts.get("max_duration", 5)),
        bool(opts.get("detect_text", False)),
    )
    path, output = data["input_path"], data["output_folder"]
    if os.path.isfile(path):
        results = engine.slice_video(path, output)
    else:
        results = engine.slice_folder(path, output, separate_folders=bool(opts.get("separate_folders", False)))
    return {"results": results, "outputs": [item["file"] for item in results]}


def _video_screenshot(request):
    from core.screenshot import extract_frames_from_folder

    data, opts = _inputs(request), _options(request)
    results = extract_frames_from_folder(
        data["input_path"], data["output_folder"],
        count_per_video=int(opts.get("frame_count", 5)),
        detect_faces=bool(opts.get("detect_faces", False)),
        delete_faces=bool(opts.get("delete_face_images", False)),
        delete_face_videos=bool(opts.get("delete_face_videos", False)),
        model_path=opts.get("model_path"),
        separate_folders=bool(opts.get("separate_folders", True)),
    )
    outputs = [image for item in results for image in item.get("images", [])]
    return {"results": results, "outputs": outputs}


def _text_recognition(request):
    from gui.text_recognition_tab import TextRecognitionWorker

    data, opts = _inputs(request), _options(request)
    worker = TextRecognitionWorker(
        data["input_path"], float(opts.get("frame_interval", 1.0)),
        float(opts.get("threshold", 0.3)), int(opts.get("max_workers", 4)),
    )
    return _run_worker(worker)


def _face_detection(request):
    from gui.face_detection_tab import FaceDetectionWorker

    data, opts = _inputs(request), _options(request)
    worker = FaceDetectionWorker(
        data["input_path"], float(opts.get("min_face_ratio", 2)),
        int(opts.get("sample_count", 8)), opts.get("model_path"),
        float(opts.get("score_threshold", 0.5)), bool(opts.get("auto_delete", False)),
        int(opts.get("max_workers", 2)),
    )
    return _run_worker(worker, ("video_done",))


def _audio_mix(request):
    from core.mixer import VideoMixer

    data, opts = _inputs(request), _options(request)
    engine = VideoMixer(
        bool(opts.get("cover_enabled", False)), opts.get("cover_folder", ""),
        float(opts.get("cover_duration_min", 0.5)), float(opts.get("cover_duration_max", 1.0)),
    )
    outputs = engine.mix_folder(data["clips_folder"], data["media_folder"], data["output_folder"])
    return {"results": outputs, "outputs": outputs}


def _video_mix(request):
    from core.video_mixer import VideoMixerEngine

    data, opts = _inputs(request), _options(request)
    config = {
        "video_folder": data["video_folder"], "clips_folder": data["clips_folder"],
        "output_folder": data["output_folder"], "head_tail": bool(opts.get("head_tail", False)),
        "head_min": float(opts.get("head_min", 0.2)), "head_max": float(opts.get("head_max", 0.5)),
        "tail_min": float(opts.get("tail_min", 0.2)), "tail_max": float(opts.get("tail_max", 0.5)),
        "slice_count_min": int(opts.get("slice_count_min", 1)), "slice_count_max": int(opts.get("slice_count_max", 3)),
        "slice_duration_min": float(opts.get("slice_duration_min", 1.0)),
        "slice_duration_max": float(opts.get("slice_duration_max", 3.0)),
        "mode": int(opts.get("mode", 0)), "mix_count": int(opts.get("mix_count", 1)),
        "cover_enabled": bool(opts.get("cover_enabled", False)), "cover_folder": opts.get("cover_folder", ""),
        "cover_duration_min": float(opts.get("cover_duration_min", 0.5)),
        "cover_duration_max": float(opts.get("cover_duration_max", 1.0)),
    }
    outputs = VideoMixerEngine(config).run()
    return {"results": outputs, "outputs": outputs}


def _video_resize(request):
    from core.video_resizer import VideoResizer, matches_target_size
    from utils.media_utils import collect_videos
    from utils.path_utils import build_output_path

    data, opts = _inputs(request), _options(request)
    target = opts.get("target_ratio", "9:16")
    engine = VideoResizer(target, int(opts.get("blur_strength", 6)))
    path, output_folder = data["input_path"], data["output_folder"]
    videos = collect_videos(path)
    if not videos:
        raise ValueError("输入路径中没有找到视频文件")
    if opts.get("process_mode", "all") == "mismatched":
        videos = [video for video in videos if not matches_target_size(video, target)]
    results = []
    root = path if os.path.isdir(path) else os.path.dirname(path)
    for video in videos:
        rel_base = os.path.splitext(os.path.relpath(video, root))[0]
        output = build_output_path(output_folder, rel_base, target.replace(":", "x"), dedupe=True)
        engine.resize_video(video, output)
        results.append({"input": video, "output": output, "ratio": target})
    return {"results": results, "outputs": [item["output"] for item in results]}


def _video_enhance(request):
    from core.wink_enhancer import find_wink_exe
    from gui.video_enhance_tab import VideoEnhanceWorker

    data, opts = _inputs(request), _options(request)
    worker = VideoEnhanceWorker(
        data["input_path"], data["output_folder"], int(opts.get("level", 2)),
        opts.get("wink_exe") or find_wink_exe(), bool(opts.get("include_images", False)),
        bool(opts.get("skip_existing", True)), int(opts.get("retry", 0)),
        int(opts.get("timeout", 1800)),
    )
    return _run_worker(worker, ("file_done", "summary"))


def _keyword_remove(request):
    from gui.keyword_remove_tab import KeywordRemoveWorker

    data, opts = _inputs(request), _options(request)
    worker = KeywordRemoveWorker(
        data["input_path"], data["output_folder"], data["keywords"],
        float(opts.get("padding", 0.15)), opts.get("match_mode", "segment"),
        float(opts.get("estimate_min_duration", 0.6)), opts.get("model_type", "FireRedASR"),
        data["model_path"],
    )
    return _run_worker(worker, ("video_done",))


def _subtitle_generate(request):
    from gui.subtitle_tab import SubtitleWorker

    data, opts = _inputs(request), _options(request)
    worker = SubtitleWorker(
        data["input_path"], data["output_folder"], opts.get("font_name", "Microsoft YaHei"),
        int(opts.get("font_size", 18)), opts.get("font_color", "#FFFFFF"),
        opts.get("outline_color", "#000000"), int(opts.get("outline_width", 2)),
        opts.get("position", "下"), data["model_path"], opts.get("model_type", "FireRedASR"),
        bool(opts.get("enable_correction", False)), bool(opts.get("keep_srt", False)),
    )
    return _run_worker(worker, ("video_done",))


def _kaipai_process(request):
    from gui.kaipai_cloud_tab import KaipaiWorker

    data, opts = _inputs(request), _options(request)
    extensions = (".jpg", ".jpeg", ".png", ".bmp", ".webp") if "图片" in data["task_name"] else (".mp4", ".avi", ".mov", ".mkv", ".flv")
    files = _collect_input_files(data["input_path"], extensions)
    if not files:
        raise ValueError("输入路径中没有找到任务支持的文件")
    return _run_worker(KaipaiWorker(files, data["task_name"], opts.get("params") or {}))


def _kaipai_download(request):
    import requests

    data = _inputs(request)
    os.makedirs(data["output_folder"], exist_ok=True)
    results = []
    for item in data["items"]:
        url = item["url"] if isinstance(item, dict) else item
        filename = item.get("filename") if isinstance(item, dict) else ""
        filename = os.path.basename(filename or os.path.basename(url.split("?", 1)[0]) or "kaipai_output")
        output = os.path.join(data["output_folder"], filename)
        try:
            response = requests.get(url, timeout=120)
            response.raise_for_status()
            with open(output, "wb") as stream:
                stream.write(response.content)
            results.append({"url": url, "output": output, "success": True})
        except Exception as exc:
            results.append({"url": url, "output": "", "success": False, "error": str(exc)})
    return {"results": results, "outputs": [item["output"] for item in results if item["success"]]}


def _kaipai_quota(_request):
    from gui.kaipai_cloud_tab import get_skill_client

    client = get_skill_client()
    result = client.wapi.request(
        "/skill/config.json", method="POST", body={"gid": "", "version": "v1.0.0"}
    )
    return {"results": [result]}


def _video_fission(request):
    from core.video_fission import VideoFission

    data, opts = _inputs(request), _options(request)
    sources = []
    for item in data["input_sources"]:
        if isinstance(item, str):
            sources.append((item, int(opts.get("count", 1))))
        else:
            sources.append((item["path"], int(item.get("count", 1))))
    engine = VideoFission(opts)
    results = engine.fission_folder(
        sources, data["output_folder"], bool(opts.get("separate_folder", True)),
        max_workers=opts.get("max_workers"),
    )
    outputs = [path for item in results for path in item.get("outputs", [])]
    return {"results": results, "outputs": outputs}


def _voice_library(data, opts=None):
    from core.voice_clone import VoiceLibrary

    opts = opts or {}
    return VoiceLibrary(opts.get("voices_dir") or data.get("voices_dir", r"D:\Models\CosyVoice3\voices"))


def _voice_profile_list(request):
    data, opts = _inputs(request), _options(request)
    return {"results": _voice_library(data, opts).list_profiles()}


def _voice_profile_create(request):
    data, opts = _inputs(request), _options(request)
    profile = _voice_library(data, opts).create(data["name"], data["reference_audio"], data["reference_text"])
    return {"results": [profile]}


def _voice_profile_delete(request):
    data, opts = _inputs(request), _options(request)
    _voice_library(data, opts).delete(data["profile_id"])
    return {"results": [{"profile_id": data["profile_id"], "deleted": True}]}


def _voice_clone_apply(request):
    from gui.config import get_config
    from gui.voice_clone_tab import DEFAULT_CONDA, DEFAULT_MODEL, VoiceCloneWorker

    data, opts = _inputs(request), _options(request)
    profiles = {item["id"]: item for item in _voice_library(data, opts).list_profiles()}
    if data["profile_id"] not in profiles:
        raise ValueError("音色不存在: {}".format(data["profile_id"]))
    root = os.path.dirname(os.path.abspath(__file__))
    model_type = opts.get("asr_type", "FireRedASR")
    default_asr = r"D:\Models\FireRed" if model_type == "FireRedASR" else r"D:\Models\FunASR\paraformer-large-zh-en-timestamp-onnx-offline"
    settings = {
        "input_dir": data["input_path"], "output_dir": data["output_folder"],
        "model_dir": opts.get("model_dir", DEFAULT_MODEL), "conda_exe": opts.get("conda_exe", DEFAULT_CONDA),
        "server_script": os.path.join(root, "services", "cosyvoice_server.py"),
        "text_source": opts.get("text_source", "auto"), "asr_type": model_type,
        "asr_model_dir": opts.get("asr_model_path", get_config("settings", "fireredasr_model_path", default_asr)),
        "speed": float(opts.get("speed", 1.0)),
    }
    return _run_worker(VoiceCloneWorker(settings, profiles[data["profile_id"]]))


def _voice_synthesize(request):
    from gui.voice_clone_tab import DEFAULT_CONDA, DEFAULT_MODEL
    from core.voice_clone import CosyVoiceService

    data, opts = _inputs(request), _options(request)
    profiles = {item["id"]: item for item in _voice_library(data, opts).list_profiles()}
    if data["profile_id"] not in profiles:
        raise ValueError("音色不存在: {}".format(data["profile_id"]))
    root = os.path.dirname(os.path.abspath(__file__))
    service = CosyVoiceService(
        opts.get("conda_exe", DEFAULT_CONDA), "cosyvoice3",
        os.path.join(root, "services", "cosyvoice_server.py"),
        opts.get("model_dir", DEFAULT_MODEL),
    )
    try:
        service.start()
        synthesis = service.synthesize(
            data["text"], profiles[data["profile_id"]], data["output_path"],
            float(opts.get("speed", 1.0)),
        )
    finally:
        service.stop()
    return {
        "results": [{"output": data["output_path"], "service_result": synthesis}],
        "outputs": [data["output_path"]],
    }


def _video_download(request):
    from core.taobao_downloader import close_shared_browser, download_video

    data = _inputs(request)
    urls = data["urls"] if isinstance(data["urls"], list) else [data["urls"]]
    results = []
    try:
        for url in urls:
            success, result = download_video(url, data["output_folder"])
            results.append({"url": url, "success": bool(success), "result": result})
    finally:
        close_shared_browser()
    return {"results": results, "outputs": [item["result"] for item in results if item["success"]]}


def _download_auth_status(_request):
    from core.taobao_downloader import check_auth_file

    result = check_auth_file()
    return {"results": [{"status": result}]}


def _download_login(_request):
    from core.taobao_downloader import login_and_save

    success, message = login_and_save()
    return {"results": [{"success": bool(success), "message": message}]}


def _settings_get(_request):
    from gui.config import reload_config

    return {"results": [reload_config()]}


def _settings_update(request):
    from gui.config import set_config

    data = _inputs(request)
    if not isinstance(data["values"], dict):
        raise ValueError("values 必须是对象")
    for key, value in data["values"].items():
        set_config(data["section"], key, value)
    return {"results": [{"section": data["section"], "updated_keys": sorted(data["values"])}]}


def _settings_secret_set(request):
    from gui.config import set_secret

    data = _inputs(request)
    set_secret(data["section"], data["key"], str(data["value"]))
    return {"results": [{"section": data["section"], "key": data["key"], "updated": True}]}


HANDLERS = {
    "video_slice": _video_slice, "video_screenshot": _video_screenshot,
    "text_recognition": _text_recognition, "face_detection": _face_detection,
    "audio_mix": _audio_mix, "video_mix": _video_mix, "video_resize": _video_resize,
    "video_enhance": _video_enhance, "keyword_remove": _keyword_remove,
    "subtitle_generate": _subtitle_generate, "kaipai_process": _kaipai_process,
    "kaipai_download": _kaipai_download, "kaipai_quota": _kaipai_quota,
    "video_fission": _video_fission, "voice_profile_list": _voice_profile_list,
    "voice_profile_create": _voice_profile_create, "voice_profile_delete": _voice_profile_delete,
    "voice_clone_apply": _voice_clone_apply, "voice_synthesize": _voice_synthesize,
    "video_download": _video_download, "download_auth_status": _download_auth_status,
    "download_login": _download_login, "settings_get": _settings_get,
    "settings_update": _settings_update, "settings_secret_set": _settings_secret_set,
}


def run_operation(operation: str, request: Dict[str, Any]) -> Dict[str, Any]:
    try:
        handler = HANDLERS[operation]
    except KeyError as exc:
        raise ValueError("不支持的 operation: {}".format(operation)) from exc
    result = handler(request)
    result.setdefault("operation", operation)
    return result
