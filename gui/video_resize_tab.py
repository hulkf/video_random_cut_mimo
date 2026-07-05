import os

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QRadioButton, QButtonGroup
)
from PyQt5.QtCore import QThread, pyqtSignal

from core.video_resizer import (
    VideoResizer, collect_videos, matches_target_size,
    PIPELINE_9X16_TO_3X4_TO_9X16, SIZE_PRESETS
)
from gui.config import get_config, set_config


class VideoResizeWorker(QThread):
    progress = pyqtSignal(int, int, str)
    video_done = pyqtSignal(dict)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, input_folder, output_folder, target_ratio, process_mode):
        super().__init__()
        self.input_folder = input_folder
        self.output_folder = output_folder
        self.target_ratio = target_ratio
        self.process_mode = process_mode

    def run(self):
        try:
            videos = collect_videos(self.input_folder)
            if len(videos) == 0:
                raise ValueError("输入文件夹中没有找到视频文件")

            engine = VideoResizer(self.target_ratio)
            if self.process_mode == "mismatched":
                videos = [
                    video_path for video_path in videos
                    if not matches_target_size(video_path, self.target_ratio)
                ]

            total = len(videos)
            results = []

            if total == 0:
                self.finished.emit(results)
                return

            for index, video_path in enumerate(videos):
                rel_path = engine_target_rel_path(video_path, self.input_folder, self.target_ratio)
                output_path = os.path.join(self.output_folder, rel_path)

                self.progress.emit(index, total, rel_path)
                result_path = engine.resize_video(video_path, output_path)
                result = {
                    "input": video_path,
                    "output": result_path,
                    "ratio": self.target_ratio,
                }
                results.append(result)
                self.video_done.emit(result)
                self.progress.emit(index + 1, total, rel_path)

            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


def engine_target_rel_path(video_path, input_folder, target_ratio):
    rel_path = os.path.relpath(video_path, input_folder)
    rel_base, _ = os.path.splitext(rel_path)
    if target_ratio == PIPELINE_9X16_TO_3X4_TO_9X16:
        return f"{rel_base}_9x16_to_3x4_to_9x16.mp4"
    return f"{rel_base}_{target_ratio.replace(':', 'x')}.mp4"


