import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.video_task_center import QuotaExceeded, TaskCenter


class VideoTaskCenterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.center = TaskCenter(Path(self.temp.name) / "tasks.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_plan_classifies_local_cloud_and_mixed_tasks(self):
        local = self.center.create_plan(
            "VT-LOCAL", "本地尺寸转换", [{"id": "resize", "request": {
                "operation": "video_resize", "input_path": "D:/in", "output_folder": "D:/out"
            }}]
        )
        cloud = self.center.create_plan(
            "VT-CLOUD", "云端全消", [{"id": "clear", "request": {
                "operation": "kaipai_process", "input_path": "D:/in", "task_name": "视频智能全消"
            }, "item_count": 10}]
        )
        mixed = self.center.create_plan(
            "VT-MIXED", "混合处理", [
                {"id": "resize", "request": {"operation": "video_resize", "input_path": "D:/in", "output_folder": "D:/mid"}},
                {"id": "repair", "request": {"operation": "kaipai_process", "input_path": "D:/mid", "task_name": "视频画质修复"}, "item_count": 2},
            ]
        )
        self.assertEqual(local["execution_type"], "local")
        self.assertEqual(cloud["execution_type"], "cloud")
        self.assertEqual(mixed["execution_type"], "mixed")
        self.assertEqual(cloud["quota_estimate"], {"videoscreenclear": 10})

    def test_confirm_reserves_each_quota_independently(self):
        self.center.create_plan("VT-A", "A", [
            {"id": "clear", "request": {"operation": "kaipai_process", "input_path": "D:/a", "task_name": "videoscreenclear"}, "item_count": 40},
            {"id": "repair", "request": {"operation": "kaipai_process", "input_path": "D:/b", "task_name": "hdvideoallinone"}, "item_count": 50},
        ])
        self.center.confirm("VT-A", 1, start_worker=False)
        self.center.create_plan("VT-B", "B", [
            {"id": "clear", "request": {"operation": "kaipai_process", "input_path": "D:/c", "task_name": "videoscreenclear"}, "item_count": 11},
        ])
        with self.assertRaises(QuotaExceeded):
            self.center.confirm("VT-B", 1, start_worker=False)
        quota = self.center.quota_summary()
        self.assertEqual(quota["videoscreenclear"]["committed"], 40)
        self.assertEqual(quota["videoscreenclear"]["reserved_pending"], 40)
        self.assertEqual(quota["hdvideoallinone"]["committed"], 50)

    def test_execute_persists_steps_and_outputs(self):
        self.center.create_plan("VT-RUN", "本地任务", [
            {"id": "resize", "name": "转尺寸", "request": {
                "operation": "video_resize", "input_path": "D:/in", "output_folder": "D:/out"
            }},
            {"id": "validate", "name": "校验", "request": {
                "operation": "validate", "path": "D:/out/a.mp4"
            }},
        ])
        self.center.confirm("VT-RUN", 1, start_worker=False)
        calls = []

        def runner(request, _context):
            calls.append(request["operation"])
            return {"success": True, "operation": request["operation"], "outputs": ["D:/out/a.mp4"]}

        result = self.center.execute("VT-RUN", runner)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls, ["video_resize", "validate"])
        detail = self.center.get("VT-RUN")
        self.assertEqual([step["status"] for step in detail["steps"]], ["completed", "completed"])
        self.assertEqual(detail["output_directories"], ["D:/out"])

    def test_cloud_item_checkpoint_is_reused_after_interruption(self):
        self.center.create_plan("VT-RECOVER", "全消", [{
            "id": "clear", "request": {
                "operation": "kaipai_process", "input_path": "D:/in", "task_name": "videoscreenclear"
            }, "item_count": 1,
        }])
        self.center.confirm("VT-RECOVER", 1, start_worker=False)
        context = self.center.execution_context("VT-RECOVER", "clear")
        context.cloud_item("D:/in/a.mp4", state="submitted", cloud_task_id="cloud-1")
        self.assertEqual(context.resume_items()["D:/in/a.mp4"]["cloud_task_id"], "cloud-1")
        context.cloud_item("D:/in/a.mp4", state="completed", cloud_task_id="cloud-1", output_url="https://out/a.mp4")
        self.assertEqual(context.resume_items()["D:/in/a.mp4"]["output_url"], "https://out/a.mp4")

    def test_pause_and_resume_do_not_affect_another_task(self):
        step = [{"id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}}]
        self.center.create_plan("VT-1", "一", step)
        self.center.create_plan("VT-2", "二", step)
        self.center.confirm("VT-1", 1, start_worker=False)
        self.center.confirm("VT-2", 1, start_worker=False)
        self.center.control("VT-1", "pause", start_worker=False)
        self.assertEqual(self.center.get("VT-1")["status"], "paused")
        self.assertEqual(self.center.get("VT-2")["status"], "queued")
        self.center.control("VT-1", "resume", start_worker=False)
        self.assertEqual(self.center.get("VT-1")["status"], "queued")

    def test_board_counts_waiting_cloud_inside_running_only_once(self):
        step = [{"id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}}]
        for task_id in ("VT-1", "VT-2", "VT-3"):
            self.center.create_plan(task_id, task_id, step)
            self.center.confirm(task_id, 1, start_worker=False)
        self.center._set_task_status("VT-1", "running", current_step=0)
        self.center._set_task_status("VT-2", "waiting_cloud", current_step=0)
        self.center._set_task_status("VT-3", "paused", current_step=0)
        board = self.center.list_tasks()
        self.assertEqual(board["counts"]["in_progress"], 2)
        self.assertEqual(board["counts"]["executing"], 1)
        self.assertEqual(board["counts"]["waiting_cloud"], 1)
        self.assertEqual(board["counts"]["paused"], 1)

    def test_board_counts_all_matching_tasks_even_when_rows_are_limited(self):
        step = [{"id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}}]
        for index in range(3):
            self.center.create_plan(f"VT-{index}", str(index), step)
        board = self.center.list_tasks(limit=1)
        self.assertEqual(board["counts"]["awaiting_confirmation"], 3)
        self.assertEqual(board["total"], 3)
        self.assertEqual(board["shown"], 1)

    def test_cannot_reuse_finished_task_id_for_a_new_plan(self):
        step = [{"id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}}]
        self.center.create_plan("VT-OLD", "旧任务", step)
        self.center.confirm("VT-OLD", 1, start_worker=False)
        self.center.execute("VT-OLD", lambda request, context: {"success": True})
        with self.assertRaisesRegex(ValueError, "不能修改计划"):
            self.center.create_plan("VT-OLD", "重试", step)

    def test_cloud_quota_requires_a_known_positive_item_count(self):
        with self.assertRaisesRegex(ValueError, "无法确定"):
            self.center.create_plan("VT-UNKNOWN", "未知数量", [{
                "id": "clear", "request": {
                    "operation": "kaipai_process", "input_path": "D:/not-found", "task_name": "videoscreenclear"
                }
            }])

    def test_changed_input_snapshot_stops_before_execution(self):
        source = Path(self.temp.name) / "input"
        source.mkdir()
        video = source / "a.mp4"
        video.write_bytes(b"first")
        self.center.create_plan("VT-CHANGED", "输入变化", [{
            "id": "resize", "request": {
                "operation": "video_resize", "input_path": str(source),
                "output_folder": str(Path(self.temp.name) / "out"),
            }
        }])
        self.center.confirm("VT-CHANGED", 1, start_worker=False)
        video.write_bytes(b"changed")
        called = False

        def runner(_request, _context):
            nonlocal called
            called = True
            return {"success": True}

        result = self.center.execute("VT-CHANGED", runner)
        self.assertFalse(called)
        self.assertEqual(result["status"], "failed")
        self.assertIn("输入内容已变化", result["error"])

    def test_same_output_directory_is_serialized_between_tasks(self):
        output = str(Path(self.temp.name) / "shared")
        step = [{"id": "one", "request": {"operation": "video_resize", "input_path": "D:/missing", "output_folder": output}}]
        for task_id in ("VT-LOCK-1", "VT-LOCK-2"):
            self.center.create_plan(task_id, task_id, step)
            self.center.confirm(task_id, 1, start_worker=False)
        first_entered = threading.Event()
        release_first = threading.Event()
        order = []

        def first_runner(_request, _context):
            order.append("first-start")
            first_entered.set()
            release_first.wait(3)
            order.append("first-end")
            return {"success": True}

        def second_runner(_request, _context):
            order.append("second-start")
            return {"success": True}

        first = threading.Thread(target=self.center.execute, args=("VT-LOCK-1", first_runner))
        second = threading.Thread(target=self.center.execute, args=("VT-LOCK-2", second_runner))
        first.start()
        self.assertTrue(first_entered.wait(2))
        second.start()
        time.sleep(0.2)
        self.assertNotIn("second-start", order)
        release_first.set()
        first.join(3)
        second.join(3)
        self.assertEqual(order, ["first-start", "first-end", "second-start"])

    def test_card_binding_survives_session_changes(self):
        self.center.create_plan("VT-CARD", "卡片", [{"id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}}])
        task = self.center.bind_card("VT-CARD", "oc_chat", "om_card")
        self.assertEqual(task["chat_id"], "oc_chat")
        self.assertEqual(task["card_message_id"], "om_card")


if __name__ == "__main__":
    unittest.main()
