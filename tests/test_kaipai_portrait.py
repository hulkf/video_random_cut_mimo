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
