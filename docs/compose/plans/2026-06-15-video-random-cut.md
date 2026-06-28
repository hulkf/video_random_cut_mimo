# Video Random Cut Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a PyQt5 GUI tool for video slicing with text detection and random video mixing based on audio length.

**Architecture:** Two main modules - (1) Video Slicer: cuts videos into configurable segments, uses PaddleOCR to detect text in frames, outputs clips + JSON report marking text-containing segments; (2) Video Mixer: extracts audio, randomly selects clips to match audio duration, produces final MP4.

**Tech Stack:** Python 3.8+, PyQt5, FFmpeg (subprocess), PaddleOCR, moviepy

---

## File Structure

```
video_random_cut_mimo/
├── main.py                    # Entry point
├── requirements.txt           # Dependencies
├── gui/
│   ├── __init__.py
│   ├── main_window.py         # Main window with tabs
│   ├── slice_tab.py           # Slicing tab UI
│   └── mix_tab.py             # Mixing tab UI
├── core/
│   ├── __init__.py
│   ├── slicer.py              # Video slicing logic
│   ├── text_detector.py       # PaddleOCR text detection
│   ├── mixer.py               # Video mixing logic
│   └── audio_extractor.py     # Audio extraction
└── utils/
    ├── __init__.py
    └── video_utils.py         # FFmpeg wrapper functions
```

---

### Task 1: Project Setup

**Covers:** N/A (scaffolding)

**Files:**
- Create: `requirements.txt`
- Create: `main.py`
- Create: `gui/__init__.py`
- Create: `core/__init__.py`
- Create: `utils/__init__.py`

- [ ] **Step 1: Create requirements.txt**

```
PyQt5>=5.15.0
paddlepaddle>=2.4.0
paddleocr>=2.6.0
moviepy>=1.0.3
Pillow>=9.0.0
```

- [ ] **Step 2: Create main.py entry point**

```python
import sys
from PyQt5.QtWidgets import QApplication
from gui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create __init__.py files**

Create empty `__init__.py` in gui/, core/, utils/ directories.

---

### Task 2: Video Utility Functions

**Covers:** S1 (Video slicing)

**Files:**
- Create: `utils/video_utils.py`

- [ ] **Step 1: Create video_utils.py with FFmpeg helpers**

```python
import subprocess
import json
import os

def get_video_duration(video_path):
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])

def extract_frames(video_path, output_dir, frame_interval=1.0):
    """Extract frames from video at specified interval."""
    os.makedirs(output_dir, exist_ok=True)
    cmd = [
        "ffmpeg", "-i", video_path, "-vf", f"fps=1/{frame_interval}",
        "-q:v", "2", os.path.join(output_dir, "frame_%04d.jpg")
    ]
    subprocess.run(cmd, capture_output=True)
    return sorted([
        os.path.join(output_dir, f)
        for f in os.listdir(output_dir)
        if f.endswith(".jpg")
    ])

def cut_video(video_path, start_time, duration, output_path):
    """Cut a segment from video."""
    cmd = [
        "ffmpeg", "-ss", str(start_time), "-i", video_path,
        "-t", str(duration), "-c", "copy", "-y", output_path
    ]
    subprocess.run(cmd, capture_output=True)
    return output_path

def extract_audio(video_path, output_path):
    """Extract audio from video."""
    cmd = [
        "ffmpeg", "-i", video_path, "-vn", "-acodec", "copy",
        "-y", output_path
    ]
    subprocess.run(cmd, capture_output=True)
    return output_path

def concat_videos(video_list, output_path):
    """Concatenate multiple videos."""
    list_file = output_path + ".txt"
    with open(list_file, "w") as f:
        for video in video_list:
            f.write(f"file '{video}'\n")
    cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file,
        "-c", "copy", "-y", output_path
    ]
    subprocess.run(cmd, capture_output=True)
    os.remove(list_file)
    return output_path
```

---

### Task 3: Text Detection Module

**Covers:** S1 (Text detection in frames)

**Files:**
- Create: `core/text_detector.py`

- [ ] **Step 1: Create text_detector.py**

```python
from paddleocr import PaddleOCR

