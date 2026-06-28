# Video Screenshot Tab & Cover Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a video screenshot extraction tab with face detection filtering, and add a cover image feature to the video mixer.

**Architecture:** Two independent features sharing existing patterns (PyQt5 tabs, QThread workers, ffmpeg-based video utils, opencv face detection). Feature 1 creates new core + GUI files. Feature 2 modifies existing files.

**Tech Stack:** Python 3, PyQt5, ffmpeg/ffprobe, opencv-python (cv2), os, subprocess

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `core/screenshot.py` | Extract random frames from videos, detect faces in images |
| Create | `gui/screenshot_tab.py` | GUI tab for screenshot extraction with face filtering |
| Modify | `gui/main_window.py` | Register new screenshot tab |
| Modify | `core/video_mixer.py` | Add cover image segment to plan generation |
| Modify | `gui/video_mix_tab.py` | Add cover image settings UI |
| Modify | `config.json` | Add screenshot and cover_image config sections |

---

### Task 1: Create core/screenshot.py — Screenshot Extraction Engine

**Files:**
- Create: `core/screenshot.py`

- [ ] **Step 1: Write screenshot extraction and face detection module**

```python
import os
import random
import subprocess
import cv2


def get_video_duration(video_path):
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if result.returncode != 0 or not result.stdout:
        return 0
    import json
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def get_video_fps(video_path):
    """Get video fps using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "default=noprint_wrappers=1:nokey=1", video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if result.returncode != 0 or not result.stdout.strip():
        return 30.0
    fps_str = result.stdout.strip()
    if "/" in fps_str:
        num, den = fps_str.split("/")
        return float(num) / float(den)
    return float(fps_str)


def extract_random_frames(video_path, output_dir, count=5, prefix="frame"):
    """Extract random frames from a video as JPG images.
    
    Returns list of saved image paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    duration = get_video_duration(video_path)
    if duration <= 0:
        return []
    
    video_name = os.path.splitext(os.path.basename(video_path))[0]
    saved = []
    
    for i in range(count):
        t = random.uniform(0, duration)
        output_path = os.path.join(output_dir, f"{video_name}_{prefix}_{i:04d}.jpg")
        cmd = [
            "ffmpeg", "-ss", str(t), "-i", video_path,
            "-vframes", "1", "-q:v", "2",
            "-y", output_path
        ]
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore")
        if result.returncode == 0 and os.path.exists(output_path):
            saved.append(output_path)
    
    return saved


def detect_face_in_image(image_path, face_cascade=None, profile_cascade=None):
    """Detect if an image contains a face.
    
    Returns True if face is detected.
    """
    if face_cascade is None:
        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
    if profile_cascade is None:
        profile_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_profileface.xml"
        )
    
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
    
    return len(faces) > 0 or len(profiles) > 0


def delete_images(paths):
    """Delete image files. Returns (deleted_count, failed_count)."""
    deleted = 0
    failed = 0
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
                deleted += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return deleted, failed


def extract_frames_from_folder(folder_path, output_dir, count_per_video=5,
                                detect_faces=False, delete_faces=False,
                                progress_callback=None, video_done_callback=None):
    """Extract random frames from all videos in a folder.
    
    Returns list of dicts with keys: video, images, has_faces, face_images.
    """
    video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
    video_files = []
    for root, dirs, files in os.walk(folder_path):
        for f in files:
            if f.lower().endswith(video_exts):
                video_files.append(os.path.join(root, f))
    
    face_cascade = None
    profile_cascade = None
    if detect_faces:
        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        profile_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_profileface.xml"
        )
    
    all_results = []
    total = len(video_files)
    
    for idx, video_path in enumerate(video_files):
        rel_path = os.path.relpath(video_path, folder_path)
        video_output = os.path.join(output_dir, os.path.splitext(os.path.basename(video_path))[0])
        
        images = extract_random_frames(video_path, video_output, count=count_per_video)
        
        face_images = []
        if detect_faces and images:
            for img_path in images:
                if detect_face_in_image(img_path, face_cascade, profile_cascade):
                    face_images.append(img_path)
        
        if delete_faces and face_images:
            delete_images(face_images)
            images = [i for i in images if i not in face_images]
        
        result = {
            "video": rel_path,
            "full_path": video_path,
            "images": images,
            "face_images": face_images,
            "has_faces": len(face_images) > 0
        }
        all_results.append(result)
        
        if video_done_callback:
            video_done_callback(result)
        if progress_callback:
            progress_callback(idx + 1, total)
    
    return all_results
```

