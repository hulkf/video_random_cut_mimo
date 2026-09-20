import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class VideoConcatTemplateTests(unittest.TestCase):
    def test_template_001_declares_required_inputs_and_cover_defaults(self):
        from core.video_concat_templates import get_video_concat_template

        template = get_video_concat_template("001")

        self.assertEqual(template["id"], "001")
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

    def test_video_concat_tab_contains_template_001_secondary_tab(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication
        from gui.video_concat_tab import VideoConcatTab

        app = QApplication.instance() or QApplication([])
        tab = VideoConcatTab()
        try:
            self.assertEqual(tab.template_tabs.count(), 1)
            self.assertTrue(tab.template_tabs.tabText(0).startswith("模板001"))
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


if __name__ == "__main__":
    unittest.main()
