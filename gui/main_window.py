from PyQt5.QtWidgets import QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QScrollArea, QFrame
from PyQt5.QtCore import Qt, pyqtSignal
from gui.slice_tab import SliceTab
from gui.text_recognition_tab import TextRecognitionTab
from gui.audio_mix_tab import AudioMixTab
from gui.video_mix_tab import VideoMixTab
from gui.video_concat_tab import VideoConcatTab
from gui.face_detection_tab import FaceDetectionTab
from gui.screenshot_tab import ScreenshotTab
from gui.subtitle_tab import SubtitleTab
from gui.settings_tab import SettingsTab
from gui.kaipai_cloud_tab import KaipaiCloudTab


class FlowLayout(QVBoxLayout):
    """流式布局，支持自动换行"""
    pass


class WrapTabWidget(QWidget):
    """支持换行的Tab组件"""
    tab_changed = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.tabs = []
        self.buttons = []
        self.current_index = -1

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Tab按钮区域（可滚动）
        self.tab_bar = QWidget()
        self.tab_bar_layout = QHBoxLayout(self.tab_bar)
        self.tab_bar_layout.setContentsMargins(8, 8, 8, 8)
        self.tab_bar_layout.setSpacing(4)
        self.tab_bar_layout.addStretch()
        self.main_layout.addWidget(self.tab_bar)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        self.main_layout.addWidget(sep)

        # 内容区域
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.addWidget(self.content_widget, 1)

    def addTab(self, widget, title):
        idx = len(self.tabs)
        self.tabs.append(widget)
        widget.hide()
        self.content_layout.addWidget(widget)

        btn = QPushButton(title)
        btn.setCheckable(True)
        btn.setMinimumHeight(32)
        btn.setStyleSheet("""
            QPushButton {
                background-color: #3c3c3c;
                color: #9e9e9e;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
                margin: 2px;
            }
            QPushButton:checked {
                background-color: #26a69a;
                color: white;
            }
            QPushButton:hover {
                background-color: #484848;
            }
        """)
        btn.clicked.connect(lambda checked, i=idx: self.setCurrentIndex(i))
        self.buttons.append(btn)

        # 插入到stretch前面
        self.tab_bar_layout.insertWidget(self.tab_bar_layout.count() - 1, btn)

        if self.current_index == -1:
            self.setCurrentIndex(0)

    def setCurrentIndex(self, idx):
        if idx < 0 or idx >= len(self.tabs):
            return

        # 隐藏当前
        if 0 <= self.current_index < len(self.tabs):
            self.tabs[self.current_index].hide()
            self.buttons[self.current_index].setChecked(False)

        # 显示新的
        self.current_index = idx
        self.tabs[idx].show()
        self.buttons[idx].setChecked(True)
        self.tab_changed.emit(idx)

    def currentIndex(self):
        return self.current_index


class MainWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.setWindowTitle("视频混剪工具")
        self.setMinimumSize(900, 700)

        self.tabs = WrapTabWidget()
        self.setCentralWidget(self.tabs)

        self.slice_tab = SliceTab()
        self.screenshot_tab = ScreenshotTab()
        self.text_recognition_tab = TextRecognitionTab()
        self.face_detection_tab = FaceDetectionTab()
        self.audio_mix_tab = AudioMixTab()
        self.video_mix_tab = VideoMixTab()
        self.video_concat_tab = VideoConcatTab()
        self.subtitle_tab = SubtitleTab()
        self.kaipai_cloud_tab = KaipaiCloudTab()
        self.settings_tab = SettingsTab(app)

        self.tabs.addTab(self.slice_tab, "视频切片")
        self.tabs.addTab(self.screenshot_tab, "视频截图")
        self.tabs.addTab(self.text_recognition_tab, "文字识别")
        self.tabs.addTab(self.face_detection_tab, "人脸识别")
        self.tabs.addTab(self.audio_mix_tab, "音频混剪")
        self.tabs.addTab(self.video_mix_tab, "视频混剪")
        self.tabs.addTab(self.video_concat_tab, "视频拼接")
        self.tabs.addTab(self.subtitle_tab, "视频字幕")
        self.tabs.addTab(self.kaipai_cloud_tab, "开拍云端")
        self.tabs.addTab(self.settings_tab, "设置")
