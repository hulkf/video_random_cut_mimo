import os

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QTextEdit, QComboBox, QDoubleSpinBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal

from core.keyword_remover import KeywordRemover, collect_videos, parse_keywords
from gui.config import get_config, set_config
from gui.subtitle_tab import FIREMODELS_DIR, FUNASR_DIR, SENSEVOICE_DIR


class KeywordRemoveWorker(QThread):
    progress = pyqtSignal(int, int)
    video_done = pyqtSignal(dict)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, input_folder, output_folder, keywords,
                 padding, model_type, model_path):
        super().__init__()
        self.input_folder = input_folder
        self.output_folder = output_folder
        self.keywords = keywords
        self.padding = padding
        self.model_type = model_type
        self.model_path = model_path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            videos = collect_videos(self.input_folder)
            if not videos:
                raise ValueError("输入文件夹中没有找到视频文件")

            os.makedirs(self.output_folder, exist_ok=True)
            from core.onnx_asr import OnnxASR
            asr = OnnxASR(self.model_path, self.model_type)
            remover = KeywordRemover(self.keywords, self.padding)
            results = []
            total = len(videos)

            for index, video_path in enumerate(videos):
                if self._cancelled:
                    break

                result = self.process_video(video_path, asr, remover)
                results.append(result)
                self.video_done.emit(result)
                self.progress.emit(index + 1, total)

            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))

    def process_video(self, video_path, asr, remover):
        rel_path = os.path.relpath(video_path, self.input_folder)
        rel_base, _ = os.path.splitext(rel_path)
        output_path = os.path.join(self.output_folder, f"{rel_base}_keywords_removed.mp4")

        try:
            segments = asr.transcribe(video_path)
            if not segments:
                return {
                    "video": rel_path,
                    "output": "",
                    "success": False,
                    "message": "识别失败或没有识别到口播",
                    "ranges": [],
                }

            import utils.video_utils as vu
            duration = vu.get_video_duration(video_path)
            delete_ranges = remover.find_delete_ranges(segments, duration)
            result_path, actual_ranges = remover.remove_ranges(
                video_path, output_path, delete_ranges
            )

            if actual_ranges:
                message = f"删除 {len(actual_ranges)} 段"
            else:
                message = "未命中关键词，已复制原视频"

            return {
                "video": rel_path,
                "output": result_path,
                "success": True,
                "message": message,
                "ranges": actual_ranges,
            }
        except Exception as e:
            return {
                "video": rel_path,
                "output": "",
                "success": False,
                "message": str(e),
                "ranges": [],
            }


