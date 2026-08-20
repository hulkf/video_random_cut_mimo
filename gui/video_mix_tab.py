from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QProgressBar,
    QMessageBox, QGroupBox, QCheckBox, QSpinBox, QDoubleSpinBox, QComboBox,
    QScrollArea
)
from PyQt5.QtCore import Qt, pyqtSignal
from core.video_mixer import VideoMixerEngine
from gui.config import get_config, set_config
from gui.common.base_tab import BaseTab
from gui.common.base_worker import BaseWorker
from gui.common.path_row import PathRow, MODE_FOLDER
import os


class VideoMixWorker(BaseWorker):
    # progress/finished/error 继承 BaseWorker（progress(int,int,str)）
    sub_progress = pyqtSignal(int)  # 单个任务的子进度（0-100）

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        try:
            engine = VideoMixerEngine(self.config)
            results = engine.run(self._on_progress)
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))

    def _on_progress(self, cur, total, msg, sub):
        """引擎进度回调（worker 线程内执行）：轮询停止标志。"""
        if self.stopped():
            raise InterruptedError("用户停止")
        self.progress.emit(cur, total, msg)
        self.sub_progress.emit(sub)


class VideoMixTab(BaseTab):
    def __init__(self):
        super().__init__()
        self.init_ui()
        self.load_config()

    def init_ui(self):
        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        video_group = QGroupBox("基底视频文件夹")
        video_layout = QVBoxLayout()
        video_layout.setSpacing(8)
        self.video_folder_input = PathRow("选择基底视频文件夹...", mode=MODE_FOLDER,
                                          on_change=lambda p: self.save_config())
        video_layout.addWidget(self.video_folder_input)
        video_group.setLayout(video_layout)

        head_tail_group = QGroupBox("首尾保留")
        head_tail_layout = QVBoxLayout()

        self.head_tail_check = QCheckBox("启用首尾保留")
        self.head_tail_check.setMinimumHeight(26)
        self.head_tail_check.setChecked(True)
        self.head_tail_check.stateChanged.connect(self.on_head_tail_changed)

        head_row = QHBoxLayout()
        head_row.addWidget(QLabel("头部时长(秒):"))
        self.head_min = QSpinBox()
        self.head_min.setRange(1, 30)
        self.head_min.setValue(3)
        self.head_min.setMinimumHeight(28)
        head_row.addWidget(self.head_min)
        head_row.addWidget(QLabel("~"))
        self.head_max = QSpinBox()
        self.head_max.setRange(1, 30)
        self.head_max.setValue(5)
        self.head_max.setMinimumHeight(28)
        head_row.addWidget(self.head_max)

        tail_row = QHBoxLayout()
        tail_row.addWidget(QLabel("尾部时长(秒):"))
        self.tail_min = QSpinBox()
        self.tail_min.setRange(1, 30)
        self.tail_min.setValue(3)
        self.tail_min.setMinimumHeight(28)
        tail_row.addWidget(self.tail_min)
        tail_row.addWidget(QLabel("~"))
        self.tail_max = QSpinBox()
        self.tail_max.setRange(1, 30)
        self.tail_max.setValue(5)
        self.tail_max.setMinimumHeight(28)
        tail_row.addWidget(self.tail_max)

        head_tail_layout.addWidget(self.head_tail_check)
        head_tail_layout.addLayout(head_row)
        head_tail_layout.addLayout(tail_row)
        head_tail_group.setLayout(head_tail_layout)

        slice_group = QGroupBox("切片设置")
        slice_layout = QVBoxLayout()

        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("切片数量:"))
        self.slice_count_min = QSpinBox()
        self.slice_count_min.setRange(1, 20)
        self.slice_count_min.setValue(3)
        self.slice_count_min.setMinimumHeight(28)
        count_row.addWidget(self.slice_count_min)
        count_row.addWidget(QLabel("~"))
        self.slice_count_max = QSpinBox()
        self.slice_count_max.setRange(1, 20)
        self.slice_count_max.setValue(5)
        self.slice_count_max.setMinimumHeight(28)
        count_row.addWidget(self.slice_count_max)

        dur_row = QHBoxLayout()
        dur_row.addWidget(QLabel("切片时长(秒):"))
        self.slice_duration_min = QSpinBox()
        self.slice_duration_min.setRange(1, 30)
        self.slice_duration_min.setValue(3)
        self.slice_duration_min.setMinimumHeight(28)
        dur_row.addWidget(self.slice_duration_min)
        dur_row.addWidget(QLabel("~"))
        self.slice_duration_max = QSpinBox()
        self.slice_duration_max.setRange(1, 30)
        self.slice_duration_max.setValue(5)
        self.slice_duration_max.setMinimumHeight(28)
        dur_row.addWidget(self.slice_duration_max)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("间隔模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["均衡", "随机"])
        self.mode_combo.setMinimumHeight(28)
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch()

        slice_layout.addLayout(count_row)
        slice_layout.addLayout(dur_row)
        slice_layout.addLayout(mode_row)
        slice_group.setLayout(slice_layout)

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

        cover_dur_row = QHBoxLayout()
        cover_dur_row.addWidget(QLabel("封面时长(秒):"))
        self.cover_duration_min = QDoubleSpinBox()
        self.cover_duration_min.setRange(0.1, 10.0)
        self.cover_duration_min.setValue(0.5)
        self.cover_duration_min.setSingleStep(0.1)
        self.cover_duration_min.setDecimals(1)
        self.cover_duration_min.setMinimumHeight(28)
        self.cover_duration_min.setEnabled(False)
        cover_dur_row.addWidget(self.cover_duration_min)
        cover_dur_row.addWidget(QLabel("~"))
        self.cover_duration_max = QDoubleSpinBox()
        self.cover_duration_max.setRange(0.1, 10.0)
        self.cover_duration_max.setValue(1.0)
        self.cover_duration_max.setSingleStep(0.1)
        self.cover_duration_max.setDecimals(1)
        self.cover_duration_max.setMinimumHeight(28)
        self.cover_duration_max.setEnabled(False)
        cover_dur_row.addWidget(self.cover_duration_max)
        cover_layout.addLayout(cover_dur_row)

        cover_group.setLayout(cover_layout)

        clips_group = QGroupBox("视频切片文件夹")
        clips_layout = QVBoxLayout()
        clips_layout.setSpacing(8)
        self.clips_folder_input = PathRow("选择切片视频文件夹...", mode=MODE_FOLDER,
                                          on_change=lambda p: self.save_config())
        clips_layout.addWidget(self.clips_folder_input)
        clips_group.setLayout(clips_layout)

        output_group = QGroupBox("输出设置")
        output_layout = QVBoxLayout()

        output_folder_row = QHBoxLayout()
        output_folder_row.setSpacing(8)
        self.output_folder_input = PathRow("选择输出文件夹...", mode=MODE_FOLDER,
                                           on_change=lambda p: self.save_config())
        output_folder_row.addWidget(self.output_folder_input, 1)

        mix_count_row = QHBoxLayout()
        mix_count_row.addWidget(QLabel("单条视频混剪数量:"))
        self.mix_count = QSpinBox()
        self.mix_count.setRange(1, 100)
        self.mix_count.setValue(1)
        self.mix_count.setMinimumHeight(28)
        mix_count_row.addWidget(self.mix_count)
        mix_count_row.addStretch()

        output_layout.addLayout(output_folder_row)
        output_layout.addLayout(mix_count_row)
        output_group.setLayout(output_layout)

        self.start_btn = QPushButton("开始混剪")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self.start_mixing)

        progress_group = QGroupBox("处理进度")
        progress_layout = QVBoxLayout()

        global_row = QHBoxLayout()
        global_row.addWidget(QLabel("全局进度:"))
        self.global_progress_bar = QProgressBar()
        self.global_progress_bar.setRange(0, 100)
        global_row.addWidget(self.global_progress_bar)
        self.global_progress_label = QLabel("0%")
        global_row.addWidget(self.global_progress_label)
        progress_layout.addLayout(global_row)

        task_row = QHBoxLayout()
        task_row.addWidget(QLabel("当前任务:"))
        self.task_progress_bar = QProgressBar()
        self.task_progress_bar.setRange(0, 100)
        task_row.addWidget(self.task_progress_bar)
        self.task_progress_label = QLabel("0%")
        task_row.addWidget(self.task_progress_label)
        progress_layout.addLayout(task_row)

        progress_group.setLayout(progress_layout)

        self.status_label = QLabel("就绪")

        desc_label = QLabel(
            "混剪逻辑说明：\n"
            "1. 基底视频提供时间轴和音频\n"
            "2. 启用封面图时，使用随机图片作为视频开头（无音频，可选时长）\n"
            "3. 启用首尾保留时，基底视频的开头和结尾固定保留\n"
            "4. 中间部分按切片数量和时长区间截取片段\n"
            "5. 其余空位由切片视频随机填充\n"
            "6. 切片视频不使用原始音频，使用基底视频时间轴上的音频"
        )
        desc_label.setStyleSheet("color: gray; padding: 5px;")
        desc_label.setWordWrap(True)

        layout.addWidget(video_group)
        layout.addWidget(head_tail_group)
        layout.addWidget(slice_group)
        layout.addWidget(cover_group)
        layout.addWidget(clips_group)
        layout.addWidget(output_group)
        layout.addWidget(self.start_btn)
        layout.addWidget(progress_group)
        layout.addWidget(self.status_label)
        layout.addWidget(desc_label)
        layout.addStretch()

        container.setLayout(layout)
        scroll.setWidget(container)
        outer_layout.addWidget(scroll)
        self.setLayout(outer_layout)
    
    def load_config(self):
        self.video_folder_input.setText(get_config("video_mix", "video_folder", ""))
        self.clips_folder_input.setText(get_config("video_mix", "clips_folder", ""))
        self.output_folder_input.setText(get_config("video_mix", "output_folder", ""))
        self.head_tail_check.setChecked(get_config("video_mix", "head_tail", "1") == "1")
        self.head_min.setValue(int(get_config("video_mix", "head_min", "3")))
        self.head_max.setValue(int(get_config("video_mix", "head_max", "5")))
        self.tail_min.setValue(int(get_config("video_mix", "tail_min", "3")))
        self.tail_max.setValue(int(get_config("video_mix", "tail_max", "5")))
        self.slice_count_min.setValue(int(get_config("video_mix", "slice_count_min", "3")))
        self.slice_count_max.setValue(int(get_config("video_mix", "slice_count_max", "5")))
        self.slice_duration_min.setValue(int(get_config("video_mix", "slice_duration_min", "3")))
        self.slice_duration_max.setValue(int(get_config("video_mix", "slice_duration_max", "5")))
        self.mode_combo.setCurrentIndex(int(get_config("video_mix", "mode", "0")))
        self.mix_count.setValue(int(get_config("video_mix", "mix_count", "1")))
        self.cover_check.setChecked(get_config("video_mix", "cover_enabled", "false") == "true")
        self.cover_folder_input.setText(get_config("video_mix", "cover_folder", ""))
        self.cover_duration_min.setValue(float(get_config("video_mix", "cover_duration_min", "0.5")))
        self.cover_duration_max.setValue(float(get_config("video_mix", "cover_duration_max", "1.0")))
        self.on_cover_changed(Qt.Checked if self.cover_check.isChecked() else Qt.Unchecked)
    
    def save_config(self):
        set_config("video_mix", "video_folder", self.video_folder_input.text())
        set_config("video_mix", "clips_folder", self.clips_folder_input.text())
        set_config("video_mix", "output_folder", self.output_folder_input.text())
        set_config("video_mix", "head_tail", "1" if self.head_tail_check.isChecked() else "0")
        set_config("video_mix", "head_min", str(self.head_min.value()))
        set_config("video_mix", "head_max", str(self.head_max.value()))
        set_config("video_mix", "tail_min", str(self.tail_min.value()))
        set_config("video_mix", "tail_max", str(self.tail_max.value()))
        set_config("video_mix", "slice_count_min", str(self.slice_count_min.value()))
        set_config("video_mix", "slice_count_max", str(self.slice_count_max.value()))
        set_config("video_mix", "slice_duration_min", str(self.slice_duration_min.value()))
        set_config("video_mix", "slice_duration_max", str(self.slice_duration_max.value()))
        set_config("video_mix", "mode", str(self.mode_combo.currentIndex()))
        set_config("video_mix", "mix_count", str(self.mix_count.value()))
        set_config("video_mix", "cover_enabled", str(self.cover_check.isChecked()).lower())
        set_config("video_mix", "cover_folder", self.cover_folder_input.text())
        set_config("video_mix", "cover_duration_min", str(self.cover_duration_min.value()))
        set_config("video_mix", "cover_duration_max", str(self.cover_duration_max.value()))
    
    def on_head_tail_changed(self, state):
        enabled = state == Qt.Checked
        self.head_min.setEnabled(enabled)
        self.head_max.setEnabled(enabled)
        self.tail_min.setEnabled(enabled)
        self.tail_max.setEnabled(enabled)
    
    def on_cover_changed(self, state):
        enabled = state == Qt.Checked
        self.cover_folder_input.setEnabled(enabled)
        self.cover_folder_btn.setEnabled(enabled)
        self.cover_duration_min.setEnabled(enabled)
        self.cover_duration_max.setEnabled(enabled)
    
    def browse_cover_folder(self):
        self.cover_folder_input._browse()
    
    def browse_video_folder(self):
        self.video_folder_input._browse()
    
    def browse_clips_folder(self):
        self.clips_folder_input._browse()
    
    def browse_output_folder(self):
        self.output_folder_input._browse()
    
    def start_mixing(self):
        video_folder = self.video_folder_input.text()
        clips_folder = self.clips_folder_input.text()
        output_folder = self.output_folder_input.text()
        
        if not video_folder or not clips_folder or not output_folder:
            QMessageBox.warning(self, "警告", "请填写所有必填项")
            return
        
        self.save_config()
        
        config = {
            "video_folder": video_folder,
            "clips_folder": clips_folder,
            "output_folder": output_folder,
            "head_tail": self.head_tail_check.isChecked(),
            "head_min": self.head_min.value(),
            "head_max": self.head_max.value(),
            "tail_min": self.tail_min.value(),
            "tail_max": self.tail_max.value(),
            "slice_count_min": self.slice_count_min.value(),
            "slice_count_max": self.slice_count_max.value(),
            "slice_duration_min": self.slice_duration_min.value(),
            "slice_duration_max": self.slice_duration_max.value(),
            "mode": self.mode_combo.currentIndex(),
            "mix_count": self.mix_count.value(),
            "cover_enabled": self.cover_check.isChecked(),
            "cover_folder": self.cover_folder_input.text(),
            "cover_duration_min": self.cover_duration_min.value(),
            "cover_duration_max": self.cover_duration_max.value()
        }
        
        worker = VideoMixWorker(config)
        worker.sub_progress.connect(self.on_sub_progress)
        if not self.start_worker(worker):
            return

    def set_busy(self, busy):
        self.start_btn.setEnabled(not busy)

    def on_worker_progress(self, current, total, message):
        global_progress = int((current / total) * 100) if total > 0 else 0
        self.global_progress_bar.setValue(global_progress)
        self.global_progress_label.setText(f"{global_progress}%")
        self.status_label.setText(f"进度 {current}/{total} - {message}")

    def on_sub_progress(self, sub_progress):
        self.task_progress_bar.setValue(sub_progress)
        self.task_progress_label.setText(f"{sub_progress}%")
    
    def on_worker_finished(self, results):
        super().on_worker_finished(results)
        self.global_progress_bar.setValue(100)
        self.global_progress_label.setText("100%")
        self.task_progress_bar.setValue(100)
        self.task_progress_label.setText("100%")
        self.status_label.setText("混剪完成")
        QMessageBox.information(self, "完成", f"已完成 {len(results)} 个混剪视频")
    
    def on_worker_error(self, msg):
        super().on_worker_error(msg)
        self.status_label.setText("混剪失败")
