import os
import json
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QSpinBox, QFileDialog,
    QTableWidget, QTableWidgetItem,
    QMessageBox, QGroupBox, QCheckBox, QComboBox
)
from PyQt5.QtCore import pyqtSignal

from core.slicer import VideoSlicer
from gui.common.base_tab import BaseTab
from gui.common.base_worker import BaseWorker
from gui.common.path_row import PathRow, MODE_FOLDER
from gui.common.progress_panel import ProgressPanel
from gui.config import get_config, set_config


class SliceWorker(BaseWorker):
    """视频切片线程（P2：继承 BaseWorker，暂停/继续保留子类逻辑）。"""
    video_done = pyqtSignal(list)
    paused = pyqtSignal()
    resumed = pyqtSignal()

    def __init__(self, folder_path, output_dir, min_dur, max_dur, separate_folders=False, max_workers=2):
        super().__init__()
        self.folder_path = folder_path
        self.output_dir = output_dir
        self.min_dur = min_dur
        self.max_dur = max_dur
        self.separate_folders = separate_folders
        self.max_workers = max(1, int(max_workers))
        self._paused = False
        self._resume_index = 0

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def stop(self):
        """覆盖：设置停止标志（super）并解除暂停。"""
        super().stop()
        self._paused = False

    def _wait_if_paused(self):
        while self._paused and not self.stopped():
            time.sleep(0.1)

    def _slice_one_video(self, folder_dir, file):
        slicer = VideoSlicer(self.min_dur, self.max_dur, detect_text=False)
        video_path = os.path.join(folder_dir, file)
        video_name = os.path.splitext(file)[0]
        if self.separate_folders:
            video_output = os.path.join(self.output_dir, video_name)
        else:
            video_output = self.output_dir
        return slicer.slice_video(video_path, video_output)

    def work(self):
        video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")

        if os.path.isfile(self.folder_path):
            video_files = [os.path.basename(self.folder_path)]
            folder_dir = os.path.dirname(self.folder_path)
        elif os.path.isdir(self.folder_path):
            video_files = [f for f in os.listdir(self.folder_path) if f.lower().endswith(video_exts)]
            folder_dir = self.folder_path
        else:
            self.error.emit(f"路径不存在: {self.folder_path}")
            return

        total = len(video_files)
        if total == 0:
            self.error.emit(f"未找到视频文件\n路径: {self.folder_path}\n请检查路径是否正确，或文件扩展名是否为 .mp4/.avi/.mov/.mkv/.flv")
            return

        all_results = []
        completed = 0
        next_index = self._resume_index
        worker_count = min(self.max_workers, total)

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {}

            def submit_next():
                nonlocal next_index
                while len(futures) < worker_count and next_index < total and not self.stopped():
                    self._wait_if_paused()
                    if self.stopped():
                        break
                    file = video_files[next_index]
                    futures[executor.submit(self._slice_one_video, folder_dir, file)] = next_index
                    next_index += 1

            submit_next()
            while futures:
                done, _ = wait(futures, timeout=0.1, return_when=FIRST_COMPLETED)
                if self.stopped():
                    for future in futures:
                        future.cancel()
                    break
                if not done:
                    continue

                for future in done:
                    idx = futures.pop(future)
                    results = future.result()
                    all_results.extend(results)
                    self.video_done.emit(results)
                    completed += 1
                    self.progress.emit(completed, total, "")
                    self._resume_index = idx + 1

                submit_next()

        report_path = os.path.join(self.output_dir, "slice_report.json")
        os.makedirs(self.output_dir, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)

        self.emit_finished(all_results)


