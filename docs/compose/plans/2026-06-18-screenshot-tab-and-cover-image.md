# Video Screenshot Tab & Cover Image Feature Implementation Plan

> [!NOTE]
> This document may not reflect the current implementation.
> See the final report for up-to-date state:
> [Final Report](../reports/screenshot-tab-and-cover-image.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two features: (1) a Video Screenshot tab for random frame extraction with face detection/deletion, and (2) a cover image feature in Video Mix that uses a random image as the first silent segment with blur padding.

**Architecture:** Feature 1 adds a new tab (`screenshot_tab.py`) with worker thread, reusing existing face detection logic from `screenshot.py`. Feature 2 extends `VideoMixerEngine` and `video_mix_tab.py` with image-to-video conversion and blur padding via `_blur_pad_video` in `video_utils.py`.

**Tech Stack:** PyQt5, OpenCV (haarcascades), FFmpeg (subprocess), existing `video_utils.py` helpers

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `gui/screenshot_tab.py` | Screenshot tab UI + worker thread |
| Modify | `gui/main_window.py` | Add screenshot tab to main window |
| Modify | `core/video_mixer.py` | Add cover image logic to `generate_plan` and `create_mix` |
| Modify | `gui/video_mix_tab.py` | Add cover image UI controls |
| Modify | `config.json` | Add screenshot and cover image config sections |

---

### Task 1: Create Screenshot Tab UI

**Covers:** Feature 1 - Video Screenshot Tab

**Files:**
- Create: `gui/screenshot_tab.py`
- Modify: `gui/main_window.py:18-28`

- [ ] **Step 1: Create `gui/screenshot_tab.py` with full UI and worker**

```python
import os
import subprocess
import json
import random
import cv2
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QSpinBox, QFileDialog,
    QTableWidget, QTableWidgetItem, QProgressBar,
    QMessageBox, QGroupBox, QCheckBox, QAbstractItemView
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from gui.config import get_config, set_config


class ScreenshotWorker(QThread):
    progress = pyqtSignal(int, int)
    video_done = pyqtSignal(dict)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    
    def __init__(self, folder_path, output_folder, frame_count, delete_faces):
        super().__init__()
        self.folder_path = folder_path
        self.output_folder = output_folder
        self.frame_count = frame_count
        self.delete_faces = delete_faces
    
    def run(self):
        try:
            video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
            video_files = []
            for root, dirs, files in os.walk(self.folder_path):
                for f in files:
                    if f.lower().endswith(video_exts):
                        video_files.append(os.path.join(root, f))
            
            face_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
            profile_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_profileface.xml"
            )
            eye_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_eye.xml"
            )
            
            os.makedirs(self.output_folder, exist_ok=True)
            all_results = []
            total = len(video_files)
            
            for idx, video_path in enumerate(video_files):
                result = self._process_video(
                    video_path, face_cascade, profile_cascade, eye_cascade
                )
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
    
    def _detect_face_in_image(self, image_path, face_cascade, profile_cascade, eye_cascade):
        img = cv2.imread(image_path)
        if img is None:
            return False
        
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        
        h, w = gray.shape[:2]
        min_size = max(30, min(w, h) // 8)
        
        faces = face_cascade.detectMultiScale(
            gray, scaleFactor=1.05, minNeighbors=5, minSize=(min_size, min_size)
        )
        profiles = profile_cascade.detectMultiScale(
            gray, scaleFactor=1.05, minNeighbors=5, minSize=(min_size, min_size)
        )
        
        all_faces = list(faces) + list(profiles)
        for (x, y, w, h) in all_faces:
            face_roi = gray[y:y+h, x:x+w]
            eyes = eye_cascade.detectMultiScale(face_roi, scaleFactor=1.1, minNeighbors=5)
            if len(eyes) >= 1:
                return True
        
        return False
    
    def _process_video(self, video_path, face_cascade, profile_cascade, eye_cascade):
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        video_output = os.path.join(self.output_folder, video_name)
        
        images = self._extract_random_frames(video_path, video_output, self.frame_count)
        
        face_images = []
        for img_path in images:
            if self._detect_face_in_image(img_path, face_cascade, profile_cascade, eye_cascade):
                face_images.append(img_path)
        
        deleted_count = 0
        if self.delete_faces and face_images:
            for img_path in face_images:
                try:
                    if os.path.exists(img_path):
                        os.remove(img_path)
                        deleted_count += 1
                except Exception:
                    pass
        
        return {
            "video": os.path.relpath(video_path, self.folder_path),
            "full_path": video_path,
            "images": images,
            "face_images": face_images,
            "has_faces": len(face_images) > 0,
            "deleted": deleted_count
        }


class ScreenshotTab(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.results = []
        self.init_ui()
        self.load_config()
    
    def init_ui(self):
        layout = QVBoxLayout()
        
        input_group = QGroupBox("输入设置")
        input_layout = QVBoxLayout()
        
        folder_layout = QHBoxLayout()
        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("选择视频文件夹...")
        folder_btn = QPushButton("浏览")
        folder_btn.clicked.connect(self.browse_folder)
        folder_layout.addWidget(self.folder_input)
        folder_layout.addWidget(folder_btn)
        
        output_layout = QHBoxLayout()
        self.output_input = QLineEdit()
        self.output_input.setPlaceholderText("截图输出文件夹...")
        output_btn = QPushButton("浏览")
        output_btn.clicked.connect(self.browse_output)
        output_layout.addWidget(self.output_input)
        output_layout.addWidget(output_btn)
        
        input_layout.addLayout(folder_layout)
        input_layout.addLayout(output_layout)
        input_group.setLayout(input_layout)
        
        params_group = QGroupBox("截图参数")
        params_layout = QHBoxLayout()
        
        params_layout.addWidget(QLabel("每个视频截图数:"))
        self.frame_count = QSpinBox()
        self.frame_count.setRange(1, 50)
        self.frame_count.setValue(5)
        params_layout.addWidget(self.frame_count)
        
        params_group.setLayout(params_layout)
        
        options_group = QGroupBox("操作选项")
        options_layout = QVBoxLayout()
        
        self.delete_face_check = QCheckBox("自动删除包含人脸的截图")
        self.delete_face_check.setToolTip("勾选后，检测完成时自动删除所有包含人脸的截图")
        options_layout.addWidget(self.delete_face_check)
        
        options_group.setLayout(options_layout)
        
        action_group = QGroupBox("批量操作")
        action_layout = QHBoxLayout()
        
        self.select_all_face_btn = QPushButton("选中所有含人脸截图")
        self.select_all_face_btn.clicked.connect(self.select_all_face)
        self.select_all_face_btn.setEnabled(False)
        action_layout.addWidget(self.select_all_face_btn)
        
        self.select_no_face_btn = QPushButton("选中所有无人脸截图")
        self.select_no_face_btn.clicked.connect(self.select_all_no_face)
        self.select_no_face_btn.setEnabled(False)
        action_layout.addWidget(self.select_no_face_btn)
        
        self.delete_selected_btn = QPushButton("删除选中截图")
        self.delete_selected_btn.clicked.connect(self.delete_selected)
        self.delete_selected_btn.setEnabled(False)
        action_layout.addWidget(self.delete_selected_btn)
        
        action_group.setLayout(action_layout)
        
        self.start_btn = QPushButton("开始截图")
        self.start_btn.clicked.connect(self.start_screenshot)
        
        self.progress_bar = QProgressBar()
        
        self.stats_label = QLabel("统计: 等待截图...")
        self.stats_label.setStyleSheet("font-weight: bold; padding: 5px;")
        
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(3)
        self.result_table.setHorizontalHeaderLabels(["视频文件", "截图数", "包含人脸"])
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        
        layout.addWidget(input_group)
        layout.addWidget(params_group)
        layout.addWidget(options_group)
        layout.addWidget(action_group)
        layout.addWidget(self.start_btn)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.stats_label)
        layout.addWidget(self.result_table)
        
        layout.addStretch()
        
        desc_label = QLabel(
            "功能说明：\n"
            "1. 从指定文件夹的所有视频中随机抽取指定数量的帧\n"
            "2. 截图保存到输出文件夹，按视频名称分子文件夹\n"
            "3. 可自动检测并删除包含人脸的截图\n"
            "4. 支持批量选中和删除操作"
        )
        desc_label.setStyleSheet("color: gray; padding: 10px;")
        layout.addWidget(desc_label)
        
        self.setLayout(layout)
    
    def load_config(self):
        self.folder_input.setText(get_config("screenshot", "folder", ""))
        self.output_input.setText(get_config("screenshot", "output", ""))
        self.frame_count.setValue(int(get_config("screenshot", "frame_count", "5")))
        self.delete_face_check.setChecked(get_config("screenshot", "delete_faces", "false") == "true")
    
    def save_config(self):
        set_config("screenshot", "folder", self.folder_input.text())
        set_config("screenshot", "output", self.output_input.text())
        set_config("screenshot", "frame_count", str(self.frame_count.value()))
        set_config("screenshot", "delete_faces", str(self.delete_face_check.isChecked()).lower())
    
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
        self.result_table.setRowCount(0)
        self.stats_label.setText("统计: 截图进行中...")
        self.results = []
        
        self.worker = ScreenshotWorker(
            folder, output,
            self.frame_count.value(),
            self.delete_face_check.isChecked()
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
        self.result_table.setItem(row, 0, QTableWidgetItem(result["video"]))
        self.result_table.setItem(row, 1, QTableWidgetItem(str(len(result["images"]))))
        
        face_item = QTableWidgetItem("是" if result["has_faces"] else "否")
        if result["has_faces"]:
            face_item.setForeground(Qt.red)
        self.result_table.setItem(row, 2, face_item)
    
    def on_finished(self, results):
        self.start_btn.setEnabled(True)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(100)
        
        total_images = sum(len(r["images"]) for r in results)
        face_count = sum(1 for r in results if r["has_faces"])
        deleted = sum(r.get("deleted", 0) for r in results)
        
        msg = f"统计: 共{len(results)}个视频, {total_images}张截图, {face_count}个含人脸"
        if deleted > 0:
            msg += f", 已删除{deleted}张人脸截图"
        self.stats_label.setText(msg)
        
        self.select_all_face_btn.setEnabled(face_count > 0)
        self.select_no_face_btn.setEnabled(face_count < len(results))
        self.delete_selected_btn.setEnabled(True)
        
        QMessageBox.information(self, "完成", msg)
    
    def on_error(self, msg):
        self.start_btn.setEnabled(True)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.stats_label.setText("统计: 截图失败")
        QMessageBox.critical(self, "错误", msg)
    
    def select_all_face(self):
        self.result_table.clearSelection()
        for i in range(self.result_table.rowCount()):
            if i < len(self.results) and self.results[i]["has_faces"]:
                for col in range(self.result_table.columnCount()):
                    item = self.result_table.item(i, col)
                    if item:
                        item.setSelected(True)
    
    def select_all_no_face(self):
        self.result_table.clearSelection()
        for i in range(self.result_table.rowCount()):
            if i < len(self.results) and not self.results[i]["has_faces"]:
                for col in range(self.result_table.columnCount()):
                    item = self.result_table.item(i, col)
                    if item:
                        item.setSelected(True)
    
    def delete_selected(self):
        selected = self.result_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "警告", "请先选中要删除的截图")
            return
        
        rows = set()
        for item in selected:
            rows.add(item.row())
        
        videos_to_delete = []
        for row in rows:
            if row < len(self.results):
                videos_to_delete.append(self.results[row])
        
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除选中的{len(videos_to_delete)}个视频的所有截图吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            deleted = 0
            for r in videos_to_delete:
                for img_path in r.get("images", []):
                    try:
                        if os.path.exists(img_path):
                            os.remove(img_path)
                            deleted += 1
                    except Exception:
                        pass
            
            self.results = [r for r in self.results if r not in videos_to_delete]
            self._refresh_table()
            
            face_count = sum(1 for r in self.results if r["has_faces"])
            self.stats_label.setText(
                f"统计: 已删除{deleted}张截图, "
                f"剩余{len(self.results)}个视频, {face_count}个含人脸"
            )
            QMessageBox.information(self, "完成", f"已删除{deleted}张截图")
    
    def _refresh_table(self):
        self.result_table.setRowCount(0)
        for r in self.results:
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            self.result_table.setItem(row, 0, QTableWidgetItem(r["video"]))
            self.result_table.setItem(row, 1, QTableWidgetItem(str(len(r["images"]))))
            face_item = QTableWidgetItem("是" if r["has_faces"] else "否")
            if r["has_faces"]:
                face_item.setForeground(Qt.red)
            self.result_table.setItem(row, 2, face_item)
```

- [ ] **Step 2: Add screenshot tab to main window**

Modify `gui/main_window.py`:

```python
from gui.slice_tab import SliceTab
from gui.text_recognition_tab import TextRecognitionTab
from gui.audio_mix_tab import AudioMixTab
from gui.video_mix_tab import VideoMixTab
from gui.face_detection_tab import FaceDetectionTab
from gui.screenshot_tab import ScreenshotTab  # Add this import


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频混剪工具")
        self.setMinimumSize(900, 700)
        
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        
        self.slice_tab = SliceTab()
        self.text_recognition_tab = TextRecognitionTab()
        self.face_detection_tab = FaceDetectionTab()
        self.audio_mix_tab = AudioMixTab()
        self.video_mix_tab = VideoMixTab()
        self.screenshot_tab = ScreenshotTab()  # Add this
        
        self.tabs.addTab(self.slice_tab, "视频切片")
        self.tabs.addTab(self.screenshot_tab, "视频截图")  # Add this tab
        self.tabs.addTab(self.text_recognition_tab, "文字识别")
        self.tabs.addTab(self.face_detection_tab, "人脸识别")
        self.tabs.addTab(self.audio_mix_tab, "音频混剪")
        self.tabs.addTab(self.video_mix_tab, "视频混剪")
```

- [ ] **Step 3: Add screenshot config to config.json**

Add to `config.json`:

```json
{
  "slice": { ... },
  "video_mix": { ... },
  "test": { ... },
  "audio_mix": { ... },
  "text_recognition": { ... },
  "face_detection": { ... },
  "screenshot": {
    "folder": "",
    "output": "",
    "frame_count": "5",
    "delete_faces": "false"
  }
}
```

- [ ] **Step 4: Verify screenshot tab works**

Run: `python main.py` and test the Video Screenshot tab.

---

### Task 2: Add Cover Image Feature to Video Mix

**Covers:** Feature 2 - Cover Image in Video Mix

**Files:**
- Modify: `core/video_mixer.py:13-251`
- Modify: `gui/video_mix_tab.py:40-334`
- Modify: `utils/video_utils.py:83-125` (reuse `_blur_pad_video`)

- [ ] **Step 1: Add image-to-video conversion helper to `utils/video_utils.py`**

Add this function after `_blur_pad_video` (around line 126):

```python
def image_to_video(image_path, duration, output_path, target_w=1080, target_h=1920):
    """Convert a static image to a video with specified duration.
    
    If image aspect ratio is not 9:16, apply blur padding.
    No audio track is added.
    """
    import cv2
    img = cv2.imread(image_path)
    if img is None:
        raise RuntimeError(f"Failed to read image: {image_path}")
    
    h, w = img.shape[:2]
    src_ratio = w / h
    target_ratio = target_w / target_h
    
    if abs(src_ratio - target_ratio) < 0.01 and w == target_w and h == target_h:
        vf = f"loop=-1:size=1:start=0"
    else:
        if src_ratio > target_ratio:
            fit_w, fit_h = target_w, int(target_w / src_ratio)
        else:
            fit_h = target_h
            fit_w = int(target_h * src_ratio)
        
        vf = (
            f"loop=-1:size=1:start=0,"
            f"scale={fit_w}:{fit_h},"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1"
        )
    
    cmd = [
        "ffmpeg", "-loop", "1", "-i", image_path,
        "-t", str(duration),
        "-vf", f"scale={target_w}:{target_h},setsar=1" if abs(src_ratio - target_ratio) < 0.01 and w == target_w and h == target_h else (
            f"split[bg][fg];"
            f"[bg]scale={target_w}:{target_h},crop={target_w}:{target_h},boxblur=20:5[blurred];"
            f"[fg]scale={fit_w}:{fit_h}[fg_scaled];"
            f"[blurred][fg_scaled]overlay=(W-w)/2:(H-h)/2,setsar=1"
        ),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-an",
        "-y", output_path
    ]
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                            errors="ignore", timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(f"image_to_video failed: {result.stderr}")
    return output_path
```

- [ ] **Step 2: Modify `VideoMixerEngine` in `core/video_mixer.py`**

Add cover image support to the engine:

```python
import os
import random
import tempfile
import shutil
from utils.video_utils import (
    get_video_duration, cut_video,
    concat_videos, add_audio, remove_audio,
    image_to_video  # Add this import
)


class VideoMixerEngine:
    def __init__(self, config):
        self.config = config
        self.clips_folder = config["clips_folder"]
        self.output_folder = config["output_folder"]
        self.head_tail = config["head_tail"]
        self.head_min = config["head_min"]
        self.head_max = config["head_max"]
        self.tail_min = config["tail_min"]
        self.tail_max = config["tail_max"]
        self.slice_count_min = config["slice_count_min"]
        self.slice_count_max = config["slice_count_max"]
        self.slice_duration_min = config["slice_duration_min"]
        self.slice_duration_max = config["slice_duration_max"]
        self.mode = config["mode"]
        self.mix_count = config["mix_count"]
        # Cover image settings
        self.cover_enabled = config.get("cover_enabled", False)
        self.cover_folder = config.get("cover_folder", "")
        self.cover_duration_min = config.get("cover_duration_min", 2)
        self.cover_duration_max = config.get("cover_duration_max", 4)
    
    def get_base_videos(self, video_folder):
        video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
        videos = []
        for root, dirs, files in os.walk(video_folder):
            for f in files:
                if f.lower().endswith(video_exts):
                    videos.append(os.path.join(root, f))
        return videos
    
    def get_clip_videos(self):
        video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
        clips = []
        for root, dirs, files in os.walk(self.clips_folder):
            for f in files:
                if f.lower().endswith(video_exts):
                    clips.append(os.path.join(root, f))
        return clips
    
    def get_cover_images(self):
        """Get list of cover images from the specified folder."""
        if not self.cover_enabled or not self.cover_folder:
            return []
        image_exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
        images = []
        for f in os.listdir(self.cover_folder):
            if f.lower().endswith(image_exts):
                images.append(os.path.join(self.cover_folder, f))
        return images
    
    def generate_plan(self, duration):
        """Generate a plan for the video mix.
        
        Returns a list of segments describing what goes where:
        - {"type": "cover", "duration": N} (new)
        - {"type": "head", "start": 0, "duration": N}
        - {"type": "gap", "start": X, "duration": Y}
        - {"type": "middle", "start": X, "duration": Y}
        - {"type": "gap", "start": X, "duration": Y}
        - {"type": "tail", "start": X, "duration": Y}
        """
        plan = []
        
        # Add cover image segment at the beginning if enabled
        if self.cover_enabled and self.cover_folder:
            cover_images = self.get_cover_images()
            if cover_images:
                cover_duration = random.uniform(
                    self.cover_duration_min, self.cover_duration_max
                )
                plan.append({"type": "cover", "duration": cover_duration})
        
        if self.head_tail:
            head_duration = random.uniform(self.head_min, self.head_max)
            tail_duration = random.uniform(self.tail_min, self.tail_max)
        else:
            head_duration = 0
            tail_duration = 0
        
        head_duration = min(head_duration, duration * 0.3)
        tail_duration = min(tail_duration, duration * 0.3)
        
        if head_duration > 0:
            plan.append({"type": "head", "start": 0, "duration": head_duration})
        
        middle_start = head_duration
        middle_end = duration - tail_duration
        middle_duration = middle_end - middle_start
        
        if middle_duration > 0:
            slice_count = random.randint(self.slice_count_min, self.slice_count_max)
            
            if self.mode == 0:
                slice_durations = []
                remaining = middle_duration
                for i in range(slice_count):
                    d = random.uniform(self.slice_duration_min, self.slice_duration_max)
                    d = min(d, remaining / (slice_count - i))
                    d = max(d, 0.5)
                    slice_durations.append(d)
                    remaining -= d
                
                total_slices = sum(slice_durations)
                gap_time = middle_duration - total_slices
                gap_interval = gap_time / (slice_count + 1) if slice_count > 0 else 0
                
                current_pos = middle_start + gap_interval
                for d in slice_durations:
                    if gap_interval > 0.1:
                        gap_start = current_pos - gap_interval
                        plan.append({"type": "gap", "start": gap_start, "duration": gap_interval})
                    plan.append({"type": "middle", "start": current_pos, "duration": d})
                    current_pos += d + gap_interval
                
                if gap_interval > 0.1:
                    plan.append({"type": "gap", "start": current_pos - gap_interval, "duration": gap_interval})
            else:
                middle_segments = []
                for i in range(slice_count):
                    max_start_pos = max(0, middle_duration - self.slice_duration_min)
                    if max_start_pos > 0:
                        start_offset = random.uniform(0, max_start_pos)
                    else:
                        start_offset = 0
                    d = random.uniform(self.slice_duration_min, self.slice_duration_max)
                    actual_start = middle_start + start_offset
                    if actual_start + d <= middle_end:
                        middle_segments.append({"start": actual_start, "duration": d})
                
                middle_segments.sort(key=lambda x: x["start"])
                
                current_pos = middle_start
                for seg in middle_segments:
                    if seg["start"] > current_pos + 0.1:
                        plan.append({"type": "gap", "start": current_pos, "duration": seg["start"] - current_pos})
                    plan.append({"type": "middle", "start": seg["start"], "duration": seg["duration"]})
                    current_pos = seg["start"] + seg["duration"]
                
                if middle_end - current_pos > 0.1:
                    plan.append({"type": "gap", "start": current_pos, "duration": middle_end - current_pos})
        
        if tail_duration > 0:
            plan.append({"type": "tail", "start": duration - tail_duration, "duration": tail_duration})
        
        return plan
    
    def fill_gap(self, gap_duration, clips, tmp_dir, part_index):
        """Fill a gap with clip videos.
        
        Returns a list of clip segments that fill the gap.
        Multiple clips can be added, and the last one is trimmed if needed.
        """
        if not hasattr(self, '_clip_duration_cache'):
            self._clip_duration_cache = {}
        
        for clip in clips:
            if clip not in self._clip_duration_cache:
                self._clip_duration_cache[clip] = get_video_duration(clip)
        
        clips_in_gap = []
        remaining = gap_duration
        current_start = 0
        
        while remaining > 0.1:
            clip = random.choice(clips)
            clip_duration = self._clip_duration_cache[clip]
            
            if clip_duration >= remaining:
                clip_start = random.uniform(0, max(0, clip_duration - remaining))
                actual_duration = remaining
            else:
                clip_start = 0
                actual_duration = clip_duration
            
            clip_path = os.path.join(tmp_dir, f"clip_{part_index}_{len(clips_in_gap)}.mp4")
            cut_video(clip, clip_start, actual_duration, clip_path)
            
            clips_in_gap.append({
                "path": clip_path,
                "duration": actual_duration,
                "type": "clip"
            })
            
            remaining -= actual_duration
            part_index += 1
        
        return clips_in_gap
    
    def create_mix(self, base_video, clips, output_path, progress_callback=None):
        """Create a single mixed video."""
        duration = get_video_duration(base_video)
        plan = self.generate_plan(duration)
        
        if not plan:
            return None
        
        tmp_dir = tempfile.mkdtemp()
        try:
            all_parts = []
            total_steps = len(plan) + 2
            
            cover_images = self.get_cover_images()
            
            for i, segment in enumerate(plan):
                if progress_callback:
                    progress_callback(int((i + 1) / total_steps * 100), f"处理片段 {i+1}/{len(plan)}")
                
                if segment["type"] == "cover":
                    # Add cover image as first segment (no audio)
                    if cover_images:
                        cover_img = random.choice(cover_images)
                        cover_path = os.path.join(tmp_dir, f"cover_{len(all_parts)}.mp4")
                        image_to_video(
                            cover_img,
                            segment["duration"],
                            cover_path
                        )
                        all_parts.append({
                            "path": cover_path,
                            "duration": segment["duration"],
                            "type": "cover"
                        })
                elif segment["type"] in ("head", "tail", "middle"):
                    seg_path = os.path.join(tmp_dir, f"base_{len(all_parts)}.mp4")
                    cut_video(base_video, segment["start"], segment["duration"], seg_path)
                    all_parts.append({
                        "path": seg_path,
                        "duration": segment["duration"],
                        "type": "base"
                    })
                elif segment["type"] == "gap":
                    gap_clips = self.fill_gap(segment["duration"], clips, tmp_dir, len(all_parts))
                    all_parts.extend(gap_clips)
            
            if progress_callback:
                progress_callback(int((len(plan) + 1) / total_steps * 100), "拼接视频...")
            
            concat_path = os.path.join(tmp_dir, "concat.mp4")
            part_paths = [p["path"] for p in all_parts]
            concat_videos(part_paths, concat_path)
            
            if progress_callback:
                progress_callback(int((len(plan) + 2) / total_steps * 100), "添加音频...")
            
            add_audio(concat_path, base_video, output_path)
            
            return output_path
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    
    def run(self, callback=None):
        """Run the mixing process."""
        os.makedirs(self.output_folder, exist_ok=True)
        
        base_videos = self.get_base_videos(self.config["video_folder"])
        clips = self.get_clip_videos()
        
        if not base_videos:
            raise ValueError("No base videos found")
        if not clips:
            raise ValueError("No clip videos found")
        
        results = []
        total_videos = len(base_videos) * self.mix_count
        
        for idx, base_video in enumerate(base_videos):
            base_name = os.path.splitext(os.path.basename(base_video))[0]
            for i in range(self.mix_count):
                output_name = f"{base_name}_mix_{i+1}.mp4" if self.mix_count > 1 else f"{base_name}_mix.mp4"
                output_path = os.path.join(self.output_folder, output_name)
                
                if callback:
                    callback(idx * self.mix_count + i, total_videos, f"{base_name} - 处理中...", 0)
                
                self.create_mix(base_video, clips, output_path)
                results.append(output_path)
                
                if callback:
                    callback(idx * self.mix_count + i + 1, total_videos, f"{base_name} - 完成", 100)
        
        return results
```

- [ ] **Step 3: Add cover image UI controls to `gui/video_mix_tab.py`**

Add after the video group and before the clips group in `init_ui`:

```python
# Add import at top
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QCheckBox, QSpinBox, QComboBox,
    QTableWidget, QTableWidgetItem
)

# In init_ui method, add after video_group and before clips_group:

        cover_group = QGroupBox("封面图设置")
        cover_layout = QVBoxLayout()
        
        self.cover_check = QCheckBox("启用封面图")
        self.cover_check.setChecked(False)
        self.cover_check.stateChanged.connect(self.on_cover_changed)
        cover_layout.addWidget(self.cover_check)
        
        cover_folder_layout = QHBoxLayout()
        cover_folder_layout.addWidget(QLabel("封面图文件夹:"))
        self.cover_folder_input = QLineEdit()
        self.cover_folder_input.setPlaceholderText("选择封面图文件夹...")
        self.cover_folder_input.setEnabled(False)
        cover_folder_btn = QPushButton("浏览")
        cover_folder_btn.clicked.connect(self.browse_cover_folder)
        cover_folder_btn.setEnabled(False)
        self.cover_folder_btn = cover_folder_btn
        cover_folder_layout.addWidget(self.cover_folder_input)
        cover_folder_layout.addWidget(cover_folder_btn)
        cover_layout.addLayout(cover_folder_layout)
        
        cover_duration_layout = QHBoxLayout()
        cover_duration_layout.addWidget(QLabel("封面时长(秒):"))
        self.cover_duration_min = QSpinBox()
        self.cover_duration_min.setRange(1, 10)
        self.cover_duration_min.setValue(2)
        self.cover_duration_min.setEnabled(False)
        cover_duration_layout.addWidget(self.cover_duration_min)
        cover_duration_layout.addWidget(QLabel("~"))
        self.cover_duration_max = QSpinBox()
        self.cover_duration_max.setRange(1, 10)
        self.cover_duration_max.setValue(4)
        self.cover_duration_max.setEnabled(False)
        cover_duration_layout.addWidget(self.cover_duration_max)
        cover_layout.addLayout(cover_duration_layout)
        
        cover_group.setLayout(cover_layout)

# In init_ui, update the layout.addWidget calls to include cover_group:
        layout.addWidget(video_group)
        layout.addWidget(cover_group)  # Add this
        layout.addWidget(clips_group)
        layout.addWidget(output_group)
        layout.addWidget(self.start_btn)
        layout.addWidget(progress_group)
        layout.addWidget(self.status_label)
```

- [ ] **Step 4: Add cover image config save/load methods**

Add to `VideoMixTab` class:

```python
    def on_cover_changed(self, state):
        enabled = state == Qt.Checked
        self.cover_folder_input.setEnabled(enabled)
        self.cover_folder_btn.setEnabled(enabled)
        self.cover_duration_min.setEnabled(enabled)
        self.cover_duration_max.setEnabled(enabled)
    
    def browse_cover_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择封面图文件夹")
        if folder:
            self.cover_folder_input.setText(folder)
            self.save_config()
```

- [ ] **Step 5: Update `load_config` and `save_config` methods**

Update the existing methods in `gui/video_mix_tab.py`:

```python
    def load_config(self):
        self.video_folder_input.setText(get_config("video_mix", "video_folder", ""))
        self.clips_folder_input.setText(get_config("video_mix", "clips_folder", ""))
        self.output_folder_input.setText(get_config("video_mix", "output_folder", ""))
        self.head_tail_check.setChecked(get_config("video_mix", "head_tail", "1") == "1")
        self.head_min.setValue(int(get_config("video_mix", "head_min", "3")))
        self.head_max.setValue(int(get_config("video_mix", "head_max", "5")))
        self.tail_min.setValue(int(get_config("video_mix", "tail_min", "3")))
        self.tail_max.setValue(int(get_config("video_mix", "tail_max", "5")))
        self.slice_count_min.setValue(int(get_config("video_mix", "slice_count_min", "3")))
        self.slice_count_max.setValue(int(get_config("video_mix", "slice_count_max", "5")))
        self.slice_duration_min.setValue(int(get_config("video_mix", "slice_duration_min", "3")))
        self.slice_duration_max.setValue(int(get_config("video_mix", "slice_duration_max", "5")))
        self.mode_combo.setCurrentIndex(int(get_config("video_mix", "mode", "0")))
        self.mix_count.setValue(int(get_config("video_mix", "mix_count", "1")))
        # Cover image settings
        self.cover_check.setChecked(get_config("video_mix", "cover_enabled", "false") == "true")
        self.cover_folder_input.setText(get_config("video_mix", "cover_folder", ""))
        self.cover_duration_min.setValue(int(get_config("video_mix", "cover_duration_min", "2")))
        self.cover_duration_max.setValue(int(get_config("video_mix", "cover_duration_max", "4")))
        self.on_cover_changed(Qt.Checked if self.cover_check.isChecked() else Qt.Unchecked)
    
    def save_config(self):
        set_config("video_mix", "video_folder", self.video_folder_input.text())
        set_config("video_mix", "clips_folder", self.clips_folder_input.text())
        set_config("video_mix", "output_folder", self.output_folder_input.text())
        set_config("video_mix", "head_tail", "1" if self.head_tail_check.isChecked() else "0")
        set_config("video_mix", "head_min", str(self.head_min.value()))
        set_config("video_mix", "head_max", str(self.head_max.value()))
        set_config("video_mix", "tail_min", str(self.tail_min.value()))
        set_config("video_mix", "tail_max", str(self.tail_max.value()))
        set_config("video_mix", "slice_count_min", str(self.slice_count_min.value()))
        set_config("video_mix", "slice_count_max", str(self.slice_count_max.value()))
        set_config("video_mix", "slice_duration_min", str(self.slice_duration_min.value()))
        set_config("video_mix", "slice_duration_max", str(self.slice_duration_max.value()))
        set_config("video_mix", "mode", str(self.mode_combo.currentIndex()))
        set_config("video_mix", "mix_count", str(self.mix_count.value()))
        # Cover image settings
        set_config("video_mix", "cover_enabled", str(self.cover_check.isChecked()).lower())
        set_config("video_mix", "cover_folder", self.cover_folder_input.text())
        set_config("video_mix", "cover_duration_min", str(self.cover_duration_min.value()))
        set_config("video_mix", "cover_duration_max", str(self.cover_duration_max.value()))
```

- [ ] **Step 6: Update `start_mixing` config dict**

Update the config dict in `start_mixing` method:

```python
        config = {
            "video_folder": video_folder,
            "clips_folder": clips_folder,
            "output_folder": output_folder,
            "head_tail": self.head_tail_check.isChecked(),
            "head_min": self.head_min.value(),
            "head_max": self.head_max.value(),
            "tail_min": self.tail_min.value(),
            "tail_max": self.tail_max.value(),
            "slice_count_min": self.slice_count_min.value(),
            "slice_count_max": self.slice_count_max.value(),
            "slice_duration_min": self.slice_duration_min.value(),
            "slice_duration_max": self.slice_duration_max.value(),
            "mode": self.mode_combo.currentIndex(),
            "mix_count": self.mix_count.value(),
            # Cover image settings
            "cover_enabled": self.cover_check.isChecked(),
            "cover_folder": self.cover_folder_input.text(),
            "cover_duration_min": self.cover_duration_min.value(),
            "cover_duration_max": self.cover_duration_max.value()
        }
```

- [ ] **Step 7: Update description label**

Update the description label in `video_mix_tab.py`:

```python
        desc_label = QLabel(
            "混剪逻辑说明：\n"
            "1. 基底视频提供时间轴和音频\n"
            "2. 启用封面图时，使用随机图片作为视频开头（无音频，可选时长）\n"
            "3. 启用首尾保留时，基底视频的开头和结尾固定保留\n"
            "4. 中间部分按切片数量和时长区间截取片段\n"
            "5. 其余空位由切片视频随机填充\n"
            "6. 切片视频不使用原始音频，使用基底视频时间轴上的音频"
        )
```

- [ ] **Step 8: Add cover image config to config.json**

Update `config.json` video_mix section:

```json
{
  "video_mix": {
    "video_folder": "D:/千川素材/306 市场素材/模特素材",
    "clips_folder": "D:\\千川素材\\306 市场素材\\平铺素材切片",
    "output_folder": "D:\\千川素材\\306 市场素材\\视频混剪结果",
    "head_tail": "1",
    "head_min": "3",
    "head_max": "5",
    "tail_min": "3",
    "tail_max": "5",
    "slice_count_min": "3",
    "slice_count_max": "5",
    "slice_duration_min": "3",
    "slice_duration_max": "5",
    "mode": "0",
    "mix_count": "5",
    "cover_enabled": "false",
    "cover_folder": "",
    "cover_duration_min": "2",
    "cover_duration_max": "4"
  }
}
```

- [ ] **Step 9: Verify cover image feature works**

Run: `python main.py` and test the Video Mix tab with cover image enabled.

---

### Task 3: Final Verification

**Covers:** Both features

- [ ] **Step 1: Run the application and test all tabs**

Run: `python main.py`

Verify:
1. Video Screenshot tab appears and works
2. Video Mix tab has cover image controls
3. Cover image feature produces correct output
4. All config saves/loads correctly

- [ ] **Step 2: Test edge cases**

Test with:
- Empty folders
- Non-9:16 images for cover
- Multiple cover images in folder
- Cover enabled + head_tail enabled together

---

## Summary

| Task | Description | Files Modified |
|------|-------------|----------------|
| 1 | Screenshot Tab | `gui/screenshot_tab.py` (new), `gui/main_window.py`, `config.json` |
| 2 | Cover Image Feature | `core/video_mixer.py`, `gui/video_mix_tab.py`, `utils/video_utils.py`, `config.json` |
| 3 | Final Verification | Test all features |

Total: ~650 lines of new/modified code across 6 files.
