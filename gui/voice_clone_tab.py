# -*- coding: utf-8 -*-
import os
import tempfile

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QDoubleSpinBox, QVBoxLayout, QWidget
)

from core.voice_clone import CosyVoiceService, VoiceClonePipeline, VoiceLibrary
from gui.config import get_config, set_config
from gui.common.base_tab import BaseTab
from gui.common.base_worker import BaseWorker
from gui.common.path_row import PathRow, MODE_FOLDER


DEFAULT_ROOT = r"D:\Models\CosyVoice3"
DEFAULT_MODEL = os.path.join(DEFAULT_ROOT, "pretrained_models", "Fun-CosyVoice3-0.5B")
DEFAULT_VOICES = os.path.join(DEFAULT_ROOT, "voices")
DEFAULT_CONDA = r"D:\Anaconda\Scripts\conda.exe"


class VoiceCloneWorker(BaseWorker):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, settings, profile, preview_text=""):
        super().__init__()
        self.settings = settings
        self.profile = profile
        self.preview_text = preview_text
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            service = CosyVoiceService(
                self.settings["conda_exe"], "cosyvoice3",
                self.settings["server_script"], self.settings["model_dir"]
            )
            service.start(lambda text: self.progress.emit(0, 0, text))
            if self.preview_text:
                output = os.path.join(tempfile.gettempdir(), "cosyvoice3_preview.wav")
                service.synthesize(self.preview_text, self.profile, output, self.settings["speed"])
                self.finished.emit([{"output": output, "preview": True}])
                return

            pipeline = VoiceClonePipeline(
                service, self.settings["asr_type"], self.settings["asr_model_dir"],
                self.settings["text_source"]
            )
            videos = pipeline.find_videos(self.settings["input_dir"])
            if not videos:
                raise RuntimeError("输入文件夹中没有视频")
            results = []
            for index, video in enumerate(videos, 1):
                if self._stop:
                    break
                relative = os.path.relpath(video, self.settings["input_dir"])
                output = os.path.join(
                    self.settings["output_dir"], os.path.splitext(relative)[0] + "_voice.mp4"
                )
                self.progress.emit(index - 1, len(videos), f"正在处理：{relative}")
                try:
                    result = pipeline.apply(video, output, self.profile, self.settings["speed"])
                    result["success"] = True
                except Exception as exc:
                    result = {"video": video, "success": False, "error": str(exc)}
                results.append(result)
                self.progress.emit(index, len(videos), f"已完成 {index}/{len(videos)}")
            self.finished.emit(results)
        except Exception as exc:
            self.error.emit(str(exc))


