from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from core import kaipai_cli


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
            record = {"event": "execution.accepted", "data": {"session_id": "v2:real", "task_ids": ["tool_real"]}}
            (directory / "submit.jsonl").write_text(json.dumps(record), encoding="utf-8")
            raise TimeoutError("interrupted after acceptance")
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
    assert invocations == ["validate", "auth", "submit", "progress"]
