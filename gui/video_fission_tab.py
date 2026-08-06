import os
import subprocess

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QSpinBox, QSlider, QComboBox, QCheckBox,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor

from core.video_fission import VideoFission
from gui.config import get_config, set_config


class VideoFissionWorker(QThread):
    progress = pyqtSignal(int, int, str)
    video_done = pyqtSignal(dict)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, options, input_folder, output_folder, count):
        super().__init__()
        self.options = options
        self.input_folder = input_folder
        self.output_folder = output_folder
        self.count = count

    def run(self):
        try:
            engine = VideoFission(self.options)
            results = engine.fission_folder(
                self.input_folder, self.output_folder,
                count=self.count, callback=self._cb
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

    # ── UI ────────────────────────────────────────────────────
    def init_ui(self):
        main = QVBoxLayout()
        main.setSpacing(14)
        main.setContentsMargins(18, 14, 18, 14)

        # ── 输入输出 ───────────────────────────────────────────
        io_group = QGroupBox("输入 / 输出")
        io_lay = QVBoxLayout()
        io_lay.setSpacing(10)

        in_row = QHBoxLayout()
        in_row.setSpacing(8)
        self.input_edit = QLineEdit()
        self.input_edit.setMinimumHeight(34)
        self.input_edit.setPlaceholderText("放视频的文件夹")
        in_btn = QPushButton("浏览")
        in_btn.setFixedWidth(72)
        in_btn.setMinimumHeight(34)
        in_btn.clicked.connect(self._browse_input)
        in_row.addWidget(self.input_edit, 1)
        in_row.addWidget(in_btn)
        io_lay.addLayout(in_row)

        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        self.output_edit = QLineEdit()
        self.output_edit.setMinimumHeight(34)
        self.output_edit.setPlaceholderText("裂变后的视频保存到这里")
        out_btn = QPushButton("浏览")
        out_btn.setFixedWidth(72)
        out_btn.setMinimumHeight(34)
        out_btn.clicked.connect(self._browse_output)
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(out_btn)
        io_lay.addLayout(out_row)

        io_group.setLayout(io_lay)
        main.addWidget(io_group)

        # ── 裂变参数（一行搞定）────────────────────────────────
        param_group = QGroupBox("裂变参数")
        param_lay = QHBoxLayout()
        param_lay.setSpacing(16)

        # 数量
        param_lay.addWidget(QLabel("每个视频生成:"))
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 200)
        self.count_spin.setValue(10)
        self.count_spin.setMinimumHeight(32)
        self.count_spin.setToolTip("同一个视频裂变成多少个不同版本")
        param_lay.addWidget(self.count_spin)
        param_lay.addWidget(QLabel("个版本"))

        param_lay.addSpacing(20)

        # 强度
        param_lay.addWidget(QLabel("强度:"))
        self.intensity_combo = QComboBox()
        self.intensity_combo.addItems(["轻微", "中等", "强烈"])
        self.intensity_combo.setCurrentIndex(0)
        self.intensity_combo.setMinimumHeight(32)
        param_lay.addWidget(self.intensity_combo)

        param_lay.addSpacing(20)

        # 编码速度
        param_lay.addWidget(QLabel("编码:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(["ultrafast", "superfast", "veryfast"])
        self.preset_combo.setCurrentText("ultrafast")
        self.preset_combo.setMinimumHeight(32)
        param_lay.addWidget(self.preset_combo)

        param_lay.addWidget(QLabel("CRF:"))
        self.crf_slider = QSlider(Qt.Horizontal)
        self.crf_slider.setRange(16, 28)
        self.crf_slider.setValue(20)
        self.crf_slider.setMinimumWidth(100)
        self.crf_slider.valueChanged.connect(self._on_crf)
        param_lay.addWidget(self.crf_slider)
        self.crf_label = QLabel("20")
        self.crf_label.setMinimumWidth(24)
        param_lay.addWidget(self.crf_label)

        param_lay.addStretch()
        param_group.setLayout(param_lay)
        main.addWidget(param_group)

        # ── 文件层保险 ──────────────────────────────────────────
        file_group = QGroupBox("文件层保险（可选，默认开启）")
        file_lay = QHBoxLayout()
        file_lay.setSpacing(16)
        self.meta_cb = QCheckBox("清空视频元数据")
        self.meta_cb.setChecked(True)
        self.meta_cb.setToolTip("去除视频内置的作者/软件/描述等信息，并写入随机注释，抹掉工具痕迹")
        self.ts_cb = QCheckBox("随机化文件时间戳")
        self.ts_cb.setChecked(True)
        self.ts_cb.setToolTip("将产物的创建/修改/访问时间改为随机值，文件属性层面也互不相同")
        file_lay.addWidget(self.meta_cb)
        file_lay.addWidget(self.ts_cb)
        file_lay.addStretch()
        file_group.setLayout(file_lay)
        main.addWidget(file_group)

        # ── 开始按钮 ───────────────────────────────────────────
        self.start_btn = QPushButton("开始裂变")
        self.start_btn.setMinimumHeight(44)
        self.start_btn.clicked.connect(self._start)
        main.addWidget(self.start_btn)

        # ── 进度 ───────────────────────────────────────────────
        pg = QGroupBox("处理进度")
        pl = QVBoxLayout()
        pl.setSpacing(6)
        pr = QHBoxLayout()
        pr.setSpacing(8)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setMinimumHeight(26)
        self.pct_label = QLabel("0%")
        self.pct_label.setMinimumWidth(40)
        pr.addWidget(self.progress_bar, 1)
        pr.addWidget(self.pct_label)
        pl.addLayout(pr)
        self.status_lbl = QLabel("就绪")
        pl.addWidget(self.status_lbl)
        pg.setLayout(pl)
        main.addWidget(pg)

        # ── 结果表（双击打开文件夹）────────────────────────────
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["源视频", "产物数", "所在文件夹"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setMinimumHeight(180)
        self.table.doubleClicked.connect(self._open_folder)
        main.addWidget(self.table, 1)

        # ── 底部说明 ───────────────────────────────────────────
        hint = QLabel(
            "原理：对画面做随机调色 + 轻微噪点 + 像素重采样，每份参数不同，"
            "平台指纹(pHash)各不相同，但人眼几乎看不出差别。"
            "不使用水平翻转，文字/人脸不会镜像反转。音频直接复制。"
            "分辨率与宽高比严格锁定原视频，不会改动。"
            "\n双击结果行可打开对应文件夹查看产物。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; padding: 4px 2px;")
        hint.setMaximumHeight(56)
        main.addWidget(hint)

        self.setLayout(main)

    # ── 配置 ──────────────────────────────────────────────────
    def load_config(self):
        self.input_edit.setText(get_config("video_fission", "input_folder", ""))
        self.output_edit.setText(get_config("video_fission", "output_folder", ""))
        self.count_spin.setValue(int(get_config("video_fission", "count", 10)))
        idx_map = {"mild": 0, "medium": 1, "strong": 2}
        self.intensity_combo.setCurrentIndex(idx_map.get(get_config("video_fission", "intensity", "mild"), 0))
        preset = get_config("video_fission", "preset", "ultrafast")
        self.preset_combo.setCurrentText(preset if preset in ("ultrafast", "superfast", "veryfast") else "ultrafast")
        self.crf_slider.setValue(int(get_config("video_fission", "crf", "20")))
        self.meta_cb.setChecked(get_config("video_fission", "clean_metadata", True) in (True, "true", "True"))
        self.ts_cb.setChecked(get_config("video_fission", "random_timestamps", True) in (True, "true", "True"))
        self._on_crf(self.crf_slider.value())

    def save_config(self):
        set_config("video_fission", "input_folder", self.input_edit.text())
        set_config("video_fission", "output_folder", self.output_edit.text())
        set_config("video_fission", "count", str(self.count_spin.value()))
        imap = {0: "mild", 1: "medium", 2: "strong"}
        set_config("video_fission", "intensity", imap.get(self.intensity_combo.currentIndex(), "mild"))
        set_config("video_fission", "preset", self.preset_combo.currentText())
        set_config("video_fission", "crf", str(self.crf_slider.value()))
        set_config("video_fission", "clean_metadata", str(self.meta_cb.isChecked()))
        set_config("video_fission", "random_timestamps", str(self.ts_cb.isChecked()))

    # ── 回调 ──────────────────────────────────────────────────
    def _on_crf(self, v):
        self.crf_label.setText(str(v))

    def _intensity_key(self):
        return ["mild", "medium", "strong"][self.intensity_combo.currentIndex()]

    def _browse_input(self):
        d = QFileDialog.getExistingDirectory(self, "选择视频文件夹")
        if d:
            self.input_edit.setText(d)
            self.save_config()

    def _browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存文件夹")
        if d:
            self.output_edit.setText(d)
            self.save_config()

    def _open_folder(self, index):
        row = index.row()
        folder = self.table.item(row, 2).text()
        if os.path.isdir(folder):
            subprocess.Popen(["explorer", folder])

    # ── 执行 ──────────────────────────────────────────────────
    def _start(self):
        input_dir = self.input_edit.text().strip()
        output_dir = self.output_edit.text().strip()
        count = self.count_spin.value()

        if not input_dir or not output_dir:
            QMessageBox.warning(self, "提示", "请选择输入和输出文件夹")
            return
        if not os.path.isdir(input_dir):
            QMessageBox.warning(self, "提示", "输入文件夹不存在")
            return

        self.save_config()
        self.table.setRowCount(0)
        self.progress_bar.setValue(0)
        self.pct_label.setText("0%")
        self.status_lbl.setText("准备处理...")
        self.start_btn.setEnabled(False)

        options = {
            "intensity": self._intensity_key(),
            "preset": self.preset_combo.currentText(),
            "crf": self.crf_slider.value(),
            "clean_metadata": self.meta_cb.isChecked(),
            "random_timestamps": self.ts_cb.isChecked(),
        }

        self.worker = VideoFissionWorker(options, input_dir, output_dir, count)
        self.worker.progress.connect(self._on_progress)
        self.worker.video_done.connect(self._on_video_done)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_progress(self, current, total, rel):
        pct = int((current / total) * 100) if total else 0
        self.progress_bar.setValue(pct)
        self.pct_label.setText("{}%".format(pct))
        self.status_lbl.setText("处理 {}/{}: {}".format(current, total, rel))

    def _on_video_done(self, result):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(os.path.basename(result["input"])))
        self.table.setItem(row, 1, QTableWidgetItem(str(len(result["outputs"]))))
        self.table.setItem(row, 2, QTableWidgetItem(result["subfolder"]))

    def _on_finished(self, results):
        self.start_btn.setEnabled(True)
        self.progress_bar.setValue(100)
        self.pct_label.setText("100%")
        total_videos = sum(len(r["outputs"]) for r in results)
        self.status_lbl.setText("完成！共 {} 个源视频 → {} 个裂变产物".format(len(results), total_videos))
        QMessageBox.information(self, "完成",
            "裂变完成！\n\n源视频: {} 个\n生成产物: {} 个\n\n产物按源视频分文件夹存放，双击结果行可查看。".format(
                len(results), total_videos))

    def _on_error(self, msg):
        self.start_btn.setEnabled(True)
        self.status_lbl.setText("处理失败")
        QMessageBox.critical(self, "错误", msg)
