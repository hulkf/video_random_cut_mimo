"""Persistent, session-independent orchestration for video tool operations."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


DEFAULT_DB = Path(__file__).resolve().parents[1] / ".task_center" / "tasks.db"
DAILY_LIMITS = {"videoscreenclear": 50, "hdvideoallinone": 50}
CLOUD_OPERATIONS = {"kaipai_process", "kaipai_download", "kaipai_quota", "video_enhance"}
RECOVERABLE_CLOUD_OPERATIONS = {"kaipai_process"}
CARDINALITY_CHANGING_OPERATIONS = {"video_fission", "video_concat", "video_mix", "audio_mix"}
VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".m4v", ".ts", ".mts", ".m2ts"}
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,79}$")
TERMINAL_STATES = {"completed", "partial_failed", "failed", "cancelled", "stopped_unknown"}
ACTIVE_STATES = {"queued", "running", "waiting_cloud", "pausing", "cancelling"}


class QuotaExceeded(ValueError):
    pass


def _now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _cloud_capability(request: dict[str, Any]) -> str:
    if request.get("operation") != "kaipai_process":
        return ""
    raw = str(request.get("task_name") or (request.get("inputs") or {}).get("task_name") or "")
    aliases = {
        "视频智能全消": "videoscreenclear",
        "videoscreenclear": "videoscreenclear",
        "视频画质修复": "hdvideoallinone",
        "hdvideoallinone": "hdvideoallinone",
    }
    return aliases.get(raw, "")


def _request_input(request: dict[str, Any], key: str, default: Any = None) -> Any:
    inputs = request.get("inputs")
    if isinstance(inputs, dict) and key in inputs:
        return inputs[key]
    return request.get(key, default)


def _estimate_items(step: dict[str, Any]) -> int:
    explicit = step.get("item_count")
    path = str(_request_input(step["request"], "input_path", "") or "")
    actual: int | None = None
    if os.path.isfile(path):
        suffix = Path(path).suffix.lower()
        if suffix == ".zip":
            try:
                with zipfile.ZipFile(path) as archive:
                    actual = sum(
                        1 for item in archive.infolist()
                        if not item.is_dir() and Path(item.filename).suffix.lower() in VIDEO_SUFFIXES
                    )
            except (OSError, zipfile.BadZipFile) as exc:
                raise ValueError("无法读取 ZIP 中的视频清单: {}".format(path)) from exc
        elif suffix in {".rar", ".7z"}:
            try:
                from core.material_organizer import _find_7zip

                executable = _find_7zip()
                if not executable:
                    raise ValueError("未找到 7-Zip，无法核定压缩包内视频数量")
                completed = subprocess.run(
                    [executable, "l", "-slt", path], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=30,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if completed.returncode:
                    raise ValueError("7-Zip 无法读取压缩包视频清单")
                actual = sum(
                    1 for line in completed.stdout.splitlines()
                    if line.startswith("Path = ") and Path(line[7:].strip()).suffix.lower() in VIDEO_SUFFIXES
                )
            except subprocess.TimeoutExpired as exc:
                raise ValueError("读取压缩包视频清单超时") from exc
        else:
            actual = 1
    if os.path.isdir(path):
        actual = sum(1 for item in Path(path).rglob("*") if item.is_file() and item.suffix.lower() in VIDEO_SUFFIXES)
    if explicit is not None:
        count = int(explicit)
        if count < 0:
            raise ValueError("item_count 不能小于 0")
        if actual is not None and count != actual:
            raise ValueError("item_count 与实际待处理文件数不一致：声明{}，实际{}".format(count, actual))
        return count
    return actual or 0


def _snapshot_path(raw_path: str) -> dict[str, Any]:
    path = Path(raw_path)
    if not path.exists():
        return {"path": str(path), "exists": False, "items": []}
    if path.is_file():
        stat = path.stat()
        return {"path": str(path), "exists": True, "items": [[path.name, stat.st_size, stat.st_mtime_ns]]}
    items = []
    for item in sorted(path.rglob("*"), key=lambda value: str(value.relative_to(path)).casefold()):
        if item.is_file():
            stat = item.stat()
            items.append([str(item.relative_to(path)), stat.st_size, stat.st_mtime_ns])
    return {"path": str(path), "exists": True, "items": items}


def _input_snapshot(request: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots = []
    for key in ("input_path", "folder_a", "folder_b", "source"):
        value = _request_input(request, key, "")
        if isinstance(value, str) and value and "://" not in value:
            snapshots.append({"key": key, **_snapshot_path(value)})
    return snapshots


def _output_resources(request: dict[str, Any]) -> list[str]:
    resources = []
    for key in ("output_folder", "output_path", "output_dir"):
        value = _request_input(request, key, "")
        if isinstance(value, str) and value.strip():
            resources.append(os.path.normcase(str(Path(value).resolve(strict=False))))
    return sorted(set(resources))


def _paths_overlap(left: str, right: str) -> bool:
    try:
        common = os.path.commonpath([left, right])
    except ValueError:
        return False
    return common in {left, right}


def _result_errors(operation: str, result: dict[str, Any]) -> list[str]:
    errors = []
    if result.get("success") is False:
        errors.append(str(result.get("error") or "步骤返回失败"))
    validation = result.get("validation")
    if operation == "validate" and isinstance(validation, dict) and not validation.get("valid"):
        errors.append("媒体校验未通过")
    summary = result.get("summary")
    if isinstance(summary, dict):
        for key, value in summary.items():
            if str(key).startswith("all_") and value is False:
                errors.append("汇总校验未通过: " + str(key))
    for item in result.get("results") or []:
        if isinstance(item, dict) and (
            str(item.get("status", "")).lower() in {"失败", "failed", "error"}
            or item.get("success") is False
        ):
            errors.append(str(item.get("error") or item.get("file") or "文件处理失败"))
    return errors


class TaskExecutionContext:
    def __init__(self, center: "TaskCenter", task_id: str, step_id: str) -> None:
        self.center = center
        self.task_id = task_id
        self.step_id = step_id

    def check(self) -> None:
        from core.task_control import TaskControlSignal

        status = self.center._task_status(self.task_id)
        if status in {"pausing", "paused"}:
            raise TaskControlSignal("paused")
        if status in {"cancelling", "cancelled"}:
            raise TaskControlSignal("cancelled")

    def before_cloud_submit(self, _source_path: str) -> None:
        self.check()
        self.center.begin_cloud_submission(self.task_id, self.step_id, _source_path)

    def cloud_item(
        self,
        source_path: str,
        *,
        state: str,
        cloud_task_id: str = "",
        output_url: str = "",
        error: str = "",
    ) -> None:
        self.center.checkpoint_cloud_item(
            self.task_id,
            self.step_id,
            source_path,
            state=state,
            cloud_task_id=cloud_task_id,
            output_url=output_url,
            error=error,
        )

    def resume_items(self) -> dict[str, dict[str, Any]]:
        return self.center.cloud_items(self.task_id, self.step_id)


class TaskCenter:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or os.environ.get("VIDEO_TASK_CENTER_DB") or DEFAULT_DB)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _init_db(self) -> None:
        with closing(self._connect()) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    cargo_number TEXT NOT NULL DEFAULT '',
                    plan_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    execution_type TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    current_step INTEGER NOT NULL DEFAULT -1,
                    error TEXT NOT NULL DEFAULT '',
                    worker_pid INTEGER,
                    worker_token TEXT NOT NULL DEFAULT '',
                    chat_id TEXT NOT NULL DEFAULT '',
                    card_message_id TEXT NOT NULL DEFAULT '',
                    parameter_lines_json TEXT NOT NULL DEFAULT '[]',
                    risk_note TEXT NOT NULL DEFAULT '',
                    authorized_operations_json TEXT NOT NULL DEFAULT '[]',
                    confirmed_by TEXT NOT NULL DEFAULT '',
                    confirmation_message_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    confirmed_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS steps (
                    task_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    step_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    execution_type TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    result_json TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, step_id),
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS cloud_items (
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    state TEXT NOT NULL,
                    cloud_task_id TEXT NOT NULL DEFAULT '',
                    output_url TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, step_id, source_path),
                    FOREIGN KEY(task_id, step_id) REFERENCES steps(task_id, step_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS quota_reservations (
                    day TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    reserved_count INTEGER NOT NULL,
                    submitted_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(day, capability, task_id, step_id)
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resource_locks (
                    resource_key TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    owner_pid INTEGER NOT NULL,
                    owner_token TEXT NOT NULL DEFAULT '',
                    acquired_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)").fetchall()}
            migrations = {
                "parameter_lines_json": "TEXT NOT NULL DEFAULT '[]'",
                "risk_note": "TEXT NOT NULL DEFAULT ''",
                "authorized_operations_json": "TEXT NOT NULL DEFAULT '[]'",
                "confirmed_by": "TEXT NOT NULL DEFAULT ''",
                "confirmation_message_id": "TEXT NOT NULL DEFAULT ''",
                "worker_token": "TEXT NOT NULL DEFAULT ''",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
            lock_columns = {row["name"] for row in db.execute("PRAGMA table_info(resource_locks)").fetchall()}
            if "owner_token" not in lock_columns:
                db.execute("ALTER TABLE resource_locks ADD COLUMN owner_token TEXT NOT NULL DEFAULT ''")
            db.commit()

    def create_plan(
        self,
        task_id: str,
        title: str,
        steps: list[dict[str, Any]],
        *,
        cargo_number: str = "",
        chat_id: str = "",
        card_message_id: str = "",
        parameter_lines: list[str] | None = None,
        risk_note: str = "",
        authorized_operations: list[str] | None = None,
    ) -> dict[str, Any]:
        if not TASK_ID_RE.fullmatch(str(task_id or "")):
            raise ValueError("task_id 必须为 2-80 位字母、数字、点、短横线或下划线")
        if not str(title or "").strip():
            raise ValueError("title 不能为空")
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps 必须是非空数组")
        parameter_lines = [str(line).strip() for line in parameter_lines or [] if str(line).strip()]
        authorized_operations = sorted({str(value).strip() for value in authorized_operations or [] if str(value).strip()})
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        types: set[str] = set()
        quota: dict[str, int] = {}
        for index, raw in enumerate(steps):
            if not isinstance(raw, dict) or not isinstance(raw.get("request"), dict):
                raise ValueError("每个步骤都必须包含 request 对象")
            request = dict(raw["request"])
            operation = str(request.get("operation") or "").strip()
            if not operation:
                raise ValueError("步骤 request.operation 不能为空")
            step_id = str(raw.get("id") or f"step-{index + 1}").strip()
            if not TASK_ID_RE.fullmatch(step_id) or step_id in seen:
                raise ValueError("步骤 id 无效或重复: {}".format(step_id))
            seen.add(step_id)
            execution_type = "cloud" if operation in CLOUD_OPERATIONS else "local"
            types.add(execution_type)
            source_step_id = str(raw.get("items_from_step") or "")
            source_step = next((item for item in normalized if item["id"] == source_step_id), None) if source_step_id else None
            if source_step_id and not source_step:
                raise ValueError("items_from_step 必须引用前面的步骤: {}".format(source_step_id))
            if source_step:
                declared_count = raw.get("item_count")
                source_operation = str(source_step["request"].get("operation") or "")
                if source_operation in CARDINALITY_CHANGING_OPERATIONS:
                    if declared_count is None:
                        raise ValueError(
                            "上游步骤 {} 可能改变视频数量；派生步骤 {} 必须声明准确 item_count".format(
                                source_step_id, step_id
                            )
                        )
                    item_count = int(declared_count)
                    if item_count <= 0:
                        raise ValueError("派生步骤 item_count 必须大于 0")
                else:
                    item_count = int(source_step["item_count"])
                    if declared_count is not None and int(declared_count) != item_count:
                        raise ValueError("派生步骤 item_count 必须与上游步骤一致")
            else:
                item_count = _estimate_items({**raw, "request": request})
            capability = _cloud_capability(request)
            if capability:
                direct_input = str(_request_input(request, "input_path", "") or "")
                if not source_step and not (os.path.isfile(direct_input) or os.path.isdir(direct_input)):
                    raise ValueError(f"{step_id} 的开拍输入路径不存在，不能核定额度")
                if item_count <= 0:
                    raise ValueError(f"{step_id} 无法确定待提交视频数量，不能预留开拍额度")
                quota[capability] = quota.get(capability, 0) + item_count
            normalized.append({
                "id": step_id,
                "name": str(raw.get("name") or operation),
                "request": request,
                "execution_type": execution_type,
                "item_count": item_count,
                "items_from_step": source_step_id,
                "input_key": str(raw.get("input_key") or ""),
                "input_snapshot": [] if raw.get("items_from_step") else _input_snapshot(request),
            })
        execution_type = "mixed" if len(types) > 1 else next(iter(types))
        now = _now()
        plan = {
            "steps": normalized, "quota_estimate": quota,
            "parameter_lines": parameter_lines, "risk_note": str(risk_note or ""),
            "authorized_operations": authorized_operations,
        }
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT plan_version, status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if old and str(old["status"]) != "awaiting_confirmation":
                raise ValueError("任务当前状态为{}，不能修改计划".format(old["status"]))
            version = int(old["plan_version"]) + 1 if old else 1
            db.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))
            db.execute(
                "INSERT INTO tasks(task_id,title,cargo_number,plan_version,status,execution_type,plan_json,chat_id,card_message_id,parameter_lines_json,risk_note,authorized_operations_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, title.strip(), str(cargo_number or ""), version, "awaiting_confirmation", execution_type, _json(plan), chat_id, card_message_id, _json(parameter_lines), str(risk_note or ""), _json(authorized_operations), now, now),
            )
            for index, step in enumerate(normalized):
                request = dict(step["request"])
                if step["items_from_step"]:
                    request["_items_from_step"] = step["items_from_step"]
                    request["_input_key"] = step["input_key"]
                if step["input_snapshot"]:
                    request["_input_snapshot"] = step["input_snapshot"]
                db.execute(
                    "INSERT INTO steps(task_id,step_index,step_id,name,operation,execution_type,request_json,item_count,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (task_id, index, step["id"], step["name"], request["operation"], step["execution_type"], _json(request), step["item_count"], now),
                )
            self._event(db, task_id, "plan_created", {"version": version})
            db.commit()
        return self.get(task_id)

    def confirm(
        self,
        task_id: str,
        plan_version: int,
        *,
        start_worker: bool = True,
        confirmed_by: str = "",
        confirmation_message_id: str = "",
    ) -> dict[str, Any]:
        today = _today()
        now = _now()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not task:
                raise ValueError("任务不存在: {}".format(task_id))
            if int(task["plan_version"]) != int(plan_version):
                raise ValueError("计划版本已变化，请重新确认最新版本")
            if task["status"] != "awaiting_confirmation":
                if task["status"] in ACTIVE_STATES or task["status"] == "completed":
                    db.commit()
                    return self.get(task_id)
                raise ValueError("任务当前状态为{}，不能确认".format(task["status"]))
            plan = _decode(task["plan_json"], {})
            for capability, count in (plan.get("quota_estimate") or {}).items():
                limit = DAILY_LIMITS.get(capability)
                if limit is None or count <= 0:
                    continue
                committed = db.execute(
                    "SELECT COALESCE(SUM(reserved_count),0) FROM quota_reservations WHERE day=? AND capability=? AND status='active'",
                    (today, capability),
                ).fetchone()[0]
                if int(committed) + int(count) > limit:
                    raise QuotaExceeded("{} 今日额度不足：需要{}次，可预留{}次".format(capability, count, max(limit - int(committed), 0)))
            for step in plan.get("steps") or []:
                capability = _cloud_capability(step["request"])
                count = int(step.get("item_count") or 0)
                if capability and count > 0:
                    db.execute(
                        "INSERT INTO quota_reservations(day,capability,task_id,step_id,reserved_count,submitted_count,status,updated_at) VALUES(?,?,?,?,?,0,'active',?)",
                        (today, capability, task_id, step["id"], count, now),
                    )
            db.execute(
                "UPDATE tasks SET status='queued',worker_pid=-1,worker_token='',confirmed_at=?,confirmed_by=?,confirmation_message_id=?,updated_at=? WHERE task_id=?",
                (now, str(confirmed_by or ""), str(confirmation_message_id or ""), now, task_id),
            )
            self._event(db, task_id, "confirmed", {
                "version": plan_version,
                "confirmed_by": str(confirmed_by or ""),
                "confirmation_message_id": str(confirmation_message_id or ""),
            })
            db.commit()
        if start_worker:
            self._spawn_worker(task_id)
        return self.get(task_id)

    def _spawn_worker(self, task_id: str) -> int:
        root = Path(__file__).resolve().parents[1]
        log_dir = self.db_path.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["VIDEO_TASK_CENTER_DB"] = str(self.db_path)
        worker_token = uuid.uuid4().hex
        with closing(self._connect()) as db:
            claimed = db.execute(
                "UPDATE tasks SET worker_token=?,updated_at=? WHERE task_id=? AND worker_pid=-1 AND worker_token=''",
                (worker_token, _now(), task_id),
            )
            db.commit()
        if claimed.rowcount != 1:
            with closing(self._connect()) as db:
                row = db.execute("SELECT worker_pid FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            return int(row["worker_pid"] or 0) if row else 0
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            with (log_dir / (task_id + ".log")).open("a", encoding="utf-8") as sink:
                process = subprocess.Popen(
                    [sys.executable, str(root / "video_tool.py"), "task-worker", "--task-id", task_id, "--worker-token", worker_token],
                    cwd=str(root), stdout=sink, stderr=subprocess.STDOUT, env=env,
                    creationflags=flags, close_fds=True,
                )
        except Exception:
            with closing(self._connect()) as db:
                db.execute(
                    "UPDATE tasks SET status='failed',worker_pid=NULL,worker_token='',error='执行器启动失败',updated_at=? WHERE task_id=? AND worker_token=?",
                    (_now(), task_id, worker_token),
                )
                db.commit()
            raise
        with closing(self._connect()) as db:
            db.execute(
                "UPDATE tasks SET worker_pid=?, updated_at=? WHERE task_id=? AND worker_pid=-1 AND worker_token=?",
                (process.pid, _now(), task_id, worker_token),
            )
            db.commit()
        return int(process.pid)

    def _wait_previous_worker_exit(self, pid: int, timeout: float = 6.0) -> None:
        deadline = time.monotonic() + timeout
        while pid and self._pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if pid and self._pid_alive(pid):
            raise RuntimeError("原任务执行器尚未退出，请稍后再继续")

    def execute(
        self,
        task_id: str,
        runner: Callable[[dict[str, Any], TaskExecutionContext], dict[str, Any]],
        *,
        worker_token: str = "",
    ) -> dict[str, Any]:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT status,worker_pid,worker_token FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not task:
                raise ValueError("任务不存在: {}".format(task_id))
            if task["status"] != "queued":
                db.commit()
                return self.get(task_id)
            owner_pid = int(task["worker_pid"] or 0)
            current_pid = os.getpid()
            expected_token = str(task["worker_token"] or "")
            if expected_token and worker_token != expected_token:
                db.commit()
                return self.get(task_id)
            if owner_pid not in {-1, current_pid}:
                db.commit()
                return self.get(task_id)
            claimed = db.execute(
                "UPDATE tasks SET status='running',worker_pid=?,updated_at=? WHERE task_id=? AND status='queued' AND worker_pid IN (-1,?)",
                (current_pid, _now(), task_id, current_pid),
            )
            if claimed.rowcount != 1:
                db.commit()
                return self.get(task_id)
            db.commit()
        steps = self._step_rows(task_id)
        with closing(self._connect()) as db:
            task_plan = db.execute("SELECT authorized_operations_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        authorized_operations = set(_decode(task_plan["authorized_operations_json"], [])) if task_plan else set()
        try:
            for step in steps:
                if step["status"] == "completed":
                    continue
                context = self.execution_context(task_id, step["step_id"])
                context.check()
                self._set_step_status(task_id, step["step_id"], "running")
                self._set_task_status(task_id, "running", current_step=int(step["step_index"]))
                request = self._resolve_request(task_id, step)
                request.pop("task_id", None)
                if isinstance(request.get("inputs"), dict):
                    request["inputs"] = {key: value for key, value in request["inputs"].items() if key != "task_id"}
                if request.get("operation") in authorized_operations:
                    request["authorization"] = {"confirmed": True, "scope": request.get("operation", "")}
                else:
                    request.pop("authorization", None)
                request["_task_center_context"] = context
                request["_task_controller"] = context
                resources = _output_resources(request)
                self._acquire_resources(task_id, step["step_id"], resources, context)
                try:
                    result = runner(request, context)
                finally:
                    self._release_resources(task_id, step["step_id"])
                current = self._task_status(task_id)
                if result.get("status") in {"paused", "cancelled"} or current in {"pausing", "cancelling"}:
                    target = "cancelled" if result.get("status") == "cancelled" or current == "cancelling" else "paused"
                    self._save_step_result(task_id, step["step_id"], result, "pending")
                    self._set_task_status(task_id, target, current_step=int(step["step_index"]))
                    self._release_unsubmitted_quota(task_id, final=target == "cancelled")
                    return self.get(task_id)
                failures = _result_errors(str(step["operation"]), result)
                if failures:
                    message = "；".join(failures[:3])
                    self._save_step_result(task_id, step["step_id"], result, "failed", message)
                    self._set_task_status(task_id, "partial_failed", current_step=int(step["step_index"]), error=message)
                    self._release_unsubmitted_quota(task_id, final=True)
                    return self.get(task_id)
                self._save_step_result(task_id, step["step_id"], result, "completed")
            self._set_task_status(task_id, "completed", current_step=len(steps) - 1)
            self._release_unsubmitted_quota(task_id, final=True)
        except Exception as exc:
            from core.task_control import TaskControlSignal

            if isinstance(exc, TaskControlSignal):
                status = "paused" if exc.status == "paused" else "cancelled"
                self._set_task_status(task_id, status)
                self._release_unsubmitted_quota(task_id, final=status == "cancelled")
            else:
                self._set_task_status(task_id, "failed", error=str(exc))
                self._release_unsubmitted_quota(task_id, final=True)
        return self.get(task_id)

    def _resolve_request(self, task_id: str, step: sqlite3.Row) -> dict[str, Any]:
        request = _decode(step["request_json"], {})
        expected_snapshot = request.pop("_input_snapshot", [])
        for expected in expected_snapshot:
            current = _snapshot_path(str(expected.get("path") or ""))
            if current != {key: expected[key] for key in ("path", "exists", "items")}:
                raise ValueError("输入内容已变化，请创建新计划并重新确认: {}".format(expected.get("path")))
        source_step = str(request.pop("_items_from_step", "") or "")
        input_key = str(request.pop("_input_key", "") or "")
        if source_step:
            with closing(self._connect()) as db:
                row = db.execute("SELECT result_json FROM steps WHERE task_id=? AND step_id=?", (task_id, source_step)).fetchone()
            if not row:
                raise ValueError("找不到上游步骤: {}".format(source_step))
            prior = _decode(row["result_json"], {})
            urls: list[dict[str, str]] = []
            paths: list[str] = []
            for output in prior.get("outputs") or []:
                if isinstance(output, str) and output:
                    if "://" in output:
                        urls.append({"url": output, "filename": ""})
                    else:
                        paths.append(output)
            for item in prior.get("results") or []:
                if not isinstance(item, dict):
                    continue
                item_urls = list(item.get("output_urls") or [])
                if item.get("output_url"):
                    item_urls.insert(0, item["output_url"])
                for url in item_urls:
                    if isinstance(url, str) and url:
                        urls.append({"url": url, "filename": str(item.get("file") or "")})
                for key in ("output", "output_path", "path"):
                    value = item.get(key)
                    if isinstance(value, str) and value and "://" not in value and value not in paths:
                        paths.append(value)
            operation = str(request.get("operation") or "")
            target_key = input_key or ("items" if operation == "kaipai_download" else "input_path")
            if target_key == "items":
                if not urls:
                    raise ValueError("上游步骤没有可供下载的云端结果: {}".format(source_step))
                bound_value: Any = urls
            else:
                if not paths:
                    raise ValueError("上游步骤没有可供后续处理的本地文件: {}".format(source_step))
                parents = {os.path.normcase(os.path.abspath(str(Path(path).parent))) for path in paths}
                if len(paths) == 1:
                    bound_value = paths[0]
                elif len(parents) == 1:
                    bound_value = str(Path(paths[0]).parent)
                else:
                    raise ValueError("上游文件分布在多个目录，必须明确整理到同一目录后再传递")
            if isinstance(request.get("inputs"), dict):
                request["inputs"] = {**request["inputs"], target_key: bound_value}
            else:
                request[target_key] = bound_value
        return request

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _acquire_resources(self, task_id: str, step_id: str, resources: list[str], context: TaskExecutionContext) -> None:
        if not resources:
            return
        while True:
            context.check()
            now = _now()
            with closing(self._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                task = db.execute("SELECT worker_token FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                owner_token = str(task["worker_token"] or "") if task else ""
                stale = db.execute(
                    "SELECT l.resource_key,l.owner_pid,l.owner_token,t.status,t.worker_pid,t.worker_token "
                    "FROM resource_locks l LEFT JOIN tasks t ON t.task_id=l.task_id"
                ).fetchall()
                for row in stale:
                    task_missing = row["status"] is None
                    ownership_changed = (
                        not task_missing
                        and (int(row["worker_pid"] or 0) != int(row["owner_pid"])
                             or str(row["worker_token"] or "") != str(row["owner_token"] or ""))
                    )
                    terminal = not task_missing and str(row["status"]) in TERMINAL_STATES
                    if task_missing or ownership_changed or terminal or not self._pid_alive(int(row["owner_pid"])):
                        db.execute("DELETE FROM resource_locks WHERE resource_key=?", (row["resource_key"],))
                active_locks = db.execute("SELECT resource_key,task_id,owner_pid,owner_token FROM resource_locks").fetchall()
                conflicts = [
                    row for row in active_locks
                    if any(_paths_overlap(resource, str(row["resource_key"])) for resource in resources)
                    and (str(row["task_id"]) != task_id or str(row["owner_token"] or "") != owner_token)
                ]
                if not conflicts:
                    for resource in resources:
                        db.execute(
                            "INSERT OR REPLACE INTO resource_locks(resource_key,task_id,step_id,owner_pid,owner_token,acquired_at) VALUES(?,?,?,?,?,?)",
                            (resource, task_id, step_id, os.getpid(), owner_token, now),
                        )
                    db.commit()
                    self._set_task_status(task_id, "running", error="")
                    return
                waiting_for = str(conflicts[0]["resource_key"])
                db.commit()
            # The current worker still owns this task while it waits. Keeping the
            # task in ``running`` prevents another executor in the same process
            # from claiming the temporarily blocked task.
            self._set_task_status(task_id, "running", error="等待输出目录可用: " + waiting_for)
            time.sleep(0.5)

    def _release_resources(self, task_id: str, step_id: str) -> None:
        with closing(self._connect()) as db:
            db.execute("DELETE FROM resource_locks WHERE task_id=? AND step_id=?", (task_id, step_id))
            db.commit()

    def control(self, task_id: str, action: str, *, start_worker: bool = True) -> dict[str, Any]:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,worker_pid FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("任务不存在: {}".format(task_id))
            status = str(row["status"])
            previous_pid = int(row["worker_pid"] or 0)
            if action == "pause":
                if status == "queued":
                    target = "paused"
                elif status in {"running", "waiting_cloud"}:
                    target = "pausing"
                else:
                    raise ValueError("任务当前状态为{}，不能暂停".format(status))
            elif action == "resume":
                if status != "paused":
                    raise ValueError("任务当前状态为{}，不能继续".format(status))
                target = "queued"
            elif action == "cancel":
                if status in TERMINAL_STATES:
                    db.commit()
                    return self.get(task_id)
                target = "cancelling" if status in {"running", "waiting_cloud", "pausing"} else "cancelled"
            else:
                raise ValueError("不支持的任务控制动作: {}".format(action))
            next_pid = -1 if target == "queued" else previous_pid
            if target == "queued":
                db.execute("UPDATE tasks SET status=?,worker_pid=?,worker_token='',updated_at=? WHERE task_id=?", (target, next_pid, _now(), task_id))
            else:
                db.execute("UPDATE tasks SET status=?,worker_pid=?,updated_at=? WHERE task_id=?", (target, next_pid, _now(), task_id))
            self._event(db, task_id, "control_" + action, {"from": status, "to": target})
            db.commit()
        if target == "queued" and start_worker:
            self._wait_previous_worker_exit(previous_pid)
            self._spawn_worker(task_id)
        if target == "cancelled":
            self._release_unsubmitted_quota(task_id, final=True)
        return self.get(task_id)

    def bind_card(self, task_id: str, chat_id: str, card_message_id: str) -> dict[str, Any]:
        if not str(chat_id or "").strip() or not str(card_message_id or "").strip():
            raise ValueError("chat_id 和 card_message_id 不能为空")
        with closing(self._connect()) as db:
            cursor = db.execute(
                "UPDATE tasks SET chat_id=?,card_message_id=?,updated_at=? WHERE task_id=?",
                (chat_id.strip(), card_message_id.strip(), _now(), task_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("任务不存在: {}".format(task_id))
            db.commit()
        return self.get(task_id)

    def execution_context(self, task_id: str, step_id: str) -> TaskExecutionContext:
        return TaskExecutionContext(self, task_id, step_id)

    def checkpoint_cloud_item(self, task_id: str, step_id: str, source_path: str, *, state: str, cloud_task_id: str = "", output_url: str = "", error: str = "") -> None:
        now = _now()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT state FROM cloud_items WHERE task_id=? AND step_id=? AND source_path=?",
                (task_id, step_id, source_path),
            ).fetchone()
            if state in {"submitted", "completed"} and not existing:
                raise RuntimeError("云端结果缺少提交意图记录，拒绝更新额度台账")
            db.execute(
                "INSERT INTO cloud_items(task_id,step_id,source_path,state,cloud_task_id,output_url,error,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(task_id,step_id,source_path) DO UPDATE SET state=excluded.state,cloud_task_id=CASE WHEN excluded.cloud_task_id<>'' THEN excluded.cloud_task_id ELSE cloud_items.cloud_task_id END,output_url=CASE WHEN excluded.output_url<>'' THEN excluded.output_url ELSE cloud_items.output_url END,error=excluded.error,updated_at=excluded.updated_at",
                (task_id, step_id, source_path, state, cloud_task_id, output_url, error, now),
            )
            submitted = db.execute("SELECT COUNT(*) FROM cloud_items WHERE task_id=? AND step_id=? AND cloud_task_id<>''", (task_id, step_id)).fetchone()[0]
            db.execute("UPDATE quota_reservations SET submitted_count=?, updated_at=? WHERE task_id=? AND step_id=?", (submitted, now, task_id, step_id))
            if state == "submitted":
                db.execute("UPDATE tasks SET status='waiting_cloud', updated_at=? WHERE task_id=? AND status NOT IN ('pausing','cancelling')", (now, task_id))
            self._event(db, task_id, "cloud_item_" + state, {"step_id": step_id, "source_path": source_path, "cloud_task_id": cloud_task_id})
            db.commit()

    def begin_cloud_submission(self, task_id: str, step_id: str, source_path: str) -> None:
        """Atomically reserve one real submission on the actual local calendar day."""
        now = _now()
        today = _today()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            reservation = db.execute(
                "SELECT reserved_count FROM quota_reservations WHERE day=? AND task_id=? AND step_id=? AND status='active'",
                (today, task_id, step_id),
            ).fetchone()
            if not reservation:
                raise QuotaExceeded("任务未持有今日额度预留；跨日执行必须重新建立计划并确认")
            existing = db.execute(
                "SELECT state FROM cloud_items WHERE task_id=? AND step_id=? AND source_path=?",
                (task_id, step_id, source_path),
            ).fetchone()
            if existing:
                raise RuntimeError("该文件已有云端提交记录，拒绝重复提交: {}".format(source_path))
            started = db.execute(
                "SELECT COUNT(*) FROM cloud_items WHERE task_id=? AND step_id=?",
                (task_id, step_id),
            ).fetchone()[0]
            if int(started) >= int(reservation["reserved_count"]):
                raise QuotaExceeded("本步骤已达到确认时预留的视频数量，拒绝继续提交")
            db.execute(
                "INSERT INTO cloud_items(task_id,step_id,source_path,state,updated_at) VALUES(?,?,?,?,?)",
                (task_id, step_id, source_path, "submitting", now),
            )
            self._event(db, task_id, "cloud_item_submitting", {"step_id": step_id, "source_path": source_path})
            db.commit()

    def cloud_items(self, task_id: str, step_id: str) -> dict[str, dict[str, Any]]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM cloud_items WHERE task_id=? AND step_id=?", (task_id, step_id)).fetchall()
        return {str(row["source_path"]): dict(row) for row in rows}

    def list_tasks(self, *, status: str = "", cargo_number: str = "", limit: int = 100, offset: int = 0, reconcile: bool = True) -> dict[str, Any]:
        if reconcile:
            self.reconcile_workers()
        clauses, values = [], []
        if status:
            clauses.append("status=?")
            values.append(status)
        if cargo_number:
            clauses.append("cargo_number=?")
            values.append(cargo_number)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with closing(self._connect()) as db:
            status_rows = db.execute("SELECT status,updated_at FROM tasks" + where, values).fetchall()
            page_limit = max(1, min(int(limit), 500))
            page_offset = max(0, int(offset))
            rows = db.execute("SELECT * FROM tasks" + where + " ORDER BY updated_at DESC LIMIT ? OFFSET ?", (*values, page_limit, page_offset)).fetchall()
        tasks = [self._task_summary(row) for row in rows]
        statuses = [str(item["status"]) for item in status_rows]
        today = _today()
        return {
            "counts": {
                "in_progress": sum(value in {"running", "waiting_cloud", "pausing", "cancelling"} for value in statuses),
                "executing": sum(value in {"running", "pausing", "cancelling"} for value in statuses),
                "waiting_cloud": statuses.count("waiting_cloud"),
                "queued": statuses.count("queued"),
                "awaiting_confirmation": statuses.count("awaiting_confirmation"),
                "paused": statuses.count("paused"),
                "needs_attention": sum(value in {"failed", "partial_failed", "stopped_unknown"} for value in statuses),
                "completed_today": sum(
                    str(item["status"]) == "completed" and str(item["updated_at"]).startswith(today)
                    for item in status_rows
                ),
            },
            "tasks": tasks,
            "total": len(status_rows),
            "shown": len(tasks),
            "offset": page_offset,
            "limit": page_limit,
            "quota": self.quota_summary(),
            "updated_at": _now(),
        }

    def reconcile_workers(self, task_id: str = "") -> list[str]:
        """Recover only cloud-safe work; fence uncertain/local crash states for review."""
        clauses = " AND task_id=?" if task_id else ""
        values = (task_id,) if task_id else ()
        recover: list[str] = []
        now = _now()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT task_id,status,current_step,worker_pid FROM tasks "
                "WHERE status IN ('queued','running','waiting_cloud','pausing','cancelling')" + clauses,
                values,
            ).fetchall()
            for row in rows:
                pid = int(row["worker_pid"] or 0)
                if pid == -1 or self._pid_alive(pid):
                    continue
                pending = db.execute(
                    "SELECT step_id,operation,execution_type FROM steps WHERE task_id=? AND status<>'completed' ORDER BY step_index LIMIT 1",
                    (row["task_id"],),
                ).fetchone()
                uncertain = db.execute(
                    "SELECT COUNT(*) FROM cloud_items WHERE task_id=? AND state IN ('submitting','submission_unknown')",
                    (row["task_id"],),
                ).fetchone()[0]
                if pending and pending["operation"] in RECOVERABLE_CLOUD_OPERATIONS and int(uncertain) == 0 and row["status"] not in {"pausing", "cancelling"}:
                    db.execute(
                        "UPDATE tasks SET status='queued',worker_pid=-1,worker_token='',error='执行器重启，正在按已保存云端编号恢复',updated_at=? WHERE task_id=?",
                        (now, row["task_id"]),
                    )
                    recover.append(str(row["task_id"]))
                else:
                    reason = "存在没有云端编号的提交结果，已停止自动重提" if uncertain else "本地步骤执行器已中断，需创建新任务确认后重试"
                    db.execute(
                        "UPDATE tasks SET status='stopped_unknown',error=?,updated_at=? WHERE task_id=?",
                        (reason, now, row["task_id"]),
                    )
                    self._event(db, str(row["task_id"]), "worker_lost", {"reason": reason})
            db.commit()
        for recover_id in recover:
            try:
                self._spawn_worker(recover_id)
            except Exception as exc:
                self._set_task_status(recover_id, "stopped_unknown", error="恢复执行器启动失败: " + str(exc))
        return recover

    def get(self, task_id: str) -> dict[str, Any]:
        with closing(self._connect()) as db:
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not task:
                raise ValueError("任务不存在: {}".format(task_id))
            steps = db.execute("SELECT * FROM steps WHERE task_id=? ORDER BY step_index", (task_id,)).fetchall()
        detail = self._task_summary(task)
        detail["steps"] = [self._step_summary(row) for row in steps]
        detail["step_counts"] = {
            "total": len(detail["steps"]),
            "completed": sum(step["status"] == "completed" for step in detail["steps"]),
            "failed": sum(step["status"] == "failed" for step in detail["steps"]),
            "pending": sum(step["status"] in {"pending", "running"} for step in detail["steps"]),
        }
        detail["quota_estimate"] = (_decode(task["plan_json"], {}).get("quota_estimate") or {})
        input_paths: set[str] = set()
        output_directories: set[str] = set()
        for row in steps:
            request = _decode(row["request_json"], {})
            for key in ("input_path", "folder_a", "folder_b", "path", "source"):
                value = _request_input(request, key, "")
                if isinstance(value, str) and value.strip():
                    input_paths.add(value if "://" in value else os.path.normpath(value))
            for key in ("output_folder", "output_dir"):
                value = _request_input(request, key, "")
                if isinstance(value, str) and value.strip():
                    output_directories.add(os.path.normpath(value))
            output_path = _request_input(request, "output_path", "")
            if isinstance(output_path, str) and output_path.strip():
                output_directories.add(os.path.normpath(str(Path(output_path).parent)))
            result = _decode(row["result_json"], {})
            result_folder = result.get("output_folder")
            if isinstance(result_folder, str) and result_folder.strip():
                output_directories.add(os.path.normpath(result_folder))
            for output in result.get("outputs") or []:
                if isinstance(output, str) and output.strip() and "://" not in output:
                    output_directories.add(os.path.normpath(str(Path(output).parent)))
        detail["input_paths"] = sorted(input_paths)
        detail["output_directories"] = sorted(output_directories)
        return detail

    def quota_summary(self, day: str | None = None) -> dict[str, Any]:
        day = day or _today()
        with closing(self._connect()) as db:
            rows = db.execute("SELECT capability,COALESCE(SUM(reserved_count),0) reserved,COALESCE(SUM(submitted_count),0) submitted FROM quota_reservations WHERE day=? AND status='active' GROUP BY capability", (day,)).fetchall()
        current = {row["capability"]: dict(row) for row in rows}
        return {
            capability: {
                "limit": limit,
                "committed": int(current.get(capability, {}).get("reserved", 0)) if capability in current else 0,
                "reserved_pending": max(
                    (int(current.get(capability, {}).get("reserved", 0)) if capability in current else 0)
                    - (int(current.get(capability, {}).get("submitted", 0)) if capability in current else 0),
                    0,
                ),
                "submitted": int(current.get(capability, {}).get("submitted", 0)) if capability in current else 0,
                "available": max(limit - (int(current.get(capability, {}).get("reserved", 0)) if capability in current else 0), 0),
            }
            for capability, limit in DAILY_LIMITS.items()
        }

    def _task_summary(self, row: sqlite3.Row) -> dict[str, Any]:
        step = None
        if int(row["current_step"]) >= 0:
            with closing(self._connect()) as db:
                current = db.execute("SELECT * FROM steps WHERE task_id=? AND step_index=?", (row["task_id"], row["current_step"])).fetchone()
                if current:
                    step = self._step_summary(current)
        return {
            "task_id": row["task_id"], "title": row["title"], "cargo_number": row["cargo_number"],
            "plan_version": int(row["plan_version"]), "status": row["status"],
            "execution_type": row["execution_type"], "current_step": step,
            "error": row["error"], "created_at": row["created_at"], "confirmed_at": row["confirmed_at"],
            "updated_at": row["updated_at"], "chat_id": row["chat_id"], "card_message_id": row["card_message_id"],
            "parameter_lines": _decode(row["parameter_lines_json"], []), "risk_note": row["risk_note"],
            "authorized_operations": _decode(row["authorized_operations_json"], []),
            "confirmed_by": row["confirmed_by"], "confirmation_message_id": row["confirmation_message_id"],
        }

    def _step_summary(self, row: sqlite3.Row) -> dict[str, Any]:
        items = self.cloud_items(row["task_id"], row["step_id"])
        result = _decode(row["result_json"], {})
        counts: dict[str, int] = {}
        for item in items.values():
            counts[item["state"]] = counts.get(item["state"], 0) + 1
        return {
            "index": int(row["step_index"]), "step_id": row["step_id"], "name": row["name"],
            "operation": row["operation"], "execution_type": row["execution_type"],
            "item_count": int(row["item_count"]), "status": row["status"], "item_counts": counts,
            "error": row["error"], "started_at": row["started_at"], "finished_at": row["finished_at"], "updated_at": row["updated_at"],
            "output_count": len(result.get("outputs") or []),
            "validation": result.get("summary") or result.get("validation") or {},
        }

    def _task_status(self, task_id: str) -> str:
        with closing(self._connect()) as db:
            row = db.execute("SELECT status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise ValueError("任务不存在: {}".format(task_id))
        return str(row["status"])

    def _step_rows(self, task_id: str) -> list[sqlite3.Row]:
        with closing(self._connect()) as db:
            return db.execute("SELECT * FROM steps WHERE task_id=? ORDER BY step_index", (task_id,)).fetchall()

    def _set_task_status(self, task_id: str, status: str, *, current_step: int | None = None, error: str = "") -> None:
        fields, values = ["status=?", "updated_at=?", "error=?"], [status, _now(), error]
        if current_step is not None:
            fields.append("current_step=?")
            values.append(current_step)
        values.append(task_id)
        with closing(self._connect()) as db:
            db.execute("UPDATE tasks SET {} WHERE task_id=?".format(",".join(fields)), values)
            self._event(db, task_id, "status_changed", {"status": status, "error": error})
            db.commit()

    def _set_step_status(self, task_id: str, step_id: str, status: str) -> None:
        now = _now()
        with closing(self._connect()) as db:
            db.execute("UPDATE steps SET status=?,started_at=COALESCE(started_at,?),updated_at=? WHERE task_id=? AND step_id=?", (status, now, now, task_id, step_id))
            db.commit()

    def _save_step_result(self, task_id: str, step_id: str, result: dict[str, Any], status: str, error: str = "") -> None:
        now = _now()
        with closing(self._connect()) as db:
            db.execute("UPDATE steps SET status=?,result_json=?,error=?,finished_at=CASE WHEN ? IN ('completed','failed') THEN ? ELSE finished_at END,updated_at=? WHERE task_id=? AND step_id=?", (status, _json(result), error, status, now, now, task_id, step_id))
            db.commit()

    def _release_unsubmitted_quota(self, task_id: str, *, final: bool) -> None:
        with closing(self._connect()) as db:
            if final:
                db.execute("UPDATE quota_reservations SET reserved_count=submitted_count,updated_at=? WHERE task_id=?", (_now(), task_id))
            db.commit()

    def _event(self, db: sqlite3.Connection, task_id: str, event_type: str, detail: dict[str, Any]) -> None:
        db.execute("INSERT INTO task_events(task_id,event_type,detail_json,created_at) VALUES(?,?,?,?)", (task_id, event_type, _json(detail), _now()))


def run_task_worker(task_id: str, db_path: str | Path | None = None, worker_token: str = "") -> dict[str, Any]:
    center = TaskCenter(db_path)

    def runner(request: dict[str, Any], _context: TaskExecutionContext) -> dict[str, Any]:
        from video_tool import run_request

        return run_request(request)

    return center.execute(task_id, runner, worker_token=worker_token)
