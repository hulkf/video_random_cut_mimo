import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import kaipai_portrait
from core.video_task_center import TaskCenter


class PortraitTaskTest(unittest.TestCase):
    def test_requires_exact_sources_and_blocks_changed_file_before_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            style = root / "1234"
            style.mkdir()
            source = style / "one.mp4"
            source.write_bytes(b"first")
            center = TaskCenter(root / "tasks.sqlite")
            with patch.object(kaipai_portrait, "MATERIAL_ROOT", root):
                for invalid in ([str(style)], [str(source), str(source)], ["one.mp4"]):
                    with self.assertRaises(ValueError):
                        kaipai_portrait.sources(invalid)
                plan = center.create_plan(
                    "bj-portrait-test", "开拍人像增强",
                    [{"id": "portrait", "request": {
                        "operation": kaipai_portrait.OPERATION, "inputs": {"input_sources": [str(source)]},
                    }}],
                    parameter_lines=[str(source)], risk_note="使用已有会员", authorized_operations=[kaipai_portrait.OPERATION],
                )
            self.assertEqual(plan["status"], "awaiting_confirmation")
            self.assertEqual(plan["execution_type"], "cloud")
            self.assertEqual(plan["steps"][0]["item_count"], 1)
            center.confirm(plan["task_id"], plan["plan_version"], start_worker=False)
            source.write_bytes(b"changed content")
            result = center.execute(plan["task_id"], lambda *_: self.fail("changed file reached worker"))
            self.assertEqual(result["status"], "failed")
            self.assertIn("输入内容已变化", result["error"])

    def test_direct_executor_requires_task_center(self):
        with self.assertRaises(PermissionError):
            kaipai_portrait.run({"inputs": {"input_sources": []}})

    def test_failed_portrait_resumes_same_confirmed_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "1234" / "one.mp4"
            source.parent.mkdir()
            source.write_bytes(b"original")
            center = TaskCenter(root / "tasks.sqlite")
            with patch.object(kaipai_portrait, "MATERIAL_ROOT", root):
                plan = center.create_plan(
                    "portrait-resume", "开拍恢复",
                    [{"id": "portrait", "request": {"operation": kaipai_portrait.OPERATION,
                      "inputs": {"input_sources": [str(source)]}}}],
                    authorized_operations=[kaipai_portrait.OPERATION],
                )
            center.confirm(plan["task_id"], plan["plan_version"], start_worker=False)
            def fail_upload(*_):
                raise RuntimeError("upload did not start")
            failed = center.execute(plan["task_id"], fail_upload)
            self.assertEqual(failed["status"], "failed")
            with patch.object(center, "_spawn_worker") as spawn, \
                    patch.object(center, "_pid_alive", return_value=False):
                resumed = center.control(plan["task_id"], "resume")
            self.assertEqual(resumed["status"], "queued")
            self.assertEqual(resumed["plan_version"], plan["plan_version"])
            self.assertEqual(resumed["error"], "")
            spawn.assert_called_once_with(plan["task_id"])
            with self.assertRaises(ValueError):
                center.control(plan["task_id"], "resume", start_worker=False)
