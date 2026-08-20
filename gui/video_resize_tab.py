import os

from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QMessageBox, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QRadioButton, QButtonGroup, QSlider
)
from PyQt5.QtCore import Qt, pyqtSignal

from core.video_resizer import (
    VideoResizer, collect_videos, matches_target_size,
    DEFAULT_BLUR_STRENGTH, PIPELINE_9X16_TO_3X4_TO_9X16, SIZE_PRESETS
)
from gui.common.base_tab import BaseTab
from gui.common.base_worker import BaseWorker
from gui.common.path_row import PathRow, MODE_FOLDER
from gui.common.progress_panel import ProgressPanel
from gui.config import get_config, set_config


class VideoResizeWorker(BaseWorker):
    """视频尺寸处理线程（P2 试点：继承 BaseWorker，信号协议统一）。"""
    video_done = pyqtSignal(dict)

    def __init__(self, input_folder, output_folder, target_ratio, process_mode, blur_strength):
        super().__init__()
        self.input_folder = input_folder
        self.output_folder = output_folder
        self.target_ratio = target_ratio
        self.process_mode = process_mode
        self.blur_strength = blur_strength

    def work(self):
        videos = collect_videos(self.input_folder)
        if len(videos) == 0:
            raise ValueError("输入文件夹中没有找到视频文件")

        engine = VideoResizer(self.target_ratio, self.blur_strength)
        if self.process_mode == "mismatched":
            videos = [
                video_path for video_path in videos
                if not matches_target_size(video_path, self.target_ratio)
            ]

        total = len(videos)
        results = []

        if total == 0:
            self.emit_finished(results)
            return

        for index, video_path in enumerate(videos):
            if self.stopped():
                break
            rel_path = engine_target_rel_path(video_path, self.input_folder, self.target_ratio)
            output_path = os.path.join(self.output_folder, rel_path)

            self.emit_progress(index, total, rel_path)
            result_path = engine.resize_video(video_path, output_path)
            result = {
                "input": video_path,
                "output": result_path,
                "ratio": self.target_ratio,
            }
            results.append(result)
            self.video_done.emit(result)
            self.emit_progress(index + 1, total, rel_path)

        self.emit_finished(results)


def engine_target_rel_path(video_path, input_folder, target_ratio):
    rel_path = os.path.relpath(video_path, input_folder)
    rel_base, _ = os.path.splitext(rel_path)
    if target_ratio == PIPELINE_9X16_TO_3X4_TO_9X16:
        return f"{rel_base}_9x16_to_3x4_to_9x16.mp4"
    return f"{rel_base}_{target_ratio.replace(':', 'x')}.mp4"