- [ ] **Step 2: Verify module can be imported**

Run: `cd D:\JR_project\video_random_cut_mimo && python -c "from core.screenshot import extract_random_frames, detect_face_in_image, extract_frames_from_folder; print('OK')"`
Expected: `OK`

---

### Task 2: Create gui/screenshot_tab.py — Screenshot GUI Tab

**Files:**
- Create: `gui/screenshot_tab.py`

- [ ] **Step 1: Write the screenshot tab GUI**

```python
import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QSpinBox, QFileDialog,
    QTableWidget, QTableWidgetItem, QProgressBar,
    QMessageBox, QGroupBox, QCheckBox, QAbstractItemView
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from core.screenshot import extract_frames_from_folder
from gui.config import get_config, set_config


class ScreenshotWorker(QThread):
    progress = pyqtSignal(int, int)
    video_done = pyqtSignal(dict)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    
    def __init__(self, folder_path, output_dir, count_per_video,
                 detect_faces, delete_faces):
        super().__init__()
        self.folder_path = folder_path
        self.output_dir = output_dir
        self.count_per_video = count_per_video
        self.detect_faces = detect_faces
        self.delete_faces = delete_faces
    
    def run(self):
        try:
            results = extract_frames_from_folder(
                self.folder_path, self.output_dir,
                count_per_video=self.count_per_video,
                detect_faces=self.detect_faces,
                delete_faces=self.delete_faces,
                progress_callback=lambda cur, total: self.progress.emit(cur, total),
                video_done_callback=lambda r: self.video_done.emit(r)
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


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
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 100)
        self.count_spin.setValue(5)
        params_layout.addWidget(self.count_spin)
        
        params_group.setLayout(params_layout)
        
        face_group = QGroupBox("人脸检测")
        face_layout = QVBoxLayout()
        
        self.detect_face_check = QCheckBox("启用人脸检测")
        self.detect_face_check.setToolTip("检测截图中是否包含人脸")
        self.detect_face_check.stateChanged.connect(self.on_detect_face_changed)
        face_layout.addWidget(self.detect_face_check)
        
        self.delete_face_check = QCheckBox("自动删除含人脸的截图")
        self.delete_face_check.setToolTip("检测完成后自动删除包含人脸的截图")
        self.delete_face_check.setEnabled(False)
        face_layout.addWidget(self.delete_face_check)
        
        face_group.setLayout(face_layout)
        
        action_group = QGroupBox("批量操作")
        action_layout = QHBoxLayout()
        
        self.delete_selected_btn = QPushButton("删除选中截图")
        self.delete_selected_btn.clicked.connect(self.delete_selected)
        action_layout.addWidget(self.delete_selected_btn)
        
        self.open_folder_btn = QPushButton("打开输出文件夹")
        self.open_folder_btn.clicked.connect(self.open_output_folder)
        action_layout.addWidget(self.open_folder_btn)
        
        action_group.setLayout(action_layout)
        
        self.start_btn = QPushButton("开始截图")
        self.start_btn.clicked.connect(self.start_screenshot)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        
        self.stats_label = QLabel("统计: 等待截图...")
        self.stats_label.setStyleSheet("font-weight: bold; padding: 5px;")
        
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels(["视频文件", "截图数", "含人脸", "打开"])
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.result_table.cellClicked.connect(self.on_cell_clicked)
        
        layout.addWidget(input_group)
        layout.addWidget(params_group)
        layout.addWidget(face_group)
        layout.addWidget(action_group)
        layout.addWidget(self.start_btn)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.stats_label)
        layout.addWidget(self.result_table)
        
        self.setLayout(layout)
    
    def load_config(self):
        self.folder_input.setText(get_config("screenshot", "folder", ""))
        self.output_input.setText(get_config("screenshot", "output", ""))
        self.count_spin.setValue(int(get_config("screenshot", "count", "5")))
        self.detect_face_check.setChecked(get_config("screenshot", "detect_face", "false") == "true")
        self.delete_face_check.setChecked(get_config("screenshot", "delete_face", "false") == "true")
    
    def save_config(self):
        set_config("screenshot", "folder", self.folder_input.text())
        set_config("screenshot", "output", self.output_input.text())
        set_config("screenshot", "count", str(self.count_spin.value()))
        set_config("screenshot", "detect_face", str(self.detect_face_check.isChecked()).lower())
        set_config("screenshot", "delete_face", str(self.delete_face_check.isChecked()).lower())
    
    def on_detect_face_changed(self, state):
        self.delete_face_check.setEnabled(state == Qt.Checked)
    
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
        self.progress_bar.setValue(0)
        self.result_table.setRowCount(0)
        self.stats_label.setText("统计: 截图进行中...")
        self.results = []
        
        self.worker = ScreenshotWorker(
            folder, output, self.count_spin.value(),
            self.detect_face_check.isChecked(),
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
        
        open_btn = QPushButton("打开")
        open_btn.clicked.connect(lambda checked, r=result: self.open_folder(r["images"]))
        self.result_table.setCellWidget(row, 3, open_btn)
    
    def on_finished(self, results):
        self.start_btn.setEnabled(True)
        self.progress_bar.setValue(100)
        
        total_images = sum(len(r["images"]) for r in results)
        face_count = sum(1 for r in results if r["has_faces"])
        total_face_images = sum(len(r["face_images"]) for r in results)
        
        self.stats_label.setText(
            f"统计: {len(results)}个视频, {total_images}张截图, "
            f"{face_count}个视频含人脸, {total_face_images}张含人脸截图"
        )
        QMessageBox.information(self, "完成",
            f"截图完成\n共{total_images}张截图\n{total_face_images}张含人脸")
    
    def on_error(self, msg):
        self.start_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.stats_label.setText("统计: 截图失败")
        QMessageBox.critical(self, "错误", msg)
    
    def on_cell_clicked(self, row, col):
        if col == 3 and row < len(self.results):
            self.open_folder(self.results[row]["images"])
    
    def open_folder(self, images):
        if images:
            folder = os.path.dirname(images[0])
            try:
                os.startfile(folder)
            except Exception:
                pass
    
    def open_output_folder(self):
        output = self.output_input.text()
        if output and os.path.exists(output):
            os.startfile(output)
    
    def delete_selected(self):
        selected = self.result_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "警告", "请先选中要删除的截图")
            return
        
        rows = set(item.row() for item in selected)
        images_to_delete = []
        for row in rows:
            if row < len(self.results):
                images_to_delete.extend(self.results[row]["images"])
        
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除选中的{len(rows)}个视频的截图吗？\n共{len(images_to_delete)}张图片",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            from core.screenshot import delete_images
            deleted, failed = delete_images(images_to_delete)
            QMessageBox.information(self, "完成", f"已删除{deleted}张截图")
            self.result_table.setRowCount(0)
            self.results = [r for r in self.results if r["full_path"] not in
                           [self.results[row]["full_path"] for row in rows]]
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

- [ ] **Step 2: Verify module can be imported**

Run: `cd D:\JR_project\video_random_cut_mimo && python -c "from gui.screenshot_tab import ScreenshotTab; print('OK')"`
Expected: `OK`

---

### Task 3: Register Screenshot Tab in Main Window

**Files:**
- Modify: `gui/main_window.py`

- [ ] **Step 1: Add screenshot tab import and registration**

In `gui/main_window.py`, add import and tab:

```python
from gui.screenshot_tab import ScreenshotTab
```

After line 22 (after `self.face_detection_tab = FaceDetectionTab()`), add:

```python
self.screenshot_tab = ScreenshotTab()
```

After line 27 (after `self.tabs.addTab(self.face_detection_tab, "人脸识别")`), add:

```python
self.tabs.addTab(self.screenshot_tab, "视频截图")
```

- [ ] **Step 2: Verify main window loads without error**

Run: `cd D:\JR_project\video_random_cut_mimo && python -c "from gui.main_window import MainWindow; print('OK')"`
Expected: `OK`

---

### Task 4: Add Cover Image Feature to video_mixer.py

**Files:**
- Modify: `core/video_mixer.py`

- [ ] **Step 1: Add cover image support to VideoMixerEngine**

Add these imports at the top of `core/video_mixer.py`:

```python
import subprocess
import json
```

In `__init__`, add cover-related config parsing after line 28:

```python
self.cover_enabled = config.get("cover_enabled", False)
self.cover_folder = config.get("cover_folder", "")
self.cover_min = config.get("cover_min", 2)
self.cover_max = config.get("cover_max", 4)
```

Add new method `image_to_video` after `get_clip_videos` (after line 46):

```python
def image_to_video(self, image_path, duration, output_path, target_w=1080, target_h=1920):
    """Convert an image to a video segment with blur padding for non-9:16.
    
    Returns the output path.
    """
    cmd_probe = [
        "ffprobe", "-v", "quiet", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json", image_path
    ]
    result = subprocess.run(cmd_probe, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    try:
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        src_w, src_h = stream["width"], stream["height"]
    except Exception:
        src_w, src_h = target_w, target_h
    
    src_ratio = src_w / src_h
    target_ratio = target_w / target_h
    
    if abs(src_ratio - target_ratio) < 0.01:
        vf = f"scale={target_w}:{target_h},setsar=1"
    else:
        if src_ratio > target_ratio:
            fit_w, fit_h = target_w, int(target_w / src_ratio)
        else:
            fit_h = target_h
            fit_w = int(target_h * src_ratio)
        
        vf = (
            f"scale={fit_w}:{fit_h},"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"split[bg][fg];"
            f"[bg]scale={target_w}:{target_h},boxblur=20:5[blurred];"
            f"[fg]scale={fit_w}:{fit_h}[fg_scaled];"
            f"[blurred][fg_scaled]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    
    cmd = [
        "ffmpeg", "-loop", "1", "-i", image_path,
        "-vf", vf,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-an", "-y", output_path
    ]
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=3600)
    if result.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(f"image_to_video failed: {result.stderr}")
    return output_path
```

Modify `generate_plan` method. After line 57 (`plan = []`), add cover segment logic before the head/tail logic:

```python
if self.cover_enabled and self.cover_folder:
    image_exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
    cover_images = []
    for f in os.listdir(self.cover_folder):
        if f.lower().endswith(image_exts):
            cover_images.append(os.path.join(self.cover_folder, f))
    
    if cover_images:
        cover_image = random.choice(cover_images)
        cover_duration = random.uniform(self.cover_min, self.cover_max)
        plan.append({"type": "cover", "image": cover_image, "duration": cover_duration})
```

In `create_mix` method, inside the loop `for i, segment in enumerate(plan):` (around line 193), add a new branch for the cover type. Before the existing `if segment["type"] in ("head", "tail", "middle"):` block, add:

```python
if segment["type"] == "cover":
    seg_path = os.path.join(tmp_dir, f"cover_{len(all_parts)}.mp4")
    self.image_to_video(segment["image"], segment["duration"], seg_path)
    all_parts.append({
        "path": seg_path,
        "duration": segment["duration"],
        "type": "cover"
    })
```

- [ ] **Step 2: Verify module can be imported**

Run: `cd D:\JR_project\video_random_cut_mimo && python -c "from core.video_mixer import VideoMixerEngine; print('OK')"`
Expected: `OK`

---

### Task 5: Add Cover Image Settings to video_mix_tab.py

**Files:**
- Modify: `gui/video_mix_tab.py`

- [ ] **Step 1: Add cover image UI elements**

In `init_ui` method, after the `head_tail_group` section (after line 89 `video_layout.addWidget(head_tail_group)`), insert a new cover group:

```python
cover_group = QGroupBox("封面图设置")
cover_layout = QVBoxLayout()

self.cover_check = QCheckBox("启用封面图")
self.cover_check.setChecked(False)
self.cover_check.stateChanged.connect(self.on_cover_changed)
cover_layout.addWidget(self.cover_check)

cover_folder_layout = QHBoxLayout()
self.cover_folder_input = QLineEdit()
self.cover_folder_input.setPlaceholderText("选择封面图片文件夹...")
self.cover_folder_input.setEnabled(False)
cover_folder_btn = QPushButton("浏览")
cover_folder_btn.setEnabled(False)
cover_folder_btn.clicked.connect(self.browse_cover_folder)
self.cover_folder_btn = cover_folder_btn
cover_folder_layout.addWidget(self.cover_folder_input)
cover_folder_layout.addWidget(cover_folder_btn)
cover_layout.addLayout(cover_folder_layout)

cover_time_layout = QHBoxLayout()
cover_time_layout.addWidget(QLabel("封面时长(秒):"))
self.cover_min = QSpinBox()
self.cover_min.setRange(1, 10)
self.cover_min.setValue(2)
self.cover_min.setEnabled(False)
cover_time_layout.addWidget(self.cover_min)
cover_time_layout.addWidget(QLabel("~"))
self.cover_max = QSpinBox()
self.cover_max.setRange(1, 10)
self.cover_max.setValue(4)
self.cover_max.setEnabled(False)
cover_time_layout.addWidget(self.cover_max)
cover_layout.addLayout(cover_time_layout)

cover_group.setLayout(cover_layout)
```

Change `video_layout.addWidget(cover_group)` to be placed before `video_layout.addWidget(head_tail_group)` — actually, let me be precise. After the line `video_layout.addLayout(folder_layout)`, add:

```python
video_layout.addWidget(cover_group)
```

But wait — looking at the code, the cover_group needs to be defined before it's added. Let me restructure: insert the cover_group definition right after `head_tail_group` is fully defined but before `slice_group`. Actually, the cleanest approach is to insert it between `head_tail_group` and `slice_group`:

After line 89 (`video_layout.addWidget(head_tail_group)`), before line 91 (`slice_group = QGroupBox("切片设置")`), insert:

```python
cover_group = QGroupBox("封面图设置")
cover_layout = QVBoxLayout()

self.cover_check = QCheckBox("启用封面图")
self.cover_check.setChecked(False)
self.cover_check.stateChanged.connect(self.on_cover_changed)
cover_layout.addWidget(self.cover_check)

cover_folder_layout = QHBoxLayout()
self.cover_folder_input = QLineEdit()
self.cover_folder_input.setPlaceholderText("选择封面图片文件夹...")
self.cover_folder_input.setEnabled(False)
cover_folder_btn = QPushButton("浏览")
cover_folder_btn.setEnabled(False)
cover_folder_btn.clicked.connect(self.browse_cover_folder)
self.cover_folder_btn = cover_folder_btn
cover_folder_layout.addWidget(self.cover_folder_input)
cover_folder_layout.addWidget(cover_folder_btn)
cover_layout.addLayout(cover_folder_layout)

cover_time_layout = QHBoxLayout()
cover_time_layout.addWidget(QLabel("封面时长(秒):"))
self.cover_min = QSpinBox()
self.cover_min.setRange(1, 10)
self.cover_min.setValue(2)
self.cover_min.setEnabled(False)
cover_time_layout.addWidget(self.cover_min)
cover_time_layout.addWidget(QLabel("~"))
self.cover_max = QSpinBox()
self.cover_max.setRange(1, 10)
self.cover_max.setValue(4)
self.cover_max.setEnabled(False)
cover_time_layout.addWidget(self.cover_max)
cover_layout.addLayout(cover_time_layout)

cover_group.setLayout(cover_layout)
video_layout.addWidget(cover_group)
```

Add `on_cover_changed` method after `on_head_tail_changed`:

```python
def on_cover_changed(self, state):
    enabled = state == Qt.Checked
    self.cover_folder_input.setEnabled(enabled)
    self.cover_folder_btn.setEnabled(enabled)
    self.cover_min.setEnabled(enabled)
    self.cover_max.setEnabled(enabled)
```

Add `browse_cover_folder` method:

```python
def browse_cover_folder(self):
    folder = QFileDialog.getExistingDirectory(self, "选择封面图片文件夹")
    if folder:
        self.cover_folder_input.setText(folder)
        self.save_config()
```

In `load_config`, add after line 229:

```python
self.cover_check.setChecked(get_config("video_mix", "cover_enabled", "false") == "true")
self.cover_folder_input.setText(get_config("video_mix", "cover_folder", ""))
self.cover_min.setValue(int(get_config("video_mix", "cover_min", "2")))
self.cover_max.setValue(int(get_config("video_mix", "cover_max", "4")))
```

In `save_config`, add after line 245:

```python
set_config("video_mix", "cover_enabled", str(self.cover_check.isChecked()).lower())
set_config("video_mix", "cover_folder", self.cover_folder_input.text())
set_config("video_mix", "cover_min", str(self.cover_min.value()))
set_config("video_mix", "cover_max", str(self.cover_max.value()))
```

In `start_mixing`, in the config dict (around line 302), add before the closing brace:

```python
"cover_enabled": self.cover_check.isChecked(),
"cover_folder": self.cover_folder_input.text(),
"cover_min": self.cover_min.value(),
"cover_max": self.cover_max.value(),
```

- [ ] **Step 2: Verify module can be imported**

Run: `cd D:\JR_project\video_random_cut_mimo && python -c "from gui.video_mix_tab import VideoMixTab; print('OK')"`
Expected: `OK`

---

### Task 6: Add Config Sections

**Files:**
- Modify: `config.json`

- [ ] **Step 1: Add screenshot config section**

Add to `config.json`:

```json
"screenshot": {
    "folder": "",
    "output": "",
    "count": "5",
    "detect_face": "false",
    "delete_face": "false"
}
```

- [ ] **Step 2: Add cover_image config section to video_mix**

Add to the `video_mix` section:

```json
"cover_enabled": "false",
"cover_folder": "",
"cover_min": "2",
"cover_max": "4"
```

---

### Task 7: End-to-End Verification

**Files:**
- None (verification only)

- [ ] **Step 1: Run full application test**

Run: `cd D:\JR_project\video_random_cut_mimo && python -c "from gui.main_window import MainWindow; from PyQt5.QtWidgets import QApplication; import sys; app = QApplication(sys.argv); w = MainWindow(); print('Tabs:', [w.tabs.tabText(i) for i in range(w.tabs.count())]); print('OK')"`
Expected: Shows all 6 tabs including "视频截图", prints OK

- [ ] **Step 2: Verify screenshot core functions**

Run: `cd D:\JR_project\video_random_cut_mimo && python -c "from core.screenshot import extract_random_frames, detect_face_in_image, get_video_duration; print('extract_random_frames:', callable(extract_random_frames)); print('detect_face_in_image:', callable(detect_face_in_image)); print('OK')"`
Expected: All True, OK

- [ ] **Step 3: Verify video_mixer cover support**

Run: `cd D:\JR_project\video_random_cut_mimo && python -c "from core.video_mixer import VideoMixerEngine; e = VideoMixerEngine({'clips_folder':'.','output_folder':'.','head_tail':0,'head_min':3,'head_max':5,'tail_min':3,'tail_max':5,'slice_count_min':3,'slice_count_max':5,'slice_duration_min':3,'slice_duration_max':5,'mode':0,'mix_count':1,'cover_enabled':True,'cover_folder':'.','cover_min':2,'cover_max':4}); print('cover_enabled:', e.cover_enabled); print('OK')"`
Expected: `cover_enabled: True`, OK
