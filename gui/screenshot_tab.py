import os
import subprocess
import json
import random
import cv2
import numpy as np
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QSpinBox, QFileDialog,
    QTreeWidget, QTreeWidgetItem, QProgressBar,
    QMessageBox, QGroupBox, QCheckBox, QAbstractItemView, QComboBox,
    QScrollArea, QFrame
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from gui.config import get_config, set_config
from core.screenshot import SCRFDetector


class ScreenshotWorker(QThread):
    progress = pyqtSignal(int, int)
    video_done = pyqtSignal(dict)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    
    def __init__(self, folder_path, output_folder, frame_count, delete_faces, 
                 separate_folders=True, delete_face_videos=False):
        super().__init__()
        self.folder_path = folder_path
        self.output_folder = output_folder
        self.frame_count = frame_count
        self.delete_faces = delete_faces
        self.separate_folders = separate_folders
        self.delete_face_videos = delete_face_videos
    
    def run(self):
        try:
            video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
            video_files = []
            for root, dirs, files in os.walk(self.folder_path):
                for f in files:
                    if f.lower().endswith(video_exts):
                        video_files.append(os.path.join(root, f))
            
            # 使用SCRFD检测器
            model_path = r"D:\Models\scrfd_10g\det_10g.onnx"
            detector = None
            if os.path.exists(model_path):
                detector = SCRFDetector(model_path)
            
            os.makedirs(self.output_folder, exist_ok=True)
            all_results = []
            total = len(video_files)
            
            for idx, video_path in enumerate(video_files):
                result = self._process_video(video_path, detector)
                all_results.append(result)
                self.video_done.emit(result)
                self.progress.emit(idx + 1, total)
            
            self.finished.emit(all_results)
        except Exception as e:
            self.error.emit(str(e))
    
    def _get_video_duration(self, video_path):
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="ignore")
        if result.returncode != 0 or not result.stdout:
            return 0
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    
    def _extract_random_frames(self, video_path, output_dir, count):
        os.makedirs(output_dir, exist_ok=True)
        duration = self._get_video_duration(video_path)
        if duration <= 0:
            return []
        
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        saved = []
        
        for i in range(count):
            t = random.uniform(0, duration)
            output_path = os.path.join(output_dir, f"{video_name}_frame_{i:04d}.jpg")
            cmd = [
                "ffmpeg", "-ss", str(t), "-i", video_path,
                "-vframes", "1", "-q:v", "2",
                "-y", output_path
            ]
            result = subprocess.run(cmd, capture_output=True,
                                    encoding="utf-8", errors="ignore")
            if result.returncode == 0 and os.path.exists(output_path):
                saved.append(output_path)
        
        return saved
    
    def _detect_face_in_image(self, image_path, detector):
        """使用SCRFD检测图片中是否包含人脸"""
        if detector is None:
            return False
        
        # 使用np.fromfile+cv2.imdecode读取含中文路径的图片
        try:
            img = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            img = None
        
        if img is None:
            return False
        
        results = detector.detect(img)
        return len(results) > 0
    
    def _process_video(self, video_path, detector):
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        if self.separate_folders:
            video_output = os.path.join(self.output_folder, video_name)
        else:
            video_output = self.output_folder
        
        images = self._extract_random_frames(video_path, video_output, self.frame_count)
        
        face_images = []
        for img_path in images:
            if self._detect_face_in_image(img_path, detector):
                face_images.append(img_path)
        
        # 删除包含人脸的截图
        deleted_count = 0
        if self.delete_faces and face_images:
            for img_path in face_images:
                try:
                    if os.path.exists(img_path):
                        os.remove(img_path)
                        deleted_count += 1
                except Exception:
                    pass
        
        # 删除包含人脸的视频
        video_deleted = False
        if self.delete_face_videos and face_images:
            try:
                if os.path.exists(video_path):
                    os.remove(video_path)
                    video_deleted = True
            except Exception:
                pass
        
        return {
            "video": os.path.relpath(video_path, self.folder_path),
            "full_path": video_path,
            "images": images,
            "face_images": face_images,
            "has_faces": len(face_images) > 0,
            "deleted": deleted_count,
            "video_deleted": video_deleted
        }


