from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from core import kaipai_cli, kaipai_playwright


def test_resume_after_unknown_submit_does_not_submit_again(tmp_path, monkeypatch):
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"test")
    monkeypatch.setattr(kaipai_cli, "MATERIAL_ROOT", tmp_path)
    monkeypatch.setattr(kaipai_cli, "cli_environment", lambda: (["fake-cli"], {}))
    context = SimpleNamespace(task_id="task", step_id="step", check=lambda: None,
                              center=SimpleNamespace(db_path=tmp_path / "tasks.db"))
    invocations = []

    def invoke(command, env, directory, name, args, check, timeout):
        invocations.append(name)
        if name == "room":
            return 0, [], [{"type": "session", "session_id": "v2:original"}]
        return 0, [], []

    monkeypatch.setattr(kaipai_cli, "_run", invoke)
    request = {"inputs": {"input_path": str(source), "mode": "video-repair"},
               "_task_center_context": context}
    for _ in range(2):
        with pytest.raises(RuntimeError, match="原会话提交结果待核实"):
            kaipai_cli.run(request)
    assert invocations == ["auth", "room", "submit"]


def test_artifact_must_be_completed_and_match_original_task():
    task = {"id": "tool_original", "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"parts": [{"url": "https://kaipai.example/video.mp4", "mediaType": "video/mp4"}]}]}
    assert kaipai_cli._artifact([task], "tool_original", False) == "https://kaipai.example/video.mp4"
    with pytest.raises(RuntimeError, match="原任务"):
        kaipai_cli._artifact([task], "tool_other", False)
    assert "SENSITIVE_MEDIA_CONTENT" in kaipai_cli._rejection(
        [{"meta": {"code": 422000, "msg": "SENSITIVE_MEDIA_CONTENT"}}]
    )


def test_smartedit_requires_an_explicit_template_and_checks_the_render_receipt():
    with pytest.raises(ValueError, match="SEC"):
        kaipai_cli.template_id({"mode": "smartedit"})
    assert kaipai_cli.template_id({"mode": "smartedit", "template_material_id": "SEC0076"}) == "SEC0076"
    part = {"url": "https://kaipai.example/draft.mp4", "mediaType": "video/mp4",
            "data": {"template_material_id": "SEC0076"}}
    task = {"id": "original", "status": {"state": "TASK_STATE_COMPLETED"}, "artifacts": [{"parts": [part]}]}
    with pytest.raises(RuntimeError, match="SEC"):
        kaipai_cli._artifact([task], "original", False, "SEC0015")
    assert kaipai_cli._artifact([task], "original", False, "SEC0076") == part["url"]
    receipt = {"data": part.pop("data")}
    task["artifacts"] = [{"parts": [part, receipt]}]
    assert kaipai_cli._artifact([task], "original", False, "SEC0076") == part["url"]
    task["artifacts"] = [{"parts": [part]}, {"parts": [receipt]}]
    with pytest.raises(RuntimeError, match="SEC"):
        kaipai_cli._artifact([task], "original", False, "SEC0076")


def test_smartedit_recovers_its_own_session_without_resubmitting(tmp_path, monkeypatch):
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(kaipai_cli, "MATERIAL_ROOT", tmp_path)
    monkeypatch.setattr(kaipai_cli, "cli_environment", lambda: (["fake-cli"], {}))
    context = SimpleNamespace(task_id="task", step_id="step", check=lambda: None,
                              center=SimpleNamespace(db_path=tmp_path / "tasks.db"))
    invocations = []

    def invoke(command, env, directory, name, args, check, timeout):
        invocations.append(name)
        if name == "validate":
            assert args[-2:] == ("--validate-only", "--json")
            return 0, [{"params": {"templateMaterialId": "SEC0076"}}], []
        if name == "submit":
            assert args == ("smartedit", "--video-file", str(source), "--template-material-id", "SEC0076", "--detach")
            record = {"event": "execution.accepted", "data": {"session_id": "v2:real", "task_ids": []}}
            (directory / "submit.jsonl").write_text(json.dumps(record), encoding="utf-8")
            raise TimeoutError("interrupted after acceptance")
        if name == "history-snapshot":
            assert args == ("history-detail", "-r", "v2:real")
            return 0, [{"session_id": "v2:real", "active_task_ids": ["tool_real"]}], []
        if name == "progress":
            assert args == ("task-progress", "-r", "v2:real", "--task-id", "tool_real", "--watch")
            return 1, [], []
        return 0, [], []

    monkeypatch.setattr(kaipai_cli, "_run", invoke)
    request = {"inputs": {"input_path": str(source), "mode": "smartedit", "template_material_id": "SEC0076"},
               "_task_center_context": context}
    with pytest.raises(TimeoutError):
        kaipai_cli.run(request)
    with pytest.raises(RuntimeError, match="原任务"):
        kaipai_cli.run(request)
    assert invocations == ["validate", "auth", "submit", "history-snapshot", "progress"]


def test_smartedit_artifact_is_only_final_after_replacement(tmp_path):
    draft, clean, receipt = (tmp_path / name for name in ("draft.mp4", "clean.mp4", "name-card-review.json"))
    draft.write_bytes(b"draft with a name card")
    clean.write_bytes(b"clean result")
    receipt.write_text(json.dumps({
        "result": {"task_id": "task", "final_video": str(draft), "name_card": "removed"},
        "retained_identity": {"path": str(draft), "size": clean.stat().st_size, "mtime_ns": clean.stat().st_mtime_ns},
    }), encoding="utf-8")
    assert not kaipai_cli.reviewed_artifact(draft, "task")
    clean.replace(draft)
    assert kaipai_cli.reviewed_artifact(draft, "task")
    assert not kaipai_cli.reviewed_artifact(draft, "other-task")


def test_flat_lay_handoff_only_accepts_the_expected_completed_artifact(tmp_path):
    import hashlib

    task_id = "bj-kaipai-cli-" + "a" * 24
    artifact_id = "artifact_" + "b" * 64
    upstream = {"task_id": task_id, "artifact_id": artifact_id}
    key = hashlib.sha256(f"{task_id}:kaipai-cli".encode()).hexdigest()[:24]
    product = tmp_path / "kaipai-cli" / key / "result-1" / "clip.mp4"
    product.parent.mkdir(parents=True)
    product.write_bytes(b"result")
    step = {"step_id": "kaipai-cli", "operation": "kaipai_cli_process", "kaipai_mode": "video-remove-auto"}
    artifact = {"artifact_id": artifact_id, "path": str(product), "step_id": step["step_id"],
                "media_type": "video", "stage": "final"}
    task = {"status": "completed", "steps": [step], "artifacts": [artifact]}
    center = SimpleNamespace(db_path=tmp_path / "tasks.db", get=lambda _: task)
    request = {"mode": "smartedit", "input_path": str(product), "upstream": upstream,
               "source_revision": f"{product.stat().st_size}:{product.stat().st_mtime_ns}"}
    assert kaipai_cli.request_source(request, center) == product
    with pytest.raises(ValueError, match="不一致"):
        kaipai_cli.request_source({**request, "input_path": str(tmp_path / "other.mp4")}, center)
    with pytest.raises(ValueError, match="不是已完成"):
        kaipai_playwright.request_sources({"effect": "goods", "input_sources": [str(product)], "upstream": upstream}, center)
    artifact["stage"] = "intermediate"
    with pytest.raises(ValueError, match="可接续"):
        kaipai_cli.request_source(request, center)
