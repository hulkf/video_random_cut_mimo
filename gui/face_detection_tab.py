import os
import csv
import subprocess
import cv2
import numpy as np
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog,
    QTableWidget, QTableWidgetItem, QProgressBar,
    QMessageBox, QGroupBox, QCheckBox, QSpinBox,
    QAbstractItemView
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from gui.config import get_config, set_config
from core.screenshot import SCRFDetector


class FaceDetectionWorker(QThread):
    progress = pyqtSignal(int, int)
    video_done = pyqtSignal(dict)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    
    def __init__(self, folder_path, min_face_ratio=2, sample_count=8, 
                 model_path=None, score_thresh=0.5, auto_delete=False):
        super().__init__()
        self.folder_path = folder_path
        self.min_face_ratio = min_face_ratio
        self.sample_count = sample_count
        self.model_path = model_path or r"D:\Models\scrfd_10g\det_10g.onnx"
        self.score_thresh = score_thresh
        self.auto_delete = auto_delete
    
    def run(self):
        try:
            # 初始化SCRFD检测器
            detector = None
            if os.path.exists(self.model_path):
                detector = SCRFDetector(self.model_path, score_thresh=self.score_thresh)
            else:
                self.error.emit(f"模型文件不存在: {self.model_path}")
                return
            
            video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
            results = []
            
            video_files = []
            for root, dirs, files in os.walk(self.folder_path):
                for f in files:
                    if f.lower().endswith(video_exts):
                        video_files.append(os.path.join(root, f))
            
            total = len(video_files)
            for idx, video_path in enumerate(video_files):
                has_face = self._detect_face(video_path, detector)
                rel_path = os.path.relpath(video_path, self.folder_path)
                
                video_deleted = False
                if has_face and self.auto_delete:
                    try:
                        os.remove(video_path)
                        video_deleted = True
                    except Exception:
                        pass
                
                result = {
                    "file": rel_path,
                    "full_path": video_path,
                    "has_face": has_face,
                    "video_deleted": video_deleted
                }
                results.append(result)
                self.video_done.emit(result)
                self.progress.emit(idx + 1, total)
            
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))
    
    def _detect_face(self, video_path, detector):
        """使用SCRFD检测视频是否包含人脸"""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return False
        
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames == 0:
            cap.release()
            return False
        
        face_detections = 0
        step = max(1, total_frames // self.sample_count)
        
        for i in range(self.sample_count):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i * step)
            ret, frame = cap.read()
            if not ret:
                continue
            
            # 使用SCRFD检测人脸
            faces = detector.detect(frame)
            
            if len(faces) > 0:
                face_detections += 1
        
        cap.release()
        
        return face_detections >= self.min_face_ratio


