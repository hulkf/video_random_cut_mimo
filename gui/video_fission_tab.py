import os
import subprocess

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QSpinBox, QSlider, QComboBox, QCheckBox,
)
from PyQt5.QtCore import Qt, pyqtSignal, QSize

from core.video_fission import VideoFission, FissionStopped
from gui.config import get_config, set_config
from utils.path_utils import strip_quotes
from gui.common.base_tab import BaseTab
from gui.common.base_worker import BaseWorker
from gui.common.path_row import LINEEDIT_QSS


class VideoFissionWorker(BaseWorker):
    # progress/finished/error 继承 BaseWorker（progress(int,int,str)）
    video_done = pyqtSignal(dict)   # 单个视频完成（额外专用信号）
    stopped = pyqtSignal(list)      # 中断时携带部分结果（额外专用信号）

    def __init__(self, options, input_sources, output_folder, separate_folder, max_workers=0):
        super().__init__()
        self.options = options
        # input_sources: [(path, count), ...] 每个输入源独立裂变数量
        self.input_sources = input_sources
        self.output_folder = output_folder
        self.separate_folder = separate_folder
        self.max_workers = max_workers
        self._engine = None

    def run(self):
        try:
            self._engine = VideoFission(self.options)
            results = self._engine.fission_folder(
                self.input_sources, self.output_folder,
                separate_folder=self.separate_folder,
                callback=self._cb,
                max_workers=self.max_workers or None,
            )
            self.finished.emit(results)
        except FissionStopped:
            self.stopped.emit(list(self._engine.partial_results))
        except Exception as e:
            self.error.emit(str(e))

    def request_stop(self):
        """请求中断：置父类停止标志 + 立即终止正在运行的 ffmpeg 并停止后续处理。"""
        super().request_stop()
        if self._engine is not None:
            self._engine.request_stop()

    def _cb(self, current, total, rel):
        if self.stopped():
            raise FissionStopped("用户中断")
        self.progress.emit(current, total, rel)


