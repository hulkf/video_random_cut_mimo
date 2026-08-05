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

    # ── 构建 UI ──────────────────────────────────────────────
    def init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(16, 12, 16, 12)

        # ── 1) 输入 / 输出文件夹 ──────────────────────────────
        input_group = QGroupBox("输入 / 输出")
        il = QVBoxLayout()
        il.setSpacing(10)

        # 输入行
        in_row = QHBoxLayout()
        in_row.setSpacing(8)
        self.input_folder = QLineEdit()
        self.input_folder.setMinimumHeight(32)
        self.input_folder.setPlaceholderText("输入文件夹（放视频的目录）")
        in_btn = QPushButton("浏览")
        in_btn.setFixedWidth(70)
        in_btn.setMinimumHeight(32)
        in_btn.clicked.connect(self.browse_input)
        in_row.addWidget(self.input_folder, 1)
        in_row.addWidget(in_btn)
        il.addLayout(in_row)

        # 输出行
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        self.output_folder = QLineEdit()
        self.output_folder.setMinimumHeight(32)
        self.output_folder.setPlaceholderText("输出文件夹（裂变后的视频存到这里）")
        out_btn = QPushButton("浏览")
        out_btn.setFixedWidth(70)
        out_btn.setMinimumHeight(32)
        out_btn.clicked.connect(self.browse_output)
        out_row.addWidget(self.output_folder, 1)
        out_row.addWidget(out_btn)
        il.addLayout(out_row)

        input_group.setLayout(il)
        main_layout.addWidget(input_group)

        # ── 2) 变换选项 + 强度（左右两栏）──────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        # 左栏：变换勾选
        tg = QGroupBox("变换选项")
        tl = QVBoxLayout()
        tl.setSpacing(8)

        self.flip_cb = QCheckBox("水平翻转")
        self.flip_cb.setToolTip("画面左右镜像。去重效果最好，对产品/风景几乎无感；含文字或人脸朝向的视频慎用")
        self.color_cb = QCheckBox("调色")
        self.color_cb.setToolTip("随机微调色相/饱和度/亮度/对比度，肉眼几乎无感")
        self.noise_cb = QCheckBox("加噪点")
        self.noise_cb.setToolTip("像素级极轻微扰动，改变指纹但看不出")
        self.resample_cb = QCheckBox("缩放重采样")
        self.resample_cb.setToolTip("缩放约1%%后裁回原尺寸，零观感差别")

        for cb in (self.flip_cb, self.color_cb, self.noise_cb, self.resample_cb):
            cb.setMinimumHeight(28)
            tl.addWidget(cb)
        tl.addStretch()
        tg.setLayout(tl)
        top_row.addWidget(tg, 1)

        # 右栏：强度 + 随机化 + 编码
        right_col = QVBoxLayout()
        right_col.setSpacing(10)

        # 强度
        ig = QGroupBox("变换幅度")
        ig_layout = QHBoxLayout()
        ig_layout.setSpacing(12)
        self.intensity_group = QButtonGroup(self)
        self.intensity_buttons = {}
        for key, text in [("mild", "轻微"), ("medium", "中等"), ("strong", "强烈")]:
            radio = QRadioButton(text)
            radio.setMinimumHeight(28)
            self.intensity_group.addButton(radio)
            self.intensity_buttons[key] = radio
            ig_layout.addWidget(radio)
        ig_layout.addStretch()
        ig.setLayout(ig_layout)
        right_col.addWidget(ig)

        # 随机化
        rg = QGroupBox("随机化")
        rg_layout = QHBoxLayout()
        rg_layout.setSpacing(8)
        self.random_cb = QCheckBox("每条视频随机参数")
        self.random_cb.setChecked(True)
        self.random_cb.setToolTip("开启后每条视频的变换参数都不同，保证输出互不重复")
        self.random_cb.setMinimumHeight(28)
        rg_layout.addWidget(self.random_cb)
        rg_layout.addStretch()
        rg.setLayout(rg_layout)
        right_col.addWidget(rg)

        # 编码设置
        eg = QGroupBox("编码")
        eg_layout = QHBoxLayout()
        eg_layout.setSpacing(8)
        eg_layout.addWidget(QLabel("速度:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(["ultrafast", "superfast", "veryfast"])
        self.preset_combo.setCurrentText("ultrafast")
        self.preset_combo.setMinimumHeight(28)
        eg_layout.addWidget(self.preset_combo)
        eg_layout.addWidget(QLabel("CRF:"))
        self.crf_slider = QSlider(Qt.Horizontal)
        self.crf_slider.setRange(16, 28)
        self.crf_slider.setValue(20)
        self.crf_slider.setMinimumWidth(120)
        self.crf_slider.valueChanged.connect(self.on_crf_changed)
        eg_layout.addWidget(self.crf_slider, 1)
        self.crf_label = QLabel("20")
        self.crf_label.setMinimumWidth(24)
        eg_layout.addWidget(self.crf_label)
        eg_layout.addStretch()
        eg.setLayout(eg_layout)
        right_col.addWidget(eg)

        # 随机种子
        sg = QGroupBox("随机种子（可选）")
        sg_layout = QHBoxLayout()
        sg_layout.setSpacing(8)
        self.seed_edit = QLineEdit()
        self.seed_edit.setMinimumHeight(30)
        self.seed_edit.setPlaceholderText("留空=每次随机；填固定值可复现结果")
        sg_layout.addWidget(self.seed_edit, 1)
        sg.setLayout(sg_layout)
        right_col.addWidget(sg)

        right_col.addStretch()
        top_row.addLayout(right_col, 1)
        main_layout.addLayout(top_row)

        # ── 3) 开始按钮 ───────────────────────────────────────
        self.start_btn = QPushButton("开始裂变")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.clicked.connect(self.start_fission)
        main_layout.addWidget(self.start_btn)

        # ── 4) 进度 ────────────────────────────────────────────
        pg = QGroupBox("处理进度")
        pl = QVBoxLayout()
        pl.setSpacing(6)
        prow = QHBoxLayout()
        prow.setSpacing(8)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setMinimumHeight(24)
        self.progress_label = QLabel("0%")
        self.progress_label.setMinimumWidth(36)
        prow.addWidget(self.progress_bar, 1)
        prow.addWidget(self.progress_label)
        pl.addLayout(prow)
        self.status_label = QLabel("就绪")
        pl.addWidget(self.status_label)
        pg.setLayout(pl)
        main_layout.addWidget(pg)

        # ── 5) 结果表格 ────────────────────────────────────────
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(2)
        self.result_table.setHorizontalHeaderLabels(["输入文件", "输出文件"])
        self.result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.result_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.result_table.setMinimumHeight(160)
        main_layout.addWidget(self.result_table, 1)

        # ── 6) 底部提示 ────────────────────────────────────────
        hint = QLabel(
            "原理：平台靠画面感知哈希(pHash)判重。本工具对画面做温和且随机的变换"
            "（翻转/调色/噪点/像素重采样），人眼几乎看不出差别，但 pHash 会明显改变。"
            "音频直接复制、分辨率保持不变，处理速度快。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; padding: 6px 2px;")
        main_layout.addWidget(hint)

        self.setLayout(main_layout)

    # ── 配置持久化 ───────────────────────────────────────────
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
        self.preset_combo.setCurrentText(preset if preset in ("ultrafast", "superfast", "veryfast") else "ultrafast")
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

    # ── 回调 ─────────────────────────────────────────────────
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
        folder = QFileDialog.getExistingDirectory(self, "选择输入文件夹")
        if folder:
            self.input_folder.setText(folder)
            self.save_config()

    def browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if folder:
            self.output_folder.setText(folder)
            self.save_config()

    # ── 执行 ─────────────────────────────────────────────────
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