class TextDetector:
    def __init__(self):
        self.ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    
    def detect_text(self, image_path):
        """Detect text in image, return True if text found."""
        result = self.ocr.ocr(image_path, cls=True)
        if result and result[0]:
            return True
        return False
    
    def has_text_in_frames(self, frame_paths, threshold=0.3):
        """Check if enough frames contain text."""
        if not frame_paths:
            return False
        text_count = 0
        for frame in frame_paths:
            if self.detect_text(frame):
                text_count += 1
        return (text_count / len(frame_paths)) >= threshold
```

---

### Task 4: Video Slicer Module

**Covers:** S1 (Video slicing with text detection)

**Files:**
- Create: `core/slicer.py`

- [ ] **Step 1: Create slicer.py**

```python
import os
import json
from core.text_detector import TextDetector
from utils.video_utils import (
    get_video_duration, extract_frames, cut_video
)

class VideoSlicer:
    def __init__(self, min_duration=3, max_duration=5):
        self.min_duration = min_duration
        self.max_duration = max_duration
        self.text_detector = TextDetector()
    
    def slice_video(self, video_path, output_dir, callback=None):
        """Slice a single video into segments."""
        os.makedirs(output_dir, exist_ok=True)
        duration = get_video_duration(video_path)
        results = []
        start = 0
        segment_index = 0
        
        while start < duration:
            segment_duration = min(
                self.max_duration,
                duration - start
            )
            if segment_duration < self.min_duration:
                break
            
            output_path = os.path.join(
                output_dir,
                f"segment_{segment_index:04d}.mp4"
            )
            cut_video(video_path, start, segment_duration, output_path)
            
            frames_dir = os.path.join(output_dir, f"frames_{segment_index:04d}")
            frames = extract_frames(output_path, frames_dir)
            has_text = self.text_detector.has_text_in_frames(frames)
            
            results.append({
                "file": output_path,
                "start": start,
                "duration": segment_duration,
                "has_text": has_text
            })
            
            if callback:
                callback(segment_index, has_text)
            
            start += segment_duration
            segment_index += 1
        
        return results
    
    def slice_folder(self, folder_path, output_dir, callback=None):
        """Slice all videos in a folder."""
        video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
        all_results = []
        
        for file in os.listdir(folder_path):
            if file.lower().endswith(video_exts):
                video_path = os.path.join(folder_path, file)
                video_name = os.path.splitext(file)[0]
                video_output = os.path.join(output_dir, video_name)
                results = self.slice_video(
                    video_path, video_output, callback
                )
                all_results.extend(results)
        
        report_path = os.path.join(output_dir, "slice_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        
        return all_results
```

---

### Task 5: Audio Extractor Module

**Covers:** S2 (Audio extraction for mixing)

**Files:**
- Create: `core/audio_extractor.py`

- [ ] **Step 1: Create audio_extractor.py**

```python
import os
from utils.video_utils import get_video_duration, extract_audio

class AudioExtractor:
    def get_audio_duration(self, audio_path):
        """Get duration of audio file."""
        return get_video_duration(audio_path)
    
    def extract_audio_from_video(self, video_path, output_path):
        """Extract audio from video file."""
        return extract_audio(video_path, output_path)
    
    def get_audio_from_folder(self, folder_path):
        """Get list of audio files from folder."""
        audio_exts = (".mp3", ".wav", ".aac", ".flac", ".ogg")
        return [
            os.path.join(folder_path, f)
            for f in os.listdir(folder_path)
            if f.lower().endswith(audio_exts)
        ]
```

---

### Task 6: Video Mixer Module

**Covers:** S2 (Random video mixing)

**Files:**
- Create: `core/mixer.py`

- [ ] **Step 1: Create mixer.py**

```python
import os
import random
from core.audio_extractor import AudioExtractor
from utils.video_utils import get_video_duration, concat_videos

class VideoMixer:
    def __init__(self):
        self.audio_extractor = AudioExtractor()
    
    def mix_videos(self, clips_dir, audio_path, output_path, callback=None):
        """Mix clips to match audio duration."""
        audio_duration = self.audio_extractor.get_audio_duration(audio_path)
        
        clip_files = [
            os.path.join(clips_dir, f)
            for f in os.listdir(clips_dir)
            if f.endswith(".mp4")
        ]
        
        if not clip_files:
            raise ValueError("No video clips found")
        
        selected_clips = []
        current_duration = 0
        used_indices = set()
        
        while current_duration < audio_duration and len(used_indices) < len(clip_files):
            available = [
                i for i in range(len(clip_files))
                if i not in used_indices
            ]
            if not available:
                used_indices.clear()
                available = list(range(len(clip_files)))
            
            idx = random.choice(available)
            clip = clip_files[idx]
            clip_duration = get_video_duration(clip)
            
            if current_duration + clip_duration <= audio_duration * 1.1:
                selected_clips.append(clip)
                current_duration += clip_duration
                used_indices.add(idx)
                
                if callback:
                    callback(len(selected_clips), current_duration, audio_duration)
        
        return concat_videos(selected_clips, output_path)
    
    def get_random_clips(self, clips_dir, target_duration, count=10):
        """Get random clips totaling target duration."""
        clip_files = [
            os.path.join(clips_dir, f)
            for f in os.listdir(clips_dir)
            if f.endswith(".mp4")
        ]
        
        selected = []
        current_duration = 0
        used_indices = set()
        
        while current_duration < target_duration and len(used_indices) < len(clip_files) * 2:
            available = [
                i for i in range(len(clip_files))
                if i not in used_indices
            ]
            if not available:
                used_indices.clear()
                available = list(range(len(clip_files)))
            
            idx = random.choice(available)
            clip = clip_files[idx]
            clip_duration = get_video_duration(clip)
            
            if current_duration + clip_duration <= target_duration * 1.1:
                selected.append(clip)
                current_duration += clip_duration
                used_indices.add(idx)
        
        return selected
```

---

### Task 7: Main Window GUI

**Covers:** S3 (GUI Interface)

**Files:**
- Create: `gui/main_window.py`

- [ ] **Step 1: Create main_window.py**

```python
from PyQt5.QtWidgets import QMainWindow, QTabWidget
from gui.slice_tab import SliceTab
from gui.mix_tab import MixTab

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频混剪工具")
        self.setMinimumSize(800, 600)
        
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        
        self.slice_tab = SliceTab()
        self.mix_tab = MixTab()
        
        self.tabs.addTab(self.slice_tab, "视频切片")
        self.tabs.addTab(self.mix_tab, "视频混剪")
```

---

### Task 8: Slice Tab GUI

**Covers:** S3 (Slicing UI)

**Files:**
- Create: `gui/slice_tab.py`

- [ ] **Step 1: Create slice_tab.py**

```python
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QSpinBox, QFileDialog,
    QTableWidget, QTableWidgetItem, QProgressBar,
    QMessageBox, QGroupBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from core.slicer import VideoSlicer

class SliceWorker(QThread):
    progress = pyqtSignal(int, bool)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    
    def __init__(self, folder_path, output_dir, min_dur, max_dur):
        super().__init__()
        self.folder_path = folder_path
        self.output_dir = output_dir
        self.min_dur = min_dur
        self.max_dur = max_dur
    
    def run(self):
        try:
            slicer = VideoSlicer(self.min_dur, self.max_dur)
            results = slicer.slice_folder(
                self.folder_path,
                self.output_dir,
                lambda idx, has_text: self.progress.emit(idx, has_text)
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))

