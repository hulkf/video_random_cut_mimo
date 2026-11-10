"""Execute the enrolled browser portrait workflow inside a video task."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


OPERATION = "kaipai_portrait"
MATERIAL_ROOT = Path("D:/千川素材")
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


def sources(values, material_root=None):
    root = (material_root or MATERIAL_ROOT).resolve(strict=True)
    if not isinstance(values, list) or not 1 <= len(values) <= 20:
        raise ValueError("请提供 1 至 20 个明确的视频文件")
    files = []
    for value in values:
        candidate = Path(value)
        if not candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("需要款式素材目录下的绝对视频文件路径")
        relative = candidate.relative_to(root)
        if len(relative.parts) < 2 or candidate.resolve(strict=True) != candidate:
            raise ValueError("输入必须是款式素材目录内未重定向的文件")
        if not candidate.is_file() or candidate.suffix.lower() not in VIDEO_SUFFIXES:
            raise ValueError("输入不是受支持的视频文件")
        if candidate in files:
            raise ValueError("同一批中不能重复选择同一视频")
        files.append(candidate)
    return files


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _run(command, directory, log, context, timeout, *, env=None):
    context.check()
    with log.open("ab") as output, subprocess.Popen(
        command, cwd=directory, stdout=output, stderr=subprocess.STDOUT, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ) as process:
        try:
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                context.check()
                if time.monotonic() >= deadline:
                    raise TimeoutError("执行阶段超时，请检查原任务的本地或开拍进度")
                time.sleep(0.5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        if process.returncode:
            raise RuntimeError(f"执行阶段失败，请核查原任务（{log.name}）")


def run(request):
    context = request.get("_task_center_context")
    if context is None:
        raise PermissionError("开拍人像增强须由确认后的视频任务执行")
    files = sources(request["inputs"]["input_sources"])
    configured = os.environ.get("VIDEO_KAIPAI_PORTRAIT_SKILL_ROOT")
    if not configured:
        raise RuntimeError("当前执行入口未配置开拍人像增强工作流")
    skill = Path(configured).resolve(strict=True)
    scripts = skill / "scripts"
    ego = Path(os.environ["USERPROFILE"]) / ".local/share/ego/active_version_dir/ego-browser.exe"
    if not all(path.is_file() for path in (ego, scripts/"process.py", scripts/"kaipai.mjs", scripts/"verify.py")):
        raise RuntimeError("开拍执行组件未齐全")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("缺少视频准备和验收组件")
    key = hashlib.sha256(f"{context.task_id}\0{context.step_id}".encode()).hexdigest()[:24]
    directory = context.center.db_path.resolve().parent / "kaipai-portrait" / key
    directory.mkdir(parents=True, exist_ok=True)
    jobs = list(directory.glob("*/job.json"))
    if not jobs:
        _run([sys.executable, str(scripts/"process.py"), "--input", *map(str, files),
              "--output-root", str(directory), "--material-root", str(MATERIAL_ROOT)],
             directory, directory/"prepare.log", context, 3600)
        jobs = list(directory.glob("*/job.json"))
    if len(jobs) != 1:
        raise RuntimeError("任务没有唯一的本地准备记录，请核查原任务")
    job = jobs[0]
    plan = _read(job)
    if plan.get("local_stage") != "prepared" or [Path(entry["source"]) for entry in plan["files"]] != files:
        raise RuntimeError("原批次未完成准备，或素材清单已变化")
    state = job.parent / "kaipai-state.json"
    log = directory / "kaipai.log"
    if not state.is_file() or _read(state).get("stage") != "downloaded":
        launcher = job.with_name("kaipai-scoped.mjs")
        arguments = json.dumps([str(job), {"taskName": "本杰明-开拍-" + key}], ensure_ascii=False)
        launcher.write_text(
            f"const {{run}} = await import({json.dumps((scripts/'kaipai.mjs').as_uri())});\n"
            f"await run(...{arguments});\n", encoding="utf-8",
        )
        entry = launcher.resolve().as_uri()
        env = {key: os.environ[key] for key in
               ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PATH")
               if key in os.environ}
        try:
            _run([str(ego), "nodejs", "-e", f"await import({json.dumps(entry)})"],
                 directory, log, context, 2400, env=env)
        except (RuntimeError, TimeoutError) as exc:
            stage = _read(state).get("stage") if state.is_file() else "未开始上传"
            raise RuntimeError(f"开拍阶段 {stage} 中断，请核查原批次，避免重复提交") from exc
    verification = job.with_name("verification.json")
    if not verification.is_file() or not _read(state).get("localVerified"):
        _run([sys.executable, str(scripts/"verify.py"), str(job)], directory,
             directory/"verify.log", context, 3600)
    rows = _read(verification)["files"]
    if len(rows) != len(files) or not all(Path(row["output"]).is_file() for row in rows):
        raise RuntimeError("原批次成片缺失或验收数量不符")
    return {"outputs": [row["output"] for row in rows],
            "validation": [{"path": row["output"], **row} for row in rows]}