class FaceDetectionTab(QWidget):
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
        
        params_group = QGroupBox("检测参数")
        params_layout = QHBoxLayout()
        
        params_layout.addWidget(QLabel("最少检测帧数:"))
        self.min_face_ratio = QSpinBox()
        self.min_face_ratio.setRange(1, 20)
        self.min_face_ratio.setValue(2)
        self.min_face_ratio.setMinimumHeight(28)
        self.min_face_ratio.setToolTip("需要在多少帧中检测到人脸才判定为有人脸\n值越大越严格，误报越少")
        params_layout.addWidget(self.min_face_ratio)
        
        params_layout.addWidget(QLabel("采样帧数:"))
        self.sample_count = QSpinBox()
        self.sample_count.setRange(3, 20)
        self.sample_count.setValue(8)
        self.sample_count.setMinimumHeight(28)
        self.sample_count.setToolTip("从视频中均匀采样多少帧进行检测\n帧数越多检测越准，但速度越慢")
        params_layout.addWidget(self.sample_count)
        
        params_group.setLayout(params_layout)
        
        options_group = QGroupBox("操作选项")
        options_layout = QVBoxLayout()
        
        self.delete_face_check = QCheckBox("检测完成后询问删除包含人脸的视频")
        self.delete_face_check.setMinimumHeight(26)
        self.delete_face_check.setToolTip("勾选后，检测完成时会询问是否删除所有包含人脸的视频")
        options_layout.addWidget(self.delete_face_check)
        
        self.auto_delete_face_check = QCheckBox("检测时直接删除包含人脸的视频（不询问）")
        self.auto_delete_face_check.setMinimumHeight(26)
        self.auto_delete_face_check.setToolTip("勾选后，检测到包含人脸的视频会直接删除，不会询问")
        self.auto_delete_face_check.setStyleSheet("color: red; font-weight: bold;")
        options_layout.addWidget(self.auto_delete_face_check)
        
        options_group.setLayout(options_layout)
        
        action_group = QGroupBox("批量操作")
        action_layout = QHBoxLayout()
        
        self.select_all_face_btn = QPushButton("选中所有含人脸视频")
        self.select_all_face_btn.clicked.connect(self.select_all_face_videos)
        self.select_all_face_btn.setEnabled(False)
        action_layout.addWidget(self.select_all_face_btn)
        
        self.select_no_face_btn = QPushButton("选中所有无人脸视频")
        self.select_no_face_btn.clicked.connect(self.select_all_no_face_videos)
        self.select_no_face_btn.setEnabled(False)
        action_layout.addWidget(self.select_no_face_btn)
        
        self.delete_selected_btn = QPushButton("删除选中视频")
        self.delete_selected_btn.clicked.connect(self.delete_selected)
        self.delete_selected_btn.setEnabled(False)
        action_layout.addWidget(self.delete_selected_btn)
        
        self.open_selected_btn = QPushButton("打开选中视频")
        self.open_selected_btn.clicked.connect(self.open_selected)
        self.open_selected_btn.setEnabled(False)
        action_layout.addWidget(self.open_selected_btn)
        
        action_group.setLayout(action_layout)
        
        export_group = QGroupBox("导出")
        export_layout = QHBoxLayout()
        
        self.export_btn = QPushButton("导出检测结果")
        self.export_btn.clicked.connect(self.export_results)
        self.export_btn.setEnabled(False)
        export_layout.addWidget(self.export_btn)
        
        export_group.setLayout(export_layout)
        
        self.start_btn = QPushButton("开始检测")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self.start_detection)

        self.progress_bar = QProgressBar()

        self.stats_label = QLabel("统计: 等待检测...")
        self.stats_label.setStyleSheet("font-weight: bold; padding: 5px;")

        self.result_table = QTableWidget()
        self.result_table.setColumnCount(3)
        self.result_table.setHorizontalHeaderLabels(["文件", "包含人脸", "打开"])
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.result_table.setMinimumHeight(200)
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.cellClicked.connect(self.on_cell_clicked)
        
        layout.addWidget(input_group)
        layout.addWidget(params_group)
        layout.addWidget(options_group)
        layout.addWidget(action_group)
        layout.addWidget(self.start_btn)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.stats_label)
        layout.addWidget(self.result_table)
        layout.addWidget(export_group)
        
        self.setLayout(layout)
    
    def load_config(self):
        self.folder_input.setText(get_config("face_detection", "folder", ""))
        self.delete_face_check.setChecked(get_config("face_detection", "delete_face", "false") == "true")
        self.auto_delete_face_check.setChecked(get_config("face_detection", "auto_delete_face", "false") == "true")
        self.min_face_ratio.setValue(int(get_config("face_detection", "min_face_ratio", "2")))
        self.sample_count.setValue(int(get_config("face_detection", "sample_count", "8")))
    
    def save_config(self):
        set_config("face_detection", "folder", self.folder_input.text())
        set_config("face_detection", "delete_face", str(self.delete_face_check.isChecked()).lower())
        set_config("face_detection", "auto_delete_face", str(self.auto_delete_face_check.isChecked()).lower())
        set_config("face_detection", "min_face_ratio", str(self.min_face_ratio.value()))
        set_config("face_detection", "sample_count", str(self.sample_count.value()))
    
    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择视频文件夹")
        if folder:
            self.folder_input.setText(folder)
            self.save_config()
    
    def start_detection(self):
        folder = self.folder_input.text()
        
        if not folder:
            QMessageBox.warning(self, "警告", "请选择视频文件夹")
            return
        
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "警告", "任务正在执行中")
            return
        
        self.save_config()
        self.start_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.select_all_face_btn.setEnabled(False)
        self.select_no_face_btn.setEnabled(False)
        self.delete_selected_btn.setEnabled(False)
        self.open_selected_btn.setEnabled(False)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.result_table.setRowCount(0)
        self.stats_label.setText("统计: 检测进行中...")
        self.results = []
        
        self.worker = FaceDetectionWorker(
            folder,
            self.min_face_ratio.value(),
            self.sample_count.value(),
            auto_delete=self.auto_delete_face_check.isChecked()
        )
        self.worker.progress.connect(self.on_progress)
        self.worker.video_done.connect(self.on_video_done)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()
    
    def on_progress(self, current, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
    
    def on_video_done(self, result):
        self.results.append(result)
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        self.result_table.setItem(row, 0, QTableWidgetItem(result["file"]))
        
        # 显示人脸状态和删除状态
        face_text = "是"
        if result.get("video_deleted"):
            face_text = "是(已删除)"
        elif not result["has_face"]:
            face_text = "否"
        
        face_item = QTableWidgetItem(face_text)
        if result["has_face"]:
            face_item.setForeground(Qt.red)
        self.result_table.setItem(row, 1, face_item)
        
        # 已删除的视频不显示打开按钮
        if not result.get("video_deleted"):
            open_btn = QPushButton("打开")
            open_btn.clicked.connect(lambda checked, r=result: self.open_video(r["full_path"]))
            self.result_table.setCellWidget(row, 2, open_btn)
    
    def on_finished(self, results):
        self.start_btn.setEnabled(True)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(100)
        
        face_count = sum(1 for r in results if r["has_face"])
        deleted_count = sum(1 for r in results if r.get("video_deleted"))
        
        if deleted_count > 0:
            self.stats_label.setText(
                f"统计: 共{len(results)}个视频, {face_count}个包含人脸, 已删除{deleted_count}个"
            )
        else:
            self.stats_label.setText(
                f"统计: 共{len(results)}个视频, {face_count}个包含人脸, {len(results)-face_count}个无人脸"
            )
        
        remaining = len(results) - deleted_count
        self.select_all_face_btn.setEnabled(face_count > deleted_count)
        self.select_no_face_btn.setEnabled(face_count < remaining)
        self.delete_selected_btn.setEnabled(True)
        self.open_selected_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        
        # 如果没有自动删除，且勾选了询问删除
        if not self.auto_delete_face_check.isChecked() and self.delete_face_check.isChecked() and face_count > deleted_count:
            remaining_faces = face_count - deleted_count
            reply = QMessageBox.question(
                self, "确认删除",
                f"检测到{remaining_faces}个包含人脸的视频，是否删除？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.delete_videos([r for r in results if r["has_face"] and not r.get("video_deleted")])
                return
        
        if deleted_count > 0:
            QMessageBox.information(
                self, "完成",
                f"检测完成，共{len(results)}个视频，{face_count}个包含人脸\n"
                f"已自动删除{deleted_count}个包含人脸的视频"
            )
        else:
            QMessageBox.information(
                self, "完成",
                f"检测完成，共{len(results)}个视频，{face_count}个包含人脸\n"
                f"可在下方表格中选中视频后批量删除或打开"
            )
    
    def on_error(self, msg):
        self.start_btn.setEnabled(True)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.stats_label.setText("统计: 检测失败")
        QMessageBox.critical(self, "错误", msg)
    
    def on_cell_clicked(self, row, col):
        if col == 2:
            if row < len(self.results):
                self.open_video(self.results[row]["full_path"])
    
    def open_video(self, path):
        if not os.path.exists(path):
            QMessageBox.warning(self, "警告", f"文件不存在: {path}")
            return
        try:
            os.startfile(path)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法打开视频: {str(e)}")
    
    def open_selected(self):
        selected = self.result_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "警告", "请先选中要打开的视频")
            return
        
        rows = set()
        for item in selected:
            rows.add(item.row())
        
        for row in rows:
            if row < len(self.results):
                self.open_video(self.results[row]["full_path"])
    
    def select_all_face_videos(self):
        self.result_table.clearSelection()
        for i in range(self.result_table.rowCount()):
            if i < len(self.results) and self.results[i]["has_face"]:
                item0 = self.result_table.item(i, 0)
                item1 = self.result_table.item(i, 1)
                if item0:
                    item0.setSelected(True)
                if item1:
                    item1.setSelected(True)
    
    def select_all_no_face_videos(self):
        self.result_table.clearSelection()
        for i in range(self.result_table.rowCount()):
            if i < len(self.results) and not self.results[i]["has_face"]:
                item0 = self.result_table.item(i, 0)
                item1 = self.result_table.item(i, 1)
                if item0:
                    item0.setSelected(True)
                if item1:
                    item1.setSelected(True)
    
    def delete_selected(self):
        selected = self.result_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "警告", "请先选中要删除的视频")
            return
        
        rows = set()
        for item in selected:
            rows.add(item.row())
        
        videos_to_delete = []
        for row in rows:
            if row < len(self.results):
                videos_to_delete.append(self.results[row])
        
        face_in_selection = sum(1 for r in videos_to_delete if r["has_face"])
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除选中的{len(videos_to_delete)}个视频吗？\n"
            f"（其中{face_in_selection}个包含人脸）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.delete_videos(videos_to_delete)
    
    def delete_videos(self, videos):
        deleted = 0
        failed = 0
        
        for r in videos:
            if os.path.exists(r["full_path"]):
                try:
                    os.remove(r["full_path"])
                    deleted += 1
                except Exception:
                    failed += 1
        
        self.result_table.setRowCount(0)
        self.results = [r for r in self.results if r["full_path"] not in [v["full_path"] for v in videos]]
        
        for r in self.results:
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            self.result_table.setItem(row, 0, QTableWidgetItem(r["file"]))
            
            face_item = QTableWidgetItem("是" if r["has_face"] else "否")
            if r["has_face"]:
                face_item.setForeground(Qt.red)
            self.result_table.setItem(row, 1, face_item)
            
            open_btn = QPushButton("打开")
            open_btn.clicked.connect(lambda checked, rr=r: self.open_video(rr["full_path"]))
            self.result_table.setCellWidget(row, 2, open_btn)
        
        face_count = sum(1 for r in self.results if r["has_face"])
        self.stats_label.setText(
            f"统计: 已删除{deleted}个视频, "
            f"剩余{len(self.results)}个视频, {face_count}个包含人脸"
        )
        
        msg = f"已删除{deleted}个视频"
        if failed > 0:
            msg += f", {failed}个删除失败"
        QMessageBox.information(self, "完成", msg)
    
    def export_results(self):
        if not self.results:
            QMessageBox.warning(self, "警告", "没有可导出的结果")
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出检测结果", "face_detection_results.csv",
            "CSV文件 (*.csv);;所有文件 (*)"
        )
        if not file_path:
            return
        
        try:
            with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["文件", "包含人脸", "完整路径"])
                for r in self.results:
                    writer.writerow([r["file"], "是" if r["has_face"] else "否", r["full_path"]])
            QMessageBox.information(self, "完成", f"结果已导出到: {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导出失败: {str(e)}")
