# -*- coding: utf-8 -*-
"""进度面板控件（P2-4）：进度条 + 百分比 + 状态行，一行复用。

解决「进度区在 16 个 tab 重复实现且写法各异」问题：
统一 set_progress / set_status / reset 接口，交互体验全项目一致。
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QProgressBar,
)


class ProgressPanel(QGroupBox):
    """进度面板。

    用法:
        panel = ProgressPanel("处理进度")
        panel.set_progress(current, total, message)   # 进度更新
        panel.set_status("就绪")                       # 仅状态文字
        panel.reset()                                  # 归零 + 状态复位
        panel.bar / panel.percent_label / panel.status_label  # 直接访问（高级场景）
    """

    def __init__(self, title="处理进度", parent=None):
        super().__init__(title, parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("进度:"))
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        row.addWidget(self.bar)
        self.percent_label = QLabel("0%")
        self.percent_label.setMinimumWidth(48)
        row.addWidget(self.percent_label)
        layout.addLayout(row)

        self.status_label = QLabel("就绪")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    # ── 统一接口 ─────────────────────────────────────────────
    def set_progress(self, current: int, total: int, message: str = "") -> None:
        """进度更新：current/total → 百分比；message 非空时同时更新状态行。"""
        percent = int((current / total) * 100) if total else 0
        self.bar.setValue(max(0, min(100, percent)))
        self.percent_label.setText(f"{percent}%")
        if message:
            self.status_label.setText(message)

    def set_status(self, text: str) -> None:
        """仅更新状态文字。"""
        self.status_label.setText(text)

    def reset(self, status_text: str = "就绪") -> None:
        """归零进度 + 状态复位。"""
        self.bar.setValue(0)
        self.percent_label.setText("0%")
        self.status_label.setText(status_text)
