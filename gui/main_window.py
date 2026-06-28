from PyQt5.QtWidgets import QMainWindow, QTabWidget
from gui.slice_tab import SliceTab
from gui.text_recognition_tab import TextRecognitionTab
from gui.audio_mix_tab import AudioMixTab
from gui.video_mix_tab import VideoMixTab
from gui.face_detection_tab import FaceDetectionTab
from gui.screenshot_tab import ScreenshotTab
from gui.subtitle_tab import SubtitleTab
from gui.settings_tab import SettingsTab
from gui.kaipai_cloud_tab import KaipaiCloudTab


class MainWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.setWindowTitle("视频混剪工具")
        self.setMinimumSize(900, 700)
        
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        
        self.slice_tab = SliceTab()
        self.screenshot_tab = ScreenshotTab()
        self.text_recognition_tab = TextRecognitionTab()
        self.face_detection_tab = FaceDetectionTab()
        self.audio_mix_tab = AudioMixTab()
        self.video_mix_tab = VideoMixTab()
        self.subtitle_tab = SubtitleTab()
        self.kaipai_cloud_tab = KaipaiCloudTab()
        self.settings_tab = SettingsTab(app)
        
        self.tabs.addTab(self.slice_tab, "视频切片")
        self.tabs.addTab(self.screenshot_tab, "视频截图")
        self.tabs.addTab(self.text_recognition_tab, "文字识别")
        self.tabs.addTab(self.face_detection_tab, "人脸识别")
        self.tabs.addTab(self.audio_mix_tab, "音频混剪")
        self.tabs.addTab(self.video_mix_tab, "视频混剪")
        self.tabs.addTab(self.subtitle_tab, "视频字幕")
        self.tabs.addTab(self.kaipai_cloud_tab, "开拍云端")
        self.tabs.addTab(self.settings_tab, "设置")
