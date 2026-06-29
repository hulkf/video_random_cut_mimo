from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QCheckBox, QDoubleSpinBox, QComboBox,
    QScrollArea
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from core.video_concatenator import VideoConcatenatorEngine
from gui.config import get_config, set_config


class VideoConcatWorker(QThread):
    progress = pyqtSignal(int, int, str, int)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        try:
            engine = VideoConcatenatorEngine(self.config)
            results = engine.run(
                lambda cur, total, msg, sub: self.progress.emit(cur, total, msg, sub)
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class VideoConcatTab(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
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
        folder_a_layout = QHBoxLayout()
        folder_a_layout.setSpacing(8)
        self.folder_a_input = QLineEdit()
        self.folder_a_input.setPlaceholderText("选择文件夹A...")
        self.folder_a_input.setMinimumHeight(30)
        folder_a_btn = QPushButton("浏览")
        folder_a_btn.setFixedWidth(80)
        folder_a_btn.clicked.connect(self.browse_folder_a)
        folder_a_layout.addWidget(self.folder_a_input, 1)
        folder_a_layout.addWidget(folder_a_btn)
        folder_a_group.setLayout(folder_a_layout)

        folder_b_group = QGroupBox("文件夹B（第二批视频）")
        folder_b_layout = QHBoxLayout()
        folder_b_layout.setSpacing(8)
        self.folder_b_input = QLineEdit()
        self.folder_b_input.setPlaceholderText("选择文件夹B...")
        self.folder_b_input.setMinimumHeight(30)
        folder_b_btn = QPushButton("浏览")
        folder_b_btn.setFixedWidth(80)
        folder_b_btn.clicked.connect(self.browse_folder_b)
        folder_b_layout.addWidget(self.folder_b_input, 1)
        folder_b_layout.addWidget(folder_b_btn)
        folder_b_group.setLayout(folder_b_layout)

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
        self.cover_folder_input = QLineEdit()
        self.cover_folder_input.setPlaceholderText("选择封面图文件夹...")
        self.cover_folder_input.setEnabled(False)
        self.cover_folder_input.setMinimumHeight(30)
        cover_folder_btn = QPushButton("浏览")
        cover_folder_btn.setFixedWidth(80)
        cover_folder_btn.clicked.connect(self.browse_cover_folder)
        cover_folder_btn.setEnabled(False)
        self.cover_folder_btn = cover_folder_btn
        cover_folder_row.addWidget(self.cover_folder_input, 1)
        cover_folder_row.addWidget(cover_folder_btn)
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
            "5. 封面图无音频，时长可设置区间随机"
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
        self.cover_folder_input.setText(get_config("video_concat", "cover_folder", ""))
        self.cover_mode_combo.setCurrentIndex(int(get_config("video_concat", "cover_mode", "0")))
        self.cover_duration_min.setValue(float(get_config("video_concat", "cover_duration_min", "0.5")))
        self.cover_duration_max.setValue(float(get_config("video_concat", "cover_duration_max", "1.0")))
        self.on_cover_changed(Qt.Checked if self.cover_check.isChecked() else Qt.Unchecked)

    def save_config(self):
        set_config("video_concat", "folder_a", self.folder_a_input.text())
        set_config("video_concat", "folder_b", self.folder_b_input.text())
        set_config("video_concat", "output_folder", self.output_folder_input.text())
        set_config("video_concat", "cover_enabled", str(self.cover_check.isChecked()).lower())
        set_config("video_concat", "cover_folder", self.cover_folder_input.text())
        set_config("video_concat", "cover_mode", str(self.cover_mode_combo.currentIndex()))
        set_config("video_concat", "cover_duration_min", str(self.cover_duration_min.value()))
        set_config("video_concat", "cover_duration_max", str(self.cover_duration_max.value()))

    def on_cover_changed(self, state):
        enabled = state == Qt.Checked
        self.cover_folder_input.setEnabled(enabled)
        self.cover_folder_btn.setEnabled(enabled)
        self.cover_mode_combo.setEnabled(enabled)
        self.cover_duration_min.setEnabled(enabled)
        self.cover_duration_max.setEnabled(enabled)

    def browse_folder_a(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹A")
        if folder:
            self.folder_a_input.setText(folder)
            self.save_config()

    def browse_folder_b(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹B")
        if folder:
            self.folder_b_input.setText(folder)
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

    def start_concat(self):
        folder_a = self.folder_a_input.text()
        folder_b = self.folder_b_input.text()
        output_folder = self.output_folder_input.text()

        if not folder_a or not folder_b or not output_folder:
            QMessageBox.warning(self, "警告", "请填写所有必填项")
            return

        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "警告", "任务正在执行中")
            return

        self.save_config()

        config = {
            "folder_a": folder_a,
            "folder_b": folder_b,
            "output_folder": output_folder,
            "cover_enabled": self.cover_check.isChecked(),
            "cover_folder": self.cover_folder_input.text(),
            "cover_mode": self.cover_mode_combo.currentIndex(),
            "cover_duration_min": self.cover_duration_min.value(),
            "cover_duration_max": self.cover_duration_max.value()
        }

        self.start_btn.setEnabled(False)
        self.worker = VideoConcatWorker(config)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def on_progress(self, current, total, msg, sub_progress):
        global_progress = int((current / total) * 100) if total > 0 else 0
        self.global_progress_bar.setValue(global_progress)
        self.global_progress_label.setText(f"{global_progress}%")

        if sub_progress >= 0:
            self.task_progress_bar.setValue(sub_progress)
            self.task_progress_label.setText(f"{sub_progress}%")

        self.status_label.setText(f"进度 {current}/{total} - {msg}")

    def on_finished(self, results):
        self.start_btn.setEnabled(True)
        self.global_progress_bar.setValue(100)
        self.global_progress_label.setText("100%")
        self.task_progress_bar.setValue(100)
        self.task_progress_label.setText("100%")
        self.status_label.setText("拼接完成")
        QMessageBox.information(self, "完成", f"已完成 {len(results)} 个拼接视频")

    def on_error(self, msg):
        self.start_btn.setEnabled(True)
        self.status_label.setText("拼接失败")
        QMessageBox.critical(self, "错误", msg)
