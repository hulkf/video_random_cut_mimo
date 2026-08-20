from PyQt5.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QFrame
from PyQt5.QtCore import Qt, pyqtSignal

from gui.tab_registry import TABS, stop_tab_threads
from core.ffmpeg_runner import kill_all_ffmpeg


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

        # ── 注册表驱动：实例化 + addTab（顺序 = TABS 顺序，即原 addTab 顺序）──
        for attr, title, factory in TABS:
            tab = factory(app)
            setattr(self, attr, tab)
            self.tabs.addTab(tab, title)

    def closeEvent(self, event):
        """关闭窗口时停止所有后台线程，确保进程（及启动它的终端）能随之退出。

        stop_tab_threads 只停 QThread（协作式停止 + terminate 兜底）；
        之后再 kill_all_ffmpeg() 兜底杀净所有被追踪的 ffmpeg 子进程，
        避免关窗后残留 ffmpeg 孤儿进程。

        关窗前统一保存各 tab 配置（save_config 兜底）：即使改了配置没点"开始"
        直接关窗，下次打开也能恢复（PathRow 已支持手动输入自动保存，这里是双保险）。
        """
        for attr, _title, _factory in TABS:
            tab = getattr(self, attr, None)
            if tab is None:
                continue
            save = getattr(tab, "save_config", None)
            if callable(save):
                try:
                    save()
                except Exception:
                    pass
            stop_tab_threads(tab)
        kill_all_ffmpeg()
        event.accept()
