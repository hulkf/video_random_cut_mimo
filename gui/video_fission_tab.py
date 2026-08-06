import os
import subprocess

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QSpinBox, QSlider, QComboBox, QCheckBox,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal

from core.video_fission import VideoFission
from gui.config import get_config, set_config


class VideoFissionWorker(QThread):
    progress = pyqtSignal(int, int, str)
    video_done = pyqtSignal(dict)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, options, input_paths, output_folder, count, separate_folder):
        super().__init__()
        self.options = options
        self.input_paths = input_paths
        self.output_folder = output_folder
        self.count = count
        self.separate_folder = separate_folder

    def run(self):
        try:
            engine = VideoFission(self.options)
            results = engine.fission_folder(
                self.input_paths, self.output_folder,
                count=self.count, separate_folder=self.separate_folder,
                callback=self._cb,
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))

    def _cb(self, current, total, rel):
        self.progress.emit(current, total, rel)


def strip_quotes(path):
    """剥离路径首尾的单/双引号，兼容从别处复制的带引号路径。"""
    p = (path or "").strip()
    if len(p) >= 2 and p[0] == p[-1] and p[0] in ('"', "'"):
        return p[1:-1].strip()
    return p


class VideoFissionTab(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.init_ui()
        self.load_config()

    # ── UI ────────────────────────────────────────────────────
    def init_ui(self):
        main = QVBoxLayout()
        main.setSpacing(12)
        main.setContentsMargins(18, 14, 18, 14)

        # ── 输入设置（最多 3 个源，可填文件夹或单个视频文件）──
        io_group = QGroupBox("输入设置（最多 3 个源：文件夹或单个视频文件，可留空）")
        io_lay = QVBoxLayout()
        io_lay.setSpacing(8)

        self.input_edits = []
        for i in range(1, 4):
            row = QHBoxLayout()
            row.setSpacing(8)
            edit = QLineEdit()
            edit.setMinimumHeight(32)
            edit.setPlaceholderText(
                "输入 {}：文件夹路径，或直接填一个视频文件路径（可留空）".format(i))
            btn = QPushButton("浏览")
            btn.setFixedWidth(68)
            btn.setMinimumHeight(32)
            btn.clicked.connect(lambda _, idx=i - 1: self._browse_input(idx))
            row.addWidget(edit, 1)
            row.addWidget(btn)
            io_lay.addLayout(row)
            self.input_edits.append(edit)

        io_group.setLayout(io_lay)
        main.addWidget(io_group)

        # ── 输出设置（独立放在下方，与输入分开）────────────────
        out_group = QGroupBox("输出设置（3 个输入源共享同一个输出文件夹）")
        out_lay = QHBoxLayout()
        out_lay.setSpacing(8)
        self.output_edit = QLineEdit()
        self.output_edit.setMinimumHeight(32)
        self.output_edit.setPlaceholderText("裂变后的视频保存到这里")
        out_btn = QPushButton("浏览")
        out_btn.setFixedWidth(68)
        out_btn.setMinimumHeight(32)
        out_btn.clicked.connect(self._browse_output)
        out_lay.addWidget(self.output_edit, 1)
        out_lay.addWidget(out_btn)
        out_group.setLayout(out_lay)
        main.addWidget(out_group)

        # ── 裂变参数 ───────────────────────────────────────────
        param_group = QGroupBox("裂变参数")
        param_lay = QHBoxLayout()
        param_lay.setSpacing(16)

        param_lay.addWidget(QLabel("每个视频生成:"))
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 200)
        self.count_spin.setValue(10)
        self.count_spin.setMinimumHeight(30)
        self.count_spin.setToolTip("同一个视频裂变成多少个不同版本")
        param_lay.addWidget(self.count_spin)
        param_lay.addWidget(QLabel("个版本"))

        param_lay.addSpacing(16)

        param_lay.addWidget(QLabel("强度:"))
        self.intensity_combo = QComboBox()
        self.intensity_combo.addItems(["轻微", "中等", "强烈"])
        self.intensity_combo.setCurrentIndex(0)
        self.intensity_combo.setMinimumHeight(30)
        param_lay.addWidget(self.intensity_combo)

        param_lay.addSpacing(16)

        param_lay.addWidget(QLabel("编码:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(["ultrafast", "superfast", "veryfast"])
        self.preset_combo.setCurrentText("ultrafast")
        self.preset_combo.setMinimumHeight(30)
        param_lay.addWidget(self.preset_combo)

        param_lay.addWidget(QLabel("CRF:"))
        self.crf_slider = QSlider(Qt.Horizontal)
        self.crf_slider.setRange(16, 28)
        self.crf_slider.setValue(20)
        self.crf_slider.setMinimumWidth(90)
        self.crf_slider.valueChanged.connect(self._on_crf)
        param_lay.addWidget(self.crf_slider)
        self.crf_label = QLabel("20")
        self.crf_label.setMinimumWidth(22)
        param_lay.addWidget(self.crf_label)

        param_lay.addStretch()
        param_group.setLayout(param_lay)
        main.addWidget(param_group)

        # ── 存放规则 ───────────────────────────────────────────
        rule_group = QGroupBox("存放规则")
        rule_lay = QHBoxLayout()
        rule_lay.setSpacing(8)
        self.separate_cb = QCheckBox("每个文件的裂变结果放单独文件夹")
        self.separate_cb.setChecked(True)
        self.separate_cb.setToolTip("勾选：产物放在「输出/原名_fissions」子文件夹；\n不勾选：所有产物统一平铺在输出文件夹下")
        rule_lay.addWidget(self.separate_cb)
        rule_lay.addStretch()
        rule_group.setLayout(rule_lay)
        main.addWidget(rule_group)

        # ── 开始按钮 ───────────────────────────────────────────
        self.start_btn = QPushButton("开始裂变")
        self.start_btn.setMinimumHeight(42)
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
        self.progress_bar.setMinimumHeight(24)
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
        self.table.setMinimumHeight(170)
        self.table.doubleClicked.connect(self._open_folder)
        main.addWidget(self.table, 1)

        # ── 底部说明 ───────────────────────────────────────────
        hint = QLabel(
            "原理：对画面做随机调色 + 轻微噪点 + 像素重采样，每份参数不同，"
            "平台指纹(pHash)各不相同，但人眼几乎看不出差别。"
            "分辨率与宽高比严格锁定原视频，不会改动。"
            "默认已清空元数据、随机化时间戳，让文件属性也彻底不同。"
            "输入支持文件夹或单个视频文件，路径带引号也能识别。"
            "\n双击结果行可打开对应文件夹查看产物。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; padding: 4px 2px;")
        hint.setMaximumHeight(60)
        main.addWidget(hint)

        self.setLayout(main)

    # ── 配置 ──────────────────────────────────────────────────
    def load_config(self):
        paths = get_config("video_fission", "input_folders", "")
        if isinstance(paths, list):
            for i, edit in enumerate(self.input_edits):
                if i < len(paths):
                    edit.setText(paths[i])
        self.output_edit.setText(get_config("video_fission", "output_folder", ""))
        self.count_spin.setValue(int(get_config("video_fission", "count", 10)))
        idx_map = {"mild": 0, "medium": 1, "strong": 2}
        self.intensity_combo.setCurrentIndex(idx_map.get(get_config("video_fission", "intensity", "mild"), 0))
        preset = get_config("video_fission", "preset", "ultrafast")
        self.preset_combo.setCurrentText(preset if preset in ("ultrafast", "superfast", "veryfast") else "ultrafast")
        self.crf_slider.setValue(int(get_config("video_fission", "crf", "20")))
        self.separate_cb.setChecked(get_config("video_fission", "separate_folder", True) in (True, "true", "True"))
        self._on_crf(self.crf_slider.value())

    def save_config(self):
        set_config("video_fission", "input_folders",
                   [edit.text() for edit in self.input_edits])
        set_config("video_fission", "output_folder", self.output_edit.text())
        set_config("video_fission", "count", str(self.count_spin.value()))
        imap = {0: "mild", 1: "medium", 2: "strong"}
        set_config("video_fission", "intensity", imap.get(self.intensity_combo.currentIndex(), "mild"))
        set_config("video_fission", "preset", self.preset_combo.currentText())
        set_config("video_fission", "crf", str(self.crf_slider.value()))
        set_config("video_fission", "separate_folder", str(self.separate_cb.isChecked()))

    # ── 回调 ──────────────────────────────────────────────────
    def _on_crf(self, v):
        self.crf_label.setText(str(v))

    def _intensity_key(self):
        return ["mild", "medium", "strong"][self.intensity_combo.currentIndex()]

    def _browse_input(self, idx):
        # 支持选择文件夹或单个文件
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件（或点取消再选文件夹）",
            "", "视频文件 (*.mp4 *.avi *.mov *.mkv *.flv *.wmv *.ts *.m4v)")
        if file_path:
            self.input_edits[idx].setText(file_path)
            self.save_config()
            return
        folder = QFileDialog.getExistingDirectory(self, "选择输入文件夹")
        if folder:
            self.input_edits[idx].setText(folder)
            self.save_config()

    def _browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
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
        input_paths = [strip_quotes(e.text()) for e in self.input_edits]
        input_paths = [p for p in input_paths if p]
        output_dir = strip_quotes(self.output_edit.text())
        count = self.count_spin.value()

        if not input_paths:
            QMessageBox.warning(self, "提示", "请至少填一个输入源（文件夹或视频文件）")
            return
        for p in input_paths:
            if not os.path.exists(p):
                QMessageBox.warning(self, "提示", "输入路径不存在:\n{}".format(p))
                return
        if not output_dir:
            QMessageBox.warning(self, "提示", "请选择输出文件夹")
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
        }

        self.worker = VideoFissionWorker(
            options, input_paths, output_dir, count, self.separate_cb.isChecked())
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
            "裂变完成！\n\n源视频: {} 个\n生成产物: {} 个\n\n双击结果行可查看对应文件夹。".format(
                len(results), total_videos))

    def _on_error(self, msg):
        self.start_btn.setEnabled(True)
        self.status_lbl.setText("处理失败")
        QMessageBox.critical(self, "错误", msg)