class KeywordRemoveTab(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.results = []
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
        self.input_folder.setPlaceholderText("选择视频文件夹...")
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

        keyword_group = QGroupBox("关键词设置")
        keyword_layout = QVBoxLayout()
        self.keyword_text = QTextEdit()
        self.keyword_text.setPlaceholderText("输入要删除的关键词，多个关键词可用换行、逗号或顿号分隔")
        self.keyword_text.setMaximumHeight(90)
        keyword_layout.addWidget(self.keyword_text)

        padding_row = QHBoxLayout()
        padding_row.addWidget(QLabel("前后余量(秒):"))
        self.padding_spin = QDoubleSpinBox()
        self.padding_spin.setRange(0.0, 2.0)
        self.padding_spin.setSingleStep(0.05)
        self.padding_spin.setDecimals(2)
        self.padding_spin.setValue(0.15)
        self.padding_spin.setMinimumHeight(28)
        padding_row.addWidget(self.padding_spin)
        padding_row.addStretch()
        keyword_layout.addLayout(padding_row)
        keyword_group.setLayout(keyword_layout)

        model_group = QGroupBox("识别设置")
        model_layout = QVBoxLayout()
        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        model_row.addWidget(QLabel("识别引擎:"))
        self.model_combo = QComboBox()
        self.model_combo.addItems(["FireRedASR", "FunASR (Paraformer)", "SenseVoice"])
        self.model_combo.setMinimumHeight(28)
        self.model_combo.currentIndexChanged.connect(self.on_model_changed)
        model_row.addWidget(self.model_combo)
        model_row.addStretch()
        model_layout.addLayout(model_row)

        model_path_row = QHBoxLayout()
        model_path_row.setSpacing(8)
        model_path_row.addWidget(QLabel("模型路径:"))
        self.model_path_input = QLineEdit()
        self.model_path_input.setMinimumHeight(30)
        model_browse_btn = QPushButton("浏览")
        model_browse_btn.setFixedWidth(80)
        model_browse_btn.clicked.connect(self.browse_model)
        model_path_row.addWidget(self.model_path_input, 1)
        model_path_row.addWidget(model_browse_btn)
        model_layout.addLayout(model_path_row)
        model_group.setLayout(model_layout)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始去关键词")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self.start_remove)
        self.cancel_btn = QPushButton("停止")
        self.cancel_btn.setMinimumHeight(36)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_remove)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.cancel_btn)

        self.progress_bar = QProgressBar()
        self.stats_label = QLabel("统计: 等待处理...")
        self.stats_label.setStyleSheet("font-weight: bold; padding: 5px;")

        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels(["视频文件", "删除片段", "输出文件", "状态"])
        self.result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.result_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.result_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.result_table.setColumnWidth(3, 90)
        self.result_table.setMinimumHeight(220)

        hint = QLabel(
            "命中关键词时，会按关键词在识别文本中的位置估算时间段，并删除该片段。"
            "输出会保留输入文件夹的子目录结构。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; padding: 5px;")

        layout.addWidget(input_group)
        layout.addWidget(keyword_group)
        layout.addWidget(model_group)
        layout.addLayout(btn_row)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.stats_label)
        layout.addWidget(self.result_table, 1)
        layout.addWidget(hint)
        self.setLayout(layout)

    def default_model_path(self, model_type):
        if model_type == "FunASR (Paraformer)":
            return get_config("settings", "funasr_model_path", FUNASR_DIR)
        if model_type == "SenseVoice":
            return get_config("settings", "sensevoice_model_path", SENSEVOICE_DIR)
        return get_config("settings", "fireredasr_model_path", FIREMODELS_DIR)

    def load_config(self):
        self.input_folder.setText(get_config("keyword_remove", "input_folder", ""))
        self.output_folder.setText(get_config("keyword_remove", "output_folder", ""))
        self.keyword_text.setPlainText(get_config("keyword_remove", "keywords", ""))
        self.padding_spin.setValue(float(get_config("keyword_remove", "padding", "0.15")))
        model_type = get_config("keyword_remove", "model_type", "FireRedASR")
        index = self.model_combo.findText(model_type)
        self.model_combo.setCurrentIndex(index if index >= 0 else 0)
        self.model_path_input.setText(
            get_config("keyword_remove", "model_path", self.default_model_path(self.model_combo.currentText()))
        )

    def save_config(self):
        set_config("keyword_remove", "input_folder", self.input_folder.text())
        set_config("keyword_remove", "output_folder", self.output_folder.text())
        set_config("keyword_remove", "keywords", self.keyword_text.toPlainText())
        set_config("keyword_remove", "padding", str(self.padding_spin.value()))
        set_config("keyword_remove", "model_type", self.model_combo.currentText())
        set_config("keyword_remove", "model_path", self.model_path_input.text())

    def on_model_changed(self, *args):
        self.model_path_input.setText(self.default_model_path(self.model_combo.currentText()))

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

    def browse_model(self):
        folder = QFileDialog.getExistingDirectory(self, "选择 ASR 模型目录")
        if folder:
            self.model_path_input.setText(folder)
            self.save_config()

    def start_remove(self):
        input_folder = self.input_folder.text()
        output_folder = self.output_folder.text()
        keywords = parse_keywords(self.keyword_text.toPlainText())

        if not input_folder or not output_folder:
            QMessageBox.warning(self, "警告", "请选择输入和输出文件夹")
            return
        if not keywords:
            QMessageBox.warning(self, "警告", "请至少输入一个关键词")
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "警告", "任务正在执行中")
            return

        self.save_config()
        self.results = []
        self.result_table.setRowCount(0)
        self.progress_bar.setValue(0)
        self.stats_label.setText("统计: 处理中...")
        self.start_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)

        self.worker = KeywordRemoveWorker(
            input_folder, output_folder, keywords,
            self.padding_spin.value(),
            self.model_combo.currentText(),
            self.model_path_input.text()
        )
        self.worker.progress.connect(self.on_progress)
        self.worker.video_done.connect(self.on_video_done)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def cancel_remove(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.stats_label.setText("统计: 已停止")

    def on_progress(self, current, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)

    def on_video_done(self, result):
        self.results.append(result)
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)

        ranges_text = ", ".join(
            f"{start:.2f}-{end:.2f}s" for start, end in result.get("ranges", [])
        )
        if not ranges_text:
            ranges_text = "-"

        self.result_table.setItem(row, 0, QTableWidgetItem(result["video"]))
        self.result_table.setItem(row, 1, QTableWidgetItem(ranges_text))
        self.result_table.setItem(row, 2, QTableWidgetItem(result.get("output", "")))

        status_item = QTableWidgetItem("成功" if result["success"] else "失败")
        if not result["success"]:
            status_item.setForeground(Qt.red)
            status_item.setToolTip(result.get("message", ""))
        else:
            status_item.setToolTip(result.get("message", ""))
        self.result_table.setItem(row, 3, status_item)

    def on_finished(self, results):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(100)

        success_count = sum(1 for item in results if item["success"])
        fail_count = len(results) - success_count
        msg = f"统计: 共{len(results)}个视频, 成功{success_count}个"
        if fail_count:
            msg += f", 失败{fail_count}个"
        self.stats_label.setText(msg)
        QMessageBox.information(self, "完成", msg)

    def on_error(self, msg):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.stats_label.setText("统计: 处理失败")
        QMessageBox.critical(self, "错误", msg)