class VideoResizeTab(BaseTab):
    def __init__(self):
        super().__init__()
        self.init_ui()
        self.load_config()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        input_group = QGroupBox("输入设置")
        input_layout = QVBoxLayout()
        input_layout.setSpacing(8)
        # P2：路径行用公共 PathRow 控件（内置输入框样式修复 + 浏览 + 自动保存配置）
        self.input_folder = PathRow("选择需要处理的视频文件夹...", mode=MODE_FOLDER,
                                    on_change=lambda p: self.save_config())
        input_layout.addWidget(self.input_folder)
        self.output_folder = PathRow("选择输出文件夹...", mode=MODE_FOLDER,
                                     on_change=lambda p: self.save_config())
        input_layout.addWidget(self.output_folder)
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

        blur_group = QGroupBox("模糊填充")
        blur_layout = QHBoxLayout()
        blur_layout.setSpacing(8)
        blur_layout.addWidget(QLabel("模糊程度:"))
        self.blur_slider = QSlider(Qt.Horizontal)
        self.blur_slider.setRange(1, 20)
        self.blur_slider.setValue(DEFAULT_BLUR_STRENGTH)
        self.blur_slider.setTickPosition(QSlider.TicksBelow)
        self.blur_slider.setTickInterval(1)
        self.blur_slider.valueChanged.connect(self.on_blur_changed)
        blur_layout.addWidget(self.blur_slider, 1)
        self.blur_value_label = QLabel("")
        self.blur_value_label.setMinimumWidth(70)
        blur_layout.addWidget(self.blur_value_label)
        blur_group.setLayout(blur_layout)

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

        # P2：进度区用公共 ProgressPanel
        self.progress_panel = ProgressPanel("处理进度")
        self.progress_bar = self.progress_panel.bar
        self.progress_label = self.progress_panel.percent_label
        self.status_label = self.progress_panel.status_label

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
        layout.addWidget(blur_group)
        layout.addWidget(mode_group)
        layout.addWidget(self.start_btn)
        layout.addWidget(self.progress_panel)
        layout.addWidget(self.result_table, 1)
        layout.addWidget(hint)
        self.setLayout(layout)

    # ── 配置持久化 ───────────────────────────────────────────
    def load_config(self):
        self.input_folder.setText(get_config("video_resize", "input_folder", ""))
        self.output_folder.setText(get_config("video_resize", "output_folder", ""))
        ratio = get_config("video_resize", "ratio", "9:16")
        self.ratio_buttons.get(ratio, self.ratio_buttons["9:16"]).setChecked(True)
        self.blur_slider.setValue(int(get_config("video_resize", "blur_strength", str(DEFAULT_BLUR_STRENGTH))))
        process_mode = get_config("video_resize", "process_mode", "all")
        self.mode_buttons.get(process_mode, self.mode_buttons["all"]).setChecked(True)
        self.on_ratio_changed(self.current_ratio())
        self.on_blur_changed(self.blur_slider.value())

    def save_config(self):
        set_config("video_resize", "input_folder", self.input_folder.text())
        set_config("video_resize", "output_folder", self.output_folder.text())
        set_config("video_resize", "ratio", self.current_ratio())
        set_config("video_resize", "blur_strength", str(self.blur_slider.value()))
        set_config("video_resize", "process_mode", self.current_process_mode())

    # ── 交互逻辑 ─────────────────────────────────────────────
    def on_ratio_changed(self, ratio):
        if ratio == PIPELINE_9X16_TO_3X4_TO_9X16:
            width, height = SIZE_PRESETS["9:16"]
            self.size_label.setText(f"输出尺寸: {width} x {height}，中间画面: 3:4")
            return
        width, height = SIZE_PRESETS[ratio]
        self.size_label.setText(f"输出尺寸: {width} x {height}")

    def on_ratio_toggled(self):
        self.on_ratio_changed(self.current_ratio())

    def on_blur_changed(self, value):
        self.blur_value_label.setText(str(value))

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

    # ── 任务启动（P2：统一走 BaseTab.start_worker）──
    def start_resize(self):
        input_folder = self.input_folder.text()
        output_folder = self.output_folder.text()
        ratio = self.current_ratio()
        process_mode = self.current_process_mode()
        blur_strength = self.blur_slider.value()

        if not input_folder or not output_folder:
            QMessageBox.warning(self, "警告", "请选择输入和输出文件夹")
            return

        self.save_config()
        self.result_table.setRowCount(0)
        self.progress_panel.reset("准备处理...")

        worker = VideoResizeWorker(input_folder, output_folder, ratio, process_mode, blur_strength)
        worker.video_done.connect(self.on_video_done)
        if not self.start_worker(worker):
            self.progress_panel.reset()
            return

    def set_busy(self, busy):
        self.start_btn.setEnabled(not busy)

    # ── 回调 ─────────────────────────────────────────────────
    def on_worker_progress(self, current, total, message):
        self.progress_panel.set_progress(current, total, f"处理 {current}/{total}: {message}")

    def on_video_done(self, result):
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        self.result_table.setItem(row, 0, QTableWidgetItem(result["input"]))
        self.result_table.setItem(row, 1, QTableWidgetItem(result["output"]))
        self.result_table.setItem(row, 2, QTableWidgetItem(result["ratio"]))

    def on_worker_finished(self, results):
        super().on_worker_finished(results)
        self.progress_panel.set_progress(100, 100)
        self.progress_panel.set_status(f"处理完成，共 {len(results)} 个视频")
        QMessageBox.information(self, "完成", f"视频尺寸处理完成，共 {len(results)} 个视频")

    def on_worker_error(self, message):
        super().on_worker_error(message)
        self.progress_panel.set_status("处理失败")
