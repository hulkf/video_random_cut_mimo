"""Resolve exact, completed Benjamin flat-lay task artifacts for the next stage."""

import hashlib
from pathlib import Path
import re


STEPS = {
    "remove-text": ("kaipai-cli", "video-remove-auto"),
    "smartedit": ("kaipai-smartedit", "smartedit"),
}


def resolve(center, reference, stage):
    if not isinstance(reference, dict) or set(reference) != {"task_id", "artifact_id"}:
        raise ValueError("开拍接续需要上一步任务编号和产物编号")
    task_id, artifact_id = reference["task_id"], reference["artifact_id"]
    if (stage not in STEPS or not isinstance(task_id, str) or not isinstance(artifact_id, str)
            or not re.fullmatch(r"bj-kaipai-cli-[a-f0-9]{24}", task_id)
            or not re.fullmatch(r"artifact_[a-f0-9]{64}", artifact_id)):
        raise ValueError("开拍接续编号或阶段无效")
    step_id, mode = STEPS[stage]
    task = center.get(task_id)
    steps = task["steps"]
    if (task["status"] != "completed" or len(steps) != 1 or steps[0]["step_id"] != step_id
            or steps[0]["operation"] != "kaipai_cli_process" or steps[0].get("kaipai_mode") != mode):
        raise ValueError("该任务不是已完成的开拍上一步")
    matches = [item for item in task["artifacts"] if item["artifact_id"] == artifact_id
               and item["step_id"] == step_id and item["media_type"] == "video" and item["stage"] == "final"]
    if len(matches) != 1:
        raise ValueError("找不到本任务指定的可接续成片")
    if stage == "smartedit":
        resolve(center, matches[0]["validation"].get("upstream"), "remove-text")
    path = Path(matches[0]["path"])
    key = hashlib.sha256(f"{task_id}:{step_id}".encode()).hexdigest()[:24]
    directory = center.db_path.resolve().parent / "kaipai-cli" / key / "result-1"
    if path.parent != directory or path.resolve(strict=True) != path or not path.is_file() or path.suffix.lower() != ".mp4":
        raise ValueError("产物没有落在原开拍任务的结果目录")
    if not path.stat().st_size:
        raise ValueError("本次开拍产物为空")
    return path
