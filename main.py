import logging
import sys
import os

# qt_material 用 logging 输出的两条无害 warning，提前静音
logging.getLogger("qt_material").setLevel(logging.ERROR)
logging.getLogger("root").setLevel(logging.ERROR)

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QIcon
from gui.main_window import MainWindow
from gui.config import get_config
from gui.styles import apply_theme_and_font


def main():
    app = QApplication(sys.argv)
    
    # 设置应用图标（任务栏 + 窗口左上角）
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    
    theme = get_config("settings", "theme", "dark_teal.xml")
    font_size = int(get_config("settings", "font_size", "10"))
    apply_theme_and_font(app, theme, font_size)
    window = MainWindow(app)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
