"""Persistent, file-based control for long-running headless video tasks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


CONTROL_DIR = Path(__file__).resolve().parents[1] / ".task_control"


class TaskControlSignal(RuntimeError):
    def __init__(self, status: str):
        super().__init__("任务已{}".format("暂停" if status == "paused" else "取消"))
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(task_id: str) -> Path:
    safe = "".join(ch for ch in str(task_id) if ch.isalnum() or ch in "-_.")
    if not safe:
        raise ValueError("task_id 不能为空")
    return CONTROL_DIR / (safe + ".json")


def _read(task_id: str) -> dict:
    path = _path(task_id)
    if not path.exists():
        raise ValueError("任务不存在: {}".format(task_id))
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _write(task_id: str, data: dict) -> dict:
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(task_id)
    fd, temp_name = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=str(CONTROL_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return data


class TaskController:
    def __init__(self, task_id: str):
        self.task_id = str(task_id)

    def start(self, metadata=None, request=None) -> dict:
        data = {
            "task_id": self.task_id,
            "status": "running",
            "created_at": _now(),
            "updated_at": _now(),
            "metadata": metadata or {},
        }
        if request is not None:
            data["request"] = request
        return _write(self.task_id, data)

    def status(self) -> dict:
        return _read(self.task_id)

    def check(self) -> None:
        status = self.status().get("status")
        if status in ("paused", "cancelled"):
            raise TaskControlSignal(status)

    def set_status(self, status: str) -> dict:
        if status not in ("paused", "running", "cancelled", "completed", "failed"):
            raise ValueError("不支持的任务状态: {}".format(status))
        data = self.status()
        data["status"] = status
        data["updated_at"] = _now()
        return _write(self.task_id, data)


def task_command(task_id: str, action: str) -> dict:
    controller = TaskController(task_id)
    current = controller.status()
    current_status = current.get("status")
    if action == "status":
        return current
    if action == "pause":
        if current_status in ("completed", "failed", "cancelled"):
            raise ValueError("任务当前状态为{}，不能暂停".format(current_status))
        return controller.set_status("paused")
    if action == "resume":
        if current_status != "paused":
            raise ValueError("只有已暂停任务可以继续，当前状态为{}".format(current_status))
        request = current.get("request")
        if not isinstance(request, dict):
            raise ValueError("任务没有可恢复的原始请求")
        request = dict(request)
        request["resume_existing"] = True
        request_path = _path(task_id).with_suffix(".resume.json")
        with request_path.open("w", encoding="utf-8") as stream:
            json.dump(request, stream, ensure_ascii=False, indent=2)
        log_path = _path(task_id).with_suffix(".resume.log")
        log = log_path.open("a", encoding="utf-8")
        try:
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve().parents[1] / "video_tool.py"),
                 "run", "--request", str(request_path)],
                cwd=str(Path(__file__).resolve().parents[1]),
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "DETACHED_PROCESS", 0)
                ),
            )
        finally:
            log.close()
        return controller.set_status("running")
    if action == "cancel":
        if current_status in ("completed", "failed", "cancelled"):
            return current
        return controller.set_status("cancelled")
    raise ValueError("不支持的任务控制动作: {}".format(action))
