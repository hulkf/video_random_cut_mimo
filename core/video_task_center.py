"""Persistent, session-independent orchestration for video tool operations."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


DEFAULT_DB = Path(__file__).resolve().parents[1] / ".task_center" / "tasks.db"
DAILY_LIMITS = {"videoscreenclear": 50, "hdvideoallinone": 50}
CLOUD_OPERATIONS = {"kaipai_process", "kaipai_download", "kaipai_quota", "video_enhance"}
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,79}$")
TERMINAL_STATES = {"completed", "partial_failed", "failed", "cancelled"}
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
    if explicit is not None:
        count = int(explicit)
        if count < 0:
            raise ValueError("item_count 不能小于 0")
        return count
    path = str(_request_input(step["request"], "input_path", "") or "")
    if os.path.isfile(path):
        return 1
    if os.path.isdir(path):
        suffixes = {".mp4", ".avi", ".mov", ".mkv", ".flv"}
        return sum(1 for item in Path(path).iterdir() if item.is_file() and item.suffix.lower() in suffixes)
    return 0


def _snapshot_path(raw_path: str) -> dict[str, Any]:
    path = Path(raw_path)
    if not path.exists():
        return {"path": str(path), "exists": False, "items": []}
    if path.is_file():
        stat = path.stat()
        return {"path": str(path), "exists": True, "items": [[path.name, stat.st_size, stat.st_mtime_ns]]}
    items = []
    for item in sorted(path.iterdir(), key=lambda value: value.name.casefold()):
        if item.is_file():
            stat = item.stat()
            items.append([item.name, stat.st_size, stat.st_mtime_ns])
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
            resources.append(os.path.normcase(os.path.abspath(value)))
    return sorted(set(resources))


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
                    chat_id TEXT NOT NULL DEFAULT '',
                    card_message_id TEXT NOT NULL DEFAULT '',
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
                    acquired_at TEXT NOT NULL
                );
                """
            )
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
    ) -> dict[str, Any]:
        if not TASK_ID_RE.fullmatch(str(task_id or "")):
            raise ValueError("task_id 必须为 2-80 位字母、数字、点、短横线或下划线")
        if not str(title or "").strip():
            raise ValueError("title 不能为空")
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps 必须是非空数组")
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
            item_count = _estimate_items({**raw, "request": request})
            capability = _cloud_capability(request)
            if capability:
                if item_count <= 0:
                    raise ValueError(f"{step_id} 无法确定待提交视频数量，不能预留开拍额度")
                quota[capability] = quota.get(capability, 0) + item_count
            normalized.append({
                "id": step_id,
                "name": str(raw.get("name") or operation),
                "request": request,
                "execution_type": execution_type,
                "item_count": item_count,
                "items_from_step": str(raw.get("items_from_step") or ""),
                "input_snapshot": [] if raw.get("items_from_step") else _input_snapshot(request),
            })
        execution_type = "mixed" if len(types) > 1 else next(iter(types))
        now = _now()
        plan = {"steps": normalized, "quota_estimate": quota}
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT plan_version, status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if old and str(old["status"]) != "awaiting_confirmation":
                raise ValueError("任务当前状态为{}，不能修改计划".format(old["status"]))
            version = int(old["plan_version"]) + 1 if old else 1
            db.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))
            db.execute(
                "INSERT INTO tasks(task_id,title,cargo_number,plan_version,status,execution_type,plan_json,chat_id,card_message_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, title.strip(), str(cargo_number or ""), version, "awaiting_confirmation", execution_type, _json(plan), chat_id, card_message_id, now, now),
            )
            for index, step in enumerate(normalized):
                request = dict(step["request"])
                if step["items_from_step"]:
                    request["_items_from_step"] = step["items_from_step"]
                if step["input_snapshot"]:
                    request["_input_snapshot"] = step["input_snapshot"]
                db.execute(
                    "INSERT INTO steps(task_id,step_index,step_id,name,operation,execution_type,request_json,item_count,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (task_id, index, step["id"], step["name"], request["operation"], step["execution_type"], _json(request), step["item_count"], now),
                )
            self._event(db, task_id, "plan_created", {"version": version})
            db.commit()
        return self.get(task_id)

    def confirm(self, task_id: str, plan_version: int, *, start_worker: bool = True) -> dict[str, Any]:
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
            db.execute("UPDATE tasks SET status='queued', confirmed_at=?, updated_at=? WHERE task_id=?", (now, now, task_id))
            self._event(db, task_id, "confirmed", {"version": plan_version})
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
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        with (log_dir / (task_id + ".log")).open("a", encoding="utf-8") as sink:
            process = subprocess.Popen(
                [sys.executable, str(root / "video_tool.py"), "task-worker", "--task-id", task_id],
                cwd=str(root), stdout=sink, stderr=subprocess.STDOUT, env=env,
                creationflags=flags, close_fds=True,
            )
        with closing(self._connect()) as db:
            db.execute("UPDATE tasks SET worker_pid=?, updated_at=? WHERE task_id=?", (process.pid, _now(), task_id))
            db.commit()
        return int(process.pid)

    def _wait_previous_worker_exit(self, task_id: str, timeout: float = 6.0) -> None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT worker_pid FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        pid = int(row["worker_pid"] or 0) if row else 0
        deadline = time.monotonic() + timeout
        while pid and self._pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if pid and self._pid_alive(pid):
            raise RuntimeError("原任务执行器尚未退出，请稍后再继续")

    def execute(self, task_id: str, runner: Callable[[dict[str, Any], TaskExecutionContext], dict[str, Any]]) -> dict[str, Any]:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not task:
                raise ValueError("任务不存在: {}".format(task_id))
            if task["status"] not in {"queued", "running", "waiting_cloud"}:
                db.commit()
                return self.get(task_id)
            db.execute("UPDATE tasks SET status='running', updated_at=? WHERE task_id=?", (_now(), task_id))
            db.commit()
        steps = self._step_rows(task_id)
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
                request["authorization"] = {"confirmed": True, "scope": request.get("operation", "")}
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
                failures = [item for item in result.get("results", []) if str(item.get("status", "")).lower() in {"失败", "failed", "error"} or item.get("success") is False]
                if failures:
                    self._save_step_result(task_id, step["step_id"], result, "failed", "{} 个文件失败".format(len(failures)))
                    self._set_task_status(task_id, "partial_failed", current_step=int(step["step_index"]), error="{} 个文件失败".format(len(failures)))
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
        if source_step:
            with closing(self._connect()) as db:
                row = db.execute("SELECT result_json FROM steps WHERE task_id=? AND step_id=?", (task_id, source_step)).fetchone()
            if not row:
                raise ValueError("找不到上游步骤: {}".format(source_step))
            prior = _decode(row["result_json"], {})
            items = []
            for item in prior.get("results") or []:
                url = item.get("output_url") or ((item.get("output_urls") or [""])[0])
                if url:
                    items.append({"url": url, "filename": item.get("file") or ""})
            if not items:
                raise ValueError("上游步骤没有可下载结果: {}".format(source_step))
            if isinstance(request.get("inputs"), dict):
                request["inputs"] = {**request["inputs"], "items": items}
            else:
                request["items"] = items
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
                stale = db.execute("SELECT resource_key,owner_pid FROM resource_locks").fetchall()
                for row in stale:
                    if not self._pid_alive(int(row["owner_pid"])):
                        db.execute("DELETE FROM resource_locks WHERE resource_key=?", (row["resource_key"],))
                placeholders = ",".join("?" for _ in resources)
                conflicts = db.execute(
                    f"SELECT resource_key FROM resource_locks WHERE resource_key IN ({placeholders}) AND task_id<>?",
                    (*resources, task_id),
                ).fetchall()
                if not conflicts:
                    for resource in resources:
                        db.execute(
                            "INSERT OR REPLACE INTO resource_locks(resource_key,task_id,step_id,owner_pid,acquired_at) VALUES(?,?,?,?,?)",
                            (resource, task_id, step_id, os.getpid(), now),
                        )
                    db.commit()
                    self._set_task_status(task_id, "running", error="")
                    return
                waiting_for = str(conflicts[0]["resource_key"])
                db.commit()
            self._set_task_status(task_id, "queued", error="等待输出目录可用: " + waiting_for)
            time.sleep(0.5)

    def _release_resources(self, task_id: str, step_id: str) -> None:
        with closing(self._connect()) as db:
            db.execute("DELETE FROM resource_locks WHERE task_id=? AND step_id=?", (task_id, step_id))
            db.commit()

    def control(self, task_id: str, action: str, *, start_worker: bool = True) -> dict[str, Any]:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("任务不存在: {}".format(task_id))
            status = str(row["status"])
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
            db.execute("UPDATE tasks SET status=?, updated_at=? WHERE task_id=?", (target, _now(), task_id))
            self._event(db, task_id, "control_" + action, {"from": status, "to": target})
            db.commit()
        if target == "queued" and start_worker:
            self._wait_previous_worker_exit(task_id)
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

    def cloud_items(self, task_id: str, step_id: str) -> dict[str, dict[str, Any]]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM cloud_items WHERE task_id=? AND step_id=?", (task_id, step_id)).fetchall()
        return {str(row["source_path"]): dict(row) for row in rows}

    def list_tasks(self, *, status: str = "", cargo_number: str = "", limit: int = 100) -> dict[str, Any]:
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
            rows = db.execute("SELECT * FROM tasks" + where + " ORDER BY updated_at DESC LIMIT ?", (*values, max(1, min(int(limit), 500)))).fetchall()
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
                "needs_attention": sum(value in {"failed", "partial_failed"} for value in statuses),
                "completed_today": sum(
                    str(item["status"]) == "completed" and str(item["updated_at"]).startswith(today)
                    for item in status_rows
                ),
            },
            "tasks": tasks,
            "total": len(status_rows),
            "shown": len(tasks),
            "quota": self.quota_summary(),
            "updated_at": _now(),
        }

    def get(self, task_id: str) -> dict[str, Any]:
        with closing(self._connect()) as db:
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not task:
                raise ValueError("任务不存在: {}".format(task_id))
            steps = db.execute("SELECT * FROM steps WHERE task_id=? ORDER BY step_index", (task_id,)).fetchall()
        detail = self._task_summary(task)
        detail["steps"] = [self._step_summary(row) for row in steps]
        detail["quota_estimate"] = (_decode(task["plan_json"], {}).get("quota_estimate") or {})
        detail["output_directories"] = sorted({
            str(_request_input(_decode(row["request_json"], {}), "output_folder", "") or "")
            for row in steps
            if _request_input(_decode(row["request_json"], {}), "output_folder", "")
        })
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
        }

    def _step_summary(self, row: sqlite3.Row) -> dict[str, Any]:
        items = self.cloud_items(row["task_id"], row["step_id"])
        counts: dict[str, int] = {}
        for item in items.values():
            counts[item["state"]] = counts.get(item["state"], 0) + 1
        return {
            "index": int(row["step_index"]), "step_id": row["step_id"], "name": row["name"],
            "operation": row["operation"], "execution_type": row["execution_type"],
            "item_count": int(row["item_count"]), "status": row["status"], "item_counts": counts,
            "error": row["error"], "started_at": row["started_at"], "finished_at": row["finished_at"], "updated_at": row["updated_at"],
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


def run_task_worker(task_id: str, db_path: str | Path | None = None) -> dict[str, Any]:
    center = TaskCenter(db_path)

    def runner(request: dict[str, Any], _context: TaskExecutionContext) -> dict[str, Any]:
        from video_tool import run_request

        return run_request(request)

    return center.execute(task_id, runner)