class SliceTab(BaseTab):
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

        # 输入行保留原"文件或文件夹二合一"浏览（PathRow 不支持复合模式）
        folder_layout = QHBoxLayout()
        folder_layout.setSpacing(8)
        self.folder_input = QLineEdit()
        self.folder_input.setMinimumHeight(30)
        self.folder_input.setPlaceholderText("选择视频文件或文件夹...")
        folder_btn = QPushButton("浏览")
        folder_btn.setFixedWidth(80)
        folder_btn.clicked.connect(self.browse_folder)
        folder_layout.addWidget(self.folder_input, 1)
        folder_layout.addWidget(folder_btn)

        # 输出行用公共 PathRow
        self.output_input = PathRow("输出文件夹...", mode=MODE_FOLDER,
                                    on_change=lambda p: self.save_config())

        input_layout.addLayout(folder_layout)
        input_layout.addWidget(self.output_input)
        input_group.setLayout(input_layout)

        params_group = QGroupBox("切片参数")
        params_layout = QHBoxLayout()
        params_layout.setSpacing(8)

        params_layout.addWidget(QLabel("最短时长(秒):"))
        self.min_duration = QSpinBox()
        self.min_duration.setRange(1, 60)
        self.min_duration.setValue(3)
        self.min_duration.setMinimumHeight(28)
        params_layout.addWidget(self.min_duration)

        params_layout.addWidget(QLabel("最长时长(秒):"))
        self.max_duration = QSpinBox()
        self.max_duration.setRange(1, 60)
        self.max_duration.setValue(5)
        self.max_duration.setMinimumHeight(28)
        params_layout.addWidget(self.max_duration)

        params_layout.addWidget(QLabel("并发数:"))
        self.max_workers = QSpinBox()
        self.max_workers.setRange(1, 8)
        self.max_workers.setValue(2)
        self.max_workers.setMinimumHeight(28)
        self.max_workers.setToolTip("批量视频同时处理的数量，机器卡顿时调低")
        params_layout.addWidget(self.max_workers)
        params_layout.addStretch()

        params_group.setLayout(params_layout)

        options_group = QGroupBox("输出选项")
        options_layout = QVBoxLayout()
        options_layout.setSpacing(10)

        self.separate_folders_check = QCheckBox("按原始视频分文件夹存放")
        self.separate_folders_check.setToolTip(
            "选中：每个原始视频的切片放在独立子文件夹中\n"
            "不选中：所有切片放在同一个输出文件夹"
        )
        self.separate_folders_check.setMinimumHeight(26)
        options_layout.addWidget(self.separate_folders_check)

        options_group.setLayout(options_layout)

        organize_group = QGroupBox("切片整理")
        organize_layout = QHBoxLayout()
        organize_layout.setSpacing(8)

        organize_layout.addWidget(QLabel("整理方式:"))
        self.organize_combo = QComboBox()
        self.organize_combo.addItems(["按文件名整理到独立文件夹", "提取独立文件夹到根目录"])
        self.organize_combo.setMinimumHeight(28)
        organize_layout.addWidget(self.organize_combo)

        self.organize_btn = QPushButton("整理切片")
        self.organize_btn.setMinimumWidth(80)
        self.organize_btn.setMinimumHeight(28)
        self.organize_btn.clicked.connect(self.organize_slices)
        organize_layout.addWidget(self.organize_btn)
        organize_layout.addStretch()

        organize_group.setLayout(organize_layout)

        self.start_btn = QPushButton("开始切片")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self.start_slicing)

        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setMinimumHeight(36)
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self.toggle_pause)

        self.stop_btn = QPushButton("结束")
        self.stop_btn.setMinimumHeight(36)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_slicing)

        btn_container = QWidget()
        btn_layout = QHBoxLayout(btn_container)
        btn_layout.setSpacing(8)
        btn_layout.addWidget(self.start_btn, 1)
        btn_layout.addWidget(self.pause_btn, 1)
        btn_layout.addWidget(self.stop_btn, 1)

        # 进度区用公共 ProgressPanel（保留原"总数刻度"语义，不走百分比）
        self.progress_panel = ProgressPanel()
        self.progress_bar = self.progress_panel.bar

        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels(
            ["文件", "开始时间", "时长", "总时长"]
        )
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setMinimumHeight(200)

        self.stats_label = QLabel("统计: 等待切片...")
        self.stats_label.setStyleSheet("font-weight: bold; padding: 5px;")

        layout.addWidget(input_group)
        layout.addWidget(params_group)
        layout.addWidget(options_group)
        layout.addWidget(organize_group)
        layout.addWidget(btn_container)
        layout.addWidget(self.progress_panel)
        layout.addWidget(self.stats_label)
        layout.addWidget(self.result_table, 1)

        self.setLayout(layout)

    def load_config(self):
        self.folder_input.setText(get_config("slice", "folder", ""))
        self.output_input.setText(get_config("slice", "output", ""))
        self.min_duration.setValue(int(get_config("slice", "min_duration", "3")))
        self.max_duration.setValue(int(get_config("slice", "max_duration", "5")))
        self.max_workers.setValue(int(get_config("slice", "max_workers", "2")))
        self.separate_folders_check.setChecked(get_config("slice", "separate_folders", "false") == "true")

    def save_config(self):
        set_config("slice", "folder", self.folder_input.text())
        set_config("slice", "output", self.output_input.text())
        set_config("slice", "min_duration", str(self.min_duration.value()))
        set_config("slice", "max_duration", str(self.max_duration.value()))
        set_config("slice", "max_workers", str(self.max_workers.value()))
        set_config("slice", "separate_folders", str(self.separate_folders_check.isChecked()).lower())

    def browse_folder(self):
        # 先文件后文件夹复合模式（保留原逻辑）
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件或文件夹",
            "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv *.flv);;所有文件 (*)"
        )
        if file_path:
            self.folder_input.setText(file_path)
            self.save_config()
            return
        folder = QFileDialog.getExistingDirectory(self, "选择视频文件夹")
        if folder:
            self.folder_input.setText(folder)
            self.save_config()

    # ── 任务启动（P2：统一走 BaseTab.start_worker）──
    def start_slicing(self):
        folder = self.folder_input.text()
        output = self.output_input.text()

        if not folder or not output:
            QMessageBox.warning(self, "警告", "请选择输入文件/文件夹和输出文件夹")
            return

        self.save_config()
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.result_table.setRowCount(0)
        self.stats_label.setText("统计: 切片进行中...")

        worker = SliceWorker(
            folder, output,
            self.min_duration.value(),
            self.max_duration.value(),
            self.separate_folders_check.isChecked(),
            self.max_workers.value()
        )
        worker.video_done.connect(self.on_video_done)
        if not self.start_worker(worker):
            self.stats_label.setText("统计: 等待切片...")
            return

    def set_busy(self, busy):
        self.start_btn.setEnabled(not busy)
        self.pause_btn.setEnabled(busy)
        self.pause_btn.setText("暂停")
        self.stop_btn.setEnabled(busy)

    def toggle_pause(self):
        if not self.worker or not self.worker.isRunning():
            return
        if self.worker._paused:
            self.worker.resume()
            self.pause_btn.setText("暂停")
            self.stats_label.setText("统计: 切片进行中...")
        else:
            self.worker.pause()
            self.pause_btn.setText("继续")
            self.stats_label.setText("统计: 已暂停")

    def stop_slicing(self):
        if not self.worker or not self.worker.isRunning():
            return
        self.worker.stop()
        self.stats_label.setText("统计: 正在停止...")

    # ── 回调 ─────────────────────────────────────────────────
    def on_worker_progress(self, current, total, message):
        # 保持原语义：进度条刻度 = 总数（非百分比）
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)

    def on_video_done(self, video_results):
        row = self.result_table.rowCount()
        video_total = sum(r["duration"] for r in video_results)
        minutes = int(video_total // 60)
        seconds = video_total % 60
        duration_str = f"{minutes}分{seconds:.1f}秒"

        for r in video_results:
            self.result_table.insertRow(row)
            self.result_table.setItem(row, 0, QTableWidgetItem(r["file"]))
            self.result_table.setItem(row, 1, QTableWidgetItem(str(r["start"])))
            self.result_table.setItem(row, 2, QTableWidgetItem(str(r["duration"])))
            self.result_table.setItem(row, 3, QTableWidgetItem(duration_str))
            row += 1

    def on_worker_finished(self, results):
        super().on_worker_finished(results)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(100)

        unique_videos = set()
        total_duration = 0.0
        for r in results:
            video_name = os.path.basename(r["file"]).rsplit("_", 1)[0]
            unique_videos.add(video_name)
            total_duration += r["duration"]

        minutes = int(total_duration // 60)
        seconds = total_duration % 60

        self.stats_label.setText(
            f"统计: 切割 {len(unique_videos)} 个视频, "
            f"产出 {len(results)} 个片段, "
            f"总时长 {minutes}分{seconds:.1f}秒"
        )
        QMessageBox.information(self, "完成", f"切片完成，共{len(results)}个片段")

    def on_worker_error(self, message):
        super().on_worker_error(message)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.stats_label.setText("统计: 切片失败")

    def organize_slices(self):
        output_dir = self.output_input.text()
        if not output_dir:
            QMessageBox.warning(self, "警告", "请先选择输出文件夹")
            return

        organize_mode = self.organize_combo.currentIndex()

        try:
            if organize_mode == 0:
                self._organize_by_filename(output_dir)
            else:
                self._flatten_to_root(output_dir)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"整理失败: {str(e)}")

    def _organize_by_filename(self, output_dir):
        video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
        moved_count = 0

        for item in os.listdir(output_dir):
            item_path = os.path.join(output_dir, item)
            if os.path.isfile(item_path) and item.lower().endswith(video_exts):
                if "_" in item:
                    prefix = item.split("_")[0]
                else:
                    prefix = os.path.splitext(item)[0]

                target_dir = os.path.join(output_dir, prefix)
                os.makedirs(target_dir, exist_ok=True)

                target_path = os.path.join(target_dir, item)
                if item_path != target_path:
                    os.rename(item_path, target_path)
                    moved_count += 1

        QMessageBox.information(self, "完成", f"已将{moved_count}个文件整理到独立文件夹")

    def _flatten_to_root(self, output_dir):
        video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
        moved_count = 0
        removed_dirs = []

        for item in os.listdir(output_dir):
            item_path = os.path.join(output_dir, item)
            if os.path.isdir(item_path):
                for sub_item in os.listdir(item_path):
                    if sub_item.lower().endswith(video_exts):
                        src_path = os.path.join(item_path, sub_item)
                        dst_path = os.path.join(output_dir, sub_item)

                        if src_path != dst_path:
                            if os.path.exists(dst_path):
                                base, ext = os.path.splitext(sub_item)
                                dst_path = os.path.join(output_dir, f"{base}_dup{ext}")

                            os.rename(src_path, dst_path)
                            moved_count += 1

                remaining = os.listdir(item_path)
                if not remaining:
                    os.rmdir(item_path)
                    removed_dirs.append(item)

        msg = f"已将{moved_count}个文件提取到根目录"
        if removed_dirs:
            msg += f"，删除了{len(removed_dirs)}个空文件夹"
        QMessageBox.information(self, "完成", msg)
