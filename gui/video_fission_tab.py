import os

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QRadioButton, QButtonGroup, QSlider, QCheckBox, QComboBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal

from core.video_fission import VideoFission
from gui.config import get_config, set_config


class VideoFissionWorker(QThread):
    progress = pyqtSignal(int, int, str)
    video_done = pyqtSignal(dict)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, options, input_folder, output_folder):
        super().__init__()
        self.options = options
        self.input_folder = input_folder
        self.output_folder = output_folder

    def run(self):
        try:
            engine = VideoFission(self.options)
            results = engine.fission_folder(
                self.input_folder, self.output_folder, callback=self._cb
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))

    def _cb(self, current, total, rel):
        self.progress.emit(current, total, rel)


class VideoFissionTab(QWidget):
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
        self.input_folder.setPlaceholderText("选择需要裂变的视频文件夹（自动递归处理子文件夹）...")
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

        transform_group = QGroupBox("变换选项（勾选要做的处理）")
        transform_layout = QVBoxLayout()
        transform_layout.setSpacing(6)
        self.flip_cb = QCheckBox("水平翻转（去重最有效，画面左右反转，对产品视频几乎无感）")
        self.color_cb = QCheckBox("调色（色相/饱和度/亮度/对比度，轻微随机）")
        self.noise_cb = QCheckBox("加噪点（像素级扰动，几乎看不出）")
        self.resample_cb = QCheckBox("缩放重采样（1% 缩放后裁回原尺寸，零观感差别）")
        for cb in (self.flip_cb, self.color_cb, self.noise_cb, self.resample_cb):
            cb.setMinimumHeight(26)
            transform_layout.addWidget(cb)
        transform_group.setLayout(transform_layout)

        intensity_group = QGroupBox("变换强度")
        intensity_layout = QHBoxLayout()
        intensity_layout.setSpacing(8)
        intensity_layout.addWidget(QLabel("幅度:"))
        self.intensity_group = QButtonGroup(self)
        self.intensity_buttons = {}
        for key, text in [("mild", "轻微"), ("medium", "中等"), ("strong", "强烈")]:
            radio = QRadioButton(text)
            radio.setMinimumHeight(28)
            self.intensity_group.addButton(radio)
            self.intensity_buttons[key] = radio
            intensity_layout.addWidget(radio)
        intensity_layout.addStretch()
        transform_group_intensity = intensity_group
        transform_group_intensity.setLayout(intensity_layout)

        random_group = QGroupBox("随机化")
        random_layout = QHBoxLayout()
        random_layout.setSpacing(8)
        self.random_cb = QCheckBox("每条视频随机参数（保证输出互不相同）")
        self.random_cb.setChecked(True)
        self.random_cb.setMinimumHeight(26)
        random_layout.addWidget(self.random_cb)
        random_layout.addStretch()
        random_group.setLayout(random_layout)

        encode_group = QGroupBox("编码设置")
        encode_layout = QHBoxLayout()
        encode_layout.setSpacing(8)
        encode_layout.addWidget(QLabel("编码速度:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(["superfast", "veryfast", "ultrafast"])
        self.preset_combo.setCurrentText("ultrafast")
        self.preset_combo.setMinimumHeight(28)
        encode_layout.addWidget(self.preset_combo)
        encode_layout.addWidget(QLabel("画质(CRF):"))
        self.crf_slider = QSlider(Qt.Horizontal)
        self.crf_slider.setRange(16, 28)
        self.crf_slider.setValue(20)
        self.crf_slider.setTickPosition(QSlider.TicksBelow)
        self.crf_slider.setTickInterval(1)
        self.crf_slider.valueChanged.connect(self.on_crf_changed)
        encode_layout.addWidget(self.crf_slider, 1)
        self.crf_label = QLabel("20")
        self.crf_label.setMinimumWidth(28)
        encode_layout.addWidget(self.crf_label)
        encode_layout.addStretch()
        encode_group.setLayout(encode_layout)

        seed_group = QGroupBox("随机种子（可选）")
        seed_layout = QHBoxLayout()
        seed_layout.setSpacing(8)
        self.seed_edit = QLineEdit()
        self.seed_edit.setMinimumHeight(30)
        self.seed_edit.setPlaceholderText("留空 = 每条视频随机；填固定值可复现同一批结果")
        seed_layout.addWidget(self.seed_edit, 1)
        seed_group.setLayout(seed_layout)

        self.start_btn = QPushButton("开始裂变")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self.start_fission)

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
        self.result_table.setColumnCount(2)
        self.result_table.setHorizontalHeaderLabels(["输入文件", "输出文件"])
        self.result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.result_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.result_table.setMinimumHeight(220)

        hint = QLabel(
            "原理：平台靠画面感知哈希(pHash)判重。本工具对画面做温和且随机的变换"
            "（翻转/调色/噪点/像素重采样），人眼几乎看不出差别，但 pHash 会明显改变。"
            "音频直接复制、分辨率保持不变，处理速度快。输出文件名带 _fission 后缀，"
            "并在输出目录保留原有子文件夹结构。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; padding: 5px;")

        layout.addWidget(input_group)
        layout.addWidget(transform_group)
        layout.addWidget(transform_group_intensity)
        layout.addWidget(random_group)
        layout.addWidget(encode_group)
        layout.addWidget(seed_group)
        layout.addWidget(self.start_btn)
        layout.addWidget(progress_group)
        layout.addWidget(self.result_table, 1)
        layout.addWidget(hint)
        self.setLayout(layout)

    def load_config(self):
        self.input_folder.setText(get_config("video_fission", "input_folder", ""))
        self.output_folder.setText(get_config("video_fission", "output_folder", ""))
        self.flip_cb.setChecked(get_config("video_fission", "flip", True) in (True, "true", "True"))
        self.color_cb.setChecked(get_config("video_fission", "color", True) in (True, "true", "True"))
        self.noise_cb.setChecked(get_config("video_fission", "noise", True) in (True, "true", "True"))
        self.resample_cb.setChecked(get_config("video_fission", "resample", True) in (True, "true", "True"))
        self.random_cb.setChecked(get_config("video_fission", "random_params", True) in (True, "true", "True"))
        intensity = get_config("video_fission", "intensity", "mild")
        self.intensity_buttons.get(intensity, self.intensity_buttons["mild"]).setChecked(True)
        preset = get_config("video_fission", "preset", "ultrafast")
        self.preset_combo.setCurrentText(preset if preset in ("superfast", "veryfast", "ultrafast") else "ultrafast")
        self.crf_slider.setValue(int(get_config("video_fission", "crf", "20")))
        self.on_crf_changed(self.crf_slider.value())

    def save_config(self):
        set_config("video_fission", "input_folder", self.input_folder.text())
        set_config("video_fission", "output_folder", self.output_folder.text())
        set_config("video_fission", "flip", str(self.flip_cb.isChecked()))
        set_config("video_fission", "color", str(self.color_cb.isChecked()))
        set_config("video_fission", "noise", str(self.noise_cb.isChecked()))
        set_config("video_fission", "resample", str(self.resample_cb.isChecked()))
        set_config("video_fission", "random_params", str(self.random_cb.isChecked()))
        set_config("video_fission", "intensity", self.current_intensity())
        set_config("video_fission", "preset", self.preset_combo.currentText())
        set_config("video_fission", "crf", str(self.crf_slider.value()))
        set_config("video_fission", "seed", self.seed_edit.text().strip())

    def on_crf_changed(self, value):
        self.crf_label.setText(str(value))

    def current_intensity(self):
        for key, button in self.intensity_buttons.items():
            if button.isChecked():
                return key
        return "mild"

    def current_options(self):
        seed = self.seed_edit.text().strip()
        return {
            "flip": self.flip_cb.isChecked(),
            "color": self.color_cb.isChecked(),
            "noise": self.noise_cb.isChecked(),
            "resample": self.resample_cb.isChecked(),
            "intensity": self.current_intensity(),
            "random_params": self.random_cb.isChecked(),
            "preset": self.preset_combo.currentText(),
            "crf": self.crf_slider.value(),
            "seed": seed or None,
        }

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

    def start_fission(self):
        input_folder = self.input_folder.text()
        output_folder = self.output_folder.text()
        options = self.current_options()

        if not input_folder or not output_folder:
            QMessageBox.warning(self, "警告", "请选择输入和输出文件夹")
            return
        if not (options["flip"] or options["color"] or options["noise"] or options["resample"]):
            QMessageBox.warning(self, "警告", "请至少勾选一项变换")
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

        self.worker = VideoFissionWorker(options, input_folder, output_folder)
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

    def on_finished(self, results):
        self.start_btn.setEnabled(True)
        self.progress_bar.setValue(100)
        self.progress_label.setText("100%")
        self.status_label.setText(f"裂变完成，共 {len(results)} 个视频")
        QMessageBox.information(self, "完成", f"视频裂变完成，共 {len(results)} 个视频")

    def on_error(self, msg):
        self.start_btn.setEnabled(True)
        self.status_label.setText("处理失败")
        QMessageBox.critical(self, "错误", msg)
