from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QRadioButton, QButtonGroup
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from core.mixer import VideoMixer
from gui.config import get_config, set_config


class MixWorker(QThread):
    progress = pyqtSignal(int, float, float)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    
    def __init__(self, clips_dir, audio_path, output_dir, is_folder=False):
        super().__init__()
        self.clips_dir = clips_dir
        self.audio_path = audio_path
        self.output_dir = output_dir
        self.is_folder = is_folder
    
    def run(self):
        try:
            mixer = VideoMixer()
            if self.is_folder:
                results = mixer.mix_folder(
                    self.clips_dir,
                    self.audio_path,
                    self.output_dir,
                    lambda count, total: self.progress.emit(count, 0, total)
                )
            else:
                import os
                audio_name = os.path.splitext(os.path.basename(self.audio_path))[0]
                output_path = os.path.join(self.output_dir, f"{audio_name}.mp4")
                results = [mixer.mix_videos(
                    self.clips_dir,
                    self.audio_path,
                    output_path,
                    lambda count, cur, total: self.progress.emit(count, cur, total)
                )]
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class MixTab(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.init_ui()
        self.load_config()
    
    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        clips_group = QGroupBox("切片文件夹")
        clips_layout = QHBoxLayout()
        clips_layout.setSpacing(8)
        self.clips_input = QLineEdit()
        self.clips_input.setPlaceholderText("选择切片文件夹...")
        self.clips_input.setMinimumHeight(30)
        clips_btn = QPushButton("浏览")
        clips_btn.setFixedWidth(80)
        clips_btn.clicked.connect(self.browse_clips)
        clips_layout.addWidget(self.clips_input, 1)
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
        audio_file_layout.setSpacing(8)
        self.audio_input = QLineEdit()
        self.audio_input.setPlaceholderText("选择音频文件...")
        self.audio_input.setMinimumHeight(30)
        audio_btn = QPushButton("浏览")
        audio_btn.setFixedWidth(80)
        audio_btn.clicked.connect(self.browse_audio)
        audio_file_layout.addWidget(self.audio_input, 1)
        audio_file_layout.addWidget(audio_btn)
        
        audio_layout.addWidget(self.single_audio_radio)
        audio_layout.addWidget(self.folder_audio_radio)
        audio_layout.addLayout(audio_file_layout)
        audio_group.setLayout(audio_layout)
        
        output_group = QGroupBox("输出设置")
        output_layout = QHBoxLayout()
        output_layout.setSpacing(8)
        self.output_input = QLineEdit()
        self.output_input.setPlaceholderText("输出文件夹...")
        self.output_input.setMinimumHeight(30)
        output_btn = QPushButton("浏览")
        output_btn.setFixedWidth(80)
        output_btn.clicked.connect(self.browse_output)
        output_layout.addWidget(self.output_input, 1)
        output_layout.addWidget(output_btn)
        output_group.setLayout(output_layout)
        
        self.mix_btn = QPushButton("开始混剪")
        self.mix_btn.setMinimumHeight(36)
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
    
    def load_config(self):
        self.clips_input.setText(get_config("mix", "clips", ""))
        self.audio_input.setText(get_config("mix", "audio", ""))
        self.output_input.setText(get_config("mix", "output", ""))
        is_folder = get_config("mix", "audio_type", "single") == "folder"
        self.folder_audio_radio.setChecked(is_folder)
        self.single_audio_radio.setChecked(not is_folder)
    
    def save_config(self):
        set_config("mix", "clips", self.clips_input.text())
        set_config("mix", "audio", self.audio_input.text())
        set_config("mix", "output", self.output_input.text())
        set_config("mix", "audio_type", "folder" if self.folder_audio_radio.isChecked() else "single")
    
    def browse_clips(self):
        folder = QFileDialog.getExistingDirectory(self, "选择切片文件夹")
        if folder:
            self.clips_input.setText(folder)
            self.save_config()
    
    def browse_audio(self):
        if self.single_audio_radio.isChecked():
            file, _ = QFileDialog.getOpenFileName(
                self, "选择音频文件",
                "", "音频文件 (*.mp3 *.wav *.aac *.flac *.ogg)"
            )
            if file:
                self.audio_input.setText(file)
                self.save_config()
        else:
            folder = QFileDialog.getExistingDirectory(self, "选择音频文件夹")
            if folder:
                self.audio_input.setText(folder)
                self.save_config()
    
    def browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if folder:
            self.output_input.setText(folder)
            self.save_config()
    
    def start_mixing(self):
        clips = self.clips_input.text()
        audio = self.audio_input.text()
        output = self.output_input.text()
        
        if not clips or not audio or not output:
            QMessageBox.warning(self, "警告", "请填写所有必填项")
            return
        
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "警告", "任务正在执行中")
            return
        
        is_folder = self.folder_audio_radio.isChecked()
        
        if is_folder:
            import os
            audio_exts = (".mp3", ".wav", ".aac", ".flac", ".ogg")
            audio_files = [
                f for f in os.listdir(audio)
                if f.lower().endswith(audio_exts)
            ]
            if not audio_files:
                QMessageBox.warning(self, "警告", "音频文件夹中没有音频文件")
                return
        
        self.save_config()
        self.mix_btn.setEnabled(False)
        self.worker = MixWorker(clips, audio, output, is_folder)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()
    
    def on_progress(self, count, current, total):
        if current > 0:
            progress = int((current / total) * 100)
            self.progress_bar.setValue(progress)
            self.status_label.setText(f"已使用 {count} 个片段 ({current:.1f}/{total:.1f}秒)")
        else:
            progress = int((count / total) * 100)
            self.progress_bar.setValue(progress)
            self.status_label.setText(f"正在处理 {count}/{total} 个音频文件")
    
    def on_finished(self, results):
        self.mix_btn.setEnabled(True)
        self.status_label.setText("混剪完成")
        QMessageBox.information(self, "完成", f"已完成 {len(results)} 个混剪视频\n输出目录: {self.output_input.text()}")
    
    def on_error(self, msg):
        self.mix_btn.setEnabled(True)
        self.status_label.setText("混剪失败")
        QMessageBox.critical(self, "错误", msg)
