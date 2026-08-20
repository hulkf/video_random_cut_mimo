#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无界面的视频工具入口，供 Hermes/其他 Agent 调用。

GUI 只是交互层；本模块只编排现有 core 引擎，不在 Agent 入口重复实现
FFmpeg 处理逻辑。所有 stdout 输出均为机器可读 JSON。
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from typing import Any, Dict


TOOL_NAME = "video-random-cut"
TOOL_VERSION = "0.1.0"

CAPABILITIES = {
    "tool": TOOL_NAME,
    "version": TOOL_VERSION,
    "operations": {
        "video_concat": {
            "description": "按文件名排序配对两个输入目录中的视频并拼接，可从 B 视频抽帧生成封面",
            "required": ["folder_a", "folder_b", "output_folder"],
            "options": [
                "cover_enabled", "cover_source", "cover_folder", "cover_mode",
                "cover_duration_min", "cover_duration_max", "require_9x16",
                "require_cover",
            ],
            "cover_source_values": ["folder", "video_b_frame"],
            "cover_mode_values": ["front", "back", "both"],
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
    if operation == "video_concat":
        options = request.get("options") or {}
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
        "cover_duration_min": options.get("cover_duration_min", 3.0),
        "cover_duration_max": options.get("cover_duration_max", 3.0),
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
    for output in outputs:
        info = _probe(output)
        checks = {
            "exists": info["exists"],
            "decodable": info["valid"],
            "resolution_1080x1920": info["width"] == 1080 and info["height"] == 1920,
        }
        if require_9x16 and not checks["resolution_1080x1920"]:
            raise RuntimeError(
                "输出校验失败：要求 1080x1920，实际 {}x{} ({})".format(
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
                base_duration = _probe(input_a[0])["duration"] + _probe(input_b[0])["duration"]
            cover_present = (
                config["cover_source"] == "video_b_frame"
                and config["cover_mode"] in ("front", 0, "0")
                and info["duration"] + 0.05 >= base_duration + float(config["cover_duration_min"])
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
            "all_1080x1920": all(item["checks"]["resolution_1080x1920"] for item in validated),
            "all_cover_valid": all(item["checks"]["cover_present"] for item in validated),
        },
    }


def _run_validate(request: Dict[str, Any]) -> Dict[str, Any]:
    inputs = request.get("inputs") or request
    path = inputs["path"]
    return {"operation": "validate", "validation": _probe(path)}


def run_request(request: Dict[str, Any]) -> Dict[str, Any]:
    _validate_request(request)
    operation = request["operation"]
    result = _run_concat(request) if operation == "video_concat" else _run_validate(request)
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
        return _emit(run_request(_load_request(args.request)))
    except Exception as exc:
        return _emit({
            "success": False,
            "tool": TOOL_NAME,
            "version": TOOL_VERSION,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }, 2)


if __name__ == "__main__":
    raise SystemExit(main())
