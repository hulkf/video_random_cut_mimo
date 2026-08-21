#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无界面的视频工具入口，供 Hermes/其他 Agent 调用。

GUI 只是交互层；本模块只编排现有 core 引擎，不在 Agent 入口重复实现
FFmpeg 处理逻辑。所有 stdout 输出均为机器可读 JSON。
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict

from headless_operations import OPERATIONS as TAB_OPERATIONS
from headless_operations import operation_field_schema
from headless_operations import run_operation as run_tab_operation


TOOL_NAME = "video-random-cut"
TOOL_VERSION = "0.1.0"

CAPABILITIES = {
    "tool": TOOL_NAME,
    "version": TOOL_VERSION,
    "operations": {
        "video_concat": {
            "description": "按文件名排序配对两个输入目录中的视频并拼接，可从 B 视频抽帧生成封面",
            "tab": "视频拼接",
            "required": ["folder_a", "folder_b", "output_folder"],
            "options": [
                "cover_enabled", "cover_source", "cover_folder", "cover_mode",
                "cover_duration_min", "cover_duration_max", "require_9x16",
                "require_cover",
            ],
            "cover_source_values": ["folder", "video_b_frame"],
            "cover_mode_values": ["front", "back", "both"],
        },
        "qianchuan_concat": {
            "description": "千川拼接闭环：A/B 输入先统一为 9:16，再调用原有视频拼接逻辑",
            "required": ["folder_a", "folder_b"],
            "options": [
                "cover_enabled", "cover_source", "cover_mode",
                "cover_duration_min", "cover_duration_max", "require_cover",
                "blur_strength",
            ],
        },
        "validate": {
            "description": "检查视频是否可解析、时长是否大于 0，并返回媒体信息",
            "required": ["path"],
        },
    },
    "constraints": {
        "headless": True,
        "json_only": True,
        "gui_required": False,
        "direct_ffmpeg_from_agent": False,
        "explicit_authorization_field": "authorization.confirmed + authorization.scope",
    },
}

# GUI 业务标签页的正式 Agent 能力清单。实现复用现有 core/worker，
# 无需启动桌面窗口，也不在 Agent 侧重写视频处理算法。
CAPABILITIES["operations"].update({
    name: {
        **spec,
        "description": "{}标签页的无界面能力".format(spec["tab"]),
    }
    for name, spec in TAB_OPERATIONS.items()
})

for _name, _spec in CAPABILITIES["operations"].items():
    _spec.setdefault("input_schema", {
        "type": "object",
        "required": list(_spec["required"]),
        "properties": {key: operation_field_schema(key) for key in _spec["required"]},
    })
    _spec.setdefault("option_schema", {
        "type": "object",
        "properties": {key: operation_field_schema(key) for key in _spec.get("options", [])},
    })
    _spec.setdefault("result_schema", {
        "type": "object",
        "properties": {
            "operation": {"type": "string"},
            "outputs": {"type": "array", "items": {"type": "string"}},
        },
    })

for _operation_name in ("video_concat", "qianchuan_concat"):
    _properties = CAPABILITIES["operations"][_operation_name]["option_schema"]["properties"]
    _properties.update({
        "cover_enabled": {"type": "boolean", "default": True},
        "cover_source": {"type": "string", "enum": ["folder", "video_b_frame"], "default": "video_b_frame"},
        "cover_mode": {"type": ["string", "integer"], "enum": ["front", "back", "both", 0, 1, 2], "default": "front"},
        "cover_duration_min": {"type": "number", "default": 0.2, "exclusiveMinimum": 0},
        "cover_duration_max": {"type": "number", "default": 0.5, "exclusiveMinimum": 0},
        "require_9x16": {"type": "boolean", "default": True},
        "require_cover": {"type": "boolean", "default": True},
    })
CAPABILITIES["operations"]["qianchuan_concat"]["option_schema"]["properties"]["blur_strength"] = {
    "type": "integer", "default": 6, "minimum": 0,
}