class _WrappingRow(QWidget):
    """根据可用宽度自动在“单行”与“换行”之间切换，避免窗口缩放时控件堆叠。

    main_widgets: 始终位于主行，第一个控件会拉伸占满剩余宽度（如输入框）。
    tail_widgets: 宽度足够时排在主行末尾；不够时自动换到下方缩进副行。
    """

    def __init__(self, main_widgets, tail_widgets, spacing=8, indent=12, parent=None):
        super().__init__(parent)
        self._spacing = spacing
        self._main_widgets = list(main_widgets)
        self._tail_widgets = list(tail_widgets)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(4)

        self._main = QHBoxLayout()
        self._main.setSpacing(spacing)
        self._tail = QHBoxLayout()
        self._tail.setSpacing(spacing)
        self._tail.setContentsMargins(indent, 0, 0, 0)

        self._root.addLayout(self._main)
        self._root.addLayout(self._tail)

        for i, w in enumerate(self._main_widgets):
            self._main.addWidget(w, 1 if i == 0 else 0)
        for w in self._tail_widgets:
            self._tail.addWidget(w)

        self._tail_on_main = False
        self._in_relayout = False

    def _wid_of(self, widgets):
        total = 0
        for w in widgets:
            total += max(w.sizeHint().width(), w.minimumSize().width())
        if len(widgets) > 1:
            total += self._spacing * (len(widgets) - 1)
        return total

    def _single_line_width(self):
        return self._wid_of(self._main_widgets) + self._spacing + self._wid_of(self._tail_widgets)

    def minimumSizeHint(self):
        # 始终按“换行态”报告最小宽度（只主行），让父布局允许本行缩到主行宽度；
        # 否则 tail 在主行时最小宽度被撑大，窗口缩放时永远缩不进去、换行无法触发。
        w = self._wid_of(self._main_widgets)
        h = max((x.sizeHint().height() for x in self._main_widgets), default=0)
        return QSize(w, h)

    def sizeHint(self):
        main_h = max((x.sizeHint().height() for x in self._main_widgets), default=0)
        if self._tail_on_main:
            return QSize(self._single_line_width(), main_h)
        tail_h = max((x.sizeHint().height() for x in self._tail_widgets), default=0)
        w = max(self._wid_of(self._main_widgets), self._wid_of(self._tail_widgets))
        return QSize(w, main_h + self._root.spacing() + tail_h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def showEvent(self, event):
        super().showEvent(event)
        self._relayout()

    def _relayout(self):
        if self._in_relayout:
            return
        avail = self.width()
        need = self._single_line_width()
        if avail >= need and not self._tail_on_main:
            self._in_relayout = True
            for w in self._tail_widgets:
                self._tail.removeWidget(w)
                self._main.addWidget(w)
            self._tail_on_main = True
            self._in_relayout = False
            self.updateGeometry()
        elif avail < need and self._tail_on_main:
            self._in_relayout = True
            for w in self._tail_widgets:
                self._main.removeWidget(w)
                self._tail.addWidget(w)
            self._tail_on_main = False
            self._in_relayout = False
            self.updateGeometry()


class VideoFissionTab(BaseTab):
    # QLineEdit 样式统一从 gui/common/path_row.LINEEDIT_QSS 导入（唯一来源）

    def __init__(self):
        super().__init__()
        self.init_ui()
        self.load_config()

    # ── UI ────────────────────────────────────────────────────
    def init_ui(self):
        main = QVBoxLayout()
        main.setSpacing(12)
        main.setContentsMargins(18, 14, 18, 14)

        # ── 输入设置（最多 3 个源，每个源可单独设置裂变数量）──
        io_group = QGroupBox("输入设置（最多 3 个源：文件夹或单个视频文件，每个源独立设置裂变数量）")
        io_lay = QVBoxLayout()
        io_lay.setSpacing(8)

        self.input_edits = []
        self.count_spins = []
        for i in range(1, 4):
            edit = QLineEdit()
            edit.setMinimumHeight(32)
            edit.setMinimumWidth(200)
            edit.setPlaceholderText(
                "输入 {}：文件夹路径，或直接填一个视频文件路径（可留空）".format(i))
            # 三保险：每个 QLineEdit 单独强制样式（绕开父级继承 / qt-material 覆盖）
            edit.setStyleSheet(LINEEDIT_QSS)
            btn = QPushButton("浏览")
            btn.setFixedWidth(68)
            btn.setMinimumHeight(32)
            btn.clicked.connect(lambda _, idx=i - 1: self._browse_input(idx))

            # 每个输入源自己的裂变数量（作为尾部组，宽度不够时自动换到下方副行）
            count_lbl = QLabel("每个视频生成:")
            count_spin = QSpinBox()
            count_spin.setRange(1, 200)
            count_spin.setValue(10)
            count_spin.setMinimumHeight(30)
            count_spin.setFixedWidth(70)
            count_spin.setToolTip("该输入源里的每个视频裂变成多少个不同版本")
            ver_lbl = QLabel("个版本")

            row = _WrappingRow(
                main_widgets=[edit, btn],
                tail_widgets=[count_lbl, count_spin, ver_lbl],
            )
            io_lay.addWidget(row)
            self.input_edits.append(edit)
            self.count_spins.append(count_spin)

        io_group.setLayout(io_lay)
        main.addWidget(io_group)

        # ── 输出设置（独立放在下方，与输入分开）────────────────
        out_group = QGroupBox("输出设置（3 个输入源共享同一个输出文件夹）")
        out_lay = QHBoxLayout()
        out_lay.setSpacing(8)
        self.output_edit = QLineEdit()
        self.output_edit.setMinimumHeight(32)
        self.output_edit.setPlaceholderText("裂变后的视频保存到这里")
        # 三保险：输出框也单独强制样式
        self.output_edit.setStyleSheet(LINEEDIT_QSS)
        out_btn = QPushButton("浏览")
        out_btn.setFixedWidth(68)
        out_btn.setMinimumHeight(32)
        out_btn.clicked.connect(self._browse_output)
        out_lay.addWidget(self.output_edit, 1)
        out_lay.addWidget(out_btn)
        out_group.setLayout(out_lay)
        main.addWidget(out_group)

        # ── 裂变参数（数量已在每个输入源行内单独设置）──────────
        param_group = QGroupBox("裂变参数")
        param_lay = QHBoxLayout()
        param_lay.setSpacing(14)

        param_lay.addWidget(QLabel("强度:"))
        self.intensity_combo = QComboBox()
        self.intensity_combo.addItems(["轻微", "中等", "强烈"])
        self.intensity_combo.setCurrentIndex(0)
        self.intensity_combo.setMinimumHeight(30)
        param_lay.addWidget(self.intensity_combo)

        param_lay.addSpacing(14)

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
        self.crf_slider.setMinimumWidth(80)
        self.crf_slider.valueChanged.connect(self._on_crf)
        param_lay.addWidget(self.crf_slider)
        self.crf_label = QLabel("20")
        self.crf_label.setMinimumWidth(22)
        param_lay.addWidget(self.crf_label)

        param_lay.addSpacing(14)

        param_lay.addWidget(QLabel("并行任务:"))
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(0, 16)
        self.workers_spin.setValue(0)
        self.workers_spin.setMinimumHeight(30)
        self.workers_spin.setFixedWidth(60)
        self.workers_spin.setToolTip(
            "同时编码几个视频。0=自动（硬件编码3个/软件编码按CPU核数）；\n"
            "调大可更快，但吃满CPU/显卡；电脑要做别的事时调小")
        param_lay.addWidget(self.workers_spin)
        self.workers_hint = QLabel("0=自动")
        self.workers_hint.setStyleSheet("color: gray;")
        param_lay.addWidget(self.workers_hint)

        param_lay.addStretch()
        param_group.setLayout(param_lay)
        main.addWidget(param_group)

        # ── 存放规则 ───────────────────────────────────────────
        rule_group = QGroupBox("存放规则")
        rule_lay = QHBoxLayout()
        rule_lay.setSpacing(16)
        self.separate_cb = QCheckBox("每个文件的裂变结果放单独文件夹")
        self.separate_cb.setChecked(True)
        self.separate_cb.setToolTip("勾选：产物放在「输出/原名_fissions」子文件夹；\n不勾选：所有产物统一平铺在输出文件夹下")
        rule_lay.addWidget(self.separate_cb)
        self.force_1080_cb = QCheckBox("统一转 1080×1920（9:16）")
        self.force_1080_cb.setToolTip(
            "勾选：所有产物统一输出 1080×1920 竖屏（9:16 源无损，其他比例居中裁切不变形）。\n"
            "适合千川等要求统一尺寸的平台；不勾选则保持原视频分辨率")
        rule_lay.addWidget(self.force_1080_cb)
        rule_lay.addStretch()
        rule_group.setLayout(rule_lay)
        main.addWidget(rule_group)

        # ── 开始 / 停止按钮 ────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.start_btn = QPushButton("开始裂变")
        self.start_btn.setMinimumHeight(42)
        self.start_btn.clicked.connect(self._start)
        btn_row.addWidget(self.start_btn, 1)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setMinimumHeight(42)
        self.stop_btn.setFixedWidth(110)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet(
            "QPushButton { background-color: #a32d2d; color: white; }"
            "QPushButton:disabled { background-color: #3a3a3a; color: #888; }"
        )
        self.stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(self.stop_btn)
        main.addLayout(btn_row)

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

        # ===== Tab 内部双保险：无论全局主题 QSS 怎么变，tab 内 setStyleSheet 强制 QLineEdit 样式 =====
        self.setStyleSheet(LINEEDIT_QSS)

    # ── 配置 ──────────────────────────────────────────────────
    def load_config(self):
        paths = get_config("video_fission", "input_folders", "")
        if isinstance(paths, list):
            for i, edit in enumerate(self.input_edits):
                if i < len(paths):
                    edit.setText(paths[i])
        counts = get_config("video_fission", "input_counts", "")
        if isinstance(counts, list):
            for i, spin in enumerate(self.count_spins):
                if i < len(counts):
                    try:
                        spin.setValue(int(counts[i]))
                    except (TypeError, ValueError):
                        pass
        self.output_edit.setText(get_config("video_fission", "output_folder", ""))
        idx_map = {"mild": 0, "medium": 1, "strong": 2}
        self.intensity_combo.setCurrentIndex(idx_map.get(get_config("video_fission", "intensity", "mild"), 0))
        preset = get_config("video_fission", "preset", "ultrafast")
        self.preset_combo.setCurrentText(preset if preset in ("ultrafast", "superfast", "veryfast") else "ultrafast")
        self.crf_slider.setValue(int(get_config("video_fission", "crf", "20")))
        self.workers_spin.setValue(int(get_config("video_fission", "max_workers", "0")))
        self.separate_cb.setChecked(get_config("video_fission", "separate_folder", True) in (True, "true", "True"))
        self.force_1080_cb.setChecked(get_config("video_fission", "force_1080x1920", False) in (True, "true", "True"))
        self._on_crf(self.crf_slider.value())

    def save_config(self):
        set_config("video_fission", "input_folders",
                   [edit.text() for edit in self.input_edits])
        set_config("video_fission", "input_counts",
                   [spin.value() for spin in self.count_spins])
        set_config("video_fission", "output_folder", self.output_edit.text())
        imap = {0: "mild", 1: "medium", 2: "strong"}
        set_config("video_fission", "intensity", imap.get(self.intensity_combo.currentIndex(), "mild"))
        set_config("video_fission", "preset", self.preset_combo.currentText())
        set_config("video_fission", "crf", str(self.crf_slider.value()))
        set_config("video_fission", "max_workers", str(self.workers_spin.value()))
        set_config("video_fission", "separate_folder", str(self.separate_cb.isChecked()))
        set_config("video_fission", "force_1080x1920", str(self.force_1080_cb.isChecked()))

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
        # 每个输入源配对各自的裂变数量
        input_sources = []
        for edit, spin in zip(self.input_edits, self.count_spins):
            p = strip_quotes(edit.text())
            if p:
                input_sources.append((p, spin.value()))
        output_dir = strip_quotes(self.output_edit.text())

        if not input_sources:
            QMessageBox.warning(self, "提示", "请至少填一个输入源（文件夹或视频文件）")
            return
        for p, _c in input_sources:
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

        options = {
            "intensity": self._intensity_key(),
            "preset": self.preset_combo.currentText(),
            "crf": self.crf_slider.value(),
            "force_1080x1920": self.force_1080_cb.isChecked(),
        }

        worker = VideoFissionWorker(
            options, input_sources, output_dir, self.separate_cb.isChecked(),
            max_workers=self.workers_spin.value())
        worker.video_done.connect(self._on_video_done)
        worker.stopped.connect(self._on_stopped)
        if not self.start_worker(worker):
            return

    def set_busy(self, busy):
        self.start_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)

    def _stop(self):
        """点击停止：请求中断，正在处理的视频会终止，已完成的保留。"""
        if self.worker and self.worker.isRunning():
            self.stop_btn.setEnabled(False)
            self.status_lbl.setText("正在停止...")
            self.worker.request_stop()

    def on_worker_progress(self, current, total, rel):
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

    def on_worker_finished(self, results):
        super().on_worker_finished(results)
        self.progress_bar.setValue(100)
        self.pct_label.setText("100%")
        total_videos = sum(len(r["outputs"]) for r in results)
        self.status_lbl.setText("完成！共 {} 个源视频 → {} 个裂变产物".format(len(results), total_videos))
        QMessageBox.information(self, "完成",
            "裂变完成！\n\n源视频: {} 个\n生成产物: {} 个\n\n双击结果行可查看对应文件夹。".format(
                len(results), total_videos))

    def _on_stopped(self, results):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        total_videos = sum(len(r["outputs"]) for r in results)
        if results:
            self.progress_bar.setValue(100)
            self.pct_label.setText("已停止")
        self.status_lbl.setText("已停止（保留 {} 个视频的 {} 个产物）".format(len(results), total_videos))
        QMessageBox.information(self, "已停止",
            "裂变已停止。\n\n已完成源视频: {} 个\n已保留产物: {} 个\n\n未完成的已丢弃。".format(
                len(results), total_videos))

    def on_worker_error(self, msg):
        super().on_worker_error(msg)
        self.status_lbl.setText("处理失败")
