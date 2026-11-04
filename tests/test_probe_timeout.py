import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.media_utils import probe_video


class ProbeTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jr-probe-test-")
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "input.mp4"
        self.source.write_bytes(b"synthetic input")
        self.ok = subprocess.CompletedProcess([], 0, json.dumps({
            "streams": [{"codec_type": "video", "width": 1080, "height": 1920}],
            "format": {"duration": "7.0"},
        }), "")

    def test_one_transient_timeout_does_not_fail_valid_input(self):
        with patch("utils.media_utils.subprocess.run", side_effect=[
            subprocess.TimeoutExpired("ffprobe", 10), self.ok,
        ]) as run:
            self.assertEqual(probe_video(str(self.source))["duration"], 7)
        self.assertEqual(run.call_count, 2)
        self.assertEqual([c.kwargs["timeout"] for c in run.call_args_list], [10, 10])

    def test_persistent_timeout_is_bounded_and_public_reason_has_no_path(self):
        with patch("utils.media_utils.subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 10)) as run:
            with self.assertRaisesRegex(RuntimeError, "VIDEO_PROBE_TIMEOUT") as failure:
                probe_video(str(self.source))
        self.assertEqual(run.call_count, 2)
        self.assertNotIn(self.temp.name, str(failure.exception))

    def test_changed_input_is_not_retried(self):
        def changed(*args, **kwargs):
            self.source.write_bytes(b"changed during probe")
            raise subprocess.TimeoutExpired("ffprobe", 10)
        with patch("utils.media_utils.subprocess.run", side_effect=changed) as run:
            with self.assertRaisesRegex(RuntimeError, "VIDEO_PROBE_SOURCE_CHANGED"):
                probe_video(str(self.source))
        self.assertEqual(run.call_count, 1)

    def test_invalid_media_is_not_retried(self):
        with patch("utils.media_utils.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "invalid media")) as run:
            with self.assertRaisesRegex(RuntimeError, "ffprobe failed"):
                probe_video(str(self.source))
        self.assertEqual(run.call_count, 1)

    def test_change_during_second_probe_rejects_its_result(self):
        calls = 0
        def run(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise subprocess.TimeoutExpired("ffprobe", 10)
            self.source.write_bytes(b"replacement input")
            return self.ok
        with patch("utils.media_utils.subprocess.run", side_effect=run):
            with self.assertRaisesRegex(RuntimeError, "VIDEO_PROBE_SOURCE_CHANGED"):
                probe_video(str(self.source))
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