CAPABILITIES["operations"]["qianchuan_concat"]["input_schema"]["properties"]["output_folder"] = {
    "type": "string",
    "description": "可省略；若提供，必须等于工具按 <货号> 千川素材 <MMDD> 推导出的目录",
}
CAPABILITIES["operations"]["validate"]["result_schema"] = {
    "type": "object",
    "required": ["operation", "validation"],
    "properties": {
        "operation": {"type": "string"},
        "validation": {"type": "object"},
    },
}
CAPABILITIES["operations"]["video_concat"]["result_schema"] = {
    "type": "object",
    "required": ["operation", "outputs", "validation", "summary"],
    "properties": {
        "operation": {"type": "string"},
        "outputs": {"type": "array", "items": {"type": "string"}},
        "validation": {"type": "array"},
        "summary": {"type": "object"},
    },
}
CAPABILITIES["operations"]["qianchuan_concat"]["result_schema"] = {
    "type": "object",
    "required": ["operation", "outputs", "validation", "summary", "normalization", "output_folder"],
    "properties": {
        **CAPABILITIES["operations"]["video_concat"]["result_schema"]["properties"],
        "normalization": {"type": "object"},
        "output_folder": {"type": "string"},
    },
}


def _emit(payload: Dict[str, Any], exit_code: int = 0) -> int:
    # 机器接口不依赖 Windows 当前控制台代码页；中文以 JSON 转义形式输出。
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return exit_code


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def health() -> Dict[str, Any]:
    checks = {}
    for name in ("ffmpeg", "ffprobe"):
        path = shutil.which(name)
        checks[name] = {"available": bool(path), "path": path or ""}
    try:
        importlib.import_module("core.video_concatenator")
        checks["core_import"] = {"available": True}
    except Exception as exc:  # pragma: no cover - 环境依赖异常时由真实命令覆盖
        checks["core_import"] = {"available": False, "error": str(exc)}
    try:
        importlib.import_module("headless_operations")
        checks["headless_operations"] = {"available": True}
    except Exception as exc:  # pragma: no cover - 环境依赖异常时由真实命令覆盖
        checks["headless_operations"] = {"available": False, "error": str(exc)}
    healthy = all(item.get("available") for item in checks.values())
    return {
        "success": healthy,
        "tool": TOOL_NAME,
        "version": TOOL_VERSION,
        "checked_at": _utc_now(),
        "checks": checks,
    }


def _probe(path: str) -> Dict[str, Any]:
    from utils.media_utils import probe_video

    info = probe_video(path)
    return {
        "path": os.path.abspath(path),
        "exists": os.path.isfile(path),
        "size_bytes": os.path.getsize(path) if os.path.isfile(path) else 0,
        **info,
        "valid": bool(info.get("duration", 0) > 0),
    }


def _is_9x16(width: int, height: int) -> bool:
    """Return whether a pixel-sized video has an exact 9:16 ratio."""
    return width > 0 and height > 0 and width * 16 == height * 9


def _validate_request(request: Dict[str, Any]) -> None:
    if not isinstance(request, dict):
        raise ValueError("request 必须是 JSON 对象")
    operation = request.get("operation")
    if operation not in CAPABILITIES["operations"]:
        raise ValueError("不支持的 operation: {}".format(operation))
    inputs = request.get("inputs") or request
    required = CAPABILITIES["operations"][operation]["required"]
    missing = [key for key in required if not inputs.get(key)]
    if missing:
        raise ValueError("缺少必要参数: {}".format(", ".join(missing)))
    options = request.get("options") or {}
    spec = CAPABILITIES["operations"][operation]
    authorization_required = bool(spec.get("external_service"))
    authorization_required = authorization_required or operation in {
        "voice_profile_delete", "settings_update", "settings_secret_set", "download_login",
    }
    if operation == "video_screenshot":
        authorization_required = authorization_required or bool(
            options.get("delete_face_images") or options.get("delete_face_videos")
        )
    if operation == "face_detection":
        authorization_required = authorization_required or bool(options.get("auto_delete"))
    if authorization_required:
        authorization = request.get("authorization") or {}
        scope = authorization.get("scope")
        if authorization.get("confirmed") is not True or scope not in (operation, "*"):
            raise PermissionError(
                "operation {} 需要显式授权：authorization.confirmed=true 且 scope={}（或 *）".format(
                    operation, operation
                )
            )
    if operation in ("video_concat", "qianchuan_concat"):
        cover_source = options.get("cover_source", "video_b_frame")
        if cover_source not in ("folder", "video_b_frame"):
            raise ValueError("不支持的 cover_source: {}".format(cover_source))
        cover_mode = options.get("cover_mode", "front")
        if cover_mode not in ("front", "back", "both", 0, 1, 2, "0", "1", "2"):
            raise ValueError("不支持的 cover_mode: {}".format(cover_mode))
        for key in ("cover_duration_min", "cover_duration_max"):
            if key in options:
                try:
                    if float(options[key]) <= 0:
                        raise ValueError
                except (TypeError, ValueError):
                    raise ValueError("{} 必须是大于 0 的数字".format(key)) from None


