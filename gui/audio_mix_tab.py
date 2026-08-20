from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QProgressBar,
    QMessageBox, QGroupBox, QCheckBox, QDoubleSpinBox
)
from PyQt5.QtCore import Qt, pyqtSignal
from core.mixer import VideoMixer
from gui.config import get_config, set_config
from gui.common.base_tab import BaseTab
from gui.common.base_worker import BaseWorker
from gui.common.path_row import PathRow, MODE_FOLDER
import os


class AudioMixWorker(BaseWorker):
    # progress/finished/error 继承 BaseWorker（progress(int,int,str)）

    def __init__(self, config):
        super().__init__()
        self.config = config
        self._paused = False

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def is_paused(self) -> bool:
        return self._paused

    def _wait_if_paused(self):
        while self._paused and not self.stopped():
            self.msleep(100)

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
                lambda count, total: self._on_mix_progress(count, total)
            )
        except InterruptedError:
            # 用户停止（_on_mix_progress 在 stopped 时抛 InterruptedError）：复位 UI，不报错误
            self.finished.emit([])
            return
        except Exception as e:
            if not self.stopped():
                self.error.emit(str(e))
            else:
                # 异常发生在停止过程中：仍复位 UI，避免卡死
                self.finished.emit([])
            return
        # 正常完成 / 停止后收尾：复位 UI（停止时返回空列表，避免误导"完成"弹窗）
        self.finished.emit([] if self.stopped() else results)

    def _on_mix_progress(self, count, total):
        self._wait_if_paused()
        if self.stopped():
            raise InterruptedError("用户停止")
        self.progress.emit(count, total, "")


class AudioMixTab(BaseTab):
    def __init__(self):
        super().__init__()
        self.init_ui()
        self.load_config()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        media_group = QGroupBox("音频/视频文件夹（支持音频和视频文件，自动遍历子文件夹）")
        media_layout = QVBoxLayout()

        self.media_folder_input = PathRow("选择包含音频或视频的文件夹...", mode=MODE_FOLDER,
                                          on_change=lambda p: self.save_config())
        media_layout.addWidget(self.media_folder_input)
        media_group.setLayout(media_layout)

        clips_group = QGroupBox("视频切片文件夹")
        clips_layout = QVBoxLayout()
        clips_layout.setSpacing(8)
        self.clips_folder_input = PathRow("选择切片视频文件夹...", mode=MODE_FOLDER,
                                          on_change=lambda p: self.save_config())
        clips_layout.addWidget(self.clips_folder_input)
        clips_group.setLayout(clips_layout)

        cover_group = QGroupBox("封面图设置")
        cover_layout = QVBoxLayout()

        self.cover_check = QCheckBox("启用封面图")
        self.cover_check.setMinimumHeight(26)
        self.cover_check.setChecked(False)
        self.cover_check.stateChanged.connect(self.on_cover_changed)
        cover_layout.addWidget(self.cover_check)

        cover_folder_row = QHBoxLayout()
        cover_folder_row.setSpacing(8)
        cover_folder_row.addWidget(QLabel("封面图文件夹:"))
        self.cover_folder_input = PathRow("选择封面图文件夹...", mode=MODE_FOLDER,
                                          on_change=lambda p: self.save_config())
        self.cover_folder_input.setEnabled(False)
        self.cover_folder_btn = self.cover_folder_input.browse_btn
        self.cover_folder_btn.setEnabled(False)
        cover_folder_row.addWidget(self.cover_folder_input, 1)
        cover_layout.addLayout(cover_folder_row)

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
        output_layout = QVBoxLayout()
        output_layout.setSpacing(8)
        self.output_folder_input = PathRow("选择输出文件夹...", mode=MODE_FOLDER,
                                           on_change=lambda p: self.save_config())
        output_layout.addWidget(self.output_folder_input)
        output_group.setLayout(output_layout)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始混剪")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self.start_mixing)

        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setMinimumHeight(36)
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self.toggle_pause)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setMinimumHeight(36)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_mixing)

        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.pause_btn)
        btn_row.addWidget(self.stop_btn)

        self.progress_bar = QProgressBar()
        self.status_label = QLabel("就绪")

        layout.addWidget(media_group)
        layout.addWidget(clips_group)
        layout.addWidget(cover_group)
        layout.addWidget(output_group)
        layout.addLayout(btn_row)
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
        self.media_folder_input._browse()

    def browse_clips_folder(self):
        self.clips_folder_input._browse()

    def browse_cover_folder(self):
        self.cover_folder_input._browse()

    def browse_output_folder(self):
        self.output_folder_input._browse()

    def start_mixing(self):
        media_folder = self.media_folder_input.text()
        clips_folder = self.clips_folder_input.text()
        output_folder = self.output_folder_input.text()

        if not media_folder or not clips_folder or not output_folder:
            QMessageBox.warning(self, "警告", "请填写所有必填项")
            return

        self.save_config()

        config = {
            "media_dir": media_folder,
            "clips_dir": clips_folder,
            "output_dir": output_folder,
            "cover_enabled": self.cover_check.isChecked(),
            "cover_folder": self.cover_folder_input.text(),
            "cover_duration_min": self.cover_duration_min.value(),
            "cover_duration_max": self.cover_duration_max.value(),
        }

        worker = AudioMixWorker(config)
        if not self.start_worker(worker):
            return

    def set_busy(self, busy):
        self.start_btn.setEnabled(not busy)
        self.pause_btn.setEnabled(busy)
        self.pause_btn.setText("暂停")
        self.stop_btn.setEnabled(busy)

    def toggle_pause(self):
        if not self.worker:
            return
        if self.worker.is_paused():
            self.worker.resume()
            self.pause_btn.setText("暂停")
            self.status_label.setText("继续处理中...")
        else:
            self.worker.pause()
            self.pause_btn.setText("继续")
            self.status_label.setText("已暂停")

    def stop_mixing(self):
        if self.worker:
            self.worker.stop()
            self.status_label.setText("正在停止...")
            self.stop_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)

    def on_worker_progress(self, current, total, message):
        progress = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(progress)
        self.status_label.setText(f"正在处理 {current}/{total} 个媒体文件")

    def on_worker_finished(self, results):
        super().on_worker_finished(results)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("混剪完成")
        QMessageBox.information(self, "完成", f"已完成 {len(results)} 个混剪视频")

    def on_worker_error(self, msg):
        super().on_worker_error(msg)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("混剪失败")
