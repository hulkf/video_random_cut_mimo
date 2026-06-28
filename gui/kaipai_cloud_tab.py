import os
import json
from datetime import datetime
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QTextEdit
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from gui.config import get_config, set_config

# 延迟导入 sdk，避免启动时报错
def get_skill_client():
    os.environ["MT_AK"] = get_config("kaipai", "api_key", "")
    os.environ["MT_SK"] = get_config("kaipai", "secret_key", "")
    from sdk import SkillClient
    return SkillClient()


class KaipaiWorker(QThread):
    progress = pyqtSignal(int, int, str)  # current, total, message
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    TASK_MAP = {
        "图片去水印": "eraser_watermark",
        "图片画质修复": "image_restoration",
        "视频智能全消": "videoscreenclear",
        "视频画质修复": "hdvideoallinone",
    }

    def __init__(self, files, task_name, params=None):
        super().__init__()
        self.files = files
        self.task_name = task_name
        self.params = params or {}
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            client = get_skill_client()
            self.log.emit("SDK 客户端初始化成功")

            results = []
            total = len(self.files)

            for idx, file_path in enumerate(self.files):
                if self._stop:
                    self.log.emit("用户停止")
                    break

                file_name = os.path.basename(file_path)
                self.progress.emit(idx, total, f"正在处理: {file_name}")
                self.log.emit(f"[{idx+1}/{total}] 处理: {file_name}")

                try:
                    result = client.execute(
                        task_name=self.task_name,
                        source=file_path,
                        params=self.params if self.params else None
                    )

                    output_urls = result.get("output_urls", [])
                    task_id = result.get("task_id", "")

                    results.append({
                        "file": file_name,
                        "path": file_path,
                        "status": "成功",
                        "task_id": task_id,
                        "output_url": output_urls[0] if output_urls else "",
                    })
                    self.log.emit(f"  ✓ 成功: {output_urls[0] if output_urls else 'N/A'}")
                except Exception as e:
                    results.append({
                        "file": file_name,
                        "path": file_path,
                        "status": "失败",
                        "task_id": "",
                        "output_url": "",
                        "error": str(e),
                    })
                    self.log.emit(f"  ✗ 失败: {e}")

                self.progress.emit(idx + 1, total, f"完成 {idx+1}/{total}")

            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class KaipaiCloudTab(QWidget):
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

        api_hint = QLabel("API 配置请在「设置」Tab 中填写")
        api_hint.setStyleSheet("color: gray; padding: 5px;")
        layout.addWidget(api_hint)

        task_group = QGroupBox("任务设置")
        task_layout = QVBoxLayout()

        task_row = QHBoxLayout()
        task_row.setSpacing(8)
        task_row.addWidget(QLabel("处理功能:"))
        self.task_combo = QComboBox()
        self.task_combo.addItems(["图片去水印", "图片画质修复", "视频智能全消", "视频画质修复"])
        self.task_combo.setMinimumHeight(28)
        self.task_combo.currentTextChanged.connect(self.on_task_changed)
        task_row.addWidget(self.task_combo)
        task_layout.addLayout(task_row)

        params_layout = QHBoxLayout()
        params_layout.setSpacing(8)

        params_layout.addWidget(QLabel("消除目标:"))
        self.target_combo = QComboBox()
        self.target_combo.addItems(["watermark", "text", "logo"])
        self.target_combo.setMinimumHeight(28)
        self.target_combo.setToolTip("图片去水印：选择要消除的目标类型")
        params_layout.addWidget(self.target_combo)

        params_layout.addWidget(QLabel("修复模式:"))
        self.ir_mode_combo = QComboBox()
        self.ir_mode_combo.addItems(["4", "1", "2", "3"])
        self.ir_mode_combo.setMinimumHeight(28)
        self.ir_mode_combo.setToolTip("图片画质修复：选择修复强度模式")
        params_layout.addWidget(self.ir_mode_combo)

        params_layout.addWidget(QLabel("切片方式:"))
        self.slice_way_combo = QComboBox()
        self.slice_way_combo.addItems(["1", "2"])
        self.slice_way_combo.setMinimumHeight(28)
        self.slice_way_combo.setToolTip("视频智能全消：选择视频切片处理方式")
        params_layout.addWidget(self.slice_way_combo)

        task_layout.addLayout(params_layout)
        task_group.setLayout(task_layout)

        input_group = QGroupBox("输入设置")
        input_layout = QVBoxLayout()

        file_layout = QHBoxLayout()
        file_layout.setSpacing(8)
        self.file_input = QLineEdit()
        self.file_input.setPlaceholderText("选择文件或文件夹...")
        self.file_input.setMinimumHeight(28)
        file_layout.addWidget(self.file_input, 1)

        file_btn = QPushButton("选择文件")
        file_btn.setFixedWidth(90)
        file_btn.clicked.connect(self.browse_file)
        file_layout.addWidget(file_btn)

        folder_btn = QPushButton("选择文件夹")
        folder_btn.setFixedWidth(90)
        folder_btn.clicked.connect(self.browse_folder)
        file_layout.addWidget(folder_btn)
        input_layout.addLayout(file_layout)

        input_group.setLayout(input_layout)

        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("开始处理")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self.start_processing)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setMinimumHeight(36)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_processing)

        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)

        self.progress_bar = QProgressBar()
        self.status_label = QLabel("就绪")

        log_group = QGroupBox("处理日志")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)

        result_group = QGroupBox("处理结果")
        result_layout = QVBoxLayout()

        stats_layout = QHBoxLayout()
        self.stats_label = QLabel("今日调用次数: 0 | 剩余美豆: --")
        stats_layout.addWidget(self.stats_label)
        stats_layout.addStretch()
        self.refresh_btn = QPushButton("刷新额度")
        self.refresh_btn.clicked.connect(self.refresh_quota)
        stats_layout.addWidget(self.refresh_btn)
        result_layout.addLayout(stats_layout)

        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels(["文件", "状态", "任务ID", "输出URL"])
        self.result_table.setMinimumHeight(150)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setColumnWidth(0, 200)
        self.result_table.setColumnWidth(1, 60)
        self.result_table.setColumnWidth(2, 280)
        result_layout.addWidget(self.result_table)

        result_group.setLayout(result_layout)

        layout.addWidget(api_group)
        layout.addWidget(task_group)
        layout.addWidget(input_group)
        layout.addLayout(btn_layout)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)
        layout.addWidget(log_group)
        layout.addWidget(result_group, 1)

        self.setLayout(layout)

    def load_config(self):
        task = get_config("kaipai", "last_task", "图片去水印")
        idx = self.task_combo.findText(task)
        if idx >= 0:
            self.task_combo.setCurrentIndex(idx)

    def save_config(self):
        set_config("kaipai", "last_task", self.task_combo.currentText())

    def on_task_changed(self, task_name):
        self.target_combo.setVisible(task_name == "图片去水印")
        self.ir_mode_combo.setVisible(task_name == "图片画质修复")
        self.slice_way_combo.setVisible(task_name == "视频智能全消")

    def browse_file(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "选择文件", "",
            "图片文件 (*.jpg *.jpeg *.png *.bmp *.webp);;视频文件 (*.mp4 *.avi *.mov *.mkv *.flv);;所有文件 (*)"
        )
        if file:
            self.file_input.setText(file)

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            self.file_input.setText(folder)

    def log_message(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {msg}")

    def start_processing(self):
        api_key = get_config("kaipai", "api_key", "")
        secret_key = get_config("kaipai", "secret_key", "")
        input_path = self.file_input.text()
        task_name = self.task_combo.currentText()

        if not api_key or not secret_key:
            QMessageBox.warning(self, "警告", "请先在「设置」Tab 中配置 API KEY 和 Secret Key")
            return

        if not input_path:
            QMessageBox.warning(self, "警告", "请选择文件或文件夹")
            return

        self.save_config()

        files = self._collect_files(input_path, task_name)
        if not files:
            QMessageBox.warning(self, "警告", "未找到可处理的文件")
            return

        params = self._build_params(task_name)
        self.log_message(f"开始处理 {len(files)} 个文件，任务: {task_name}")
        if params:
            self.log_message(f"自定义参数: {params}")

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)

        self.worker = KaipaiWorker(files, task_name, params)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.log.connect(self.log_message)
        self.worker.start()

    def _build_params(self, task_name):
        params = {}
        if task_name == "图片去水印":
            target = self.target_combo.currentText()
            params = {"parameter": {"target": target}}
        elif task_name == "图片画质修复":
            ir_mode = int(self.ir_mode_combo.currentText())
            params = {"parameter": {"ir_mode": ir_mode}}
        elif task_name == "视频智能全消":
            slice_way = int(self.slice_way_combo.currentText())
            params = {"extra": {"slice_way": slice_way}}
        return params

    def _collect_files(self, input_path, task_name):
        if os.path.isfile(input_path):
            return [input_path]

        if os.path.isdir(input_path):
            is_video_task = "视频" in task_name
            if is_video_task:
                exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
            else:
                exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

            files = []
            for f in os.listdir(input_path):
                if f.lower().endswith(exts):
                    files.append(os.path.join(input_path, f))
            return files

        return []

    def stop_processing(self):
        if self.worker:
            self.worker.stop()
            self.log_message("正在停止...")

    def on_progress(self, current, total, message):
        progress = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(progress)
        self.status_label.setText(message)

    def on_finished(self, results):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.results = results
        self.status_label.setText(f"处理完成: {len(results)} 个文件")

        self.result_table.setRowCount(len(results))
        for i, r in enumerate(results):
            self.result_table.setItem(i, 0, QTableWidgetItem(r["file"]))

            status_item = QTableWidgetItem(r["status"])
            if r["status"] == "成功":
                status_item.setBackground(QColor("#4CAF50"))
                status_item.setForeground(QColor("white"))
            else:
                status_item.setBackground(QColor("#F44336"))
                status_item.setForeground(QColor("white"))
            self.result_table.setItem(i, 1, status_item)

            self.result_table.setItem(i, 2, QTableWidgetItem(r.get("task_id", "")))
            self.result_table.setItem(i, 3, QTableWidgetItem(r.get("output_url", "")))

        success_count = sum(1 for r in results if r["status"] == "成功")
        self.log_message(f"处理完成: 成功 {success_count}/{len(results)}")

    def on_error(self, msg):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("处理失败")
        self.log_message(f"错误: {msg}")
        QMessageBox.critical(self, "错误", msg)

    def refresh_quota(self):
        try:
            client = get_skill_client()
            config = client.wapi.request(
                "/skill/config.json",
                method="POST",
                body={"gid": "", "version": "v1.0.0"}
            )
            self.log_message(f"刷新成功，GID: {config.get('gid', 'N/A')}")
            self.stats_label.setText(f"GID: {config.get('gid', 'N/A')}")
        except Exception as e:
            self.log_message(f"刷新失败: {e}")