def _run_concat(request: Dict[str, Any]) -> Dict[str, Any]:
    from core.video_concatenator import VideoConcatenatorEngine

    inputs = request.get("inputs") or request
    options = request.get("options") or {}
    config = {
        "folder_a": inputs["folder_a"],
        "folder_b": inputs["folder_b"],
        "output_folder": inputs["output_folder"],
        "cover_enabled": options.get("cover_enabled", True),
        "cover_source": options.get("cover_source", "video_b_frame"),
        "cover_folder": options.get("cover_folder", ""),
        "cover_mode": options.get("cover_mode", "front"),
        "cover_duration_min": options.get("cover_duration_min", 0.2),
        "cover_duration_max": options.get("cover_duration_max", 0.5),
    }
    os.makedirs(config["output_folder"], exist_ok=True)
    engine = VideoConcatenatorEngine(config)
    outputs = engine.run()
    require_9x16 = options.get("require_9x16", True)
    require_cover = options.get("require_cover", True)
    if require_cover and not config["cover_enabled"]:
        raise ValueError("成品标准要求封面，不能关闭 cover_enabled")
    input_a = engine.get_videos(config["folder_a"])
    input_b = engine.get_videos(config["folder_b"])
    validated = []
    for output_index, output in enumerate(outputs):
        info = _probe(output)
        checks = {
            "exists": info["exists"],
            "decodable": info["valid"],
            "aspect_ratio_9x16": _is_9x16(info["width"], info["height"]),
        }
        if require_9x16 and not checks["aspect_ratio_9x16"]:
            raise RuntimeError(
                "输出校验失败：要求 9:16，实际 {}x{} ({})".format(
                    info["width"], info["height"], output
                )
            )
        if not checks["exists"] or not checks["decodable"]:
            raise RuntimeError("输出校验失败：文件不存在或不可解码 ({})".format(output))
        cover_present = True
        if require_cover:
            # VideoConcatenatorEngine 的 video_b_frame + front 封面会在 A+B
            # 基础时长上增加 cover_duration_min；用媒体时长验证封面未被静默丢弃。
            base_duration = 0.0
            if input_a and input_b:
                paired_a = input_a[output_index % len(input_a)]
                paired_b = input_b[output_index % len(input_b)]
                base_duration = _probe(paired_a)["duration"] + _probe(paired_b)["duration"]
            cover_min = float(config["cover_duration_min"])
            # 编码、时间基和 -shortest 会造成约 0.05~0.10 秒尾差；容差必须
            # 足够覆盖真实误差，但仍要小于片头最短时长，避免无片头也通过。
            duration_tolerance = min(0.15, cover_min * 0.75)
            cover_present = (
                config["cover_source"] == "video_b_frame"
                and config["cover_mode"] in ("front", 0, "0")
                and info["duration"] + duration_tolerance >= base_duration + cover_min
            )
            if not cover_present:
                raise RuntimeError("输出校验失败：未确认片头封面存在或时长不正确 ({})".format(output))
        info["checks"] = {**checks, "cover_present": cover_present}
        validated.append(info)
    return {
        "operation": "video_concat",
        "outputs": [item["path"] for item in validated],
        "validation": validated,
        "summary": {
            "count": len(validated),
            "all_decodable": all(item["checks"]["decodable"] for item in validated),
            "all_9x16": all(item["checks"]["aspect_ratio_9x16"] for item in validated),
            "all_cover_valid": all(item["checks"]["cover_present"] for item in validated),
        },
    }


def _normalize_folder_9x16(input_folder: str, output_folder: str, blur_strength: int) -> Dict[str, Any]:
    from core.video_resizer import VideoResizer
    from utils.media_utils import collect_videos, probe_video

    videos = collect_videos(input_folder)
    if not videos:
        raise ValueError("输入文件夹中没有视频: {}".format(input_folder))
    os.makedirs(output_folder, exist_ok=True)
    engine = VideoResizer("9:16", blur_strength)
    converted = copied = 0
    used_names = set()
    for index, video in enumerate(videos):
        stem, extension = os.path.splitext(os.path.basename(video))
        info = probe_video(video)
        display_width = info.get("display_width", info.get("width", 0))
        display_height = info.get("display_height", info.get("height", 0))
        output_name = stem + (extension.lower() if _is_9x16(display_width, display_height) else ".mp4")
        if output_name.lower() in used_names:
            output_name = "{:06d}_{}".format(index, output_name)
        used_names.add(output_name.lower())
        output = os.path.join(output_folder, output_name)
        if _is_9x16(display_width, display_height) and not info.get("rotation", 0):
            shutil.copy2(video, output)
            copied += 1
        else:
            engine.resize_video(video, output)
            converted += 1
    return {"input_count": len(videos), "converted": converted, "copied": copied}


