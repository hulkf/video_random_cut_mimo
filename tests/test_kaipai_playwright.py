import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import kaipai_playwright as bridge


def test_plan_only_accepts_effects_supported_by_the_executor(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "capabilities", lambda: [{"effect": "portrait", "name": "人像增强"}])
    monkeypatch.setattr(bridge, "sources", lambda values: [Path(value) for value in values])
    file = tmp_path / "source.mp4"
    assert bridge.validate_inputs({"effect": "portrait", "input_sources": [str(file)]}) == [file]
    with pytest.raises(ValueError, match="未支持"):
        bridge.validate_inputs({"effect": "denoise", "input_sources": [str(file)]})


def test_resumes_only_original_playwright_run(monkeypatch, tmp_path):
    file = tmp_path / "source.mp4"
    monkeypatch.setattr(bridge, "_inputs", lambda values: [file])
    monkeypatch.setattr(bridge, "_installation", lambda: (tmp_path / "python", tmp_path, tmp_path / "profile"))
    context = SimpleNamespace(
        task_id="same-task", step_id="same-step", center=SimpleNamespace(db_path=tmp_path / "db.sqlite"),
        check=lambda: None,
    )
    identity = hashlib.sha256(b"same-task:same-step").hexdigest()[:24]
    directory = tmp_path / "kaipai-playwright" / identity
    run = directory / "run"
    run.mkdir(parents=True)
    output = directory / "results" / "source-人像增强.mp4"
    output.parent.mkdir()
    output.write_bytes(b"verified fixture")
    state = run / "state.json"
    state.write_text(json.dumps({
        "stage": "verified", "effect": "portrait", "source": str(file), "output": str(output),
        "result_sha256": "fixture",
    }), encoding="utf-8")
    invocations = []

    def run_command(command, env, directory, name, args, check, **kwargs):
        invocations.append(args)
        return 0, [], []

    monkeypatch.setattr(bridge, "_run", run_command)
    request = {
        "_task_center_context": context,
        "authorization": {"confirmed": True, "scope": bridge.OPERATION},
        "inputs": {"effect": "portrait", "input_sources": [str(file)]},
    }
    assert bridge.run(request)["outputs"] == [str(output)]
    assert invocations[1] == ["kaipai", "resume", "--run-dir", str(run)]
    request["inputs"]["effect"] = "goods"
    with pytest.raises(RuntimeError, match="原开拍批次不一致"):
        bridge.run(request)
    assert len(invocations) == 2
