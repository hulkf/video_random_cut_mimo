from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QProgressBar,
    QMessageBox, QGroupBox, QCheckBox, QDoubleSpinBox, QComboBox,
    QScrollArea
)
from PyQt5.QtCore import Qt, pyqtSignal
from core.video_concatenator import VideoConcatenatorEngine
from gui.config import get_config, set_config
from utils.path_utils import normalize_path as normalize_input_path
from gui.common.base_tab import BaseTab
from gui.common.base_worker import BaseWorker
from gui.common.path_row import PathRow, MODE_FOLDER


class VideoConcatWorker(BaseWorker):
    # progress/finished/error 继承 BaseWorker（progress(int,int,str)）
    sub_progress = pyqtSignal(int)  # 单个任务的子进度（0-100）

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        try:
            engine = VideoConcatenatorEngine(self.config)
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


class VideoConcatTab(BaseTab):
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

        folder_a_group = QGroupBox("文件夹A（第一批视频）")
        folder_a_layout = QVBoxLayout()
        folder_a_layout.setSpacing(8)
        self.folder_a_input = PathRow("选择文件夹A...", mode=MODE_FOLDER,
                                      on_change=lambda p: self.save_config())
        folder_a_layout.addWidget(self.folder_a_input)
        folder_a_group.setLayout(folder_a_layout)

        folder_b_group = QGroupBox("文件夹B（第二批视频）")
        folder_b_layout = QVBoxLayout()
        folder_b_layout.setSpacing(8)
        self.folder_b_input = PathRow("选择文件夹B...", mode=MODE_FOLDER,
                                      on_change=lambda p: self.save_config())
        folder_b_layout.addWidget(self.folder_b_input)
        folder_b_group.setLayout(folder_b_layout)

        cover_group = QGroupBox("封面图设置")
        cover_layout = QVBoxLayout()

        self.cover_check = QCheckBox("启用封面图")
        self.cover_check.setMinimumHeight(26)
        self.cover_check.setChecked(False)
        self.cover_check.stateChanged.connect(self.on_cover_changed)
        cover_layout.addWidget(self.cover_check)

        cover_source_row = QHBoxLayout()
        cover_source_row.addWidget(QLabel("封面来源:"))
        self.cover_source_combo = QComboBox()
        self.cover_source_combo.addItem("封面图文件夹随机选图", "folder")
        self.cover_source_combo.addItem("从文件夹B当前视频抽帧", "video_b_frame")
        self.cover_source_combo.setEnabled(False)
        self.cover_source_combo.setMinimumHeight(28)
        self.cover_source_combo.currentIndexChanged.connect(self.on_cover_changed)
        cover_source_row.addWidget(self.cover_source_combo)
        cover_source_row.addStretch()
        cover_layout.addLayout(cover_source_row)

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

        cover_mode_row = QHBoxLayout()
        cover_mode_row.addWidget(QLabel("封面位置:"))
        self.cover_mode_combo = QComboBox()
        self.cover_mode_combo.addItems(["开头", "结尾", "首尾都加"])
        self.cover_mode_combo.setEnabled(False)
        self.cover_mode_combo.setMinimumHeight(28)
        cover_mode_row.addWidget(self.cover_mode_combo)
        cover_mode_row.addStretch()
        cover_layout.addLayout(cover_mode_row)

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

        output_group = QGroupBox("输出设置")
        output_layout = QVBoxLayout()
        output_layout.setSpacing(8)
        self.output_folder_input = PathRow("选择输出文件夹...", mode=MODE_FOLDER,
                                           on_change=lambda p: self.save_config())
        output_layout.addWidget(self.output_folder_input)
        output_group.setLayout(output_layout)

        self.start_btn = QPushButton("开始拼接")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self.start_concat)

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
            "拼接逻辑说明：\n"
            "1. 从文件夹A和文件夹B各取一个视频进行拼接\n"
            "2. A和B视频按文件名排序后依次配对（A1+B1, A2+B2, ...）\n"
            "3. 如果两个文件夹视频数量不同，较少的文件夹会循环使用\n"
            "4. 启用封面图时，可选择在拼接视频的开头/结尾/首尾添加图片\n"
            "5. 封面图可来自图片文件夹，也可从当前配对的文件夹B视频抽帧\n"
            "6. 封面图无音频，时长可设置区间随机"
        )
        desc_label.setStyleSheet("color: gray; padding: 5px;")
        desc_label.setWordWrap(True)

        layout.addWidget(folder_a_group)
        layout.addWidget(folder_b_group)
        layout.addWidget(cover_group)
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
        self.folder_a_input.setText(get_config("video_concat", "folder_a", ""))
        self.folder_b_input.setText(get_config("video_concat", "folder_b", ""))
        self.output_folder_input.setText(get_config("video_concat", "output_folder", ""))
        self.cover_check.setChecked(get_config("video_concat", "cover_enabled", "false") == "true")
        cover_source = get_config("video_concat", "cover_source", "folder")
        cover_source_index = self.cover_source_combo.findData(cover_source)
        self.cover_source_combo.setCurrentIndex(cover_source_index if cover_source_index >= 0 else 0)
        self.cover_folder_input.setText(get_config("video_concat", "cover_folder", ""))
        self.cover_mode_combo.setCurrentIndex(int(get_config("video_concat", "cover_mode", "0")))
        self.cover_duration_min.setValue(float(get_config("video_concat", "cover_duration_min", "0.5")))
        self.cover_duration_max.setValue(float(get_config("video_concat", "cover_duration_max", "1.0")))
        self.on_cover_changed(Qt.Checked if self.cover_check.isChecked() else Qt.Unchecked)

    def save_config(self):
        set_config("video_concat", "folder_a", normalize_input_path(self.folder_a_input.text()))
        set_config("video_concat", "folder_b", normalize_input_path(self.folder_b_input.text()))
        set_config("video_concat", "output_folder", normalize_input_path(self.output_folder_input.text()))
        set_config("video_concat", "cover_enabled", str(self.cover_check.isChecked()).lower())
        set_config("video_concat", "cover_source", self.cover_source_combo.currentData())
        set_config("video_concat", "cover_folder", normalize_input_path(self.cover_folder_input.text()))
        set_config("video_concat", "cover_mode", str(self.cover_mode_combo.currentIndex()))
        set_config("video_concat", "cover_duration_min", str(self.cover_duration_min.value()))
        set_config("video_concat", "cover_duration_max", str(self.cover_duration_max.value()))

    def on_cover_changed(self, state):
        enabled = self.cover_check.isChecked()
        folder_enabled = enabled and self.cover_source_combo.currentData() == "folder"
        self.cover_source_combo.setEnabled(enabled)
        self.cover_folder_input.setEnabled(folder_enabled)
        self.cover_folder_btn.setEnabled(folder_enabled)
        self.cover_mode_combo.setEnabled(enabled)
        self.cover_duration_min.setEnabled(enabled)
        self.cover_duration_max.setEnabled(enabled)

    def browse_folder_a(self):
        self.folder_a_input._browse()

    def browse_folder_b(self):
        self.folder_b_input._browse()

    def browse_cover_folder(self):
        self.cover_folder_input._browse()

    def browse_output_folder(self):
        self.output_folder_input._browse()

    def start_concat(self):
        folder_a = normalize_input_path(self.folder_a_input.text())
        folder_b = normalize_input_path(self.folder_b_input.text())
        output_folder = normalize_input_path(self.output_folder_input.text())

        if not folder_a or not folder_b or not output_folder:
            QMessageBox.warning(self, "警告", "请填写所有必填项")
            return

        self.save_config()

        config = {
            "folder_a": folder_a,
            "folder_b": folder_b,
            "output_folder": output_folder,
            "cover_enabled": self.cover_check.isChecked(),
            "cover_source": self.cover_source_combo.currentData(),
            "cover_folder": normalize_input_path(self.cover_folder_input.text()),
            "cover_mode": self.cover_mode_combo.currentIndex(),
            "cover_duration_min": self.cover_duration_min.value(),
            "cover_duration_max": self.cover_duration_max.value()
        }

        worker = VideoConcatWorker(config)
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
        self.status_label.setText("拼接完成")
        QMessageBox.information(self, "完成", f"已完成 {len(results)} 个拼接视频")

    def on_worker_error(self, msg):
        super().on_worker_error(msg)
        self.status_label.setText("拼接失败")