class VideoResizeTab(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.init_ui()
        self.load_config()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        input_group = QGroupBox("输入设置")
        input_layout = QVBoxLayout()
        input_layout.setSpacing(8)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self.input_folder = QLineEdit()
        self.input_folder.setMinimumHeight(30)
        self.input_folder.setPlaceholderText("选择需要处理的视频文件夹...")
        input_btn = QPushButton("浏览")
        input_btn.setFixedWidth(80)
        input_btn.clicked.connect(self.browse_input)
        folder_row.addWidget(self.input_folder, 1)
        folder_row.addWidget(input_btn)
        input_layout.addLayout(folder_row)

        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        self.output_folder = QLineEdit()
        self.output_folder.setMinimumHeight(30)
        self.output_folder.setPlaceholderText("选择输出文件夹...")
        output_btn = QPushButton("浏览")
        output_btn.setFixedWidth(80)
        output_btn.clicked.connect(self.browse_output)
        output_row.addWidget(self.output_folder, 1)
        output_row.addWidget(output_btn)
        input_layout.addLayout(output_row)
        input_group.setLayout(input_layout)

        size_group = QGroupBox("尺寸设置")
        size_layout = QHBoxLayout()
        size_layout.setSpacing(8)
        size_layout.addWidget(QLabel("目标比例:"))
        self.ratio_group = QButtonGroup(self)
        self.ratio_buttons = {}
        for ratio in ["9:16", "3:4", "1:1", PIPELINE_9X16_TO_3X4_TO_9X16]:
            radio = QRadioButton(ratio)
            radio.setMinimumHeight(28)
            radio.toggled.connect(self.on_ratio_toggled)
            self.ratio_group.addButton(radio)
            self.ratio_buttons[ratio] = radio
            size_layout.addWidget(radio)
        self.size_label = QLabel("")
        self.size_label.setStyleSheet("color: gray;")
        size_layout.addWidget(self.size_label)
        size_layout.addStretch()
        size_group.setLayout(size_layout)

        mode_group = QGroupBox("处理范围")
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(8)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        mode_options = [
            ("all", "处理所有视频"),
            ("mismatched", "仅处理不符合目标尺寸的视频"),
        ]
        for key, text in mode_options:
            radio = QRadioButton(text)
            radio.setMinimumHeight(28)
            self.mode_group.addButton(radio)
            self.mode_buttons[key] = radio
            mode_layout.addWidget(radio)
        mode_layout.addStretch()
        mode_group.setLayout(mode_layout)

        self.start_btn = QPushButton("开始处理")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self.start_resize)

        progress_group = QGroupBox("处理进度")
        progress_layout = QVBoxLayout()
        progress_row = QHBoxLayout()
        progress_row.addWidget(QLabel("进度:"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        progress_row.addWidget(self.progress_bar)
        self.progress_label = QLabel("0%")
        progress_row.addWidget(self.progress_label)
        progress_layout.addLayout(progress_row)
        self.status_label = QLabel("就绪")
        progress_layout.addWidget(self.status_label)
        progress_group.setLayout(progress_layout)

        self.result_table = QTableWidget()
        self.result_table.setColumnCount(3)
        self.result_table.setHorizontalHeaderLabels(["输入文件", "输出文件", "目标比例"])
        self.result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.result_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.result_table.setColumnWidth(2, 90)
        self.result_table.setMinimumHeight(220)

        hint = QLabel(
            "处理规则：源视频更高更窄时裁剪上下保留中间；源视频更矮更宽时使用上下模糊填充。"
            "“9:16->3:4->9:16”会先裁成3:4，再上下模糊填充回9:16。"
            "会递归处理子文件夹中的视频，并在输出目录保留原有子文件夹结构。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; padding: 5px;")

        layout.addWidget(input_group)
        layout.addWidget(size_group)
        layout.addWidget(mode_group)
        layout.addWidget(self.start_btn)
        layout.addWidget(progress_group)
        layout.addWidget(self.result_table, 1)
        layout.addWidget(hint)
        self.setLayout(layout)

    def load_config(self):
        self.input_folder.setText(get_config("video_resize", "input_folder", ""))
        self.output_folder.setText(get_config("video_resize", "output_folder", ""))
        ratio = get_config("video_resize", "ratio", "9:16")
        self.ratio_buttons.get(ratio, self.ratio_buttons["9:16"]).setChecked(True)
        process_mode = get_config("video_resize", "process_mode", "all")
        self.mode_buttons.get(process_mode, self.mode_buttons["all"]).setChecked(True)
        self.on_ratio_changed(self.current_ratio())

    def save_config(self):
        set_config("video_resize", "input_folder", self.input_folder.text())
        set_config("video_resize", "output_folder", self.output_folder.text())
        set_config("video_resize", "ratio", self.current_ratio())
        set_config("video_resize", "process_mode", self.current_process_mode())

    def on_ratio_changed(self, ratio):
        if ratio == PIPELINE_9X16_TO_3X4_TO_9X16:
            width, height = SIZE_PRESETS["9:16"]
            self.size_label.setText(f"输出尺寸: {width} x {height}，中间画面: 3:4")
            return
        width, height = SIZE_PRESETS[ratio]
        self.size_label.setText(f"输出尺寸: {width} x {height}")

    def on_ratio_toggled(self):
        self.on_ratio_changed(self.current_ratio())

    def current_ratio(self):
        for ratio, button in self.ratio_buttons.items():
            if button.isChecked():
                return ratio
        return "9:16"

    def current_process_mode(self):
        for mode, button in self.mode_buttons.items():
            if button.isChecked():
                return mode
        return "all"

    def browse_input(self):
        folder = QFileDialog.getExistingDirectory(self, "选择视频文件夹")
        if folder:
            self.input_folder.setText(folder)
            self.save_config()

    def browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if folder:
            self.output_folder.setText(folder)
            self.save_config()

    def start_resize(self):
        input_folder = self.input_folder.text()
        output_folder = self.output_folder.text()
        ratio = self.current_ratio()
        process_mode = self.current_process_mode()

        if not input_folder or not output_folder:
            QMessageBox.warning(self, "警告", "请选择输入和输出文件夹")
            return

        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "警告", "任务正在执行中")
            return

        self.save_config()
        self.result_table.setRowCount(0)
        self.progress_bar.setValue(0)
        self.progress_label.setText("0%")
        self.status_label.setText("准备处理...")
        self.start_btn.setEnabled(False)

        self.worker = VideoResizeWorker(input_folder, output_folder, ratio, process_mode)
        self.worker.progress.connect(self.on_progress)
        self.worker.video_done.connect(self.on_video_done)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def on_progress(self, current, total, rel_path):
        percent = int((current / total) * 100) if total else 0
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"{percent}%")
        self.status_label.setText(f"处理 {current}/{total}: {rel_path}")

    def on_video_done(self, result):
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        self.result_table.setItem(row, 0, QTableWidgetItem(result["input"]))
        self.result_table.setItem(row, 1, QTableWidgetItem(result["output"]))
        self.result_table.setItem(row, 2, QTableWidgetItem(result["ratio"]))

    def on_finished(self, results):
        self.start_btn.setEnabled(True)
        self.progress_bar.setValue(100)
        self.progress_label.setText("100%")
        self.status_label.setText(f"处理完成，共 {len(results)} 个视频")
        QMessageBox.information(self, "完成", f"视频尺寸处理完成，共 {len(results)} 个视频")

    def on_error(self, msg):
        self.start_btn.setEnabled(True)
        self.status_label.setText("处理失败")
        QMessageBox.critical(self, "错误", msg)