def _qianchuan_output_folder(folder_a: str, folder_b: str, requested: str = "") -> str:
    """Derive and enforce `<货号> 千川素材 <MMDD>` beside the two source folders."""
    absolute_a = os.path.abspath(folder_a)
    absolute_b = os.path.abspath(folder_b)
    parent_a = os.path.dirname(os.path.normpath(absolute_a))
    parent_b = os.path.dirname(os.path.normpath(absolute_b))
    if os.path.normcase(parent_a) != os.path.normcase(parent_b):
        raise ValueError("千川 A/B 目录必须位于同一货号目录下")
    if "模特" not in os.path.basename(os.path.normpath(absolute_a)):
        raise ValueError("千川 A 目录名必须包含“模特”关键词")
    if "平铺" not in os.path.basename(os.path.normpath(absolute_b)):
        raise ValueError("千川 B 目录名必须包含“平铺”关键词")
    common_parent = parent_a
    cargo_number = os.path.basename(os.path.normpath(common_parent))
    expected = os.path.join(
        common_parent,
        "{} 千川素材 {}".format(cargo_number, datetime.now().strftime("%m%d")),
    )
    if requested and os.path.normcase(os.path.abspath(requested)) != os.path.normcase(os.path.abspath(expected)):
        raise ValueError("千川输出目录必须为工具推导目录: {}".format(expected))
    return expected


def _run_qianchuan_concat(request: Dict[str, Any]) -> Dict[str, Any]:
    inputs = request.get("inputs") or request
    options = dict(request.get("options") or {})
    output_folder = _qianchuan_output_folder(
        inputs["folder_a"], inputs["folder_b"], inputs.get("output_folder", "")
    )
    with tempfile.TemporaryDirectory(prefix="video_tool_qianchuan_") as work_folder:
        folder_a = os.path.join(work_folder, "a_9x16")
        folder_b = os.path.join(work_folder, "b_9x16")
        blur_strength = int(options.pop("blur_strength", 6))
        normalization = {
            "folder_a": _normalize_folder_9x16(inputs["folder_a"], folder_a, blur_strength),
            "folder_b": _normalize_folder_9x16(inputs["folder_b"], folder_b, blur_strength),
        }
        options["require_9x16"] = True
        concat_request = {
            "operation": "video_concat",
            "inputs": {
                "folder_a": folder_a,
                "folder_b": folder_b,
                "output_folder": output_folder,
            },
            "options": options,
        }
        result = _run_concat(concat_request)
        result["operation"] = "qianchuan_concat"
        result["output_folder"] = output_folder
        result["normalization"] = normalization
        return result


def _run_validate(request: Dict[str, Any]) -> Dict[str, Any]:
    inputs = request.get("inputs") or request
    path = inputs["path"]
    return {"operation": "validate", "validation": _probe(path)}


def run_request(request: Dict[str, Any]) -> Dict[str, Any]:
    _validate_request(request)
    operation = request["operation"]
    if operation == "video_concat":
        result = _run_concat(request)
    elif operation == "qianchuan_concat":
        result = _run_qianchuan_concat(request)
    elif operation == "validate":
        result = _run_validate(request)
    else:
        result = run_tab_operation(operation, request)
    return {"success": True, "tool": TOOL_NAME, "version": TOOL_VERSION, **result}


def _load_request(path: str) -> Dict[str, Any]:
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as stream:
        return json.load(stream)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog=TOOL_NAME)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health")
    subparsers.add_parser("capabilities")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--request", required=True, help="JSON 文件路径，或 - 表示 stdin")
    args = parser.parse_args(argv)
    try:
        if args.command == "health":
            result = health()
            return _emit(result, 0 if result["success"] else 3)
        if args.command == "capabilities":
            return _emit({"success": True, **CAPABILITIES})
        # 旧 core/worker 中仍有少量 print；统一转到 stderr，保证 stdout
        # 始终只有一个可解析 JSON，便于其他 Agent 稳定调用。
        with contextlib.redirect_stdout(sys.stderr):
            result = run_request(_load_request(args.request))
        return _emit(result)
    except Exception as exc:
        return _emit({
            "success": False,
            "tool": TOOL_NAME,
            "version": TOOL_VERSION,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }, 2)


if __name__ == "__main__":
    raise SystemExit(main())
