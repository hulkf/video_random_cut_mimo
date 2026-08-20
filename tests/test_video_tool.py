import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import video_tool


class VideoToolTests(unittest.TestCase):
    def test_capabilities_are_machine_readable(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "video_tool.py"), "capabilities"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["success"])
        self.assertIn("video_concat", payload["operations"])
        self.assertFalse(payload["constraints"]["direct_ffmpeg_from_agent"])

    def test_missing_request_parameter_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "缺少必要参数"):
            video_tool.run_request({"operation": "video_concat", "inputs": {}})

    def test_health_cli_returns_json(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "video_tool.py"), "health"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertIn(result.returncode, (0, 3))
        payload = json.loads(result.stdout)
        self.assertIn("checks", payload)

    @patch("video_tool._probe", side_effect=[
        {"valid": True, "exists": True, "path": "out.mp4", "width": 1080,
         "height": 1920, "duration": 10.0},
        {"valid": True, "exists": True, "path": "a.mp4", "width": 1080,
         "height": 1920, "duration": 3.0},
        {"valid": True, "exists": True, "path": "b.mp4", "width": 1080,
         "height": 1920, "duration": 3.0},
    ])
    @patch("video_tool.os.makedirs")
    @patch("core.video_concatenator.VideoConcatenatorEngine.run", return_value=["out.mp4"])
    @patch("core.video_concatenator.VideoConcatenatorEngine.__init__", return_value=None)
    def test_concat_is_exposed_as_one_formal_operation(self, _init, run, _makedirs, _probe):
        result = video_tool.run_request({
            "operation": "video_concat",
            "inputs": {"folder_a": "a", "folder_b": "b", "output_folder": "out"},
            "options": {"require_9x16": False, "require_cover": False, "cover_enabled": False},
        })
        self.assertTrue(result["success"])
        self.assertEqual(result["outputs"], ["out.mp4"])
        run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
