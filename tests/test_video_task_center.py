import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import closing
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

    def test_confirm_rejects_a_stale_card_before_starting_worker(self):
        step = [{"id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}}]
        self.center.create_plan(
            "VT-CARD-CONFIRM", "确认卡", step,
            chat_id="oc_current", card_message_id="om_current",
        )
        with self.assertRaisesRegex(ValueError, "最新任务卡"):
            self.center.confirm(
                "VT-CARD-CONFIRM", 1, start_worker=False,
                expected_chat_id="oc_current",
                expected_card_message_id="om_stale",
            )
        self.assertEqual(
            self.center.get("VT-CARD-CONFIRM")["status"],
            "awaiting_confirmation",
        )

    def test_control_rejects_a_card_from_another_chat(self):
        step = [{"id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}}]
        self.center.create_plan(
            "VT-CARD-CONTROL", "控制卡", step,
            chat_id="oc_current", card_message_id="om_current",
        )
        self.center.confirm("VT-CARD-CONTROL", 1, start_worker=False)
        with self.assertRaisesRegex(ValueError, "当前会话"):
            self.center.control(
                "VT-CARD-CONTROL", "pause", start_worker=False,
                expected_chat_id="oc_other",
                expected_card_message_id="om_current",
            )
        self.assertEqual(self.center.get("VT-CARD-CONTROL")["status"], "queued")

    def test_resume_rejects_a_stale_card_before_waiting_for_worker(self):
        step = [{"id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}}]
        self.center.create_plan(
            "VT-CARD-RESUME", "继续卡", step,
            chat_id="oc_current", card_message_id="om_current",
        )
        self.center.confirm("VT-CARD-RESUME", 1, start_worker=False)
        self.center.control("VT-CARD-RESUME", "pause", start_worker=False)
        with patch.object(self.center, "_wait_previous_worker_exit") as wait:
            with self.assertRaisesRegex(ValueError, "最新任务卡"):
                self.center.control(
                    "VT-CARD-RESUME", "resume",
                    expected_chat_id="oc_current",
                    expected_card_message_id="om_stale",
                )
        wait.assert_not_called()

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

    def test_resume_wait_failure_rolls_task_back_to_paused(self):
        step = [{"id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}}]
        self.center.create_plan("VT-RESUME", "继续失败", step)
        self.center.confirm("VT-RESUME", 1, start_worker=False)
        self.center.control("VT-RESUME", "pause", start_worker=False)
        with patch.object(self.center, "_wait_previous_worker_exit", side_effect=RuntimeError("still alive")):
            with self.assertRaisesRegex(RuntimeError, "still alive"):
                self.center.control("VT-RESUME", "resume")
        self.assertEqual(self.center.get("VT-RESUME")["status"], "paused")

    def test_resume_does_not_publish_queued_until_previous_worker_exits(self):
        step = [{"id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}}]
        self.center.create_plan("VT-RESUME-ORDER", "继续顺序", step)
        self.center.confirm("VT-RESUME-ORDER", 1, start_worker=False)
        self.center.control("VT-RESUME-ORDER", "pause", start_worker=False)
        observed = []

        def wait(_pid, **_kwargs):
            observed.append(self.center.get("VT-RESUME-ORDER")["status"])

        with patch.object(self.center, "_wait_previous_worker_exit", side_effect=wait), \
                patch.object(self.center, "_spawn_worker", return_value=123):
            self.center.control("VT-RESUME-ORDER", "resume")
        self.assertEqual(observed, ["paused"])
        self.assertEqual(self.center.get("VT-RESUME-ORDER")["status"], "queued")

    def test_resume_ignores_a_reused_pid_that_is_not_the_recorded_worker(self):
        step = [{"id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}}]
        self.center.create_plan("VT-RESUME-PID", "继续PID复用", step)
        self.center.confirm("VT-RESUME-PID", 1, start_worker=False)
        self.center.control("VT-RESUME-PID", "pause", start_worker=False)
        with closing(self.center._connect()) as db:
            db.execute(
                "UPDATE tasks SET worker_pid=?,worker_token=? WHERE task_id=?",
                (12345, "old-token", "VT-RESUME-PID"),
            )
            db.commit()
        with patch.object(self.center, "_pid_alive", return_value=True), \
                patch.object(self.center, "_worker_process_alive", return_value=False), \
                patch.object(self.center, "_spawn_worker", return_value=54321):
            result = self.center.control("VT-RESUME-PID", "resume")
        self.assertEqual(result["status"], "queued")

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

    def test_watchable_list_filters_historical_idle_tasks_in_sql(self):
        self.center.create_plan("VT-IDLE", "等待确认但没有待发卡", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}
        }])
        queued = self.center.create_plan("VT-ACTIVE", "活动任务", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/b.mp4"}
        }])
        self.center.confirm(queued["task_id"], 1, start_worker=False)
        pending = self.center.create_plan("VT-PENDING-CARD", "待补发卡", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/c.mp4"}
        }])
        self.center.bind_card(
            pending["task_id"], "oc_chat", "", pending_kind="plan",
            pending_revision=1, pending_uuid="uuid-plan", pending_card_json="{}",
            pending_mode="send",
        )
        board = self.center.list_tasks(reconcile=False, watchable_only=True)
        self.assertEqual({item["task_id"] for item in board["tasks"]}, {"VT-ACTIVE", "VT-PENDING-CARD"})
        self.assertEqual(board["total"], 2)

    def test_task_list_indexes_are_installed(self):
        with closing(self.center._connect()) as db:
            names = {
                str(row[0]) for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='tasks'"
                ).fetchall()
            }
        self.assertTrue({
            "idx_tasks_updated_at", "idx_tasks_status_updated_at", "idx_tasks_cargo_updated_at",
        }.issubset(names))

    def test_board_reads_page_details_without_per_task_connections(self):
        step = [{"id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}}]
        for index in range(5):
            task_id = f"VT-BATCH-{index}"
            self.center.create_plan(task_id, task_id, step)
            self.center.confirm(task_id, 1, start_worker=False)
            self.center._set_task_status(task_id, "running", current_step=0)
        with patch.object(self.center, "_connect", wraps=self.center._connect) as connect:
            board = self.center.list_tasks(reconcile=False)
        self.assertEqual(board["shown"], 5)
        self.assertLessEqual(connect.call_count, 2)

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

    def test_reconcile_starts_a_queued_worker_that_was_never_spawned(self):
        self.center.create_plan("VT-QUEUED", "待启动", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}
        }])
        self.center.confirm("VT-QUEUED", 1, start_worker=False)
        with patch.object(self.center, "_spawn_worker", return_value=123) as spawn:
            recovered = self.center.reconcile_workers("VT-QUEUED")
        self.assertEqual(recovered, ["VT-QUEUED"])
        spawn.assert_called_once_with("VT-QUEUED")

    def test_worker_spawn_failure_releases_unsubmitted_cloud_quota(self):
        self.center.create_plan("VT-SPAWN-FAIL", "启动失败", [{
            "id": "clear", "request": {
                "operation": "kaipai_process",
                "input_path": self.video_dir("spawn-fail", 2),
                "task_name": "videoscreenclear",
            },
        }])
        with patch("core.video_task_center.subprocess.Popen", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self.center.confirm("VT-SPAWN-FAIL", 1)
        self.assertEqual(self.center.get("VT-SPAWN-FAIL")["status"], "failed")
        self.assertEqual(self.center.quota_summary()["videoscreenclear"]["committed"], 0)

    def test_unknown_cloud_submission_keeps_one_quota_committed(self):
        self.center.create_plan("VT-UNKNOWN-QUOTA", "未知提交", [{
            "id": "clear", "request": {
                "operation": "kaipai_process",
                "input_path": self.video_dir("unknown-quota", 1),
                "task_name": "videoscreenclear",
            },
        }])
        self.center.confirm("VT-UNKNOWN-QUOTA", 1, start_worker=False)
        self.center.begin_cloud_submission("VT-UNKNOWN-QUOTA", "clear", "D:/in/a.mp4")
        self.center.checkpoint_cloud_item(
            "VT-UNKNOWN-QUOTA", "clear", "D:/in/a.mp4",
            state="submission_unknown", error="timeout",
        )
        self.center._release_unsubmitted_quota("VT-UNKNOWN-QUOTA", final=True)
        quota = self.center.quota_summary()["videoscreenclear"]
        self.assertEqual(quota["committed"], 1)
        self.assertEqual(quota["available"], 49)

    def test_late_cloud_callback_cannot_revive_cancelled_task(self):
        self.center.create_plan("VT-LATE-CLOUD", "迟到回调", [{
            "id": "clear", "request": {
                "operation": "kaipai_process", "input_path": self.video_dir("late-cloud", 1),
                "task_name": "videoscreenclear",
            }, "item_count": 1,
        }])
        self.center.confirm("VT-LATE-CLOUD", 1, start_worker=False)
        self.center._set_task_status("VT-LATE-CLOUD", "running")
        self.center.begin_cloud_submission("VT-LATE-CLOUD", "clear", "D:/cloud/a.mp4")
        self.center._set_task_status("VT-LATE-CLOUD", "cancelled")
        self.center.checkpoint_cloud_item(
            "VT-LATE-CLOUD", "clear", "D:/cloud/a.mp4",
            state="submitted", cloud_task_id="cloud-late",
        )
        self.assertEqual(self.center.get("VT-LATE-CLOUD")["status"], "cancelled")

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

    def test_reconcile_does_not_trust_a_reused_pid_without_worker_identity(self):
        self.center.create_plan("VT-PID-REUSED", "PID复用", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}
        }])
        self.center.confirm("VT-PID-REUSED", 1, start_worker=False)
        self.mark_worker_lost("VT-PID-REUSED")
        with patch.object(self.center, "_worker_process_alive", return_value=False), \
                patch.object(self.center, "_spawn_worker") as spawn:
            self.center.reconcile_workers("VT-PID-REUSED")
        spawn.assert_not_called()
        self.assertEqual(self.center.get("VT-PID-REUSED")["status"], "stopped_unknown")

    def test_card_binding_survives_session_changes(self):
        self.center.create_plan("VT-CARD", "卡片", [{"id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}}])
        before = self.center.get("VT-CARD")["updated_at"]
        task = self.center.bind_card(
            "VT-CARD", "oc_chat", "om_card", delivered_updated_at=before,
        )
        self.assertEqual(task["chat_id"], "oc_chat")
        self.assertEqual(task["card_message_id"], "om_card")
        self.assertEqual(task["card_delivered_updated_at"], before)
        self.assertEqual(task["updated_at"], before)

    def test_state_revision_changes_even_when_wall_clock_does_not(self):
        fixed = "2026-09-10T12:00:00.000000+08:00"
        with patch("core.video_task_center._now", return_value=fixed):
            created = self.center.create_plan("VT-REVISION", "快速任务", [{
                "id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}
            }])
            self.center.bind_card(
                "VT-REVISION", "oc_chat", "om_plan",
                delivered_updated_at=fixed, delivered_revision=created["state_revision"],
            )
            confirmed = self.center.confirm("VT-REVISION", 1, start_worker=False)
        self.assertEqual(confirmed["updated_at"], fixed)
        self.assertGreater(confirmed["state_revision"], confirmed["card_delivered_revision"])

    def test_card_delivery_ack_cannot_move_revision_or_message_backward(self):
        task = self.center.create_plan("VT-CARD-CAS", "卡片并发", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}
        }])
        self.center._set_task_status(task["task_id"], "queued")
        self.center._set_task_status(task["task_id"], "running")
        self.center.bind_card(
            task["task_id"], "oc_chat", "om_new",
            delivered_updated_at="new", delivered_revision=3,
        )
        stale = self.center.bind_card(
            task["task_id"], "oc_chat", "om_old",
            delivered_updated_at="old", delivered_revision=2,
        )
        self.assertEqual(stale["card_message_id"], "om_new")
        self.assertEqual(stale["card_delivered_revision"], 3)
        self.assertEqual(stale["card_delivered_updated_at"], "new")

    def test_pending_card_claim_preserves_older_unknown_delivery_until_ack(self):
        task = self.center.create_plan("VT-OUTBOX-CAS", "待发送卡片", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}
        }])
        self.center.confirm(task["task_id"], 1, start_worker=False)
        first = self.center.bind_card(
            task["task_id"], "oc_chat", "om_parent",
            pending_kind="confirm", pending_revision=2, pending_uuid="uuid-2",
            pending_card_json='{"revision":2}', pending_updated_at="time-2",
        )
        self.assertEqual(first["pending_card_uuid"], "uuid-2")
        self.center._set_task_status(task["task_id"], "running")
        blocked = self.center.bind_card(
            task["task_id"], "oc_chat", "om_parent",
            pending_kind="progress", pending_revision=3, pending_uuid="uuid-3",
            pending_card_json='{"revision":3}', pending_updated_at="time-3",
        )
        self.assertEqual(blocked["pending_card_uuid"], "uuid-2")
        acknowledged = self.center.bind_card(
            task["task_id"], "oc_chat", "om_reply",
            delivered_revision=2, delivered_updated_at="time-2", ack_pending_revision=2,
            ack_pending_uuid="uuid-2",
        )
        self.assertEqual(acknowledged["pending_card_kind"], "")

    def test_initial_send_outbox_persists_chat_for_gateway_recovery(self):
        task = self.center.create_plan("VT-FIRST-CARD", "首次卡片", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}
        }])
        pending = self.center.bind_card(
            task["task_id"], "oc_recovery", "",
            pending_kind="plan", pending_revision=1, pending_uuid="uuid-plan-1",
            pending_card_json='{"schema":"2.0"}', pending_updated_at="time-1",
            pending_mode="send",
        )
        self.assertEqual(pending["chat_id"], "oc_recovery")
        self.assertEqual(pending["pending_card_mode"], "send")
        self.assertEqual(pending["card_message_id"], "")

    def test_task_list_omits_large_pending_card_payload_but_status_keeps_it(self):
        task = self.center.create_plan("VT-OUTBOX-PAYLOAD", "待发送负载", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}
        }])
        self.center.bind_card(
            task["task_id"], "oc_chat", "",
            pending_kind="plan", pending_revision=1, pending_uuid="uuid-plan",
            pending_card_json='{"large":"payload"}', pending_updated_at="time-1",
            pending_mode="send",
        )
        listed = self.center.list_tasks(reconcile=False)["tasks"][0]
        self.assertEqual(listed["pending_card_json"], "")
        self.assertEqual(self.center.get(task["task_id"])["pending_card_json"], '{"large":"payload"}')

    def test_unmatched_newer_delivery_cannot_clear_an_older_pending_card(self):
        task = self.center.create_plan("VT-OUTBOX-SUPERSEDE", "新卡超越旧卡", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}
        }])
        self.center.confirm(task["task_id"], 1, start_worker=False)
        self.center.bind_card(
            task["task_id"], "oc_chat", "om_parent",
            pending_kind="progress", pending_revision=2, pending_uuid="uuid-2",
            pending_card_json='{"revision":2}', pending_updated_at="time-2",
        )
        self.center._set_task_status(task["task_id"], "running")
        current = self.center.bind_card(
            task["task_id"], "oc_chat", "om_new",
            delivered_revision=3, delivered_updated_at="time-3", ack_pending_revision=3,
            ack_pending_uuid="uuid-3",
        )
        self.assertEqual(current["card_delivered_revision"], 0)
        self.assertNotEqual(current["card_message_id"], "om_new")
        self.assertEqual(current["pending_card_uuid"], "uuid-2")

    def test_same_revision_wrong_uuid_cannot_ack_or_rebind_card(self):
        task = self.center.create_plan("VT-OUTBOX-UUID", "同版本乱序回执", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}
        }])
        self.center.confirm(task["task_id"], 1, start_worker=False)
        self.center.bind_card(
            task["task_id"], "oc_chat", "om_parent",
            pending_kind="progress", pending_revision=2, pending_uuid="uuid-good",
            pending_card_json='{"revision":2}', pending_updated_at="time-2",
        )
        rejected = self.center.bind_card(
            task["task_id"], "oc_chat", "om-wrong", delivered_revision=2,
            delivered_updated_at="wrong", ack_pending_revision=2, ack_pending_uuid="uuid-wrong",
        )
        self.assertEqual(rejected["card_delivered_revision"], 0)
        self.assertEqual(rejected["pending_card_uuid"], "uuid-good")
        no_ack = self.center.bind_card(
            task["task_id"], "oc_chat", "om-no-ack", delivered_revision=2,
            delivered_updated_at="wrong",
        )
        self.assertEqual(no_ack["card_delivered_revision"], 0)
        self.assertEqual(no_ack["pending_card_uuid"], "uuid-good")

    def test_card_delivery_cannot_claim_a_future_task_revision(self):
        task = self.center.create_plan("VT-FUTURE-CARD", "超前卡片", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}
        }])
        with self.assertRaisesRegex(ValueError, "不能超前"):
            self.center.bind_card(
                task["task_id"], "oc_chat", "om_future", delivered_revision=2,
            )
        with self.assertRaisesRegex(ValueError, "已存在的任务状态版本"):
            self.center.bind_card(
                task["task_id"], "oc_chat", "", pending_kind="plan",
                pending_revision=2, pending_uuid="uuid-future", pending_card_json="{}",
                pending_mode="send",
            )

    def test_agent_cloud_plan_requires_a_download_step(self):
        process = {
            "id": "clear", "request": {
                "operation": "kaipai_process", "input_path": self.video_dir("delivery", 1),
                "task_name": "videoscreenclear",
            },
        }
        with self.assertRaisesRegex(ValueError, "kaipai_download"):
            self.center.create_plan(
                "VT-REMOTE-ONLY", "不能只交付云端地址", [process],
                parameter_lines=["处理：智能全消"],
            )
        accepted = self.center.create_plan(
            "VT-LOCAL-RESULT", "下载结果", [
                process,
                {"id": "download", "items_from_step": "clear", "request": {
                    "operation": "kaipai_download", "output_folder": str(Path(self.temp.name) / "out")
                }},
            ],
            parameter_lines=["处理：智能全消后下载"],
        )
        self.assertEqual(len(accepted["steps"]), 2)

    def test_runner_exception_marks_current_step_failed(self):
        self.center.create_plan("VT-STEP-FAIL", "步骤失败", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}
        }])
        self.center.confirm("VT-STEP-FAIL", 1, start_worker=False)

        def fail(_request, _context):
            raise RuntimeError("runner boom")

        result = self.center.execute("VT-STEP-FAIL", fail)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["current_step"]["status"], "failed")
        self.assertIn("runner boom", result["current_step"]["error"])

    def test_repeated_identical_status_does_not_create_revision_churn(self):
        task = self.center.create_plan("VT-IDEMPOTENT-STATUS", "稳定状态", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}
        }])
        before = task["state_revision"]
        self.center._set_task_status(task["task_id"], "awaiting_confirmation")
        after = self.center.get(task["task_id"])["state_revision"]
        self.assertEqual(after, before)

    def test_delivery_tracking_migration_does_not_replay_historical_terminal_cards(self):
        self.center.create_plan("VT-LEGACY-CARD", "历史任务", [{
            "id": "one", "request": {"operation": "validate", "path": "D:/a.mp4"}
        }])
        with closing(self.center._connect()) as db:
            db.execute(
                "UPDATE tasks SET status='completed',chat_id='oc_chat',card_message_id='om_card',"
                "card_delivered_updated_at='' WHERE task_id='VT-LEGACY-CARD'"
            )
            expected = db.execute(
                "SELECT updated_at FROM tasks WHERE task_id='VT-LEGACY-CARD'"
            ).fetchone()["updated_at"]
            db.execute("PRAGMA user_version=0")
            db.commit()

        migrated = TaskCenter(self.center.db_path).get("VT-LEGACY-CARD")
        self.assertEqual(migrated["card_delivered_updated_at"], expected)

    def test_reconcile_restarts_an_interrupted_download_step(self):
        output = str(Path(self.temp.name) / "downloads")
        self.center.create_plan("VT-DOWNLOAD-LOST", "下载恢复", [{
            "id": "download", "request": {
                "operation": "kaipai_download",
                "items": [{"url": "https://out/a.mp4", "filename": "a.mp4"}],
                "output_folder": output,
            },
        }])
        self.center.confirm("VT-DOWNLOAD-LOST", 1, start_worker=False)
        self.mark_worker_lost("VT-DOWNLOAD-LOST")
        with patch.object(self.center, "_worker_process_alive", return_value=False), \
                patch.object(self.center, "_spawn_worker", return_value=123) as spawn:
            recovered = self.center.reconcile_workers("VT-DOWNLOAD-LOST")
        self.assertEqual(recovered, ["VT-DOWNLOAD-LOST"])
        spawn.assert_called_once_with("VT-DOWNLOAD-LOST")

    def test_reconcile_fails_closed_when_worker_identity_is_unknown(self):
        self.center.create_plan("VT-WORKER-UNKNOWN", "身份未知", [{
            "id": "download", "request": {
                "operation": "kaipai_download",
                "items": [{"url": "https://out/a.mp4", "filename": "a.mp4"}],
                "output_folder": str(Path(self.temp.name) / "unknown"),
            },
        }])
        self.center.confirm("VT-WORKER-UNKNOWN", 1, start_worker=False)
        self.mark_worker_lost("VT-WORKER-UNKNOWN")
        with patch.object(self.center, "_worker_process_state", return_value="unknown"), \
                patch.object(self.center, "_spawn_worker") as spawn:
            recovered = self.center.reconcile_workers("VT-WORKER-UNKNOWN")
        self.assertEqual(recovered, [])
        spawn.assert_not_called()
        task = self.center.get("VT-WORKER-UNKNOWN")
        self.assertEqual(task["status"], "stopped_unknown")
        self.assertIn("重复执行", task["error"])


if __name__ == "__main__":
    unittest.main()
