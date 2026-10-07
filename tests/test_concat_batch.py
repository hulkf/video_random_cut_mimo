import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core.concat_batch import concat_workers, ordered_batch
from core.video_concatenator import VideoConcatenatorEngine


class ConcatBatchTests(unittest.TestCase):
    def test_parallel_results_and_callbacks_keep_their_contract(self):
        caller = threading.get_ident()
        second_finished = threading.Event()
        callbacks = []

        def process(index):
            if index == 0:
                self.assertTrue(second_finished.wait(3))
            else:
                second_finished.set()
            return index * 10

        result = ordered_batch(range(2), process, workers=2,
                               callback=lambda *args: callbacks.append((threading.get_ident(), args)))
        self.assertEqual(result, [0, 10])
        self.assertTrue(all(thread == caller for thread, _ in callbacks))
        self.assertEqual(callbacks[-1][1][:2], (2, 2))

    def test_stop_does_not_submit_remaining_files_and_joins_active_workers(self):
        entered = threading.Barrier(3)
        release = threading.Event()
        finished = []
        checks = 0

        def process(index):
            entered.wait(timeout=3)
            release.wait(3)
            finished.append(index)

        def check(*_):
            nonlocal checks
            checks += 1
            if checks == 2:
                entered.wait(timeout=3)
                release.set()
                raise InterruptedError("stop")

        with self.assertRaises(InterruptedError):
            ordered_batch(range(5), process, workers=2, callback=check)
        self.assertEqual(sorted(finished), [0, 1])

    def test_failure_stops_submission(self):
        started = []

        def fail(index):
            started.append(index)
            raise RuntimeError("bad input")

        with self.assertRaisesRegex(RuntimeError, "bad input"):
            ordered_batch(range(5), fail, workers=1)
        self.assertEqual(started, [0])

    def test_cpu_default_and_invalid_worker_count(self):
        with patch("core.concat_batch.os.cpu_count", return_value=22):
            self.assertEqual(concat_workers(), 2)
        with patch("core.concat_batch.os.cpu_count", return_value=4):
            self.assertEqual(concat_workers(), 1)
        for value in [0, 4, True, "2", 1.5]:
            with self.assertRaises(ValueError):
                concat_workers(value)

    def test_pairing_and_order_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = VideoConcatenatorEngine({"folder_a": "a", "folder_b": "b",
                "output_folder": folder, "batch_workers": 2,
                "prepared_videos_a": ["a/1.mp4", "a/2.mp4"],
                "prepared_videos_b": ["b/x.mp4", "b/y.mp4", "b/z.mp4"]})
            pairs = []
            with patch.object(engine, "concat_pair", side_effect=lambda a, b, *args: pairs.append((a, b))):
                outputs = engine.run()
            self.assertEqual([Path(p).name for p in outputs], ["1+x.mp4", "2+y.mp4", "1+z.mp4"])
            self.assertCountEqual(pairs, [("a/1.mp4", "b/x.mp4"), ("a/2.mp4", "b/y.mp4"), ("a/1.mp4", "b/z.mp4")])

    def test_duplicate_output_is_rejected_before_any_encoding(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = VideoConcatenatorEngine({"folder_a": "a", "folder_b": "b",
                "output_folder": folder, "prepared_videos_a": ["a/1.mp4", "a/sub/1.mp4"],
                "prepared_videos_b": ["b/x.mp4"]})
            with patch.object(engine, "concat_pair") as encode:
                with self.assertRaisesRegex(ValueError, "重复"):
                    engine.run()
                encode.assert_not_called()

    def test_resume_skips_valid_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "1+x.mp4"
            output.touch()
            engine = VideoConcatenatorEngine({"folder_a": "a", "folder_b": "b",
                "output_folder": folder, "resume_existing": True,
                "prepared_videos_a": ["a/1.mp4"], "prepared_videos_b": ["b/x.mp4"]})
            with patch.object(engine, "_probe_video", return_value={"duration": 1}), patch.object(engine, "concat_pair") as encode:
                self.assertEqual(engine.run(), [str(output)])
                encode.assert_not_called()

    def test_template_target_is_applied_during_concat(self):
        commands = []
        with tempfile.TemporaryDirectory() as folder:
            engine = VideoConcatenatorEngine({"folder_a": "a", "folder_b": "b",
                "output_folder": folder, "limit_output_edge": 2000})
            with patch.object(engine, "_probe_video", return_value={"width": 1440, "height": 2560, "fps": 30}), \
                 patch("core.video_concatenator.get_video_duration", return_value=1), \
                 patch("core.video_concatenator.run_ffmpeg_with_fallback", side_effect=lambda build, **kw: commands.append(build(("libx264", "ultrafast", ["-crf", "23"])))):
                engine.concat_pair("a", "b", os.path.join(folder, "out.mp4"))
            filters = commands[0][commands[0].index("-filter_complex") + 1]
            self.assertIn("scale=1080:1920", filters)
            self.assertNotIn("scale=1440:2560", filters)

    def test_normalization_reuses_sources_but_rotates_when_required(self):
        from core.video_concat_pipeline import normalize_folder_9x16
        with tempfile.TemporaryDirectory() as folder, \
             patch("core.video_concat_pipeline.collect_videos", return_value=["in/z.mp4", "in/a.mp4"]), \
             patch("core.video_concat_pipeline.probe_video", side_effect=[
                 {"width": 1080, "height": 1920, "rotation": 0},
                 {"width": 1920, "height": 1080, "display_width": 1080, "display_height": 1920, "rotation": 90}]), \
             patch("core.video_concat_pipeline.shutil.copy2") as copy, \
             patch("core.video_concat_pipeline.VideoResizer") as resize:
            summary = normalize_folder_9x16("in", folder, workers=2, reuse_sources=True)
            self.assertEqual(summary["reused"], 1)
            self.assertEqual(summary["converted"], 1)
            self.assertEqual(summary["_videos"], [os.path.join(folder, "a.mp4"), "in/z.mp4"])
            copy.assert_not_called()
            resize.return_value.resize_video.assert_called_once()


if __name__ == "__main__":
    unittest.main()
