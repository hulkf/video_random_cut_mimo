from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog,
    QTableWidget, QTableWidgetItem, QProgressBar,
    QMessageBox, QGroupBox, QCheckBox, QHeaderView, QAbstractItemView,
    QStyle
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from core.text_detector import detect_single_video, _init_worker
from gui.config import get_config, set_config
import os
import sys
import shutil
import glob as glob_module
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed


class TextRecognitionWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, folder_path, frame_interval=1.0, threshold=0.3, max_workers=4):
        super().__init__()
        self.folder_path = folder_path
        self.frame_interval = frame_interval
        self.threshold = threshold
        self.max_workers = max_workers

    def run(self):
        try:
            video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
            video_files = []
            for root, dirs, files in os.walk(self.folder_path):
                for f in files:
                    if f.lower().endswith(video_exts):
                        video_files.append(os.path.join(root, f))

            total = len(video_files)
            if total == 0:
                self.finished.emit([])
                return

            tasks = [
                (v, self.frame_interval, self.threshold)
                for v in video_files
            ]

            results = []
            with ProcessPoolExecutor(max_workers=self.max_workers,
                                     initializer=_init_worker) as executor:
                future_map = {
                    executor.submit(detect_single_video, t): t[0]
                    for t in tasks
                }
                for future in as_completed(future_map):
                    video_path, has_text, frames_dir = future.result()
                    rel_path = os.path.relpath(video_path, self.folder_path)
                    results.append({
                        "file": rel_path,
                        "full_path": video_path,
                        "has_text": has_text,
                        "frames_dir": frames_dir,
                    })
                    self.progress.emit(len(results), total)

            results.sort(key=lambda r: r["file"])
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class TextRecognitionTab(QWidget):
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
        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)

        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("选择视频文件夹...")
        self.folder_input.setMinimumHeight(30)
        folder_btn = QPushButton("浏览")
        folder_btn.setFixedWidth(80)
        folder_btn.clicked.connect(self.browse_folder)
        input_layout.addWidget(self.folder_input, 1)
        input_layout.addWidget(folder_btn)
        input_group.setLayout(input_layout)

        self.start_btn = QPushButton("开始识别")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self.start_recognition)

        self.progress_bar = QProgressBar()

        self.keep_frames_check = QCheckBox("保留抽帧图片到本地")
        self.keep_frames_check.setChecked(False)

        action_btn_row = QHBoxLayout()
        self.select_text_btn = QPushButton("一键选中包含文字的视频")
        self.select_text_btn.setMinimumHeight(32)
        self.select_text_btn.clicked.connect(self.select_text_videos)
        self.select_text_btn.setEnabled(False)

        self.move_btn = QPushButton("移动选中视频到「包含文本视频」文件夹")
        self.move_btn.setMinimumHeight(32)
        self.move_btn.clicked.connect(self.move_selected)
        self.move_btn.setEnabled(False)

        self.clean_frames_btn = QPushButton("一键清除所有抽帧图片")
        self.clean_frames_btn.setMinimumHeight(32)
        self.clean_frames_btn.clicked.connect(self.clean_all_frames)

        action_btn_row.addWidget(self.select_text_btn)
        action_btn_row.addWidget(self.move_btn)
        action_btn_row.addWidget(self.clean_frames_btn)

        self.result_table = QTableWidget()
        self.result_table.setColumnCount(3)
        self.result_table.setHorizontalHeaderLabels(["文件", "包含文字", "操作"])
        self.result_table.setMinimumHeight(200)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setColumnWidth(0, 300)
        self.result_table.setColumnWidth(1, 80)
        self.result_table.setColumnWidth(2, 60)

        layout.addWidget(input_group)
        layout.addWidget(self.start_btn)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.keep_frames_check)
        layout.addLayout(action_btn_row)
        layout.addWidget(self.result_table, 1)

        self.setLayout(layout)

    def load_config(self):
        self.folder_input.setText(get_config("text_recognition", "folder", ""))
        keep = get_config("text_recognition", "keep_frames", False)
        self.keep_frames_check.setChecked(bool(keep))

    def save_config(self):
        set_config("text_recognition", "folder", self.folder_input.text())
        set_config("text_recognition", "keep_frames", self.keep_frames_check.isChecked())

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择视频文件夹")
        if folder:
            self.folder_input.setText(folder)
            self.save_config()

    def start_recognition(self):
        folder = self.folder_input.text()

        if not folder:
            QMessageBox.warning(self, "警告", "请选择视频文件夹")
            return

        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "警告", "任务正在执行中")
            return

        self.save_config()
        self.start_btn.setEnabled(False)
        self.move_btn.setEnabled(False)
        self.result_table.setRowCount(0)
        self.results = []

        self.worker = TextRecognitionWorker(folder, max_workers=4)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def on_progress(self, current, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)

    def on_finished(self, results):
        self.start_btn.setEnabled(True)
        self.move_btn.setEnabled(True)
        self.select_text_btn.setEnabled(True)
        self.results = results

        self.result_table.setRowCount(len(results))
        for i, r in enumerate(results):
            file_item = QTableWidgetItem(r["file"])
            file_item.setFlags(file_item.flags() & ~Qt.ItemIsEditable)
            self.result_table.setItem(i, 0, file_item)

            has_text = r["has_text"]
            text_item = QTableWidgetItem("是" if has_text else "否")
            text_item.setFlags(text_item.flags() & ~Qt.ItemIsEditable)
            if has_text:
                text_item.setBackground(Qt.darkGreen)
                text_item.setForeground(Qt.white)
            self.result_table.setItem(i, 1, text_item)

            play_btn = QPushButton("播放")
            play_btn.setFixedHeight(24)
            play_btn.clicked.connect(lambda checked, path=r["full_path"]: self.play_video(path))
            self.result_table.setCellWidget(i, 2, play_btn)

        text_count = sum(1 for r in results if r["has_text"])
        QMessageBox.information(self, "完成",
                                f"识别完成，共{len(results)}个视频，{text_count}个包含文字")

    def on_error(self, msg):
        self.start_btn.setEnabled(True)
        QMessageBox.critical(self, "错误", msg)

    def select_text_videos(self):
        self.result_table.clearSelection()
        self.result_table.setSelectionMode(QAbstractItemView.MultiSelection)
        selected_count = 0
        for i, r in enumerate(self.results):
            if r["has_text"]:
                self.result_table.selectRow(i)
                selected_count += 1
        self.result_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        QMessageBox.information(self, "选中完成", f"已选中 {selected_count} 个包含文字的视频")

    def play_video(self, video_path):
        if not os.path.exists(video_path):
            QMessageBox.warning(self, "警告", f"文件不存在: {video_path}")
            return
        try:
            if os.name == 'nt':
                os.startfile(video_path)
            elif sys.platform == 'darwin':
                subprocess.run(['open', video_path])
            else:
                subprocess.run(['xdg-open', video_path])
        except Exception as e:
            QMessageBox.warning(self, "警告", f"无法播放视频: {str(e)}")

    def move_selected(self):
        selected = self.result_table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.warning(self, "警告", "请先选中要移动的视频")
            return

        folder = self.folder_input.text()
        dest_dir = os.path.join(folder, "包含文本视频")

        text_videos = []
        for idx in selected:
            row = idx.row()
            if row < len(self.results) and self.results[row]["has_text"]:
                text_videos.append(self.results[row])

        if not text_videos:
            QMessageBox.warning(self, "警告", "选中的视频不包含文字，无需移动")
            return

        os.makedirs(dest_dir, exist_ok=True)
        moved = 0
        errors = 0
        for v in text_videos:
            src = v["full_path"]
            dst = os.path.join(dest_dir, os.path.basename(src))
            try:
                if os.path.exists(dst):
                    continue
                shutil.move(src, dst)
                moved += 1
            except Exception:
                errors += 1

        msg = f"已移动 {moved} 个视频到「包含文本视频」文件夹"
        if errors:
            msg += f"\n{errors} 个文件移动失败"
        QMessageBox.information(self, "完成", msg)

    def clean_all_frames(self):
        folder = self.folder_input.text()
        if not folder:
            QMessageBox.warning(self, "警告", "请先选择视频文件夹")
            return

        patterns = ["_frames_*", "_frames_tmp"]
        count = 0
        for pat in patterns:
            for item in glob_module.glob(os.path.join(folder, "**", pat), recursive=True):
                if os.path.isdir(item):
                    try:
                        shutil.rmtree(item)
                        count += 1
                    except Exception:
                        pass

        if count == 0:
            QMessageBox.information(self, "提示", "未找到抽帧图片文件夹")
        else:
            QMessageBox.information(self, "完成", f"已清除 {count} 个抽帧文件夹")
