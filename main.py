import logging
import sys

# qt_material 用 logging 输出的两条无害 warning，提前静音
logging.getLogger("qt_material").setLevel(logging.ERROR)
logging.getLogger("root").setLevel(logging.ERROR)

from PyQt5.QtWidgets import QApplication
from gui.main_window import MainWindow
from gui.config import get_config
from gui.styles import apply_theme_and_font


def main():
    app = QApplication(sys.argv)
    theme = get_config("settings", "theme", "dark_teal.xml")
    font_size = int(get_config("settings", "font_size", "10"))
    apply_theme_and_font(app, theme, font_size)
    window = MainWindow(app)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
