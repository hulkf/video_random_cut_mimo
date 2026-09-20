import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import video_task_center_cli
import video_tool
from video_task_center import PUBLIC_CAPABILITIES, TASK_CENTER_OPERATION_SPECS, dispatch


class VideoTaskCenterInterfaceTests(unittest.TestCase):
    def test_all_adapters_share_one_canonical_operation_contract(self):
        self.assertIs(PUBLIC_CAPABILITIES["operations"], TASK_CENTER_OPERATION_SPECS)
        for name, spec in TASK_CENTER_OPERATION_SPECS.items():
            self.assertEqual(video_tool.CAPABILITIES["operations"][name], spec)

    def test_python_callers_have_a_ready_to_use_public_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            result = dispatch(
                {"operation": "task_center_list", "inputs": {"limit": 1}},
                db_path=str(Path(temp) / "tasks.db"),
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["task"]["tasks"], [])

    def test_capabilities_are_platform_neutral(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "video_task_center_cli.py"), "capabilities"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["constraints"]["platform_neutral"])
        self.assertIn("task_center_events", payload["operations"])
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        self.assertNotIn("feishu", serialized)
        self.assertNotIn("card_message_id", serialized)
        self.assertNotIn("confirmation_message_id", serialized)

    def test_cli_always_emits_utf8_for_windows_pipe_callers(self):
        env = {**os.environ, "PYTHONUTF8": "0"}
        completed = subprocess.run(
            [sys.executable, str(ROOT / "video_task_center_cli.py"), "capabilities"],
            cwd=ROOT, env=env, capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout.decode("utf-8"))
        self.assertIn("创建或更新视频任务计划", payload["operations"]["task_center_plan"]["description"])

    def test_cli_always_reads_utf8_for_windows_pipe_callers(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {
                **os.environ,
                "PYTHONUTF8": "0",
                "VIDEO_TASK_CENTER_DB": str(Path(temp) / "tasks.db"),
            }
            request = {
                "operation": "task_center_list",
                "inputs": {"cargo_number": "中文货号", "limit": 1},
            }
            completed = subprocess.run(
                [sys.executable, str(ROOT / "video_task_center_cli.py"), "run", "--request", "-"],
                cwd=ROOT, env=env, input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
                capture_output=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads(completed.stdout.decode("utf-8"))["success"])

    def test_plan_and_status_use_the_same_external_database(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "VIDEO_TASK_CENTER_DB": str(Path(temp) / "tasks.db")}
            request = {
                "operation": "task_center_plan",
                "inputs": {
                    "task_id": "WEB-PLAN-1",
                    "title": "Web submitted task",
                    "parameter_lines": ["input: sample.mp4"],
                    "steps": [{
                        "id": "validate",
                        "request": {"operation": "validate", "path": "sample.mp4"},
                    }],
                },
            }
            planned = subprocess.run(
                [sys.executable, str(ROOT / "video_task_center_cli.py"), "run", "--request", "-"],
                cwd=ROOT, env=env, input=json.dumps(request), capture_output=True,
                text=True, encoding="utf-8",
            )
            self.assertEqual(planned.returncode, 0, planned.stderr)
            self.assertEqual(json.loads(planned.stdout)["task"]["status"], "awaiting_confirmation")

            status_request = {
                "operation": "task_center_status",
                "inputs": {"task_id": "WEB-PLAN-1"},
            }
            status = subprocess.run(
                [sys.executable, str(ROOT / "video_task_center_cli.py"), "run", "--request", "-"],
                cwd=ROOT, env=env, input=json.dumps(status_request), capture_output=True,
                text=True, encoding="utf-8",
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout)["task"]["task_id"], "WEB-PLAN-1")

    def test_events_are_available_to_non_hermes_consumers(self):
        with tempfile.TemporaryDirectory() as temp:
            previous = os.environ.get("VIDEO_TASK_CENTER_DB")
            os.environ["VIDEO_TASK_CENTER_DB"] = str(Path(temp) / "tasks.db")
            try:
                video_task_center_cli.run_request({
                    "operation": "task_center_plan",
                    "inputs": {
                        "task_id": "WEB-EVENT-1",
                        "title": "Event task",
                        "parameter_lines": ["input: sample.mp4"],
                        "steps": [{
                            "id": "validate",
                            "request": {"operation": "validate", "path": "sample.mp4"},
                        }],
                    },
                })
                result = video_task_center_cli.run_request({
                    "operation": "task_center_events",
                    "inputs": {"after_event_id": 0, "limit": 10},
                })
            finally:
                if previous is None:
                    os.environ.pop("VIDEO_TASK_CENTER_DB", None)
                else:
                    os.environ["VIDEO_TASK_CENTER_DB"] = previous
        self.assertEqual(result["task"]["events"][0]["event_type"], "plan_created")
        self.assertEqual(result["task"]["next_event_id"], 1)

    def test_public_interface_rejects_and_hides_hermes_delivery_state(self):
        with tempfile.TemporaryDirectory() as temp:
            previous = os.environ.get("VIDEO_TASK_CENTER_DB")
            os.environ["VIDEO_TASK_CENTER_DB"] = str(Path(temp) / "tasks.db")
            try:
                with self.assertRaisesRegex(ValueError, "presentation or transport"):
                    video_task_center_cli.run_request({
                        "operation": "task_center_plan",
                        "inputs": {
                            "task_id": "WEB-NO-CARD",
                            "title": "Public task",
                            "chat_id": "oc_forbidden",
                            "parameter_lines": ["input: sample.mp4"],
                            "steps": [{
                                "id": "validate",
                                "request": {"operation": "validate", "path": "sample.mp4"},
                            }],
                        },
                    })
                with self.assertRaisesRegex(ValueError, "presentation or transport"):
                    video_task_center_cli.run_request({
                        "operation": "task_center_list",
                        "inputs": {"pending_card_kind": "progress"},
                    })
                video_task_center_cli.run_hermes_request({
                    "operation": "task_center_plan",
                    "inputs": {
                        "task_id": "WEB-HIDDEN-CARD",
                        "title": "Hermes compatibility task",
                        "chat_id": "oc_legacy",
                        "parameter_lines": ["input: sample.mp4"],
                        "steps": [{
                            "id": "validate",
                            "request": {"operation": "validate", "path": "sample.mp4"},
                        }],
                    },
                })
                public = video_task_center_cli.run_request({
                    "operation": "task_center_status",
                    "inputs": {"task_id": "WEB-HIDDEN-CARD"},
                })["task"]
            finally:
                if previous is None:
                    os.environ.pop("VIDEO_TASK_CENTER_DB", None)
                else:
                    os.environ["VIDEO_TASK_CENTER_DB"] = previous
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("chat_id", serialized)
        self.assertNotIn("card_message_id", serialized)
        self.assertNotIn("pending_card_json", serialized)


if __name__ == "__main__":
    unittest.main()
