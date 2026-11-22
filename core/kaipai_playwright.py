"""Run the workspace's Kaipai web workflow from a confirmed video task."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

from core.kaipai_cli import _run
from core.kaipai_portrait import sources

OPERATION = "kaipai_playwright_process"
WEB_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi"}
PROCESS_ENV = {
    "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "PROGRAMDATA", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "PATH", "PATHEXT", "TEMP", "TMP",
}


def _environment():
    return {key: value for key, value in os.environ.items() if key.upper() in PROCESS_ENV}


def _installation():
    root = Path(os.environ.get("VIDEO_KAIPAI_PLAYWRIGHT_ROOT", "D:/JR_project/playwright_script")).resolve()
    python = root / ".venv" / "Scripts" / "python.exe"
    task = root / "item-kaipai"
    profile = root / ".browser-profile" / "eagle-agent"
    if not python.is_file() or not (task / "kaipai" / "cli.py").is_file():
        raise RuntimeError("Playwright 开拍执行环境未安装")
    return python, task, profile


def _inputs(values):
    files = sources(values)
    if any(file.suffix.lower() not in WEB_SUFFIXES for file in files):
        raise ValueError("开拍网页支持 MP4、MOV、M4V、AVI 视频")
    if len({file.stem.casefold() for file in files}) != len(files):
        raise ValueError("同批上传的视频须有不同的文件名")
    return files


def capabilities():
    python, task, _ = _installation()
    catalog = subprocess.run(
        [str(python), "-B", "-X", "utf8", "-m", "kaipai", "capabilities"], cwd=task,
        capture_output=True, check=True, timeout=12, encoding="utf-8", env=_environment(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return [
        {"effect": item["key"], "name": item["name"]}
        for item in json.loads(catalog.stdout) if item["workflow"] == "video-batch"
    ]


def request_sources(inputs, center):
    if "upstream" not in inputs:
        return _inputs(inputs["input_sources"])
    if inputs["effect"] != "goods":
        raise ValueError("网页接续只可使用商品画质修复")
    from core.kaipai_handoff import resolve

    path = resolve(center, inputs["upstream"], "smartedit")
    if [Path(value) for value in inputs["input_sources"]] != [path]:
        raise ValueError("商品修复输入与已验收网感成片不一致")
    stat = path.stat()
    if inputs.get("source_revision") != f"{stat.st_size}:{stat.st_mtime_ns}":
        raise ValueError("已验收网感成片在计划后发生变化")
    return [path]


def validate_inputs(inputs, center=None):
    if inputs["effect"] not in {item["effect"] for item in capabilities()}:
        raise ValueError("Playwright 未支持这个可以提交的视频效果")
    return request_sources(inputs, center)


def run(request):
    context = request.get("_task_center_context")
    grant = request.get("authorization") or {}
    if not context or grant.get("confirmed") is not True or grant.get("scope") != OPERATION:
        raise PermissionError("开拍 Playwright 须通过已确认的视频任务执行")
    inputs = request["inputs"]
    files = request_sources(inputs, context.center)
    python, task, profile = _installation()
    identity = hashlib.sha256(f"{context.task_id}:{context.step_id}".encode()).hexdigest()[:24]
    directory = context.center.db_path.resolve().parent / "kaipai-playwright" / identity
    directory.mkdir(parents=True, exist_ok=True)
    run_dir = directory / "run"
    state_file = run_dir / "state.json"
    output_dir = directory / "results"
    common = [str(python), "-B", "-X", "utf8", "-m"]
    if run_dir.exists() and not state_file.is_file():
        raise RuntimeError("原运行目录已有记录，状态文件不可读，需核查原任务")
    if state_file.is_file():
        previous = json.loads(state_file.read_text(encoding="utf-8"))
        previous_sources = [item.get("source") for item in previous.get("items", [previous])]
        if previous.get("effect") != inputs["effect"] or previous_sources != [str(file) for file in files]:
            raise RuntimeError("确认的效果或素材与原开拍批次不一致")
        command = ["resume", "--run-dir", str(run_dir)]
    else:
        command = [
            "run", "--run-dir", str(run_dir), "--browser", "ego-lite",
            "--eagle-server", "playwright-agent", "--port-file", str(profile / "DevToolsActivePort"),
            "--effect", inputs["effect"], "--output-dir", str(output_dir),
        ]
        for file in files:
            command.extend(["--input", str(file)])
    env = _environment()
    code, _, _ = _run(common, env, directory, "eagle-start",
                      ["eagle_playwright", "start", "--profile-dir", str(profile)],
                      context.check, timeout=40, cwd=task)
    if code:
        raise RuntimeError("专用 Eagle 未就绪；请核查原任务及 eagle-start.stderr")
    code, _, _ = _run(common, env, directory, "playwright",
                      ["kaipai", *command], context.check, timeout=1800, cwd=task)
    if code or not state_file.is_file():
        stage = json.loads(state_file.read_text(encoding="utf-8")).get("stage") if state_file.is_file() else None
        raise RuntimeError(f"开拍 Playwright 执行中断（{stage or '尚无回执'}），请核查原任务及 playwright.stderr")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    if state.get("stage") != "verified" or state.get("effect") != inputs["effect"]:
        raise RuntimeError("原开拍任务尚未返回指定效果的已验收成片")
    items = state.get("items", [state])
    if [item.get("source") for item in items] != [str(file) for file in files]:
        raise RuntimeError("开拍原批次文件与确认的素材不匹配")
    outputs = []
    for item in items:
        path = Path(item["output"]).resolve(strict=True)
        if not path.is_relative_to(output_dir) or not path.is_file() or not item.get("result_sha256"):
            raise RuntimeError("开拍成片路径或验收回执无效")
        outputs.append(str(path))
    return {"outputs": outputs, "batch_url": state.get("batch_url"), "effect": state["effect"]}