class ScreenshotTab(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.results = []
        self.init_ui()
        self.load_config()
    
    def init_ui(self):
        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        # ===== 输入设置 =====
        input_group = QGroupBox("输入设置")
        input_layout = QVBoxLayout()
        input_layout.setSpacing(8)

        folder_layout = QHBoxLayout()
        folder_layout.setSpacing(8)
        self.folder_input = QLineEdit()
        self.folder_input.setMinimumHeight(30)
        self.folder_input.setPlaceholderText("选择视频文件夹...")
        folder_btn = QPushButton("浏览")
        folder_btn.setFixedWidth(80)
        folder_btn.clicked.connect(self.browse_folder)
        folder_layout.addWidget(self.folder_input, 1)
        folder_layout.addWidget(folder_btn)

        output_layout = QHBoxLayout()
        output_layout.setSpacing(8)
        self.output_input = QLineEdit()
        self.output_input.setMinimumHeight(30)
        self.output_input.setPlaceholderText("截图输出文件夹...")
        output_btn = QPushButton("浏览")
        output_btn.setFixedWidth(80)
        output_btn.clicked.connect(self.browse_output)
        output_layout.addWidget(self.output_input, 1)
        output_layout.addWidget(output_btn)

        input_layout.addLayout(folder_layout)
        input_layout.addLayout(output_layout)
        input_group.setLayout(input_layout)

        # ===== 截图参数 =====
        params_group = QGroupBox("截图参数")
        params_layout = QHBoxLayout()
        params_layout.setSpacing(8)

        params_layout.addWidget(QLabel("每个视频截图数:"))
        self.frame_count = QSpinBox()
        self.frame_count.setRange(1, 50)
        self.frame_count.setValue(5)
        self.frame_count.setMinimumHeight(28)
        params_layout.addWidget(self.frame_count)
        params_layout.addStretch()

        params_group.setLayout(params_layout)

        # ===== 操作选项 =====
        options_group = QGroupBox("操作选项")
        options_layout = QVBoxLayout()
        options_layout.setSpacing(10)

        self.separate_folders_check = QCheckBox("按视频名称分子文件夹存放")
        self.separate_folders_check.setChecked(True)
        self.separate_folders_check.setToolTip(
            "选中：每个视频的截图放在独立子文件夹中\n"
            "不选中：所有截图放在同一个输出文件夹"
        )
        self.separate_folders_check.setMinimumHeight(26)
        options_layout.addWidget(self.separate_folders_check)

        self.delete_face_check = QCheckBox("自动删除包含人脸的截图")
        self.delete_face_check.setToolTip("勾选后，检测完成时自动删除所有包含人脸的截图")
        self.delete_face_check.setMinimumHeight(26)
        options_layout.addWidget(self.delete_face_check)

        self.delete_face_video_check = QCheckBox("自动删除包含人脸的视频")
        self.delete_face_video_check.setToolTip("勾选后，检测到包含人脸的视频会直接删除")
        self.delete_face_video_check.setMinimumHeight(26)
        self.delete_face_video_check.setStyleSheet("color: red; font-weight: bold;")
        options_layout.addWidget(self.delete_face_video_check)

        options_group.setLayout(options_layout)

        # ===== 截图整理 =====
        organize_group = QGroupBox("截图整理")
        organize_layout = QHBoxLayout()
        organize_layout.setSpacing(8)

        organize_layout.addWidget(QLabel("整理方式:"))
        self.organize_combo = QComboBox()
        self.organize_combo.addItems(["整理到子文件夹", "提取到根目录"])
        self.organize_combo.setMinimumHeight(28)
        organize_layout.addWidget(self.organize_combo)

        self.organize_btn = QPushButton("整理截图")
        self.organize_btn.setMinimumWidth(80)
        self.organize_btn.setMinimumHeight(28)
        self.organize_btn.clicked.connect(self.organizeScreenshots)
        organize_layout.addWidget(self.organize_btn)
        organize_layout.addStretch()

        organize_group.setLayout(organize_layout)

        # ===== 截图操作 =====
        screenshot_action_group = QGroupBox("截图操作")
        screenshot_action_layout = QVBoxLayout()
        screenshot_action_layout.setSpacing(6)

        screenshot_row1 = QHBoxLayout()
        screenshot_row1.setSpacing(8)
        self.select_all_face_img_btn = QPushButton("选中含人脸截图")
        self.select_all_face_img_btn.setMinimumHeight(28)
        self.select_all_face_img_btn.clicked.connect(self.select_all_face_images)
        self.select_all_face_img_btn.setEnabled(False)
        screenshot_row1.addWidget(self.select_all_face_img_btn, 1)

        self.select_no_face_img_btn = QPushButton("选中无人脸截图")
        self.select_no_face_img_btn.setMinimumHeight(28)
        self.select_no_face_img_btn.clicked.connect(self.select_all_no_face_images)
        self.select_no_face_img_btn.setEnabled(False)
        screenshot_row1.addWidget(self.select_no_face_img_btn, 1)

        screenshot_action_layout.addLayout(screenshot_row1)

        screenshot_row2 = QHBoxLayout()
        screenshot_row2.setSpacing(8)
        self.delete_selected_img_btn = QPushButton("删除选中截图")
        self.delete_selected_img_btn.setMinimumHeight(28)
        self.delete_selected_img_btn.clicked.connect(self.delete_selected_images)
        self.delete_selected_img_btn.setEnabled(False)
        screenshot_row2.addWidget(self.delete_selected_img_btn, 1)

        self.delete_all_face_img_btn = QPushButton("删除所有含人脸截图")
        self.delete_all_face_img_btn.setMinimumHeight(28)
        self.delete_all_face_img_btn.setStyleSheet("color: red; font-weight: bold;")
        self.delete_all_face_img_btn.clicked.connect(self.delete_all_face_images)
        self.delete_all_face_img_btn.setEnabled(False)
        screenshot_row2.addWidget(self.delete_all_face_img_btn, 1)

        screenshot_action_layout.addLayout(screenshot_row2)
        screenshot_action_group.setLayout(screenshot_action_layout)

        # ===== 视频操作 =====
        video_action_group = QGroupBox("视频操作")
        video_action_layout = QVBoxLayout()
        video_action_layout.setSpacing(6)

        video_row1 = QHBoxLayout()
        video_row1.setSpacing(8)
        self.select_all_face_btn = QPushButton("选中含人脸视频")
        self.select_all_face_btn.setMinimumHeight(28)
        self.select_all_face_btn.clicked.connect(self.select_all_face)
        self.select_all_face_btn.setEnabled(False)
        video_row1.addWidget(self.select_all_face_btn, 1)

        self.select_no_face_btn = QPushButton("选中无人脸视频")
        self.select_no_face_btn.setMinimumHeight(28)
        self.select_no_face_btn.clicked.connect(self.select_all_no_face)
        self.select_no_face_btn.setEnabled(False)
        video_row1.addWidget(self.select_no_face_btn, 1)

        video_action_layout.addLayout(video_row1)

        video_row2 = QHBoxLayout()
        video_row2.setSpacing(8)
        self.delete_selected_btn = QPushButton("删除选中视频")
        self.delete_selected_btn.setMinimumHeight(28)
        self.delete_selected_btn.clicked.connect(self.delete_selected)
        self.delete_selected_btn.setEnabled(False)
        video_row2.addWidget(self.delete_selected_btn, 1)

        self.delete_all_face_btn = QPushButton("删除所有含人脸视频")
        self.delete_all_face_btn.setMinimumHeight(28)
        self.delete_all_face_btn.setStyleSheet("color: red; font-weight: bold;")
        self.delete_all_face_btn.clicked.connect(self.delete_all_face_videos)
        self.delete_all_face_btn.setEnabled(False)
        video_row2.addWidget(self.delete_all_face_btn, 1)

        video_action_layout.addLayout(video_row2)
        video_action_group.setLayout(video_action_layout)

        # ===== 操作按钮 =====
        self.start_btn = QPushButton("开始截图")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self.start_screenshot)

        self.progress_bar = QProgressBar()

        self.stats_label = QLabel("统计: 等待截图...")
        self.stats_label.setStyleSheet("font-weight: bold; padding: 5px;")

        # ===== 结果表格（树形：视频 -> 截图） =====
        self.result_tree = QTreeWidget()
        self.result_tree.setHeaderLabels(["文件", "截图数", "包含人脸"])
        self.result_tree.setColumnCount(3)
        self.result_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.result_tree.setAlternatingRowColors(True)
        self.result_tree.setMinimumHeight(400)
        self.result_tree.header().setStretchLastSection(True)
        self.result_tree.setStyleSheet("""
            QTreeWidget {
                font-size: 9pt;
            }
            QTreeWidget::item:selected {
                background-color: #26a69a;
                color: white;
            }
            QTreeWidget::item:selected:active {
                background-color: #2bbbad;
                color: white;
            }
            QTreeWidget::item {
                padding: 2px;
            }
        """)

        desc_label = QLabel(
            "功能说明：\n"
            "1. 从指定文件夹的所有视频中随机抽取指定数量的帧\n"
            "2. 可选择按视频名称分子文件夹或统一放在根目录\n"
            "3. 可自动检测并删除包含人脸的截图\n"
            "4. 支持批量选中和删除操作\n"
            "5. 可一键整理输出文件夹（分子文件夹/提取到根目录）"
        )
        desc_label.setStyleSheet("color: gray; padding: 6px;")
        desc_label.setWordWrap(True)

        layout.addWidget(input_group)
        layout.addWidget(params_group)
        layout.addWidget(options_group)
        layout.addWidget(organize_group)
        layout.addWidget(screenshot_action_group)
        layout.addWidget(video_action_group)
        layout.addWidget(self.start_btn)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.stats_label)
        layout.addWidget(self.result_tree, 1)
        layout.addWidget(desc_label)

        scroll.setWidget(content)
        outer_layout.addWidget(scroll)
        self.setLayout(outer_layout)
    
    def load_config(self):
        self.folder_input.setText(get_config("screenshot", "folder", ""))
        self.output_input.setText(get_config("screenshot", "output", ""))
        self.frame_count.setValue(int(get_config("screenshot", "frame_count", "5")))
        self.separate_folders_check.setChecked(get_config("screenshot", "separate_folders", "true") == "true")
        self.delete_face_check.setChecked(get_config("screenshot", "delete_faces", "false") == "true")
        self.delete_face_video_check.setChecked(get_config("screenshot", "delete_face_videos", "false") == "true")
    
    def save_config(self):
        set_config("screenshot", "folder", self.folder_input.text())
        set_config("screenshot", "output", self.output_input.text())
        set_config("screenshot", "frame_count", str(self.frame_count.value()))
        set_config("screenshot", "separate_folders", str(self.separate_folders_check.isChecked()).lower())
        set_config("screenshot", "delete_faces", str(self.delete_face_check.isChecked()).lower())
        set_config("screenshot", "delete_face_videos", str(self.delete_face_video_check.isChecked()).lower())
    
    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择视频文件夹")
        if folder:
            self.folder_input.setText(folder)
            self.save_config()
    
    def browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "选择截图输出文件夹")
        if folder:
            self.output_input.setText(folder)
            self.save_config()
    
    def start_screenshot(self):
        folder = self.folder_input.text()
        output = self.output_input.text()
        
        if not folder or not output:
            QMessageBox.warning(self, "警告", "请选择输入和输出文件夹")
            return
        
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "警告", "任务正在执行中")
            return
        
        self.save_config()
        self.start_btn.setEnabled(False)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.result_tree.clear()
        self.stats_label.setText("统计: 截图进行中...")
        self.results = []
        
        self.worker = ScreenshotWorker(
            folder, output,
            self.frame_count.value(),
            self.delete_face_check.isChecked(),
            self.separate_folders_check.isChecked(),
            self.delete_face_video_check.isChecked()
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
        
        # 添加视频级条目
        video_item = QTreeWidgetItem(self.result_tree)
        video_item.setText(0, result["video"])
        video_item.setText(1, str(len(result["images"])))
        
        face_text = "是"
        if result.get("video_deleted"):
            face_text = "是(视频已删除)"
        elif not result["has_faces"]:
            face_text = "否"
        video_item.setText(2, face_text)
        
        if result["has_faces"]:
            video_item.setForeground(2, Qt.red)
        
        # 存储数据
        video_item.setData(0, Qt.UserRole, {"type": "video", "data": result})
        
        # 添加截图级条目
        face_images = set(result.get("face_images", []))
        for img_path in result.get("images", []):
            img_item = QTreeWidgetItem(video_item)
            img_name = os.path.basename(img_path)
            has_face = img_path in face_images
            
            img_item.setText(0, "  " + img_name)
            img_item.setText(1, "")
            img_item.setText(2, "是" if has_face else "否")
            
            if has_face:
                img_item.setForeground(2, Qt.red)
            
            img_item.setData(0, Qt.UserRole, {"type": "image", "path": img_path, "has_face": has_face})
        
        video_item.setExpanded(True)
    
    def on_finished(self, results):
        self.start_btn.setEnabled(True)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(100)
        
        total_images = sum(len(r["images"]) for r in results)
        face_count = sum(1 for r in results if r["has_faces"])
        deleted = sum(r.get("deleted", 0) for r in results)
        videos_deleted = sum(1 for r in results if r.get("video_deleted"))
        
        msg = f"统计: 共{len(results)}个视频, {total_images}张截图, {face_count}个含人脸"
        if deleted > 0:
            msg += f", 已删除{deleted}张人脸截图"
        if videos_deleted > 0:
            msg += f", 已删除{videos_deleted}个含人脸视频"
        self.stats_label.setText(msg)
        
        # 截图操作按钮
        has_face_images = any(len(r.get("face_images", [])) > 0 for r in results)
        self.select_all_face_img_btn.setEnabled(has_face_images)
        self.select_no_face_img_btn.setEnabled(has_face_images)
        self.delete_selected_img_btn.setEnabled(True)
        self.delete_all_face_img_btn.setEnabled(has_face_images)
        
        # 视频操作按钮
        self.select_all_face_btn.setEnabled(face_count > videos_deleted)
        self.select_no_face_btn.setEnabled(face_count < len(results))
        self.delete_selected_btn.setEnabled(True)
        self.delete_all_face_btn.setEnabled(face_count > videos_deleted)
        
        QMessageBox.information(self, "完成", msg)
    
    def on_error(self, msg):
        self.start_btn.setEnabled(True)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.stats_label.setText("统计: 截图失败")
        QMessageBox.critical(self, "错误", msg)
    
    # ===== 截图操作方法 =====
    
    def select_all_face_images(self):
        """选中所有包含人脸的截图（子节点）"""
        self.result_tree.clearSelection()
        it = self.result_tree.invisibleRootItem()
        for i in range(it.childCount()):
            video_item = it.child(i)
            for j in range(video_item.childCount()):
                img_item = video_item.child(j)
                data = img_item.data(0, Qt.UserRole)
                if data and data.get("type") == "image" and data.get("has_face"):
                    img_item.setSelected(True)
    
    def select_all_no_face_images(self):
        """选中所有不包含人脸的截图（子节点）"""
        self.result_tree.clearSelection()
        it = self.result_tree.invisibleRootItem()
        for i in range(it.childCount()):
            video_item = it.child(i)
            for j in range(video_item.childCount()):
                img_item = video_item.child(j)
                data = img_item.data(0, Qt.UserRole)
                if data and data.get("type") == "image" and not data.get("has_face"):
                    img_item.setSelected(True)
    
    def delete_selected_images(self):
        """删除选中的截图（保留视频）"""
        selected = self.result_tree.selectedItems()
        if not selected:
            QMessageBox.warning(self, "警告", "请先选中要删除的截图")
            return
        
        # 收集选中的截图路径
        img_paths = []
        for item in selected:
            data = item.data(0, Qt.UserRole)
            if data and data.get("type") == "image":
                img_paths.append(data["path"])
            elif data and data.get("type") == "video":
                # 选中的是视频节点，删除该视频的所有截图
                result = data.get("data", {})
                img_paths.extend(result.get("images", []))
        
        if not img_paths:
            QMessageBox.information(self, "提示", "没有选中要删除的截图")
            return
        
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除选中的{len(img_paths)}张截图吗？\n"
            f"（视频文件将保留）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            deleted = 0
            for img_path in img_paths:
                try:
                    if os.path.exists(img_path):
                        os.remove(img_path)
                        deleted += 1
                except Exception:
                    pass
            
            # 更新results中的图片列表（不改变视频的人脸判定状态）
            for r in self.results:
                r["images"] = [img for img in r.get("images", []) if img not in img_paths]
                r["face_images"] = [img for img in r.get("face_images", []) if img not in img_paths]
            
            self._refresh_table()
            self.stats_label.setText(f"统计: 已删除{deleted}张截图")
            QMessageBox.information(self, "完成", f"已删除{deleted}张截图")
    
    def delete_all_face_images(self):
        """删除所有包含人脸的截图（保留视频）"""
        face_videos = [r for r in self.results if r["has_faces"]]
        
        if not face_videos:
            QMessageBox.information(self, "提示", "没有包含人脸的截图")
            return
        
        total_images = sum(len(r.get("face_images", [])) for r in face_videos)
        
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除所有{len(face_videos)}个视频中的人脸截图吗？\n"
            f"（共{total_images}张人脸截图，视频文件将保留）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            deleted = 0
            for r in face_videos:
                for img_path in r.get("face_images", []):
                    try:
                        if os.path.exists(img_path):
                            os.remove(img_path)
                            deleted += 1
                    except Exception:
                        pass
                r["images"] = [img for img in r.get("images", []) 
                               if img not in r.get("face_images", [])]
                r["face_images"] = []
            
            self._refresh_table()
            
            self.select_all_face_img_btn.setEnabled(False)
            self.delete_all_face_img_btn.setEnabled(False)
            
            self.stats_label.setText(
                f"统计: 已删除{deleted}张人脸截图, "
                f"剩余{len(self.results)}个视频"
            )
            QMessageBox.information(self, "完成", f"已删除{deleted}张人脸截图")
    
    # ===== 视频操作方法 =====
    
    def select_all_face(self):
        """选中所有包含人脸的视频（父节点）"""
        self.result_tree.clearSelection()
        it = self.result_tree.invisibleRootItem()
        for i in range(it.childCount()):
            video_item = it.child(i)
            data = video_item.data(0, Qt.UserRole)
            if data and data.get("type") == "video" and data.get("data", {}).get("has_faces"):
                video_item.setSelected(True)
    
    def select_all_no_face(self):
        """选中所有不包含人脸的视频（父节点）"""
        self.result_tree.clearSelection()
        it = self.result_tree.invisibleRootItem()
        for i in range(it.childCount()):
            video_item = it.child(i)
            data = video_item.data(0, Qt.UserRole)
            if data and data.get("type") == "video" and not data.get("data", {}).get("has_faces"):
                video_item.setSelected(True)
    
    def delete_selected(self):
        """删除选中的视频（保留截图）"""
        selected = self.result_tree.selectedItems()
        if not selected:
            QMessageBox.warning(self, "警告", "请先选中要删除的视频")
            return
        
        # 收集选中的视频
        videos_to_delete = []
        for item in selected:
            data = item.data(0, Qt.UserRole)
            if data and data.get("type") == "video":
                videos_to_delete.append(data.get("data", {}))
        
        if not videos_to_delete:
            QMessageBox.information(self, "提示", "没有选中要删除的视频")
            return
        
        face_count = sum(1 for r in videos_to_delete if r.get("has_faces"))
        
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除选中的{len(videos_to_delete)}个视频吗？\n"
            f"（{face_count}个含人脸，截图文件将保留）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            deleted_videos = 0
            
            for r in videos_to_delete:
                video_path = r.get("full_path")
                if video_path and os.path.exists(video_path):
                    try:
                        os.remove(video_path)
                        deleted_videos += 1
                    except Exception:
                        pass
            
            self.results = [r for r in self.results if r not in videos_to_delete]
            self._refresh_table()
            
            face_count = sum(1 for r in self.results if r["has_faces"])
            self.stats_label.setText(
                f"统计: 已删除{deleted_videos}个视频, "
                f"剩余{len(self.results)}个视频, {face_count}个含人脸"
            )
            QMessageBox.information(self, "完成", f"已删除{deleted_videos}个视频")
    
    def delete_all_face_videos(self):
        """删除所有包含人脸的视频（保留截图）"""
        face_videos = [r for r in self.results if r["has_faces"]]
        
        if not face_videos:
            QMessageBox.information(self, "提示", "没有包含人脸的视频")
            return
        
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除所有{len(face_videos)}个包含人脸的视频吗？\n"
            f"⚠️ 此操作不可恢复！截图文件将保留",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            deleted_videos = 0
            
            for r in face_videos:
                # 删除源视频
                video_path = r.get("full_path")
                if video_path and os.path.exists(video_path):
                    try:
                        os.remove(video_path)
                        deleted_videos += 1
                    except Exception:
                        pass
            
            self.results = [r for r in self.results if r not in face_videos]
            self._refresh_table()
            
            self.delete_all_face_btn.setEnabled(False)
            self.select_all_face_btn.setEnabled(False)
            
            face_count = sum(1 for r in self.results if r["has_faces"])
            self.stats_label.setText(
                f"统计: 已删除{deleted_videos}个视频, "
                f"剩余{len(self.results)}个视频, {face_count}个含人脸"
            )
            QMessageBox.information(
                self, "完成", 
                f"已删除{deleted_videos}个包含人脸的视频"
            )
    
    def _refresh_table(self):
        self.result_tree.clear()
        for r in self.results:
            # 视频级条目
            video_item = QTreeWidgetItem(self.result_tree)
            video_item.setText(0, r["video"])
            video_item.setText(1, str(len(r["images"])))
            face_text = "是" if r["has_faces"] else "否"
            video_item.setText(2, face_text)
            if r["has_faces"]:
                video_item.setForeground(2, Qt.red)
            video_item.setData(0, Qt.UserRole, {"type": "video", "data": r})
            
            # 截图级条目
            face_images = set(r.get("face_images", []))
            for img_path in r.get("images", []):
                img_item = QTreeWidgetItem(video_item)
                img_name = os.path.basename(img_path)
                has_face = img_path in face_images
                img_item.setText(0, "  " + img_name)
                img_item.setText(1, "")
                img_item.setText(2, "是" if has_face else "否")
                if has_face:
                    img_item.setForeground(2, Qt.red)
                img_item.setData(0, Qt.UserRole, {"type": "image", "path": img_path, "has_face": has_face})
            
            video_item.setExpanded(True)
    
    def organizeScreenshots(self):
        output_dir = self.output_input.text()
        if not output_dir:
            QMessageBox.warning(self, "警告", "请先选择输出文件夹")
            return
        
        organize_mode = self.organize_combo.currentIndex()
        
        try:
            if organize_mode == 0:
                self._organize_to_subfolders(output_dir)
            else:
                self._flatten_to_root(output_dir)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"整理失败: {str(e)}")
    
    def _organize_to_subfolders(self, output_dir):
        image_exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
        moved_count = 0
        
        for item in os.listdir(output_dir):
            item_path = os.path.join(output_dir, item)
            if os.path.isfile(item_path) and item.lower().endswith(image_exts):
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
        
        QMessageBox.information(self, "完成", f"已将{moved_count}个截图整理到独立文件夹")
    
    def _flatten_to_root(self, output_dir):
        image_exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
        moved_count = 0
        removed_dirs = []
        
        for item in os.listdir(output_dir):
            item_path = os.path.join(output_dir, item)
            if os.path.isdir(item_path):
                for sub_item in os.listdir(item_path):
                    if sub_item.lower().endswith(image_exts):
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
        
        msg = f"已将{moved_count}个截图提取到根目录"
        if removed_dirs:
            msg += f"，删除了{len(removed_dirs)}个空文件夹"
        QMessageBox.information(self, "完成", msg)
