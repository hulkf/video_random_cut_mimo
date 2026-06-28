from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QCheckBox, QDoubleSpinBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from core.mixer import VideoMixer
from gui.config import get_config, set_config
import os


class AudioMixWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        try:
            mixer = VideoMixer(
                cover_enabled=self.config["cover_enabled"],
                cover_folder=self.config["cover_folder"],
                cover_duration_min=self.config["cover_duration_min"],
                cover_duration_max=self.config["cover_duration_max"],
            )
            results = mixer.mix_folder(
                self.config["clips_dir"],
                self.config["media_dir"],
                self.config["output_dir"],
                lambda count, total: self.progress.emit(count, total)
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class AudioMixTab(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.init_ui()
        self.load_config()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        media_group = QGroupBox("音频/视频文件夹（支持音频和视频文件，自动遍历子文件夹）")
        media_layout = QVBoxLayout()

        media_folder_layout = QHBoxLayout()
        media_folder_layout.setSpacing(8)
        self.media_folder_input = QLineEdit()
        self.media_folder_input.setPlaceholderText("选择包含音频或视频的文件夹...")
        self.media_folder_input.setMinimumHeight(30)
        media_btn = QPushButton("浏览")
        media_btn.setFixedWidth(80)
        media_btn.clicked.connect(self.browse_media_folder)
        media_folder_layout.addWidget(self.media_folder_input, 1)
        media_folder_layout.addWidget(media_btn)
        media_layout.addLayout(media_folder_layout)
        media_group.setLayout(media_layout)

        clips_group = QGroupBox("视频切片文件夹")
        clips_layout = QHBoxLayout()
        clips_layout.setSpacing(8)
        self.clips_folder_input = QLineEdit()
        self.clips_folder_input.setPlaceholderText("选择切片视频文件夹...")
        self.clips_folder_input.setMinimumHeight(30)
        clips_btn = QPushButton("浏览")
        clips_btn.setFixedWidth(80)
        clips_btn.clicked.connect(self.browse_clips_folder)
        clips_layout.addWidget(self.clips_folder_input, 1)
        clips_layout.addWidget(clips_btn)
        clips_group.setLayout(clips_layout)

        cover_group = QGroupBox("封面图设置")
        cover_layout = QVBoxLayout()

        self.cover_check = QCheckBox("启用封面图")
        self.cover_check.setMinimumHeight(26)
        self.cover_check.setChecked(False)
        self.cover_check.stateChanged.connect(self.on_cover_changed)
        cover_layout.addWidget(self.cover_check)

        cover_folder_layout = QHBoxLayout()
        cover_folder_layout.setSpacing(8)
        cover_folder_layout.addWidget(QLabel("封面图文件夹:"))
        self.cover_folder_input = QLineEdit()
        self.cover_folder_input.setPlaceholderText("选择封面图文件夹...")
        self.cover_folder_input.setEnabled(False)
        self.cover_folder_input.setMinimumHeight(30)
        cover_folder_btn = QPushButton("浏览")
        cover_folder_btn.setFixedWidth(80)
        cover_folder_btn.clicked.connect(self.browse_cover_folder)
        cover_folder_btn.setEnabled(False)
        self.cover_folder_btn = cover_folder_btn
        cover_folder_layout.addWidget(self.cover_folder_input, 1)
        cover_folder_layout.addWidget(cover_folder_btn)
        cover_layout.addLayout(cover_folder_layout)

        cover_duration_layout = QHBoxLayout()
        cover_duration_layout.addWidget(QLabel("封面时长(秒):"))
        self.cover_duration_min = QDoubleSpinBox()
        self.cover_duration_min.setRange(0.1, 10.0)
        self.cover_duration_min.setValue(0.5)
        self.cover_duration_min.setSingleStep(0.1)
        self.cover_duration_min.setDecimals(1)
        self.cover_duration_min.setMinimumHeight(28)
        self.cover_duration_min.setEnabled(False)
        cover_duration_layout.addWidget(self.cover_duration_min)
        cover_duration_layout.addWidget(QLabel("~"))
        self.cover_duration_max = QDoubleSpinBox()
        self.cover_duration_max.setRange(0.1, 10.0)
        self.cover_duration_max.setValue(1.0)
        self.cover_duration_max.setSingleStep(0.1)
        self.cover_duration_max.setDecimals(1)
        self.cover_duration_max.setMinimumHeight(28)
        self.cover_duration_max.setEnabled(False)
        cover_duration_layout.addWidget(self.cover_duration_max)
        cover_layout.addLayout(cover_duration_layout)

        cover_group.setLayout(cover_layout)

        output_group = QGroupBox("输出设置")
        output_layout = QHBoxLayout()
        output_layout.setSpacing(8)
        self.output_folder_input = QLineEdit()
        self.output_folder_input.setPlaceholderText("选择输出文件夹...")
        self.output_folder_input.setMinimumHeight(30)
        output_btn = QPushButton("浏览")
        output_btn.setFixedWidth(80)
        output_btn.clicked.connect(self.browse_output_folder)
        output_layout.addWidget(self.output_folder_input, 1)
        output_layout.addWidget(output_btn)
        output_group.setLayout(output_layout)

        self.start_btn = QPushButton("开始混剪")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self.start_mixing)

        self.progress_bar = QProgressBar()
        self.status_label = QLabel("就绪")

        layout.addWidget(media_group)
        layout.addWidget(clips_group)
        layout.addWidget(cover_group)
        layout.addWidget(output_group)
        layout.addWidget(self.start_btn)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)

        layout.addStretch()

        desc_label = QLabel(
            "混剪逻辑说明：\n"
            "1. 遍历音频/视频文件夹下所有子文件夹中的音频和视频文件\n"
            "2. 每个媒体文件生成一个混剪视频（视频文件会提取音频）\n"
            "3. 启用封面图时，使用随机图片作为视频开头（无音频，可选时长）\n"
            "4. 根据媒体时长从切片视频中随机抽取片段填充\n"
            "5. 使用媒体文件的声音，去除切片视频原始音频"
        )
        desc_label.setStyleSheet("color: gray; padding: 10px;")
        layout.addWidget(desc_label)

        self.setLayout(layout)

    def load_config(self):
        self.media_folder_input.setText(get_config("audio_mix", "media_folder", ""))
        self.clips_folder_input.setText(get_config("audio_mix", "clips_folder", ""))
        self.output_folder_input.setText(get_config("audio_mix", "output_folder", ""))
        self.cover_check.setChecked(get_config("audio_mix", "cover_enabled", "false") == "true")
        self.cover_folder_input.setText(get_config("audio_mix", "cover_folder", ""))
        self.cover_duration_min.setValue(float(get_config("audio_mix", "cover_duration_min", "0.5")))
        self.cover_duration_max.setValue(float(get_config("audio_mix", "cover_duration_max", "1.0")))
        self.on_cover_changed(Qt.Checked if self.cover_check.isChecked() else Qt.Unchecked)

    def save_config(self):
        set_config("audio_mix", "media_folder", self.media_folder_input.text())
        set_config("audio_mix", "clips_folder", self.clips_folder_input.text())
        set_config("audio_mix", "output_folder", self.output_folder_input.text())
        set_config("audio_mix", "cover_enabled", str(self.cover_check.isChecked()).lower())
        set_config("audio_mix", "cover_folder", self.cover_folder_input.text())
        set_config("audio_mix", "cover_duration_min", str(self.cover_duration_min.value()))
        set_config("audio_mix", "cover_duration_max", str(self.cover_duration_max.value()))

    def on_cover_changed(self, state):
        enabled = state == Qt.Checked
        self.cover_folder_input.setEnabled(enabled)
        self.cover_folder_btn.setEnabled(enabled)
        self.cover_duration_min.setEnabled(enabled)
        self.cover_duration_max.setEnabled(enabled)

    def browse_media_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择音频/视频文件夹")
        if folder:
            self.media_folder_input.setText(folder)
            self.save_config()

    def browse_clips_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择切片视频文件夹")
        if folder:
            self.clips_folder_input.setText(folder)
            self.save_config()

    def browse_cover_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择封面图文件夹")
        if folder:
            self.cover_folder_input.setText(folder)
            self.save_config()

    def browse_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if folder:
            self.output_folder_input.setText(folder)
            self.save_config()

    def start_mixing(self):
        media_folder = self.media_folder_input.text()
        clips_folder = self.clips_folder_input.text()
        output_folder = self.output_folder_input.text()

        if not media_folder or not clips_folder or not output_folder:
            QMessageBox.warning(self, "警告", "请填写所有必填项")
            return

        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "警告", "任务正在执行中")
            return

        self.save_config()
        self.start_btn.setEnabled(False)

        config = {
            "media_dir": media_folder,
            "clips_dir": clips_folder,
            "output_dir": output_folder,
            "cover_enabled": self.cover_check.isChecked(),
            "cover_folder": self.cover_folder_input.text(),
            "cover_duration_min": self.cover_duration_min.value(),
            "cover_duration_max": self.cover_duration_max.value(),
        }

        self.worker = AudioMixWorker(config)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def on_progress(self, current, total):
        progress = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(progress)
        self.status_label.setText(f"正在处理 {current}/{total} 个媒体文件")

    def on_finished(self, results):
        self.start_btn.setEnabled(True)
        self.status_label.setText("混剪完成")
        QMessageBox.information(self, "完成", f"已完成 {len(results)} 个混剪视频")

    def on_error(self, msg):
        self.start_btn.setEnabled(True)
        self.status_label.setText("混剪失败")
        QMessageBox.critical(self, "错误", msg)
