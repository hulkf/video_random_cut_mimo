"""Official, mode-scoped Kaipai CLI execution for the video task center."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit

from core.kaipai_portrait import MATERIAL_ROOT, VIDEO_SUFFIXES

OPERATION = "kaipai_cli_process"
MODES = {
    "video-repair": ("tool", "video-repair"),
    "video-remove-auto": ("tool", "video-remove-auto"),
    "video-remove-subtitle": ("tool", "video-remove-subtitle"),
    "video-remove-watermark": ("tool", "video-remove-watermark"),
    "subtitle": ("subtitle",),
    "image-repair": ("tool", "image-repair"),
    "image-remove-text": ("tool", "image-remove-text"),
    "smartedit": ("smartedit",),
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
REJECTED = {400000, 404000, 422000}


def template_id(inputs):
    value = inputs.get("template_material_id")
    if inputs.get("mode") == "smartedit":
        if not isinstance(value, str) or not re.fullmatch(r"SEC[0-9]+", value):
            raise ValueError("网感剪辑必须指定选型结果中的 SEC 模板 ID")
        return value
    if value is not None:
        raise ValueError("仅网感剪辑支持 SEC 模板 ID")
    return None


def source_path(value, mode):
    if mode not in MODES:
        raise ValueError("开拍 CLI 模式未开放")
    path = Path(value)
    suffixes = IMAGE_SUFFIXES if mode.startswith("image-") else VIDEO_SUFFIXES
    if (not path.is_absolute() or path.resolve(strict=True) != path
            or not path.is_relative_to(MATERIAL_ROOT.resolve())
            or not path.is_file() or path.suffix.lower() not in suffixes):
        raise ValueError("请选择款式素材目录下的具体开拍素材文件")
    return path


def cli_environment():
    appdata = Path(os.environ["APPDATA"])
    entry = appdata / "npm/node_modules/meitu-kaipai-cli/bin/kaipai.js"
    if not entry.is_file():
        raise RuntimeError("开拍 CLI 未安装，尚未提交")
    config = os.environ.get("VIDEO_KAIPAI_CLI_CONFIG_DIR", "")
    if not config or not Path(config).is_absolute():
        raise RuntimeError("未配置本杰明开拍 CLI 授权目录，尚未提交")
    env = {key: os.environ[key] for key in
           ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PATH")
           if key in os.environ}
    env["KAIPAI_CONFIG_DIR"] = config
    return ["node", str(entry)], env


def _run(command, env, directory, name, args, check, timeout=300, *, cwd=None):
    check()
    out = directory / (name + ".jsonl")
    err = directory / (name + ".stderr")
    with out.open("wb") as stdout, err.open("wb") as stderr:
        with subprocess.Popen(
            [*command, *args], stdout=stdout, stderr=stderr, env=env, cwd=cwd or directory,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as process:
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    check()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("开拍 CLI 查询或执行超时，请回查原任务")
                    time.sleep(.25)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            return process.returncode, _events(out), _events(err)


def _events(path):
    records = []
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def _task_ids(records):
    ids = []
    for record in records:
        data = record.get("data")
        if isinstance(data, dict) and record.get("event") in {"execution.result", "execution.accepted"}:
            ids.extend(data.get("task_ids") or [])
        if record.get("type") == "end":
            ids.extend(record.get("task_ids") or [])
    return list(dict.fromkeys(value for value in ids if isinstance(value, str) and value))


def _history_task_ids(records):
    active = [task_id for row in records for task_id in (row.get("active_task_ids") or [])
              if isinstance(task_id, str) and task_id.startswith("tool_")]
    return list(dict.fromkeys([*_task_ids(records), *active]))


def _rejection(records):
    errors = [record.get("meta") or record.get("error") for record in records]
    for error in errors:
        if not isinstance(error, dict) or error.get("code") not in REJECTED:
            continue
        message = str(error.get("msg") or error.get("message") or "")
        if message == "SENSITIVE_MEDIA_CONTENT":
            return "开拍拒绝该素材（SENSITIVE_MEDIA_CONTENT），停止处理"
        if error["code"] == 400000 and message == "invalid reference":
            return "开拍拒绝素材引用（invalid reference），需核对原因后处理"
        return "开拍明确拒绝本次请求，未受理"
    return ""


def _artifact(records, task_id, image, expected_template=None):
    for task in reversed(records):
        if task.get("id") != task_id or (task.get("status") or {}).get("state") != "TASK_STATE_COMPLETED":
            continue
        for artifact in task.get("artifacts", []):
            parts = artifact.get("parts", [])
            if expected_template:
                templates = {
                    data["template_material_id"] for part in parts
                    if isinstance((data := part.get("data")), dict)
                    and isinstance(data.get("template_material_id"), str)
                }
                if templates != {expected_template}:
                    continue
            for part in parts:
                url = part.get("url")
                mime = str(part.get("mediaType", ""))
                if (isinstance(url, str) and urlsplit(url).scheme == "https"
                        and mime.startswith("image/" if image else "video/")):
                    return url
    if expected_template:
        raise RuntimeError("开拍原任务未返回与所选 SEC 模板一致的成片，需核对原任务")
    raise RuntimeError("开拍原任务未返回已完成的媒体产物，请继续查询原任务")


def _session_id(records):
    rooms = set()
    for row in records:
        data = row.get("data")
        value = row.get("session_id") or (data.get("session_id") if isinstance(data, dict) else None)
        if isinstance(value, str) and value:
            rooms.add(value)
    if len(rooms) != 1:
        return None
    return rooms.pop()


def _claim(marker, room=None):
    with marker.open("x", encoding="utf-8") as file:
        json.dump({"room": room}, file)
        file.flush()
        os.fsync(file.fileno())


def _submit(cli, marker, mode, source, image, selected):
    if marker.is_file():
        return json.loads(marker.read_text(encoding="utf-8"))["room"]
    if selected:
        args = (*MODES[mode], "--video-file", str(source), "--template-material-id", selected)
        code, rows, _ = cli("validate", *args, "--validate-only", "--json", timeout=30)
        if code or not rows or rows[-1].get("params", {}).get("templateMaterialId") != selected:
            raise RuntimeError("开拍 CLI 未能验证指定的模板 ID，尚未提交")
    code, _, _ = cli("auth", "auth", "status", "--check", timeout=30)
    if code:
        raise RuntimeError("开拍 CLI 未授权或连接失败，尚未提交")
    if selected:
        # SmartEdit creates its own room. The marker protects the paid call
        # even if the process stops before its first session event is written.
        _claim(marker)
        cli("submit", *args, "--detach", timeout=420)
        return None
    code, _, diagnostics = cli("room", "create-room", timeout=45)
    room = _session_id(diagnostics)
    if code or not room:
        raise RuntimeError("开拍 CLI 创建会话失败，尚未提交")
    _claim(marker, room)
    cli("submit", *MODES[mode], "-r", room, "--image-file" if image else "--video-file",
        str(source), timeout=420)
    return room


def run(request):
    context = request.get("_task_center_context")
    if context is None:
        raise PermissionError("开拍 CLI 须通过已确认的视频任务执行")
    inputs = request["inputs"]
    mode = inputs["mode"]
    selected = template_id(inputs)
    source = source_path(inputs["input_path"], mode)
    stat = source.stat()
    revision = f"{stat.st_size}:{stat.st_mtime_ns}"
    if selected and inputs.get("source_revision", revision) != revision:
        raise RuntimeError("网感剪辑原片已变化，请核对原任务和选型，尚未重新提交")
    image = mode.startswith("image-")
    command, env = cli_environment()
    key = hashlib.sha256(f"{context.task_id}:{context.step_id}".encode()).hexdigest()[:24]
    directory = context.center.db_path.resolve().parent / "kaipai-cli" / key
    directory.mkdir(parents=True, exist_ok=True)

    def cli(name, *args, timeout=300):
        return _run(command, env, directory, name, args, context.check, timeout)

    room = _submit(cli, directory / "submit-once.json", mode, source, image, selected)
    records = _events(directory / "submit.jsonl") + _events(directory / "submit.stderr")
    room = room or _session_id(records)
    ids = _task_ids(records)
    if selected and room and not ids and not _rejection(records):
        for name in ("history-snapshot", "history-watch"):
            ids = _history_task_ids(_events(directory / f"{name}.jsonl") + _events(directory / f"{name}.stderr"))
            if ids:
                break
        if not ids:
            _, snapshot, diagnostics = cli("history-snapshot", "history-detail", "-r", room, timeout=30)
            ids = _history_task_ids(snapshot + diagnostics)
        if not ids:
            _, snapshot, diagnostics = cli("history-watch", "history-detail", "-r", room,
                                           "--watch", "--yield-on-update", timeout=90)
            ids = _history_task_ids(snapshot + diagnostics)
    if not room or not ids:
        error = _rejection(records)
        if error:
            raise RuntimeError(error)
        # Submitting again after an indeterminate response can charge twice.
        raise RuntimeError("开拍原会话提交结果待核实，禁止换入口重新提交")

    code, tasks, _ = cli("progress", "task-progress", "-r", room, "--task-id", *ids,
                         "--watch", timeout=900)
    if code:
        raise RuntimeError("开拍原任务未完成或查询失败，请按原任务恢复")
    outputs = []
    for index, task_id in enumerate(ids, 1):
        url = _artifact(tasks, task_id, image, selected)
        folder = directory / f"result-{index}"
        folder.mkdir(exist_ok=True)
        code, rows, _ = cli(f"download-{index}", "download", "--url", url,
                            "--output-dir", str(folder), timeout=180)
        receipt = rows[-1] if rows else {}
        downloaded = receipt.get("downloaded") or []
        if code or receipt.get("failed") or len(downloaded) != 1:
            raise RuntimeError("开拍原任务已处理，下载失败，请恢复同一任务")
        path = Path(downloaded[0]["file"]).resolve(strict=True)
        if not path.is_relative_to(folder) or not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("开拍下载结果路径或文件无效")
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
            capture_output=True, timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise RuntimeError("开拍成片未通过完整解码校验")
        outputs.append(str(path))
    result = {"outputs": outputs, "kaipai_session": room, "kaipai_task_ids": ids}
    if selected:
        result["template_material_id"] = selected
        result["requires_name_card_review"] = True
        result["validation"] = [{"path": path, "source_revision": revision} for path in outputs]
    return result
