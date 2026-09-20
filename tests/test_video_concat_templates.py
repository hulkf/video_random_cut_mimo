import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class VideoConcatTemplateTests(unittest.TestCase):
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

    def test_template_page_starts_direct_worker_without_task_center(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication
        from gui.video_concat_tab import VideoConcatPage, VideoConcatWorker

        app = QApplication.instance() or QApplication([])
        with patch(
            "gui.video_concat_tab.get_config",
            side_effect=lambda _section, _key, default="": default,
        ), patch("gui.video_concat_tab.set_config"), patch.object(
            VideoConcatPage, "start_worker", return_value=True
        ) as start_worker:
            page = VideoConcatPage("video_concat_template_001", template_id="001")
            page.folder_a_input.setText("a")
            page.folder_b_input.setText("b")
            page.output_folder_input.setText("out")
            page.start_concat()
        try:
            start_worker.assert_called_once()
            worker = start_worker.call_args.args[0]
            self.assertIsInstance(worker, VideoConcatWorker)
            self.assertEqual(worker.config["_template_id"], "001")
        finally:
            page.close()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