class SliceTab(QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()
    
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
        self.output_input.setPlaceholderText("输出文件夹...")
        output_btn = QPushButton("浏览")
        output_btn.clicked.connect(self.browse_output)
        output_layout.addWidget(self.output_input)
        output_layout.addWidget(output_btn)
        
        input_layout.addLayout(folder_layout)
        input_layout.addLayout(output_layout)
        input_group.setLayout(input_layout)
        
        params_group = QGroupBox("切片参数")
        params_layout = QHBoxLayout()
        
        params_layout.addWidget(QLabel("最短时长(秒):"))
        self.min_duration = QSpinBox()
        self.min_duration.setRange(1, 60)
        self.min_duration.setValue(3)
        params_layout.addWidget(self.min_duration)
        
        params_layout.addWidget(QLabel("最长时长(秒):"))
        self.max_duration = QSpinBox()
        self.max_duration.setRange(1, 60)
        self.max_duration.setValue(5)
        params_layout.addWidget(self.max_duration)
        
        params_group.setLayout(params_layout)
        
        self.start_btn = QPushButton("开始切片")
        self.start_btn.clicked.connect(self.start_slicing)
        
        self.progress_bar = QProgressBar()
        
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels(
            ["文件", "开始时间", "时长", "包含文字"]
        )
        
        layout.addWidget(input_group)
        layout.addWidget(params_group)
        layout.addWidget(self.start_btn)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.result_table)
        
        self.setLayout(layout)
    
    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择视频文件夹")
        if folder:
            self.folder_input.setText(folder)
    
    def browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if folder:
            self.output_input.setText(folder)
    
    def start_slicing(self):
        folder = self.folder_input.text()
        output = self.output_input.text()
        
        if not folder or not output:
            QMessageBox.warning(self, "警告", "请选择输入和输出文件夹")
            return
        
        self.start_btn.setEnabled(False)
        self.worker = SliceWorker(
            folder, output,
            self.min_duration.value(),
            self.max_duration.value()
        )
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()
    
    def on_progress(self, idx, has_text):
        self.progress_bar.setValue(idx + 1)
    
    def on_finished(self, results):
        self.start_btn.setEnabled(True)
        self.result_table.setRowCount(len(results))
        for i, r in enumerate(results):
            self.result_table.setItem(i, 0, QTableWidgetItem(r["file"]))
            self.result_table.setItem(i, 1, QTableWidgetItem(str(r["start"])))
            self.result_table.setItem(i, 2, QTableWidgetItem(str(r["duration"])))
            self.result_table.setItem(i, 3, QTableWidgetItem("是" if r["has_text"] else "否"))
        QMessageBox.information(self, "完成", f"切片完成，共{len(results)}个片段")
    
    def on_error(self, msg):
        self.start_btn.setEnabled(True)
        QMessageBox.critical(self, "错误", msg)
