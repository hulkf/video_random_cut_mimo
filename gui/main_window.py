from PyQt5.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QFrame, QApplication
from PyQt5.QtCore import Qt, pyqtSignal, QMimeData
from PyQt5.QtGui import QDrag
from gui.slice_tab import SliceTab
from gui.text_recognition_tab import TextRecognitionTab
from gui.audio_mix_tab import AudioMixTab
from gui.video_mix_tab import VideoMixTab
from gui.video_concat_tab import VideoConcatTab
from gui.video_resize_tab import VideoResizeTab
from gui.face_detection_tab import FaceDetectionTab
from gui.screenshot_tab import ScreenshotTab
from gui.subtitle_tab import SubtitleTab
from gui.settings_tab import SettingsTab
from gui.kaipai_cloud_tab import KaipaiCloudTab
from gui.config import get_config, set_config


TAB_DRAG_MIME = "application/x-video-random-cut-tab"


class DraggableTabButton(QPushButton):
    def __init__(self, title, tab_widget):
        super().__init__(title)
        self.tab_widget = tab_widget
        self._drag_start_pos = None
        self.setAcceptDrops(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return
        distance = (event.pos() - self._drag_start_pos).manhattanLength()
        if distance < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        source_index = self.tab_widget.button_index(self)
        if source_index < 0:
            return

        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(TAB_DRAG_MIME, str(source_index).encode("utf-8"))
        drag.setMimeData(mime)
        drag.exec_(Qt.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(TAB_DRAG_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(TAB_DRAG_MIME):
            event.ignore()
            return
        source_index = int(bytes(event.mimeData().data(TAB_DRAG_MIME)).decode("utf-8"))
        target_index = self.tab_widget.button_index(self)
        self.tab_widget.moveTab(source_index, target_index)
        event.acceptProposedAction()


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

        btn = DraggableTabButton(title, self)
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
        btn.clicked.connect(lambda checked, b=btn: self.setCurrentIndex(self.button_index(b)))
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

    def button_index(self, button):
        try:
            return self.buttons.index(button)
        except ValueError:
            return -1

    def moveTab(self, source_idx, target_idx):
        if source_idx < 0 or target_idx < 0:
            return
        if source_idx >= len(self.tabs) or target_idx >= len(self.tabs):
            return
        if source_idx == target_idx:
            return

        current_widget = None
        if 0 <= self.current_index < len(self.tabs):
            current_widget = self.tabs[self.current_index]

        tab = self.tabs.pop(source_idx)
        button = self.buttons.pop(source_idx)
        self.tabs.insert(target_idx, tab)
        self.buttons.insert(target_idx, button)

        for btn in self.buttons:
            self.tab_bar_layout.removeWidget(btn)
        for idx, btn in enumerate(self.buttons):
            self.tab_bar_layout.insertWidget(idx, btn)

        if current_widget is not None:
            self.current_index = self.tabs.index(current_widget)
        for idx, btn in enumerate(self.buttons):
            btn.setChecked(idx == self.current_index)
        self.saveTabOrder()

    def saveTabOrder(self):
        set_config("main_window", "tab_order", [btn.text() for btn in self.buttons])

    def applySavedOrder(self):
        saved_order = get_config("main_window", "tab_order", [])
        if not isinstance(saved_order, list):
            return

        current_widget = None
        if 0 <= self.current_index < len(self.tabs):
            current_widget = self.tabs[self.current_index]

        title_to_items = {
            button.text(): (tab, button)
            for tab, button in zip(self.tabs, self.buttons)
        }
        ordered_items = []
        used_titles = set()
        for title in saved_order:
            if title in title_to_items:
                ordered_items.append(title_to_items[title])
                used_titles.add(title)
        for tab, button in zip(self.tabs, self.buttons):
            if button.text() not in used_titles:
                ordered_items.append((tab, button))

        if len(ordered_items) != len(self.tabs):
            return

        self.tabs = [item[0] for item in ordered_items]
        self.buttons = [item[1] for item in ordered_items]

        for btn in self.buttons:
            self.tab_bar_layout.removeWidget(btn)
        for idx, btn in enumerate(self.buttons):
            self.tab_bar_layout.insertWidget(idx, btn)

        if current_widget is not None:
            self.current_index = self.tabs.index(current_widget)
        for idx, btn in enumerate(self.buttons):
            btn.setChecked(idx == self.current_index)


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
        self.video_resize_tab = VideoResizeTab()
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
        self.tabs.addTab(self.video_resize_tab, "视频尺寸")
        self.tabs.addTab(self.subtitle_tab, "视频字幕")
        self.tabs.addTab(self.kaipai_cloud_tab, "开拍云端")
        self.tabs.addTab(self.settings_tab, "设置")
        self.tabs.applySavedOrder()
