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
        self.assertEqual(
            template["required_inputs"],
            ["folder_a", "folder_b", "output_folder"],
        )
        self.assertEqual(template["default_options"]["cover_enabled"], True)
        self.assertEqual(template["default_options"]["cover_source"], "video_b_frame")
        self.assertEqual(template["default_options"]["cover_mode"], "front")
        self.assertEqual(template["default_options"]["cover_duration_min"], 0.2)
        self.assertEqual(template["default_options"]["cover_duration_max"], 0.5)

    def test_template_defaults_can_be_overridden_by_caller(self):
        from core.video_concat_templates import resolve_video_concat_template

        resolved = resolve_video_concat_template(
            "001",
            {"folder_a": "a", "folder_b": "b", "output_folder": "out"},
            {"cover_duration_max": 0.8},
        )

        self.assertEqual(resolved["options"]["cover_source"], "video_b_frame")
        self.assertEqual(resolved["options"]["cover_duration_max"], 0.8)

    def test_unknown_template_is_rejected(self):
        from core.video_concat_templates import resolve_video_concat_template

        with self.assertRaisesRegex(ValueError, "未知的视频拼接模板"):
            resolve_video_concat_template("999", {}, {})

    def test_standard_concat_is_default_tool_and_only_templates_are_tabs(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication
        from gui.video_concat_tab import VideoConcatTab

        app = QApplication.instance() or QApplication([])
        with patch(
            "gui.video_concat_tab.get_config",
            side_effect=lambda _section, _key, default="": default,
        ), patch("gui.video_concat_tab.set_config"):
            tab = VideoConcatTab()
        try:
            self.assertEqual(tab.template_tabs.count(), 1)
            self.assertEqual(tab.template_tabs.tabText(0), "模板001")
            self.assertEqual(tab.content_stack.count(), 2)
            self.assertIs(tab.content_stack.currentWidget(), tab.standard_page)
            self.assertFalse(tab.template_active)
            self.assertEqual(tab.standard_page.config_section, "video_concat")
            self.assertEqual(
                tab.template_001_page.config_section,
                "video_concat_template_001",
            )
            self.assertFalse(tab.standard_page.cover_check.isChecked())
            self.assertEqual(tab.standard_page.cover_source_combo.currentData(), "folder")
            self.assertEqual(tab.standard_page.cover_duration_min.value(), 0.5)
            self.assertEqual(tab.standard_page.cover_duration_max.value(), 1.0)
            self.assertTrue(tab.template_001_page.cover_check.isChecked())
            self.assertEqual(
                tab.template_001_page.cover_source_combo.currentData(),
                "video_b_frame",
            )
            self.assertEqual(tab.template_001_page.cover_duration_min.value(), 0.2)
            self.assertEqual(tab.template_001_page.cover_duration_max.value(), 0.5)

            tab.template_tabs.tabBarClicked.emit(0)
            app.processEvents()
            self.assertIs(tab.content_stack.currentWidget(), tab.template_001_page)
            self.assertTrue(tab.template_active)
            self.assertTrue(tab.back_to_standard_btn.isVisibleTo(tab))
            tab.show_standard_page()
            app.processEvents()
            self.assertIs(tab.content_stack.currentWidget(), tab.standard_page)
            self.assertFalse(tab.template_active)
            tab.resize(1000, 700)
            tab.show()
            app.processEvents()
            self.assertTrue(tab.template_tabs.isVisible())
            tab.hide()
            app.processEvents()
            self.assertFalse(tab.template_tabs.isVisible())
        finally:
            tab.close()
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


if __name__ == "__main__":
    unittest.main()