```

---

### Task 9: Mix Tab GUI

**Covers:** S3 (Mixing UI)

**Files:**
- Create: `gui/mix_tab.py`

- [ ] **Step 1: Create mix_tab.py**

```python
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QRadioButton, QButtonGroup
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from core.mixer import VideoMixer

class MixWorker(QThread):
    progress = pyqtSignal(int, float, float)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    
    def __init__(self, clips_dir, audio_path, output_path):
        super().__init__()
        self.clips_dir = clips_dir
        self.audio_path = audio_path
        self.output_path = output_path
    
    def run(self):
        try:
            mixer = VideoMixer()
            result = mixer.mix_videos(
                self.clips_dir,
                self.audio_path,
                self.output_path,
                lambda count, cur, total: self.progress.emit(count, cur, total)
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

class MixTab(QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()
    
    def init_ui(self):
        layout = QVBoxLayout()
        
        clips_group = QGroupBox("切片文件夹")
        clips_layout = QHBoxLayout()
        self.clips_input = QLineEdit()
        self.clips_input.setPlaceholderText("选择切片文件夹...")
        clips_btn = QPushButton("浏览")
        clips_btn.clicked.connect(self.browse_clips)
        clips_layout.addWidget(self.clips_input)
        clips_layout.addWidget(clips_btn)
        clips_group.setLayout(clips_layout)
        
        audio_group = QGroupBox("音频设置")
        audio_layout = QVBoxLayout()
        
        self.audio_type_group = QButtonGroup()
        self.single_audio_radio = QRadioButton("单个音频文件")
        self.folder_audio_radio = QRadioButton("音频文件夹")
        self.single_audio_radio.setChecked(True)
        
        self.audio_type_group.addButton(self.single_audio_radio, 0)
        self.audio_type_group.addButton(self.folder_audio_radio, 1)
        
        audio_file_layout = QHBoxLayout()
        self.audio_input = QLineEdit()
        self.audio_input.setPlaceholderText("选择音频文件...")
        audio_btn = QPushButton("浏览")
        audio_btn.clicked.connect(self.browse_audio)
        audio_file_layout.addWidget(self.audio_input)
        audio_file_layout.addWidget(audio_btn)
        
        audio_layout.addWidget(self.single_audio_radio)
        audio_layout.addWidget(self.folder_audio_radio)
        audio_layout.addLayout(audio_file_layout)
        audio_group.setLayout(audio_layout)
        
        output_group = QGroupBox("输出设置")
        output_layout = QHBoxLayout()
        self.output_input = QLineEdit()
        self.output_input.setPlaceholderText("输出视频路径...")
        output_btn = QPushButton("浏览")
        output_btn.clicked.connect(self.browse_output)
        output_layout.addWidget(self.output_input)
        output_layout.addWidget(output_btn)
        output_group.setLayout(output_layout)
        
        self.mix_btn = QPushButton("开始混剪")
        self.mix_btn.clicked.connect(self.start_mixing)
        
        self.progress_bar = QProgressBar()
        self.status_label = QLabel("就绪")
        
        layout.addWidget(clips_group)
        layout.addWidget(audio_group)
        layout.addWidget(output_group)
        layout.addWidget(self.mix_btn)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)
        
        self.setLayout(layout)
    
    def browse_clips(self):
        folder = QFileDialog.getExistingDirectory(self, "选择切片文件夹")
        if folder:
            self.clips_input.setText(folder)
    
    def browse_audio(self):
        if self.single_audio_radio.isChecked():
            file, _ = QFileDialog.getOpenFileName(
                self, "选择音频文件",
                "", "音频文件 (*.mp3 *.wav *.aac *.flac *.ogg)"
            )
            if file:
                self.audio_input.setText(file)
        else:
            folder = QFileDialog.getExistingDirectory(self, "选择音频文件夹")
            if folder:
                self.audio_input.setText(folder)
    
    def browse_output(self):
        file, _ = QFileDialog.getSaveFileName(
            self, "保存混剪视频", "", "视频文件 (*.mp4)"
        )
        if file:
            self.output_input.setText(file)
    
    def start_mixing(self):
        clips = self.clips_input.text()
        audio = self.audio_input.text()
        output = self.output_input.text()
        
        if not clips or not audio or not output:
            QMessageBox.warning(self, "警告", "请填写所有必填项")
            return
        
        if self.folder_audio_radio.isChecked():
            import os
            audio_files = [
                f for f in os.listdir(audio)
                if f.lower().endswith((".mp3", ".wav", ".aac", ".flac", ".ogg"))
            ]
            if audio_files:
                audio = os.path.join(audio, audio_files[0])
            else:
                QMessageBox.warning(self, "警告", "音频文件夹中没有音频文件")
                return
        
        self.mix_btn.setEnabled(False)
        self.worker = MixWorker(clips, audio, output)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()
    
    def on_progress(self, count, current, total):
        progress = int((current / total) * 100)
        self.progress_bar.setValue(progress)
        self.status_label.setText(f"已使用 {count} 个片段 ({current:.1f}/{total:.1f}秒)")
    
    def on_finished(self, path):
        self.mix_btn.setEnabled(True)
        self.status_label.setText("混剪完成")
        QMessageBox.information(self, "完成", f"混剪视频已保存到:\n{path}")
    
    def on_error(self, msg):
        self.mix_btn.setEnabled(True)
        self.status_label.setText("混剪失败")
        QMessageBox.critical(self, "错误", msg)
```

---

### Task 10: Install Dependencies and Test

**Covers:** All

**Files:**
- Modify: `requirements.txt` (if needed)

- [ ] **Step 1: Install dependencies**

```bash
cd D:\JR_project\video_random_cut_mimo
pip install -r requirements.txt
```

- [ ] **Step 2: Run the application**

```bash
python main.py
```

Expected: PyQt5 window opens with two tabs "视频切片" and "视频混剪"

---

## Execution Plan

Execute tasks sequentially:
1. Task 1: Project setup (quick)
2. Task 2: Video utilities
3. Task 3: Text detection
4. Task 4: Video slicer
5. Task 5: Audio extractor
6. Task 6: Video mixer
7. Task 7-9: GUI components
8. Task 10: Integration test
