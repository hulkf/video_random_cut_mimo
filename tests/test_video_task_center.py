import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.video_task_center import QuotaExceeded, TaskCenter


class VideoTaskCenterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.center = TaskCenter(Path(self.temp.name) / "tasks.db")

    def tearDown(self):
        self.temp.cleanup()

    def video_dir(self, name, count):
        folder = Path(self.temp.name) / name
        folder.mkdir(exist_ok=True)
        for index in range(count):
            (folder / f"{index}.mp4").write_bytes(b"video")
        return str(folder)

    def mark_worker_lost(self, task_id):
        db = self.center._connect()
        try:
            db.execute("UPDATE tasks SET status='running',worker_pid=999999,worker_token='lost' WHERE task_id=?", (task_id,))
            db.commit()
        finally:
            db.close()

    def test_plan_classifies_local_cloud_and_mixed_tasks(self):
        local = self.center.create_plan(
            "VT-LOCAL", "本地尺寸转换", [{"id": "resize", "request": {
                "operation": "video_resize", "input_path": "D:/in", "output_folder": "D:/out"
            }}]
        )
        cloud = self.center.create_plan(
            "VT-CLOUD", "云端全消", [{"id": "clear", "request": {
                "operation": "kaipai_process", "input_path": self.video_dir("cloud", 10), "task_name": "视频智能全消"
            }, "item_count": 10}]
        )
        mixed = self.center.create_plan(
            "VT-MIXED", "混合处理", [
                {"id": "resize", "request": {"operation": "video_resize", "input_path": "D:/in", "output_folder": "D:/mid"}},
                {"id": "repair", "request": {"operation": "kaipai_process", "input_path": self.video_dir("mixed", 2), "task_name": "视频画质修复"}, "item_count": 2},
            ]
        )
        self.assertEqual(local["execution_type"], "local")
        self.assertEqual(cloud["execution_type"], "cloud")
        self.assertEqual(mixed["execution_type"], "mixed")
        self.assertEqual(cloud["quota_estimate"], {"videoscreenclear": 10})

    def test_confirm_reserves_each_quota_independently(self):
        self.center.create_plan("VT-A", "A", [
            {"id": "clear", "request": {"operation": "kaipai_process", "input_path": self.video_dir("a", 40), "task_name": "videoscreenclear"}, "item_count": 40},
            {"id": "repair", "request": {"operation": "kaipai_process", "input_path": self.video_dir("b", 50), "task_name": "hdvideoallinone"}, "item_count": 50},
        ])
        self.center.confirm("VT-A", 1, start_worker=False)
        self.center.create_plan("VT-B", "B", [
            {"id": "clear", "request": {"operation": "kaipai_process", "input_path": self.video_dir("c", 11), "task_name": "videoscreenclear"}, "item_count": 11},
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
        self.assertEqual(detail["output_directories"], [r"D:\out"])
        self.assertEqual(detail["input_paths"], [r"D:\in", r"D:\out\a.mp4"])

    def test_cloud_item_checkpoint_is_reused_after_interruption(self):
        self.center.create_plan("VT-RECOVER", "全消", [{
            "id": "clear", "request": {
                "operation": "kaipai_process", "input_path": self.video_dir("recover", 1), "task_name": "videoscreenclear"
            }, "item_count": 1,
        }])
        self.center.confirm("VT-RECOVER", 1, start_worker=False)
        context = self.center.execution_context("VT-RECOVER", "clear")
        context.before_cloud_submit("D:/in/a.mp4")
        context.cloud_item("D:/in/a.mp4", state="submitted", cloud_task_id="cloud-1")
        self.assertEqual(context.resume_items()["D:/in/a.mp4"]["cloud_task_id"], "cloud-1")
        context.cloud_item("D:/in/a.mp4", state="completed", cloud_task_id="cloud-1", output_url="https://out/a.mp4")
        self.assertEqual(context.resume_items()["D:/in/a.mp4"]["output_url"], "https://out/a.mp4")

    def test_cross_day_cloud_submission_requires_a_new_plan(self):
        self.center.create_plan("VT-NEXT-DAY", "跨日", [{
            "id": "clear", "request": {
                "operation": "kaipai_process", "input_path": self.video_dir("next-day", 1),
                "task_name": "videoscreenclear",
            }
        }])
        self.center.confirm("VT-NEXT-DAY", 1, start_worker=False)
        with patch("core.video_task_center._today", return_value="2099-01-02"):
            with self.assertRaisesRegex(QuotaExceeded, "跨日执行"):
                self.center.begin_cloud_submission("VT-NEXT-DAY", "clear", "D:/cloud/a.mp4")

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
        board = self.center.list_tasks(reconcile=False)
        self.assertEqual(board["counts"]["in_progress"], 2)
        self.assertEqual(board["counts"]["executing"], 1)
        self.assertEqual(board["counts"]["waiting_cloud"], 1)
        self.assertEqual(board["counts"]["paused"], 1)

    def test_board_counts_all_matching_tasks_even_when_rows_are_limited(self):
        step = [{"id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}}]
        for index in range(3):
            self.center.create_plan(f"VT-{index}", str(index), step)
        board = self.center.list_tasks(limit=1, reconcile=False)
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
        with self.assertRaisesRegex(ValueError, "输入路径不存在"):
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

    def test_nested_inputs_are_counted_and_snapshotted(self):
        source = Path(self.video_dir("nested", 1))
        child = source / "child"
        child.mkdir()
        (child / "b.mov").write_bytes(b"video")
        task = self.center.create_plan("VT-NESTED", "递归输入", [{
            "id": "clear", "request": {
                "operation": "kaipai_process", "input_path": str(source), "task_name": "videoscreenclear"
            }
        }])
        self.assertEqual(task["steps"][0]["item_count"], 2)
        self.center.confirm("VT-NESTED", 1, start_worker=False)
        (child / "b.mov").write_bytes(b"changed")
        result = self.center.execute("VT-NESTED", lambda *_: {"success": True})
        self.assertEqual(result["status"], "failed")
        self.assertIn("输入内容已变化", result["error"])

    def test_upstream_outputs_bind_to_requested_input_key(self):
        self.center.create_plan("VT-CHAIN", "链式处理", [
            {"id": "resize", "item_count": 2, "request": {
                "operation": "video_resize", "input_path": "D:/in", "output_folder": "D:/mid"
            }},
            {"id": "clear", "items_from_step": "resize", "input_key": "input_path", "request": {
                "operation": "kaipai_process", "task_name": "videoscreenclear"
            }},
        ])
        self.center.confirm("VT-CHAIN", 1, start_worker=False)
        calls = []

        def runner(request, _context):
            calls.append(request)
            if request["operation"] == "video_resize":
                return {"success": True, "outputs": ["D:/mid/a.mp4", "D:/mid/b.mp4"]}
            return {"success": True}

        result = self.center.execute("VT-CHAIN", runner)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls[1]["input_path"], r"D:\mid")

    def test_archive_video_members_determine_downstream_cloud_count(self):
        archive = Path(self.temp.name) / "8819视频.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("a.mp4", b"video")
            bundle.writestr("nested/b.mov", b"video")
            bundle.writestr("readme.txt", b"ignored")
        steps = [
            {"id": "organize", "request": {
                "operation": "material_organize", "source_path": str(archive)
            }},
            {"id": "clear", "items_from_step": "organize", "request": {
                "operation": "kaipai_process", "task_name": "videoscreenclear"
            }},
        ]
        task = self.center.create_plan("VT-ARCHIVE", "归档后全消", steps)
        self.assertEqual(task["quota_estimate"], {"videoscreenclear": 2})

    def test_rar_member_list_is_counted_without_extracting(self):
        archive = Path(self.temp.name) / "8819视频.rar"
        archive.write_bytes(b"rar")
        listing = "Path = a.mp4\nPath = nested\\b.mov\nPath = readme.txt\n"
        with patch("core.material_organizer._find_7zip", return_value="7z.exe"), patch(
            "core.video_task_center.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=listing),
        ) as run:
            task = self.center.create_plan("VT-RAR", "RAR 归档", [{
                "id": "organize", "request": {
                    "operation": "material_organize", "source_path": str(archive)
                }
            }])
        self.assertEqual(task["steps"][0]["item_count"], 2)
        self.assertIn("-slt", run.call_args.args[0])

    def test_validation_failure_marks_task_partial_failed(self):
        self.center.create_plan("VT-INVALID", "校验失败", [{
            "id": "validate", "request": {"operation": "validate", "path": "D:/bad.mp4"}
        }])
        self.center.confirm("VT-INVALID", 1, start_worker=False)
        result = self.center.execute("VT-INVALID", lambda *_: {
            "success": True, "validation": {"valid": False, "errors": ["比例不符"]}
        })
        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(result["steps"][0]["status"], "failed")

    def test_only_one_executor_claims_a_queued_task(self):
        self.center.create_plan("VT-CLAIM", "唯一执行器", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}
        }])
        self.center.confirm("VT-CLAIM", 1, start_worker=False)
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def runner(_request, _context):
            calls.append(1)
            entered.set()
            release.wait(2)
            return {"success": True}

        first = threading.Thread(target=self.center.execute, args=("VT-CLAIM", runner))
        second = threading.Thread(target=self.center.execute, args=("VT-CLAIM", runner))
        first.start()
        self.assertTrue(entered.wait(1))
        second.start()
        second.join(1)
        release.set()
        first.join(2)
        self.assertEqual(len(calls), 1)

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

    def test_reconcile_recovers_known_cloud_ids_but_fences_local_work(self):
        self.center.create_plan("VT-CLOUD-LOST", "云端", [{
            "id": "clear", "request": {
                "operation": "kaipai_process", "input_path": self.video_dir("lost", 1), "task_name": "videoscreenclear"
            }, "item_count": 1,
        }])
        self.center.confirm("VT-CLOUD-LOST", 1, start_worker=False)
        self.center.begin_cloud_submission("VT-CLOUD-LOST", "clear", "D:/cloud/a.mp4")
        self.center.checkpoint_cloud_item("VT-CLOUD-LOST", "clear", "D:/cloud/a.mp4", state="submitted", cloud_task_id="cloud-1")
        self.center.create_plan("VT-LOCAL-LOST", "本地", [{
            "id": "resize", "request": {
                "operation": "video_resize", "input_path": "D:/local", "output_folder": "D:/out"
            }
        }])
        self.center.confirm("VT-LOCAL-LOST", 1, start_worker=False)
        self.mark_worker_lost("VT-CLOUD-LOST")
        self.mark_worker_lost("VT-LOCAL-LOST")
        with patch.object(self.center, "_pid_alive", return_value=False), patch.object(self.center, "_spawn_worker", return_value=123) as spawn:
            recovered = self.center.reconcile_workers()
        self.assertEqual(recovered, ["VT-CLOUD-LOST"])
        spawn.assert_called_once_with("VT-CLOUD-LOST")
        self.assertEqual(self.center.get("VT-LOCAL-LOST")["status"], "stopped_unknown")

    def test_reconcile_fences_cloud_submission_without_task_id(self):
        self.center.create_plan("VT-UNKNOWN-LOST", "未知提交", [{
            "id": "clear", "request": {
                "operation": "kaipai_process", "input_path": self.video_dir("unknown-lost", 1), "task_name": "videoscreenclear"
            }, "item_count": 1,
        }])
        self.center.confirm("VT-UNKNOWN-LOST", 1, start_worker=False)
        self.center.begin_cloud_submission("VT-UNKNOWN-LOST", "clear", "D:/cloud/a.mp4")
        self.mark_worker_lost("VT-UNKNOWN-LOST")
        with patch.object(self.center, "_pid_alive", return_value=False), patch.object(self.center, "_spawn_worker") as spawn:
            self.center.reconcile_workers("VT-UNKNOWN-LOST")
        spawn.assert_not_called()
        task = self.center.get("VT-UNKNOWN-LOST")
        self.assertEqual(task["status"], "stopped_unknown")
        self.assertIn("停止自动重提", task["error"])

    def test_card_binding_survives_session_changes(self):
        self.center.create_plan("VT-CARD", "卡片", [{"id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}}])
        task = self.center.bind_card("VT-CARD", "oc_chat", "om_card")
        self.assertEqual(task["chat_id"], "oc_chat")
        self.assertEqual(task["card_message_id"], "om_card")


if __name__ == "__main__":
    unittest.main()
