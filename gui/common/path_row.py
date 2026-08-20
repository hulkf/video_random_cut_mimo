# -*- coding: utf-8 -*-
"""路径选择行控件（P2-3）：QLineEdit + 浏览按钮，一行复用。

解决「路径输入行在 13+ 个 tab 重复实现」问题：文件夹/单文件/多文件三种模式，
浏览选择后自动回调 on_change（用于 save_config）。

P2-5 增强：
  - 支持拖拽：文件/文件夹拖入自动填入路径（文件模式下多文件 → 分号拼接）；
  - MODE_FILES 内部保存完整路径列表，on_change 传递完整列表（分号拼接），
    不再丢失多文件选择。

内置唯一一份 QLineEdit 边框修复 QSS（qt-material 主题下输入框无边框的坑，
全项目只在这里修一次，新 tab 不再需要"三保险"）。
"""
import os

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

# 多文件展示/传递的分隔符
PATH_SEP = ";"


class PathRow(QWidget):
    """路径选择行。

    Args:
        placeholder: 输入框提示文字
        mode: MODE_FOLDER / MODE_FILE / MODE_FILES
        browse_label: 浏览按钮文字（默认"浏览"）
        on_change: 浏览/拖拽选择后回调（callable(path)；MODE_FILES 时为分号拼接的完整列表）
        stretch_input: 输入框是否拉伸占满剩余宽度（默认 True）

    拖拽：
        文件夹/文件拖入自动填入。MODE_FILES 多文件以分号拼接展示，
        可通过 paths() 获取完整路径列表。
    """

    def __init__(self, placeholder="", mode=MODE_FOLDER, browse_label="浏览",
                 on_change=None, stretch_input=True, parent=None):
        super().__init__(parent)
        self._mode = mode
        self._on_change = on_change
        self._paths = []  # 内部完整路径列表（MODE_FILES 多文件时含全部）

        self.setAcceptDrops(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setStyleSheet(LINEEDIT_QSS)
        self.edit.setAcceptDrops(False)  # 由本控件统一处理拖拽
        layout.addWidget(self.edit, 1 if stretch_input else 0)

        self.browse_btn = QPushButton(browse_label)
        self.browse_btn.setFixedWidth(80)
        self.browse_btn.clicked.connect(self._browse)
        layout.addWidget(self.browse_btn)

    # ── 文本接口（对齐 QLineEdit）──
    def text(self) -> str:
        """输入框文本。MODE_FILES 时返回分号拼接的完整路径列表。"""
        return self.edit.text()

    def setText(self, text: str) -> None:
        """设置路径。分号分隔的多路径（MODE_FILES）会解析进内部列表。"""
        if text:
            self._set_paths(self._split_paths(text))
        else:
            self._paths = []
            self.edit.setText("")

    def clear(self) -> None:
        self._paths = []
        self.edit.clear()

    def paths(self) -> list:
        """返回完整路径列表（MODE_FILES 多文件时含全部；其他模式为单元素列表）。"""
        if self._paths:
            return list(self._paths)
        return self._split_paths(self.edit.text())

    def _split_paths(self, text: str) -> list:
        if not text:
            return []
        if self._mode == MODE_FILES:
            return [p.strip() for p in text.split(PATH_SEP) if p.strip()]
        return [text.strip()]

    # ── 内部：统一更新路径并回调 ─────────────────────────────
    def _set_paths(self, paths: list) -> None:
        cleaned = [p for p in paths if p and p.strip()]
        if not cleaned:
            return
        self._paths = cleaned
        if self._mode == MODE_FILES:
            self.edit.setText(PATH_SEP.join(cleaned))
        else:
            self.edit.setText(cleaned[0])
        if self._on_change is not None:
            try:
                self._on_change(self.text())
            except Exception:
                pass

    # ── 浏览 ─────────────────────────────────────────────────
    def _browse(self) -> None:
        parent = self.window()
        if self._mode == MODE_FOLDER:
            path = QFileDialog.getExistingDirectory(parent, "选择文件夹")
            if path:
                self._set_paths([path])
        elif self._mode == MODE_FILE:
            path, _ = QFileDialog.getOpenFileName(parent, "选择文件")
            if path:
                self._set_paths([path])
        elif self._mode == MODE_FILES:
            paths, _ = QFileDialog.getOpenFileNames(parent, "选择文件")
            if paths:
                self._set_paths(paths)

    # ── 拖拽 ─────────────────────────────────────────────────
    def dragEnterEvent(self, event):
        """文件/文件夹拖入时接受。"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """拖放：文件/文件夹自动填入路径。"""
        mime = event.mimeData()
        if not mime.hasUrls():
            event.ignore()
            return
        paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        paths = [p for p in paths if p]
        if not paths:
            event.ignore()
            return

        if self._mode == MODE_FOLDER:
            dirs = [p for p in paths if os.path.isdir(p)]
            if not dirs:
                event.ignore()
                return
            self._set_paths([dirs[0]])
        elif self._mode == MODE_FILE:
            self._set_paths([paths[0]])
        else:  # MODE_FILES
            self._set_paths(paths)
        event.acceptProposedAction()
