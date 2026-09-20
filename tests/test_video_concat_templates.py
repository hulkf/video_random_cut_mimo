import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class VideoConcatTemplateTests(unittest.TestCase):
    def test_template_task_client_creates_and_confirms_one_step_plan(self):
        from gui.video_concat_tab import Template001TaskCenterClient

        runner = Mock(side_effect=[
            {"task": {"task_id": "template001-fixed", "plan_version": 3}},
            {"task": {"task_id": "template001-fixed", "status": "queued"}},
        ])
        client = Template001TaskCenterClient(
            request_runner=runner,
            task_id_factory=lambda: "template001-fixed",
        )

        task = client.submit({
            "folder_a": "a",
            "folder_b": "b",
            "output_folder": "out",
            "cover_enabled": True,
            "cover_source": "video_b_frame",
            "cover_mode": 0,
            "cover_duration_min": 0.2,
            "cover_duration_max": 0.5,
            "_template_id": "001",
        })

        self.assertEqual(task["status"], "queued")
        plan_request, confirm_request = [call.args[0] for call in runner.call_args_list]
        self.assertEqual(plan_request["operation"], "task_center_plan")
        self.assertEqual(plan_request["inputs"]["task_id"], "template001-fixed")
        self.assertEqual(plan_request["inputs"]["title"], "模板001 千川视频合成")
        request = plan_request["inputs"]["steps"][0]["request"]
        self.assertEqual(request["operation"], "video_concat")
        self.assertEqual(request["template_id"], "001")
        self.assertEqual(request["inputs"], {
            "folder_a": "a", "folder_b": "b", "output_folder": "out",
        })
        self.assertNotIn("_template_id", request["options"])
        self.assertEqual(confirm_request["operation"], "task_center_confirm")
        self.assertEqual(confirm_request["inputs"]["plan_version"], 3)
        self.assertEqual(confirm_request["inputs"]["confirmed_by"], "desktop-gui")
        self.assertTrue(confirm_request["authorization"]["confirmed"])

    def test_template_001_declares_required_inputs_and_cover_defaults(self):
        from core.video_concat_templates import get_video_concat_template

        template = get_video_concat_template("001")

        self.assertEqual(template["id"], "001")
        self.assertEqual(template["name"], "模板001")
        self.assertEqual(template["version"], "2")
        self.assertEqual(
            [step["id"] for step in template["steps"]],
            ["normalize_inputs", "video_concat", "limit_output_resolution"],
        )
        self.assertEqual(template["output_count_rule"]["type"], "max_input_count")
        self.assertEqual(template["output_geometry"]["reference_role"], "folder_a")
        self.assertEqual(
            template["required_inputs"],
            ["folder_a", "folder_b", "output_folder"],
        )
        self.assertEqual(template["default_options"]["cover_enabled"], True)
        self.assertEqual(template["default_options"]["cover_source"], "video_b_frame")
        self.assertEqual(template["default_options"]["cover_mode"], "front")
        self.assertEqual(template["default_options"]["cover_duration_min"], 0.2)
        self.assertEqual(template["default_options"]["cover_duration_max"], 0.5)
        self.assertEqual(
            template["output_geometry"]["downscale_if_any_edge_above"],
            2000,
        )
        self.assertEqual(
            template["output_geometry"]["downscale_target"],
            {"width": 1080, "height": 1920},
        )

    def test_template_defaults_can_be_overridden_by_caller(self):
        from core.video_concat_templates import resolve_video_concat_template

        resolved = resolve_video_concat_template(
            "001",
            {"folder_a": "a", "folder_b": "b", "output_folder": "out"},
            {"cover_duration_max": 0.8},
        )

        self.assertEqual(resolved["options"]["cover_source"], "video_b_frame")
        self.assertEqual(resolved["options"]["cover_duration_max"], 0.8)
        self.assertEqual(resolved["template_version"], "2")
        self.assertEqual(resolved["steps"][1]["operation"], "video_concat")
        self.assertEqual(resolved["output_count_rule"]["input_roles"], ["folder_a", "folder_b"])
        self.assertEqual(resolved["output_geometry"]["required_aspect_ratio"], "9:16")

    def test_unknown_template_is_rejected(self):
        from core.video_concat_templates import resolve_video_concat_template

        with self.assertRaisesRegex(ValueError, "未知的视频拼接模板"):
            resolve_video_concat_template("999", {}, {})

    def test_concat_stays_standard_and_template_gets_its_own_top_level_tab(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication
        from gui.video_concat_tab import VideoConcatTab
        from gui.template_tab import TemplateTab
        from gui.tab_registry import TABS

        app = QApplication.instance() or QApplication([])
        with patch(
            "gui.video_concat_tab.get_config",
            side_effect=lambda _section, _key, default="": default,
        ), patch("gui.video_concat_tab.set_config"):
            concat_tab = VideoConcatTab()
            template_tab = TemplateTab()
        try:
            self.assertFalse(hasattr(concat_tab, "template_tabs"))
            self.assertEqual(concat_tab.config_section, "video_concat")
            self.assertFalse(concat_tab.cover_check.isChecked())
            self.assertEqual(concat_tab.cover_source_combo.currentData(), "folder")
            self.assertEqual(concat_tab.cover_duration_min.value(), 0.5)
            self.assertEqual(concat_tab.cover_duration_max.value(), 1.0)

            self.assertEqual(template_tab.template_tabs.count(), 1)
            self.assertEqual(template_tab.template_tabs.tabText(0), "模板001")
            self.assertEqual(
                template_tab.template_001_page.config_section,
                "video_concat_template_001",
            )
            self.assertTrue(template_tab.template_001_page.cover_check.isChecked())
            self.assertEqual(
                template_tab.template_001_page.cover_source_combo.currentData(),
                "video_b_frame",
            )
            self.assertEqual(template_tab.template_001_page.cover_duration_min.value(), 0.2)
            self.assertEqual(template_tab.template_001_page.cover_duration_max.value(), 0.5)
            self.assertIn(
                ("template_tab", "模板"),
                [(attr, title) for attr, title, _factory in TABS],
            )

            template_tab.resize(1000, 700)
            template_tab.show()
            app.processEvents()
            self.assertTrue(template_tab.template_tabs.isVisible())
            template_tab.hide()
            app.processEvents()
            self.assertFalse(template_tab.template_tabs.isVisible())
        finally:
            concat_tab.close()
            template_tab.close()
            app.processEvents()

    def test_nested_concat_page_worker_is_stopped_with_parent_tab(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtCore import QThread
        from PyQt5.QtWidgets import QApplication, QWidget
        from gui.tab_registry import stop_tab_threads

        class StoppableThread(QThread):
            def __init__(self):
                super().__init__()
                self.stop_called = False

            def run(self):
                self.exec_()

            def stop(self):
                self.stop_called = True
                self.quit()

        app = QApplication.instance() or QApplication([])
        parent = QWidget()
        child = QWidget(parent)
        child.worker = StoppableThread()
        child.worker.start()
        try:
            self.assertTrue(child.worker.wait(10) is False)
            stop_tab_threads(parent)
            self.assertTrue(child.worker.stop_called)
            self.assertFalse(child.worker.isRunning())
        finally:
            if child.worker.isRunning():
                child.worker.quit()
                child.worker.wait(1000)
            parent.close()
            app.processEvents()

    @patch("core.video_concat_pipeline.run_template_001_concat", return_value=["out.mp4"])
    @patch("core.video_concatenator.VideoConcatenatorEngine.run")
    def test_template_worker_uses_normalize_concat_limit_pipeline(self, engine_run, pipeline):
        from gui.video_concat_tab import VideoConcatWorker

        worker = VideoConcatWorker({
            "_template_id": "001",
            "folder_a": "a",
            "folder_b": "b",
            "output_folder": "out",
        })
        finished = []
        worker.finished.connect(finished.append)

        worker.run()

        self.assertEqual(finished, [["out.mp4"]])
        engine_run.assert_not_called()
        config, callback = pipeline.call_args.args
        self.assertNotIn("_template_id", config)
        self.assertTrue(callable(callback))

    def test_template_page_submits_to_task_center_instead_of_qthread(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication, QMessageBox
        from gui.video_concat_tab import VideoConcatPage

        app = QApplication.instance() or QApplication([])
        with patch(
            "gui.video_concat_tab.get_config",
            side_effect=lambda _section, _key, default="": default,
        ), patch("gui.video_concat_tab.set_config"), patch.object(
            QMessageBox, "question", return_value=QMessageBox.Yes
        ), patch.object(VideoConcatPage, "start_worker") as start_worker:
            page = VideoConcatPage("video_concat_template_001", template_id="001")
            client = Mock()
            client.submit.return_value = {"task_id": "template001-task", "status": "queued"}
            page._task_center_client = client
            page.folder_a_input.setText("a")
            page.folder_b_input.setText("b")
            page.output_folder_input.setText("out")
            page.start_concat()
        try:
            start_worker.assert_not_called()
            client.submit.assert_called_once()
            self.assertEqual(page.active_task_id, "template001-task")
            self.assertTrue(page.task_poll_timer.isActive())
            self.assertFalse(page.start_btn.isEnabled())
        finally:
            page.task_poll_timer.stop()
            page.close()
            app.processEvents()

    def test_template_page_reads_terminal_result_from_task_center(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication, QMessageBox
        from gui.video_concat_tab import VideoConcatPage

        app = QApplication.instance() or QApplication([])
        with patch(
            "gui.video_concat_tab.get_config",
            side_effect=lambda _section, _key, default="": default,
        ), patch("gui.video_concat_tab.set_config"), patch.object(
            QMessageBox, "information"
        ) as information:
            page = VideoConcatPage("video_concat_template_001", template_id="001")
            client = Mock()
            client.get.return_value = {
                "task_id": "template001-task",
                "status": "completed",
                "output_directories": ["out"],
            }
            page._task_center_client = client
            page.active_task_id = "template001-task"
            page.set_busy(True)
            page.task_poll_timer.start()
            page._poll_task_center()
        try:
            client.get.assert_called_once_with("template001-task")
            self.assertEqual(page.active_task_id, "")
            self.assertFalse(page.task_poll_timer.isActive())
            self.assertTrue(page.start_btn.isEnabled())
            self.assertEqual(page.global_progress_bar.value(), 100)
            information.assert_called_once()
        finally:
            page.close()
            app.processEvents()

    def test_template_page_restores_active_task_tracking_after_restart(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication
        from gui.video_concat_tab import VideoConcatPage

        app = QApplication.instance() or QApplication([])
        client = Mock()
        client.get.return_value = {
            "task_id": "template001-restored",
            "status": "running",
        }

        def config_value(_section, key, default=""):
            return "template001-restored" if key == "active_task_id" else default

        with patch("gui.video_concat_tab.get_config", side_effect=config_value), patch(
            "gui.video_concat_tab.set_config"
        ), patch(
            "gui.video_concat_tab.Template001TaskCenterClient", return_value=client
        ):
            page = VideoConcatPage("video_concat_template_001", template_id="001")
        try:
            client.get.assert_called_once_with("template001-restored")
            self.assertEqual(page.active_task_id, "template001-restored")
            self.assertTrue(page.task_poll_timer.isActive())
            self.assertFalse(page.start_btn.isEnabled())
        finally:
            page.task_poll_timer.stop()
            page.close()
            app.processEvents()

    def test_template_page_keeps_tracking_after_transient_restore_error(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication
        from gui.video_concat_tab import VideoConcatPage

        app = QApplication.instance() or QApplication([])
        client = Mock()
        client.get.side_effect = RuntimeError("database is locked")

        def config_value(_section, key, default=""):
            return "template001-restored" if key == "active_task_id" else default

        with patch("gui.video_concat_tab.get_config", side_effect=config_value), patch(
            "gui.video_concat_tab.set_config"
        ) as set_config_mock, patch(
            "gui.video_concat_tab.Template001TaskCenterClient", return_value=client
        ):
            page = VideoConcatPage("video_concat_template_001", template_id="001")
        try:
            self.assertEqual(page.active_task_id, "template001-restored")
            self.assertTrue(page.task_poll_timer.isActive())
            self.assertFalse(page.start_btn.isEnabled())
            self.assertNotIn(
                ("video_concat_template_001", "active_task_id", ""),
                [call.args for call in set_config_mock.call_args_list],
            )
        finally:
            page.task_poll_timer.stop()
            page.close()
            app.processEvents()

    def test_template_page_delivers_terminal_result_found_after_restart(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication, QMessageBox
        from gui.video_concat_tab import VideoConcatPage

        app = QApplication.instance() or QApplication([])
        client = Mock()
        client.get.return_value = {
            "task_id": "template001-restored",
            "status": "completed",
            "output_directories": ["out"],
        }

        def config_value(_section, key, default=""):
            return "template001-restored" if key == "active_task_id" else default

        with patch("gui.video_concat_tab.get_config", side_effect=config_value), patch(
            "gui.video_concat_tab.set_config"
        ), patch(
            "gui.video_concat_tab.Template001TaskCenterClient", return_value=client
        ), patch.object(QMessageBox, "information") as information:
            page = VideoConcatPage("video_concat_template_001", template_id="001")
            app.processEvents()
        try:
            self.assertEqual(page.active_task_id, "")
            self.assertTrue(page.start_btn.isEnabled())
            information.assert_called_once()
        finally:
            page.close()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
