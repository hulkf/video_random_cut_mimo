import json
import importlib
import os
import subprocess
import sys
import threading
import time
import unittest
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import video_tool
import headless_operations
from gui.kaipai_cloud_tab import KaipaiWorker


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
        self.assertIn("task_control", payload["operations"])
        self.assertIn("task_center_plan", payload["operations"])
        self.assertIn("task_center_confirm", payload["operations"])
        self.assertIn("task_center_list", payload["operations"])
        list_options = payload["operations"]["task_center_list"]["option_schema"]["properties"]
        self.assertEqual(list_options["watchable_only"]["type"], "boolean")
        self.assertEqual(list_options["reconcile"]["type"], "boolean")

    def test_task_center_list_rejects_string_booleans(self):
        with self.assertRaisesRegex(ValueError, "必须是布尔值"):
            video_tool.run_request({
                "operation": "task_center_list",
                "inputs": {"watchable_only": "false"},
            })

    def test_initial_card_outbox_can_be_persisted_before_feishu_returns_message_id(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VIDEO_TASK_CENTER_DB": str(Path(temp) / "tasks.db")}
        ):
            planned = video_tool.run_request({
                "operation": "task_center_plan",
                "inputs": {
                    "task_id": "VT-FIRST-CARD",
                    "title": "首次确认卡",
                    "parameter_lines": ["目标比例：9:16"],
                    "steps": [{
                        "id": "validate",
                        "request": {"operation": "validate", "path": "input.mp4"},
                    }],
                },
            })["task"]

            pending = video_tool.run_request({
                "operation": "task_center_bind_card",
                "inputs": {
                    "task_id": planned["task_id"],
                    "chat_id": "oc_chat",
                    "card_message_id": "",
                    "pending_card_kind": "plan",
                    "pending_card_revision": planned["state_revision"],
                    "pending_card_uuid": "first-card-uuid",
                    "pending_card_json": json.dumps({"schema": "2.0"}),
                    "pending_card_updated_at": planned["updated_at"],
                    "pending_card_mode": "send",
                    "pending_card_target_message_id": "",
                },
            })["task"]

        self.assertEqual(pending["chat_id"], "oc_chat")
        self.assertEqual(pending["card_message_id"], "")
        self.assertEqual(pending["pending_card_kind"], "plan")
        self.assertEqual(pending["pending_card_mode"], "send")

    def test_card_binding_without_initial_send_outbox_still_requires_message_id(self):
        with self.assertRaisesRegex(ValueError, "card_message_id"):
            video_tool.run_request({
                "operation": "task_center_bind_card",
                "inputs": {"task_id": "VT-NO-CARD", "chat_id": "oc_chat"},
            })

    def test_every_business_tab_has_a_headless_operation(self):
        exposed_tabs = {
            spec.get("tab") for spec in video_tool.CAPABILITIES["operations"].values()
            if spec.get("tab")
        }
        self.assertEqual(exposed_tabs, {
            "视频切片", "视频截图", "文字识别", "人脸识别", "音频混剪",
            "视频混剪", "视频拼接", "视频尺寸", "视频优化", "去关键词", "视频字幕",
            "开拍云端", "视频裂变", "音色复刻", "视频下载", "设置",
            "素材归档",
        })

    def test_material_organize_archives_videos_and_requires_authorization(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            archive = temp_path / "8819视频01.zip"
            payload = temp_path / "clip.mp4"
            payload.write_bytes(b"video")
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.write(payload, "nested/clip.mp4")
                bundle.writestr("notes.txt", "ignored")
            request = {
                "operation": "material_organize",
                "inputs": {"source_path": str(archive)},
                "options": {"output_root": str(temp_path / "out")},
            }
            with self.assertRaises(PermissionError):
                video_tool.run_request(request)
            request["authorization"] = {"confirmed": True, "scope": "material_organize"}
            result = video_tool.run_request(request)
            data = result["results"][0]
            self.assertEqual(data["cargo_number"], "8819")
            self.assertEqual(data["material_type"], "模特素材")
            self.assertEqual(data["video_count"], 1)
            self.assertEqual(len(list((temp_path / "out" / "8819" / "模特素材").glob("*.mp4"))), 1)
            self.assertTrue(data["archive_deleted"])

    def test_material_organize_flat_marks_kaipai_follow_up(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "7788视频平铺.mp4"
            source.write_bytes(b"video")
            result = video_tool.run_request({
                "operation": "material_organize",
                "inputs": {"source_path": str(source)},
                "options": {"material_type": "flat", "output_root": str(Path(temp) / "out")},
                "authorization": {"confirmed": True, "scope": "material_organize"},
            })
            self.assertTrue(result["results"][0]["optimization_required"])

    def test_material_organize_rar_uses_7zip(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            archive = temp_path / "8819视频01.rar"
            archive.write_bytes(b"rar")
            extracted = temp_path / "fake_extract"
            extracted.mkdir()
            (extracted / "clip.mp4").write_bytes(b"video")

            def fake_run(*_args, **_kwargs):
                target = Path(_kwargs.get("cwd", temp_path))
                return MagicMock(returncode=0, stdout="", stderr="")

            with patch("core.material_organizer._find_7zip", return_value="7z.exe"), \
                 patch("core.material_organizer.subprocess.run", side_effect=fake_run), \
                 patch("core.material_organizer.tempfile.TemporaryDirectory") as tempdir:
                tempdir.return_value.__enter__.return_value = str(extracted)
                tempdir.return_value.__exit__.return_value = False
                result = video_tool.run_request({
                    "operation": "material_organize",
                    "inputs": {"source_path": str(archive)},
                    "options": {"output_root": str(temp_path / "out")},
                    "authorization": {"confirmed": True, "scope": "material_organize"},
                })
            self.assertEqual(result["results"][0]["video_count"], 1)

    def test_every_operation_publishes_a_complete_contract(self):
        for name, spec in video_tool.CAPABILITIES["operations"].items():
            with self.subTest(operation=name):
                self.assertIn("input_schema", spec)
                self.assertIn("option_schema", spec)
                self.assertIn("result_schema", spec)
                self.assertEqual(spec["required"], spec["input_schema"]["required"])
                self.assertTrue(set(spec["required"]).issubset(spec["input_schema"]["properties"]))
                for field, field_schema in spec["input_schema"]["properties"].items():
                    self.assertIn("type", field_schema, (name, field))
                for field, field_schema in spec["option_schema"]["properties"].items():
                    self.assertIn("type", field_schema, (name, field))
        self.assertEqual(
            video_tool.CAPABILITIES["operations"]["keyword_remove"]["option_schema"]["properties"]["estimate_min_duration"]["type"],
            "number",
        )
        self.assertEqual(
            video_tool.CAPABILITIES["operations"]["video_fission"]["option_schema"]["properties"]["intensity"]["enum"],
            ["mild", "medium", "strong"],
        )
        self.assertEqual(
            set(video_tool.CAPABILITIES["operations"]["validate"]["result_schema"]["required"]),
            {"operation", "validation"},
        )
        self.assertEqual(
            set(video_tool.CAPABILITIES["operations"]["video_concat"]["result_schema"]["required"]),
            {"operation", "outputs", "validation", "summary"},
        )
        self.assertIn(
            "normalization",
            video_tool.CAPABILITIES["operations"]["qianchuan_concat"]["result_schema"]["required"],
        )

    def test_registry_and_handlers_are_complete(self):
        self.assertEqual(set(headless_operations.OPERATIONS), set(headless_operations.HANDLERS))

    def test_all_gui_operation_modules_import_without_showing_a_window(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        modules = [
            "gui.slice_tab", "gui.screenshot_tab", "gui.text_recognition_tab",
            "gui.face_detection_tab", "gui.audio_mix_tab", "gui.video_mix_tab",
            "gui.video_concat_tab", "gui.video_resize_tab", "gui.video_enhance_tab",
            "gui.keyword_remove_tab", "gui.subtitle_tab", "gui.kaipai_cloud_tab",
            "gui.video_fission_tab", "gui.voice_clone_tab", "gui.video_download_tab",
            "gui.settings_tab",
        ]
        for module in modules:
            with self.subTest(module=module):
                self.assertIsNotNone(importlib.import_module(module))

    def test_resize_requires_formal_inputs(self):
        with self.assertRaisesRegex(ValueError, "缺少必要参数"):
            video_tool.run_request({"operation": "video_resize", "inputs": {}})

    @patch("video_tool.run_tab_operation", return_value={"operation": "video_resize", "outputs": ["out.mp4"]})
    def test_non_concat_operation_uses_headless_dispatch(self, dispatch):
        result = video_tool.run_request({
            "operation": "video_resize",
            "inputs": {"input_path": "in", "output_folder": "out"},
        })
        self.assertTrue(result["success"])
        self.assertEqual(result["outputs"], ["out.mp4"])
        dispatch.assert_called_once()

    @patch("utils.path_utils.build_output_path", return_value="out/video_9x16.mp4")
    @patch("utils.media_utils.collect_videos", return_value=["in/video.mp4"])
    @patch("core.video_resizer.VideoResizer.resize_video")
    def test_resize_reuses_core_resizer(self, resize, _collect, _build):
        result = headless_operations.run_operation("video_resize", {
            "inputs": {"input_path": "in", "output_folder": "out"},
            "options": {"target_ratio": "9:16", "process_mode": "all"},
        })
        resize.assert_called_once_with("in/video.mp4", "out/video_9x16.mp4")
        self.assertEqual(result["outputs"], ["out/video_9x16.mp4"])

    @patch("video_tool._run_concat", return_value={"operation": "video_concat", "outputs": ["out.mp4"]})
    @patch("video_tool._normalize_folder_9x16", return_value={"input_count": 1, "converted": 1, "copied": 0})
    @patch("video_tool._qianchuan_output_folder", return_value="out")
    def test_qianchuan_operation_enforces_normalization_before_concat(self, output_folder, normalize, concat):
        result = video_tool.run_request({
            "operation": "qianchuan_concat",
            "inputs": {"folder_a": "model", "folder_b": "flat"},
        })
        self.assertTrue(result["success"])
        self.assertEqual(normalize.call_count, 2)
        concat_request = concat.call_args.args[0]
        self.assertTrue(concat_request["options"]["require_9x16"])
        self.assertEqual(concat_request["inputs"]["output_folder"], "out")
        self.assertEqual(result["operation"], "qianchuan_concat")
        output_folder.assert_called_once_with("model", "flat", "")

    @patch("video_tool.datetime")
    def test_qianchuan_output_folder_is_derived_and_enforced(self, mocked_datetime):
        mocked_datetime.now.return_value.strftime.return_value = "0822"
        expected = r"D:\千川素材\8819\8819 千川素材 0822"
        actual = video_tool._qianchuan_output_folder(
            r"D:\千川素材\8819\模特视频", r"D:\千川素材\8819\平铺视频"
        )
        self.assertEqual(actual, expected)
        with self.assertRaisesRegex(ValueError, "千川输出目录必须"):
            video_tool._qianchuan_output_folder(
                r"D:\千川素材\8819\模特视频", r"D:\千川素材\8819\平铺视频", r"D:\wrong"
            )

    def test_qianchuan_rejects_cross_product_folders(self):
        with self.assertRaisesRegex(ValueError, "同一货号目录"):
            video_tool._qianchuan_output_folder(
                r"D:\千川素材\8819\模特视频", r"D:\千川素材\8820\平铺视频"
            )

    @patch("video_tool.shutil.copy2")
    @patch("core.video_resizer.VideoResizer.resize_video")
    @patch("utils.media_utils.probe_video", return_value={
        "width": 1920, "height": 1080, "display_width": 1080,
        "display_height": 1920, "rotation": 90,
    })
    @patch("utils.media_utils.collect_videos", return_value=["in/rotated.mp4"])
    @patch("video_tool.os.makedirs")
    def test_rotated_portrait_is_physically_normalized(self, _mkdir, _collect, _probe, resize, copy):
        result = video_tool._normalize_folder_9x16("in", "out", 6)
        resize.assert_called_once_with("in/rotated.mp4", os.path.join("out", "rotated.mp4"))
        copy.assert_not_called()
        self.assertEqual(result["converted"], 1)

    def test_core_and_worker_adapters_accept_their_minimum_contracts(self):
        with patch("core.slicer.VideoSlicer", autospec=True) as cls, patch("headless_operations.os.path.isfile", return_value=True):
            cls.return_value.slice_video.return_value = [{"file": "slice.mp4"}]
            self.assertEqual(headless_operations._video_slice({"inputs": {"input_path": "in.mp4", "output_folder": "out"}})["outputs"], ["slice.mp4"])
        with patch("core.mixer.VideoMixer", autospec=True) as cls:
            cls.return_value.mix_folder.return_value = ["audio_mix.mp4"]
            self.assertEqual(headless_operations._audio_mix({"inputs": {"clips_folder": "c", "media_folder": "m", "output_folder": "o"}})["outputs"], ["audio_mix.mp4"])
        with patch("core.video_mixer.VideoMixerEngine", autospec=True) as cls:
            cls.return_value.run.return_value = ["video_mix.mp4"]
            self.assertEqual(headless_operations._video_mix({"inputs": {"video_folder": "v", "clips_folder": "c", "output_folder": "o"}})["outputs"], ["video_mix.mp4"])
        with patch("core.video_fission.VideoFission", autospec=True) as cls:
            cls.return_value.fission_folder.return_value = [{"outputs": ["fission.mp4"]}]
            result = headless_operations._video_fission({"inputs": {"input_sources": ["in"], "output_folder": "out"}})
            self.assertEqual(result["outputs"], ["fission.mp4"])

    @patch("headless_operations._run_worker", return_value={"results": []})
    def test_gui_worker_adapters_construct_and_run(self, run_worker):
        cases = [
            ("gui.text_recognition_tab.TextRecognitionWorker", headless_operations._text_recognition, {"input_path": "in"}),
            ("gui.face_detection_tab.FaceDetectionWorker", headless_operations._face_detection, {"input_path": "in"}),
            ("gui.video_enhance_tab.VideoEnhanceWorker", headless_operations._video_enhance, {"input_path": "in", "output_folder": "out"}),
            ("gui.keyword_remove_tab.KeywordRemoveWorker", headless_operations._keyword_remove, {"input_path": "in", "output_folder": "out", "keywords": ["词"], "model_path": "model"}),
            ("gui.subtitle_tab.SubtitleWorker", headless_operations._subtitle_generate, {"input_path": "in", "output_folder": "out", "model_path": "model"}),
        ]
        with patch("core.wink_enhancer.find_wink_exe", return_value="wink.exe"):
            for target, handler, inputs in cases:
                with self.subTest(target=target), patch(target, autospec=True) as worker:
                    handler({"inputs": inputs})
                    worker.assert_called_once()
        self.assertEqual(run_worker.call_count, len(cases))

    def test_kaipai_adapters_accept_their_contracts(self):
        with patch("headless_operations._collect_input_files", return_value=["in.mp4"]), patch(
            "gui.kaipai_cloud_tab.KaipaiWorker", autospec=True
        ) as worker, patch("headless_operations._run_worker", return_value={"results": []}) as run_worker:
            headless_operations._kaipai_process({"inputs": {"input_path": "in", "task_name": "视频高清"}})
            worker.assert_called_once()
            run_worker.assert_called_once()
        client = MagicMock()
        client.wapi.request.return_value = {"quota": 1}
        with patch("gui.kaipai_cloud_tab.get_skill_client", return_value=client):
            self.assertEqual(headless_operations._kaipai_quota({})["results"], [{"quota": 1}])
        response = MagicMock(content=b"data")
        response.iter_content.return_value = [b"data"]
        with patch("requests.get", return_value=response), patch("utils.media_utils.probe_video", return_value={"duration": 1.0}), patch("builtins.open", MagicMock()), \
                patch("headless_operations.os.makedirs"), patch("headless_operations.os.replace"):
            result = headless_operations._kaipai_download({"inputs": {"items": [{"url": "https://x/a.mp4"}], "output_folder": "out"}})
            self.assertTrue(result["results"][0]["success"])

    def test_kaipai_download_reuses_completed_file_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "a.mp4"
            output.write_bytes(b"complete")
            context = MagicMock()
            context.resume_items.return_value = {
                "https://x/a.mp4": {"state": "completed", "output_url": str(output)}
            }
            with patch("requests.get") as get, \
                    patch("utils.media_utils.probe_video", return_value={"duration": 1.0}):
                result = headless_operations._kaipai_download({"inputs": {
                    "items": [{"url": "https://x/a.mp4", "filename": "a.mp4"}],
                    "output_folder": temp,
                }, "_task_center_context": context})
            get.assert_not_called()
            self.assertEqual(result["outputs"], [str(output)])
            self.assertTrue(result["results"][0]["reused"])

    def test_kaipai_download_redownloads_corrupted_completed_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "a.mp4"
            output.write_bytes(b"corrupted")
            context = MagicMock()
            context.resume_items.return_value = {
                "https://x/a.mp4": {"state": "completed", "output_url": str(output)}
            }
            response = MagicMock()
            response.iter_content.return_value = [b"replacement"]
            probes = iter([{"duration": 0}, {"duration": 1.0}])
            with patch("requests.get", return_value=response) as get, \
                    patch("utils.media_utils.probe_video", side_effect=lambda _path: next(probes)):
                result = headless_operations._kaipai_download({"inputs": {
                    "items": [{"url": "https://x/a.mp4", "filename": "a.mp4"}],
                    "output_folder": temp,
                }, "_task_center_context": context})
            get.assert_called_once()
            self.assertEqual(output.read_bytes(), b"replacement")
            self.assertFalse(result["results"][0]["reused"])

    def test_kaipai_download_gives_distinct_urls_with_same_name_stable_paths(self):
        urls = ["https://x/one/a.mp4", "https://y/two/a.mp4"]
        items = [{"url": url, "filename": "a.mp4"} for url in urls]
        context = MagicMock()
        context.resume_items.return_value = {}
        responses = []
        for content in (b"first", b"second"):
            response = MagicMock()
            response.iter_content.return_value = [content]
            responses.append(response)

        with tempfile.TemporaryDirectory() as temp, patch("requests.get", side_effect=responses), \
                patch("utils.media_utils.probe_video", return_value={"duration": 1.0}):
            first = headless_operations._kaipai_download({
                "inputs": {"items": items, "output_folder": temp},
                "_task_center_context": context,
            })
            outputs = first["outputs"]
            self.assertEqual(len(set(outputs)), 2)
            self.assertEqual([Path(path).read_bytes() for path in outputs], [b"first", b"second"])
            self.assertTrue(all(Path(path).stem.startswith("a_") for path in outputs))

            restart_context = MagicMock()
            restart_context.resume_items.return_value = {
                url: {"state": "completed", "output_url": output}
                for url, output in zip(urls, outputs)
            }
            with patch("requests.get") as get:
                restarted = headless_operations._kaipai_download({
                    "inputs": {"items": items, "output_folder": temp},
                    "_task_center_context": restart_context,
                })
            get.assert_not_called()
            self.assertEqual(restarted["outputs"], outputs)
            self.assertTrue(all(item["reused"] for item in restarted["results"]))

    def test_kaipai_download_propagates_pause_during_streaming(self):
        from core.task_control import TaskControlSignal

        context = MagicMock()
        context.resume_items.return_value = {}
        context.check.side_effect = [None, TaskControlSignal("paused")]
        response = MagicMock()
        response.iter_content.return_value = [b"partial"]
        with tempfile.TemporaryDirectory() as temp, patch("requests.get", return_value=response), \
                patch("utils.media_utils.probe_video", return_value={"duration": 1.0}):
            with self.assertRaises(TaskControlSignal):
                headless_operations._kaipai_download({"inputs": {
                    "items": [{"url": "https://x/a.mp4", "filename": "a.mp4"}],
                    "output_folder": temp,
                }, "_task_center_context": context})
            self.assertFalse((Path(temp) / "a.mp4.part").exists())

    def test_kaipai_download_rejects_non_decodable_content(self):
        response = MagicMock()
        response.iter_content.return_value = [b"not-a-video"]
        with tempfile.TemporaryDirectory() as temp, \
                patch("requests.get", return_value=response), \
                patch("utils.media_utils.probe_video", return_value={"duration": 0}):
            result = headless_operations._kaipai_download({"inputs": {
                "items": [{"url": "https://x/a.mp4", "filename": "a.mp4"}],
                "output_folder": temp,
            }})
            self.assertFalse(result["results"][0]["success"])
            self.assertIn("可解码", result["results"][0]["error"])
            self.assertFalse((Path(temp) / "a.mp4").exists())
            self.assertFalse((Path(temp) / "a.mp4.part").exists())

    def test_kaipai_batch_runs_files_concurrently_and_preserves_order(self):
        active = 0
        peak = 0
        lock = threading.Lock()

        def execute(**kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            name = Path(kwargs["source"]).stem
            return {"task_id": f"task-{name}", "output_urls": [f"https://out/{name}.mp4"]}

        client = MagicMock()
        client.execute.side_effect = execute
        worker = KaipaiWorker(["a.mp4", "b.mp4", "c.mp4"], "视频智能全消", max_workers=3)

        results = worker._run_batch(client, 3)

        self.assertGreaterEqual(peak, 2)
        self.assertEqual([item["file"] for item in results], ["a.mp4", "b.mp4", "c.mp4"])
        self.assertEqual([item["status"] for item in results], ["成功", "成功", "成功"])
        self.assertEqual(client.execute.call_count, 3)

    def test_kaipai_reuses_persisted_cloud_task_without_resubmitting(self):
        context = MagicMock()
        context.resume_items.return_value = {
            "a.mp4": {"state": "submitted", "cloud_task_id": "cloud-a", "output_url": ""}
        }
        client = MagicMock()
        client.query.return_value = {"output_urls": ["https://out/a.mp4"]}
        worker = KaipaiWorker(["a.mp4"], "视频智能全消", task_context=context)

        result = worker._process_one(client, "a.mp4", 0, 1)

        client.execute.assert_not_called()
        client.query.assert_called_once_with("cloud-a")
        self.assertEqual(result["task_id"], "cloud-a")
        context.cloud_item.assert_called_with(
            "a.mp4", state="completed", cloud_task_id="cloud-a", output_url="https://out/a.mp4"
        )

    def test_kaipai_does_not_resubmit_an_unknown_previous_submission(self):
        context = MagicMock()
        context.resume_items.return_value = {
            "a.mp4": {"state": "submission_unknown", "cloud_task_id": "", "output_url": ""}
        }
        client = MagicMock()
        worker = KaipaiWorker(["a.mp4"], "视频智能全消", task_context=context)

        result = worker._process_one(client, "a.mp4", 0, 1)

        client.execute.assert_not_called()
        client.query.assert_not_called()
        self.assertEqual(result["status"], "失败")
        self.assertIn("停止自动重提", result["error"])

    def test_kaipai_empty_output_url_is_not_reported_as_success(self):
        context = MagicMock()
        context.resume_items.return_value = {}
        client = MagicMock()
        def execute(**kwargs):
            kwargs["on_async_submitted"]("cloud-a")
            return {"task_id": "cloud-a", "output_urls": []}
        client.execute.side_effect = execute
        worker = KaipaiWorker(["a.mp4"], "视频智能全消", task_context=context)

        result = worker._process_one(client, "a.mp4", 0, 1)

        self.assertEqual(result["status"], "失败")
        self.assertIn("未返回可下载的结果地址", result["error"])
        context.cloud_item.assert_called_with(
            "a.mp4", state="failed", cloud_task_id="cloud-a",
            error="开拍任务未返回可下载的结果地址，不能标记为成功",
        )

    def test_task_control_pause_resume_cancel(self):
        task_id = "test-control-001"
        state_dir = ROOT / ".task_control"
        state_dir.mkdir(exist_ok=True)
        state_path = state_dir / (task_id + ".json")
        try:
            from core.task_control import TaskController
            TaskController(task_id).start({"operation": "video_concat"}, {
                "operation": "video_concat",
                "task_id": task_id,
                "inputs": {"folder_a": "a", "folder_b": "b", "output_folder": "out"},
            })
            paused = video_tool.run_request({
                "operation": "task_control",
                "inputs": {"task_id": task_id, "action": "pause"},
            })
            self.assertEqual(paused["status"], "paused")
            with patch("core.task_control.subprocess.Popen"):
                resumed = video_tool.run_request({
                    "operation": "task_control",
                    "inputs": {"task_id": task_id, "action": "resume"},
                })
            self.assertEqual(resumed["status"], "running")
            cancelled = video_tool.run_request({
                "operation": "task_control",
                "inputs": {"task_id": task_id, "action": "cancel"},
            })
            self.assertEqual(cancelled["status"], "cancelled")
        finally:
            if state_path.exists():
                state_path.unlink()

    def test_voice_download_and_settings_adapters_accept_contracts(self):
        library = MagicMock()
        library.list_profiles.return_value = [{"id": "voice", "name": "v"}]
        library.create.return_value = {"id": "voice"}
        with patch("core.voice_clone.VoiceLibrary", return_value=library):
            self.assertTrue(headless_operations._voice_profile_list({})["results"])
            self.assertTrue(headless_operations._voice_profile_create({"inputs": {"name": "v", "reference_audio": "a.wav", "reference_text": "t"}})["results"])
            self.assertTrue(headless_operations._voice_profile_delete({"inputs": {"profile_id": "voice"}})["results"][0]["deleted"])
            with patch("gui.voice_clone_tab.VoiceCloneWorker", autospec=True) as worker, patch("headless_operations._run_worker", return_value={"results": []}):
                headless_operations._voice_clone_apply({"inputs": {"profile_id": "voice", "input_path": "in", "output_folder": "out"}})
                worker.assert_called_once()
            service = MagicMock()
            service.synthesize.return_value = {"ok": True}
            with patch("core.voice_clone.CosyVoiceService", return_value=service):
                result = headless_operations._voice_synthesize({"inputs": {"profile_id": "voice", "text": "hello", "output_path": "out.wav"}})
                self.assertEqual(result["outputs"], ["out.wav"])
                service.stop.assert_called_once()
        with patch("core.taobao_downloader.download_video", return_value=(True, "download.mp4")), patch("core.taobao_downloader.close_shared_browser"):
            self.assertEqual(headless_operations._video_download({"inputs": {"urls": ["https://x"], "output_folder": "out"}})["outputs"], ["download.mp4"])
        with patch("core.taobao_downloader.check_auth_file", return_value="ok"):
            self.assertEqual(headless_operations._download_auth_status({})["results"][0]["status"], "ok")
        with patch("core.taobao_downloader.login_and_save", return_value=(True, "ok")):
            self.assertTrue(headless_operations._download_login({})["results"][0]["success"])
        with patch("gui.config.reload_config", return_value={"settings": {}}):
            self.assertTrue(headless_operations._settings_get({})["results"])
        with patch("gui.config.set_config") as set_config:
            headless_operations._settings_update({"inputs": {"section": "s", "values": {"k": "v"}}})
            set_config.assert_called_once_with("s", "k", "v")
        with patch("gui.config.set_secret") as set_secret:
            headless_operations._settings_secret_set({"inputs": {"section": "s", "key": "k", "value": "v"}})
            set_secret.assert_called_once_with("s", "k", "v")

    @patch("video_tool.run_tab_operation", return_value={"operation": "video_enhance", "outputs": []})
    def test_external_operation_requires_explicit_authorization(self, dispatch):
        request = {
            "operation": "video_enhance",
            "inputs": {"input_path": "in", "output_folder": "out"},
        }
        with self.assertRaisesRegex(PermissionError, "需要显式授权"):
            video_tool.run_request(request)
        request["authorization"] = {"confirmed": True, "scope": "video_enhance"}
        video_tool.run_request(request)
        dispatch.assert_called_once()

    def test_task_plan_requires_disclosed_risk_authorization(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "input"
            source.mkdir()
            (source / "a.mp4").write_bytes(b"video")
            request = {
                "operation": "task_center_plan",
                "inputs": {
                    "task_id": "VT-RISK", "title": "云端处理",
                    "parameter_lines": ["批量提交：已启用", "并发数：9"],
                    "steps": [
                        {"id": "clear", "request": {
                            "operation": "kaipai_process", "input_path": str(source),
                            "task_name": "videoscreenclear",
                        }},
                        {"id": "download", "items_from_step": "clear", "request": {
                            "operation": "kaipai_download", "output_folder": str(Path(temp) / "out"),
                        }},
                    ],
                },
            }
            with patch.dict(os.environ, {"VIDEO_TASK_CENTER_DB": str(Path(temp) / "tasks.db")}):
                with self.assertRaisesRegex(PermissionError, "未声明授权范围"):
                    video_tool.run_request(request)
                request["inputs"]["authorized_operations"] = ["kaipai_process", "kaipai_download"]
                request["inputs"]["risk_note"] = "将提交 1 个视频到开拍智能全消"
                result = video_tool.run_request(request)
            self.assertEqual(result["task"]["authorized_operations"], ["kaipai_download", "kaipai_process"])
            self.assertEqual(result["task"]["risk_note"], "将提交 1 个视频到开拍智能全消")

    def test_task_plan_refuses_to_persist_secret_or_login_operations(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VIDEO_TASK_CENTER_DB": str(Path(temp) / "tasks.db")}
        ):
            for operation in ("settings_secret_set", "settings_update", "download_login"):
                with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "不允许进入"):
                    video_tool.run_request({
                        "operation": "task_center_plan",
                        "inputs": {
                            "task_id": "VT-" + operation, "title": operation,
                            "parameter_lines": ["参数已逐项列明"],
                            "steps": [{"id": "one", "request": {"operation": operation}}],
                        },
                    })

    @patch("video_tool.run_tab_operation", return_value={"operation": "video_screenshot", "outputs": []})
    def test_screenshot_only_requires_authorization_when_deleting(self, dispatch):
        base = {
            "operation": "video_screenshot",
            "inputs": {"input_path": "in", "output_folder": "out"},
        }
        video_tool.run_request(base)
        destructive = {**base, "options": {"delete_face_images": True}}
        with self.assertRaisesRegex(PermissionError, "需要显式授权"):
            video_tool.run_request(destructive)
        self.assertEqual(dispatch.call_count, 1)

    @patch("core.screenshot.extract_frames_from_folder", return_value=[])
    def test_screenshot_forwards_separate_folder_option(self, extract):
        headless_operations.run_operation("video_screenshot", {
            "inputs": {"input_path": "in", "output_folder": "out"},
            "options": {"separate_folders": False},
        })
        self.assertFalse(extract.call_args.kwargs["separate_folders"])

    @patch("core.voice_clone.VoiceLibrary")
    def test_voice_directory_option_is_honored(self, voice_library):
        voice_library.return_value.list_profiles.return_value = []
        headless_operations.run_operation("voice_profile_list", {
            "options": {"voices_dir": r"D:\voices"},
        })
        voice_library.assert_called_once_with(r"D:\voices")

    @patch("utils.media_utils.subprocess.run")
    def test_probe_video_reports_display_size_after_rotation(self, run):
        run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "streams": [{
                    "codec_type": "video", "width": 1920, "height": 1080,
                    "side_data_list": [{"rotation": -90}],
                }],
                "format": {"duration": "1.0"},
            }),
            stderr="",
        )
        from utils.media_utils import probe_video
        info = probe_video("rotated.mp4")
        self.assertEqual((info["display_width"], info["display_height"]), (1080, 1920))
        self.assertEqual(info["rotation"], 270)

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
        config = _init.call_args.args[0]
        self.assertEqual(config["cover_duration_min"], 0.2)
        self.assertEqual(config["cover_duration_max"], 0.5)

    @patch("video_tool._probe", side_effect=[
        {"valid": True, "exists": True, "path": "out.mp4", "width": 720,
         "height": 1280, "duration": 10.0},
        {"valid": True, "exists": True, "path": "a.mp4", "width": 720,
         "height": 1280, "duration": 3.0},
        {"valid": True, "exists": True, "path": "b.mp4", "width": 720,
         "height": 1280, "duration": 3.0},
    ])
    @patch("video_tool.os.makedirs")
    @patch("core.video_concatenator.VideoConcatenatorEngine.run", return_value=["out.mp4"])
    @patch("core.video_concatenator.VideoConcatenatorEngine.__init__", return_value=None)
    def test_concat_accepts_any_9x16_output(self, _init, run, _makedirs, _probe):
        result = video_tool.run_request({
            "operation": "video_concat",
            "inputs": {"folder_a": "a", "folder_b": "b", "output_folder": "out"},
            "options": {"require_9x16": True, "require_cover": False, "cover_enabled": False},
        })
        self.assertTrue(result["success"])
        self.assertTrue(result["validation"][0]["checks"]["aspect_ratio_9x16"])

    @patch("video_tool._probe", side_effect=[
        {"valid": True, "exists": True, "path": "out.mp4", "width": 16,
         "height": 9, "duration": 10.0},
        {"valid": True, "exists": True, "path": "a.mp4", "width": 16,
         "height": 9, "duration": 3.0},
        {"valid": True, "exists": True, "path": "b.mp4", "width": 16,
         "height": 9, "duration": 3.0},
    ])
    @patch("video_tool.os.makedirs")
    @patch("core.video_concatenator.VideoConcatenatorEngine.run", return_value=["out.mp4"])
    @patch("core.video_concatenator.VideoConcatenatorEngine.__init__", return_value=None)
    def test_concat_rejects_non_9x16_output(self, _init, run, _makedirs, _probe):
        with self.assertRaisesRegex(RuntimeError, "要求 9:16"):
            video_tool.run_request({
                "operation": "video_concat",
                "inputs": {"folder_a": "a", "folder_b": "b", "output_folder": "out"},
                "options": {"require_9x16": True, "require_cover": False, "cover_enabled": False},
            })


if __name__ == "__main__":
    unittest.main()
