import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class VideoConcatPipelineTests(unittest.TestCase):
    @patch("core.video_concat_pipeline.shutil.copy2")
    @patch("core.video_concat_pipeline.VideoResizer")
    @patch("core.video_concat_pipeline.probe_video")
    @patch("core.video_concat_pipeline.collect_videos", return_value=["in/a.mp4", "in/b.mp4"])
    def test_only_non_9x16_inputs_are_resized(self, _collect, probe, resizer_cls, copy):
        from core.video_concat_pipeline import normalize_folder_9x16

        probe.side_effect = [
            {"width": 1080, "height": 1920, "display_width": 1080, "display_height": 1920, "rotation": 0},
            {"width": 1920, "height": 1080, "display_width": 1920, "display_height": 1080, "rotation": 0},
        ]

        result = normalize_folder_9x16("in", "out", 6)

        copy.assert_called_once_with("in/a.mp4", os.path.join("out", "a.mp4"))
        resizer_cls.return_value.resize_video.assert_called_once_with(
            "in/b.mp4", os.path.join("out", "b.mp4")
        )
        self.assertEqual(result, {"input_count": 2, "converted": 1, "copied": 1})

    @patch("core.video_concat_pipeline.os.replace")
    @patch("core.video_concat_pipeline.VideoResizer")
    @patch("core.video_concat_pipeline.probe_video")
    def test_only_outputs_with_an_edge_above_2000_are_downscaled(self, probe, resizer_cls, replace):
        from core.video_concat_pipeline import limit_output_resolution

        probe.side_effect = [
            {"display_width": 1080, "display_height": 1920},
            {"display_width": 1125, "display_height": 2000},
            {"display_width": 1440, "display_height": 2560},
        ]

        result = limit_output_resolution([
            "out/1080p.mp4", "out/edge-2000.mp4", "out/over-2000.mp4",
        ])

        self.assertEqual(result["downscaled"], 1)
        self.assertEqual(result["kept"], 2)
        resizer_cls.return_value.resize_video.assert_called_once()
        resized_source, resized_target = resizer_cls.return_value.resize_video.call_args.args
        self.assertEqual(resized_source, "out/over-2000.mp4")
        replace.assert_called_once_with(resized_target, "out/over-2000.mp4")

    @patch("core.video_concat_pipeline.limit_output_resolution")
    @patch("core.video_concat_pipeline.normalize_folder_9x16")
    @patch("core.video_concat_pipeline.VideoConcatenatorEngine")
    def test_template_pipeline_normalizes_both_folders_before_concat(
        self, engine_cls, normalize, limit
    ):
        from core.video_concat_pipeline import run_template_001_concat

        normalize.return_value = {"input_count": 1, "converted": 1, "copied": 0}
        engine_cls.return_value.run.return_value = ["out/result.mp4"]
        limit.return_value = {"downscaled": 0, "kept": 1}

        callback = Mock()
        outputs = run_template_001_concat({
            "folder_a": "a",
            "folder_b": "b",
            "output_folder": "out",
            "blur_strength": 6,
        }, callback)

        self.assertEqual(outputs, ["out/result.mp4"])
        self.assertEqual(normalize.call_count, 2)
        effective = engine_cls.call_args.args[0]
        self.assertNotEqual(effective["folder_a"], "a")
        self.assertNotEqual(effective["folder_b"], "b")
        self.assertEqual(effective["output_folder"], "out")
        limit.assert_called_once()
        self.assertEqual(limit.call_args.args[:2], (["out/result.mp4"], 6))
        self.assertIs(limit.call_args.args[2], callback)


if __name__ == "__main__":
    unittest.main()
