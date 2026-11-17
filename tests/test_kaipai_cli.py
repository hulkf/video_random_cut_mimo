from pathlib import Path
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
