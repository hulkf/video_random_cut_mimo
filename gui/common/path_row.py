# -*- coding: utf-8 -*-
"""路径选择行控件（P2-3）：QLineEdit + 浏览按钮，一行复用。

解决「路径输入行在 13+ 个 tab 重复实现」问题：文件夹/单文件/多文件三种模式，
浏览选择后自动回调 on_change（用于 save_config）。

内置唯一一份 QLineEdit 边框修复 QSS（qt-material 主题下输入框无边框的坑，
全项目只在这里修一次，新 tab 不再需要"三保险"）。
"""
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QPushButton, QFileDialog,
)

# 唯一一份输入框修复样式（全项目共用；!important 防被 qt-material 主题覆盖）
LINEEDIT_QSS = """
QLineEdit {
    min-height: 30px !important;
    padding: 4px 8px !important;
    border: 1px solid #5a5a5a !important;
    border-radius: 4px !important;
    background-color: #1e1e1e !important;
    selection-background-color: #4d8fff !important;
}
QLineEdit:hover {
    border-color: #7a7a7a !important;
}
QLineEdit:focus {
    border: 1px solid #4d8fff !important;
    background-color: #252525 !important;
}
"""

MODE_FOLDER = "folder"   # 选择目录（递归处理场景）
MODE_FILE = "file"       # 选择单个文件
MODE_FILES = "files"     # 选择多个文件


class PathRow(QWidget):
    """路径选择行。

    Args:
        placeholder: 输入框提示文字
        mode: MODE_FOLDER / MODE_FILE / MODE_FILES
        browse_label: 浏览按钮文字（默认"浏览"）
        on_change: 浏览选择后回调（callable(path)，用于自动保存配置）
        stretch_input: 输入框是否拉伸占满剩余宽度（默认 True）
    """

    def __init__(self, placeholder="", mode=MODE_FOLDER, browse_label="浏览",
                 on_change=None, stretch_input=True, parent=None):
        super().__init__(parent)
        self._mode = mode
        self._on_change = on_change

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setStyleSheet(LINEEDIT_QSS)
        layout.addWidget(self.edit, 1 if stretch_input else 0)

        self.browse_btn = QPushButton(browse_label)
        self.browse_btn.setFixedWidth(80)
        self.browse_btn.clicked.connect(self._browse)
        layout.addWidget(self.browse_btn)

    # ── 文本接口（对齐 QLineEdit）──
    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str) -> None:
        self.edit.setText(text)

    def clear(self) -> None:
        self.edit.clear()

    # ── 浏览 ─────────────────────────────────────────────────
    def _browse(self) -> None:
        parent = self.window()
        path = None
        if self._mode == MODE_FOLDER:
            path = QFileDialog.getExistingDirectory(parent, "选择文件夹")
        elif self._mode == MODE_FILE:
            path, _ = QFileDialog.getOpenFileName(parent, "选择文件")
        elif self._mode == MODE_FILES:
            paths, _ = QFileDialog.getOpenFileNames(parent, "选择文件")
            if paths:
                path = paths[0]  # 多文件模式在输入框中展示第一个，完整列表由 on_change 自行获取
        if path:
            self.edit.setText(path)
            if self._on_change is not None:
                try:
                    self._on_change(path)
                except Exception:
                    pass
