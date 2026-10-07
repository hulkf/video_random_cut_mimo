import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch, Mock

from core.concat_batch import bounded_results, ordered_batch, reserve_output
from core.ffmpeg_runner import _budgeted_command, ffmpeg_thread_budget


class BatchPerformanceTests(unittest.TestCase):
    def test_bounded_submission_stops_without_consuming_remaining_inputs(self):
        consumed = []
        def items():
            for i in range(100):
                consumed.append(i)
                yield i
        stop = threading.Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = bounded_results(pool, lambda n: n, items(), workers=2,
                                      should_stop=stop.is_set)
            next(results)
            stop.set()
            list(results)
        self.assertEqual(consumed, [0, 1])

    def test_zero_workers_fails_instead_of_spinning(self):
        with ThreadPoolExecutor() as pool:
            with self.assertRaises(ValueError):
                list(bounded_results(pool, str, [1], workers=0))
        with self.assertRaises(ValueError):
            ordered_batch([1], str, workers=0)

    def test_output_names_reserved_before_files_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'same.mp4')
            reserved = set()
            self.assertEqual(reserve_output(path, reserved), path)
            self.assertEqual(reserve_output(path, reserved), str(Path(temp) / 'same_2.mp4'))
            Path(temp, 'same_3.mp4').touch()
            self.assertEqual(reserve_output(path, reserved), str(Path(temp) / 'same_4.mp4'))

    def test_ffmpeg_budget_covers_decoders_and_encoder_without_mutation(self):
        command = ['ffmpeg', '-i', 'a.mp4', '-i', 'b.mp4', '-y', 'out.mp4']
        with ffmpeg_thread_budget(4):
            result = _budgeted_command(command)
        self.assertEqual(result.count('-threads'), 3)
        self.assertIn('-filter_complex_threads', result)
        self.assertNotIn('-threads', command)
        self.assertEqual(_budgeted_command(command), command)

    def test_explicit_threads_preserved_and_context_isolated(self):
        command = ['ffmpeg', '-i', 'a', '-threads', '7', 'b']
        with ffmpeg_thread_budget(2):
            self.assertEqual(_budgeted_command(command).count('-threads'), 1)
            self.assertIn('7', _budgeted_command(command))
        results = ordered_batch([1, 2], lambda _: _budgeted_command(['ffmpeg', '-i', 'a', 'b']),
                                workers=2, ffmpeg_threads=3)
        self.assertTrue(all(command.count('-threads') == 2 for command in results))
        self.assertEqual(_budgeted_command(['ffmpeg', 'x']), ['ffmpeg', 'x'])

    def test_completed_result_notifications_run_on_caller(self):
        caller = threading.get_ident()
        seen = []
        self.assertEqual(ordered_batch([1, 2], str, workers=2,
            on_result=lambda value: seen.append((threading.get_ident(), value))), ['1', '2'])
        self.assertEqual({thread for thread, _ in seen}, {caller})

    def test_audio_mix_exhausts_overlong_candidates(self):
        from core.mixer import VideoMixer
        engine = VideoMixer()
        with patch('core.mixer.collect_files', return_value=['long.mp4']), \
             patch.object(engine, 'get_duration', side_effect=[1, 20]), \
             patch('core.mixer.random.choice', side_effect=[0, AssertionError('loop')]):
            with self.assertRaisesRegex(ValueError, 'No clips selected'):
                engine.mix_videos('clips', 'audio.wav', 'out.mp4')

    def test_audio_mix_passes_original_clips_to_video_only_concat(self):
        from core.mixer import VideoMixer
        engine = VideoMixer()
        with tempfile.TemporaryDirectory() as temp, \
             patch('core.mixer.collect_files', return_value=['clip.mp4']), \
             patch.object(engine, 'get_duration', return_value=1), \
             patch('core.mixer.concat_videos') as concat, \
             patch('core.mixer.add_audio_with_silence'), patch('core.mixer.shutil.move'):
            engine.mix_videos('clips', 'audio.wav', os.path.join(temp, 'out.mp4'))
        self.assertEqual(concat.call_args.args[0], ['clip.mp4'])

    def test_gap_rejects_zero_and_nan_duration_without_cutting(self):
        from core.video_mixer import VideoMixerEngine
        engine = object.__new__(VideoMixerEngine)
        engine._clip_duration_cache = {'zero': 0, 'nan': float('nan')}
        with patch('core.video_mixer.cut_video_no_audio') as cut:
            with self.assertRaises(ValueError):
                engine.fill_gap(5, ['zero', 'nan'], 'temp', 0)
            cut.assert_not_called()

    def test_gap_ignores_invalid_candidate_and_finishes(self):
        from core.video_mixer import VideoMixerEngine
        engine = object.__new__(VideoMixerEngine)
        engine._clip_duration_cache = {'zero': 0, 'good': 2}
        with patch('core.video_mixer.cut_video_no_audio') as cut:
            result = engine.fill_gap(5, ['zero', 'good'], 'temp', 0)
        self.assertEqual(sum(item['duration'] for item in result), 5)
        self.assertEqual(cut.call_count, 3)

    def test_resize_single_file_and_same_stem_are_safe(self):
        from core.video_resizer import VideoResizer
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp, 'in'); source.mkdir()
            for name in ['same.mp4', 'same.mov']:
                (source / name).touch()
            engine = VideoResizer()
            with patch.object(engine, 'resize_video'):
                results = engine.resize_folder(str(source), str(Path(temp, 'out')))
                single = engine.resize_folder(str(source / 'same.mp4'), str(Path(temp, 'single')))
            self.assertEqual(len({item['output'] for item in results}), 2)
            self.assertEqual(Path(single[0]['output']).name, 'same_9x16.mp4')

    def test_slice_rejects_non_progressing_duration_and_colliding_names(self):
        from core.slicer import VideoSlicer
        for minimum, maximum in [(0, 0), (-1, 2), (2, 1), (1, float('inf'))]:
            with self.assertRaises(ValueError):
                VideoSlicer(minimum, maximum)
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, 'same.mp4').touch(); Path(temp, 'same.mov').touch()
            engine = VideoSlicer()
            with patch.object(engine, 'slice_video') as cut:
                with self.assertRaises(ValueError):
                    engine.slice_folder(temp, str(Path(temp, 'out')))
                cut.assert_not_called()

    def test_screenshots_do_not_overwrite_existing_images(self):
        from core.screenshot import extract_random_frames
        with tempfile.TemporaryDirectory() as temp:
            original = Path(temp, 'video_frame_0000.jpg')
            original.write_bytes(b'keep')
            def extract(command, **kwargs):
                Path(kwargs['output_path']).write_bytes(b'new')
            with patch('core.screenshot.get_video_duration', return_value=2), \
                 patch('core.screenshot.run_ffmpeg', side_effect=extract):
                result = extract_random_frames('video.mp4', temp, 2)
            self.assertEqual(len(result), 2)
            self.assertNotIn(str(original), result)
            self.assertEqual(original.read_bytes(), b'keep')

    def test_cloud_stop_prevents_dispatching_rest_of_batch(self):
        from gui.kaipai_cloud_tab import KaipaiWorker
        worker = KaipaiWorker(['a.mp4', 'b.mp4', 'c.mp4'], '视频智能全消', max_workers=1)
        def finish_first(*args):
            worker.stop()
            return {'file': 'a.mp4', 'status': '成功'}
        with patch.object(worker, '_process_one', side_effect=finish_first) as process:
            worker._run_batch(Mock(), 3)
        self.assertEqual(process.call_count, 1)

    def test_ocr_repeated_runs_use_distinct_frame_directories(self):
        from core.text_detector import detect_single_video
        with tempfile.TemporaryDirectory() as temp, \
             patch('core.text_detector._ocr_instance', Mock()), \
             patch('core.text_detector.run_ffmpeg') as run:
            path = str(Path(temp, 'video.mp4'))
            a = detect_single_video((path, 1, .3))
            b = detect_single_video((path, 1, .3))
            self.assertNotEqual(a[2], b[2])
            self.assertIn('-y', run.call_args.args[0])

    def test_fission_reserves_names_across_overlapping_sources(self):
        from core.video_fission import VideoFission
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp, 'in'); source.mkdir()
            video = source / 'same.mp4'; video.touch()
            engine = VideoFission()
            callbacks = []
            caller = threading.get_ident()
            with patch.object(engine, 'fission_one'):
                results = engine.fission_folder([(str(source), 2), (str(video), 3)],
                    str(Path(temp, 'out')), max_workers=2,
                    callback=lambda *_: callbacks.append(threading.get_ident()))
            outputs = [path for result in results for path in result['outputs']]
            self.assertEqual(len(outputs), 5)
            self.assertEqual(len(set(outputs)), 5)
            self.assertEqual(callbacks, [caller] * 5)

    def test_fission_callback_stop_retains_completed_outputs(self):
        from core.video_fission import VideoFission, FissionStopped
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp, 'video.mp4'); video.touch()
            engine = VideoFission()
            def stop(*args):
                raise FissionStopped('stop')
            with patch.object(engine, 'fission_one') as process:
                with self.assertRaises(FissionStopped):
                    engine.fission_folder([(str(video), 5)], str(Path(temp, 'out')),
                        max_workers=1, callback=stop)
            self.assertEqual(process.call_count, 1)
            self.assertEqual(len(engine.partial_results[0]['outputs']), 1)


if __name__ == '__main__':
    unittest.main()