class VoiceCloneTab(BaseTab):
    def __init__(self):
        super().__init__()
        self.library = VoiceLibrary(get_config("voice_clone", "voices_dir", DEFAULT_VOICES))
        self.profiles = []
        self._build_ui()
        self._load_settings()
        self.refresh_voices()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)

        clone_group = QGroupBox("克隆音色")
        clone_form = QFormLayout(clone_group)
        self.voice_name = QLineEdit()
        self.reference_audio = QLineEdit()
        audio_row = QHBoxLayout()
        audio_row.addWidget(self.reference_audio, 1)
        browse_audio = QPushButton("浏览")
        browse_audio.clicked.connect(self._browse_reference)
        audio_row.addWidget(browse_audio)
        self.reference_text = QPlainTextEdit()
        self.reference_text.setMaximumHeight(76)
        self.reference_text.setPlaceholderText("填写参考音频中逐字对应的文案")
        clone_btn = QPushButton("克隆并保存")
        clone_btn.clicked.connect(self._create_voice)
        clone_form.addRow("音色名称", self.voice_name)
        clone_form.addRow("参考音频", audio_row)
        clone_form.addRow("参考文案", self.reference_text)
        clone_form.addRow("", clone_btn)

        voice_group = QGroupBox("已克隆音色")
        voice_row = QHBoxLayout(voice_group)
        self.voice_combo = QComboBox()
        voice_row.addWidget(self.voice_combo, 1)
        preview_btn = QPushButton("试听")
        preview_btn.clicked.connect(self._preview)
        delete_btn = QPushButton("删除")
        delete_btn.clicked.connect(self._delete_voice)
        voice_row.addWidget(preview_btn)
        voice_row.addWidget(delete_btn)

        batch_group = QGroupBox("批量应用")
        batch_form = QFormLayout(batch_group)
        self.input_dir = PathRow("选择视频文件夹...", mode=MODE_FOLDER)
        self.output_dir = PathRow("选择输出文件夹...", mode=MODE_FOLDER)
        batch_form.addRow("视频文件夹", self.input_dir)
        batch_form.addRow("输出文件夹", self.output_dir)
        self.text_source = QComboBox()
        self.text_source.addItem("同名 TXT 优先，没有则自动识别", "auto")
        self.text_source.addItem("仅使用同名 TXT", "txt")
        self.text_source.addItem("全部自动识别", "asr")
        batch_form.addRow("文案来源", self.text_source)
        self.asr_type = QComboBox()
        self.asr_type.addItems(["FireRedASR", "FunASR (Paraformer)"])
        batch_form.addRow("识别引擎", self.asr_type)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.75, 1.35)
        self.speed.setSingleStep(0.05)
        self.speed.setValue(1.0)
        batch_form.addRow("生成语速", self.speed)

        service_group = QGroupBox("本地引擎")
        service_form = QFormLayout(service_group)
        self.model_dir = QLineEdit(DEFAULT_MODEL)
        self.conda_exe = QLineEdit(DEFAULT_CONDA)
        service_form.addRow("CosyVoice 3 模型", self.model_dir)
        service_form.addRow("Conda", self.conda_exe)

        controls = QHBoxLayout()
        self.start_btn = QPushButton("开始批量换音")
        self.start_btn.clicked.connect(self._start_batch)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        controls.addWidget(self.start_btn)
        controls.addWidget(self.stop_btn)
        self.progress = QProgressBar()
        self.status = QLabel("就绪")

        layout.addWidget(clone_group)
        layout.addWidget(voice_group)
        layout.addWidget(batch_group)
        layout.addWidget(service_group)
        layout.addLayout(controls)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _browse_input(self):
        self.input_dir._browse()

    def _browse_output(self):
        self.output_dir._browse()

    def _load_settings(self):
        self.input_dir.setText(get_config("voice_clone", "input_dir", ""))
        self.output_dir.setText(get_config("voice_clone", "output_dir", ""))
        self.model_dir.setText(get_config("voice_clone", "model_dir", DEFAULT_MODEL))
        self.conda_exe.setText(get_config("voice_clone", "conda_exe", DEFAULT_CONDA))
        self.speed.setValue(float(get_config("voice_clone", "speed", "1.0")))

    def _save_settings(self):
        set_config("voice_clone", "input_dir", self.input_dir.text())
        set_config("voice_clone", "output_dir", self.output_dir.text())
        set_config("voice_clone", "model_dir", self.model_dir.text())
        set_config("voice_clone", "conda_exe", self.conda_exe.text())
        set_config("voice_clone", "voices_dir", str(self.library.root))
        set_config("voice_clone", "speed", str(self.speed.value()))

    def refresh_voices(self):
        current = self.voice_combo.currentData()
        self.profiles = self.library.list_profiles()
        self.voice_combo.clear()
        for profile in self.profiles:
            self.voice_combo.addItem(profile["name"], profile["id"])
        index = self.voice_combo.findData(current)
        if index >= 0:
            self.voice_combo.setCurrentIndex(index)

    def _selected_profile(self):
        index = self.voice_combo.currentIndex()
        return self.profiles[index] if 0 <= index < len(self.profiles) else None

    def _browse_reference(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择参考音频", "", "音频 (*.wav *.mp3 *.m4a *.flac *.aac)")
        if path:
            self.reference_audio.setText(path)

    def _create_voice(self):
        try:
            self.library.create(self.voice_name.text(), self.reference_audio.text(), self.reference_text.toPlainText())
            self.refresh_voices()
            self.voice_name.clear()
            QMessageBox.information(self, "完成", "音色已克隆并保存")
        except Exception as exc:
            QMessageBox.critical(self, "克隆失败", str(exc))

    def _delete_voice(self):
        profile = self._selected_profile()
        if profile and QMessageBox.question(self, "确认", f"删除音色“{profile['name']}”？") == QMessageBox.Yes:
            self.library.delete(profile["id"])
            self.refresh_voices()

    def _settings(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        model_type = self.asr_type.currentText()
        if model_type == "FireRedASR":
            asr_dir = get_config("settings", "fireredasr_model_path", r"D:\Models\FireRed")
        else:
            asr_dir = get_config("settings", "funasr_model_path", r"D:\Models\FunASR\paraformer-large-zh-en-timestamp-onnx-offline")
        return {
            "input_dir": self.input_dir.text().strip(), "output_dir": self.output_dir.text().strip(),
            "model_dir": self.model_dir.text().strip(), "conda_exe": self.conda_exe.text().strip(),
            "server_script": os.path.join(root, "services", "cosyvoice_server.py"),
            "text_source": self.text_source.currentData(), "asr_type": model_type,
            "asr_model_dir": asr_dir, "speed": self.speed.value(),
        }

    def _start_worker(self, preview_text=""):
        profile = self._selected_profile()
        if not profile:
            QMessageBox.warning(self, "提示", "请先克隆或选择一个音色")
            return
        settings = self._settings()
        if not preview_text and (not os.path.isdir(settings["input_dir"]) or not settings["output_dir"]):
            QMessageBox.warning(self, "提示", "请选择视频文件夹和输出文件夹")
            return
        self._save_settings()
        worker = VoiceCloneWorker(settings, profile, preview_text)
        if not self.start_worker(worker):
            return

    def set_busy(self, busy):
        self.start_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)

    def _start_batch(self):
        self._start_worker()

    def _preview(self):
        self._start_worker("这是一段音色复刻试听，欢迎使用本地配音功能。")

    def _stop(self):
        if self.worker:
            self.worker.stop()
            self.status.setText("将在当前视频完成后停止...")

    def on_worker_progress(self, current, total, message):
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(current)
        self.status.setText(message)

    def on_worker_finished(self, results):
        super().on_worker_finished(results)
        if results and results[0].get("preview"):
            os.startfile(results[0]["output"])
            self.status.setText("试听已生成")
            return
        successes = sum(bool(item.get("success")) for item in results)
        self.status.setText(f"完成：成功 {successes}，失败 {len(results) - successes}")
        QMessageBox.information(self, "批量换音完成", self.status.text())

    def on_worker_error(self, message):
        super().on_worker_error(message)
        self.status.setText("处理失败")
