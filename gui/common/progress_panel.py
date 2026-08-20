# -*- coding: utf-8 -*-
"""进度面板控件（P2-4）：进度条 + 百分比 + 状态行，一行复用。

解决「进度区在 16 个 tab 重复实现且写法各异」问题：
统一 set_progress / set_status / reset 接口，交互体验全项目一致。

P2-5 增强：支持「总进度 + 子进度」双进度模式（dual=True）。
  - 单进度模式：set_progress / set_status / reset（不破坏现有接口）；
  - 双进度模式：额外提供 set_sub_progress / set_sub_status，
    第二进度条 sub_bar / sub_percent_label 可直接访问。
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QProgressBar,
)


class ProgressPanel(QGroupBox):
    """进度面板。

    用法:
        panel = ProgressPanel("处理进度")               # 单进度
        panel = ProgressPanel("处理进度", dual=True)    # 总进度 + 子进度
        panel.set_progress(current, total, message)     # 总进度更新
        panel.set_sub_progress(current, total, message) # 子进度更新（dual 模式）
        panel.set_status("就绪")                         # 仅状态文字
        panel.reset()                                    # 归零 + 状态复位
        panel.bar / panel.percent_label / panel.status_label      # 直接访问（高级场景）
        panel.sub_bar / panel.sub_percent_label / panel.sub_status_label  # dual 模式
    """

    def __init__(self, title="处理进度", parent=None, dual=False):
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

        # 双进度模式：第二进度条（子进度）
        self.sub_row = None
        self.sub_bar = None
        self.sub_percent_label = None
        self.sub_status_label = None
        if dual:
            self.sub_row = QHBoxLayout()
            self.sub_row.setSpacing(8)
            self.sub_row.addWidget(QLabel("子进度:"))
            self.sub_bar = QProgressBar()
            self.sub_bar.setRange(0, 100)
            self.sub_row.addWidget(self.sub_bar)
            self.sub_percent_label = QLabel("0%")
            self.sub_percent_label.setMinimumWidth(48)
            self.sub_row.addWidget(self.sub_percent_label)
            layout.addLayout(self.sub_row)
            self.sub_status_label = QLabel("")
            self.sub_status_label.setWordWrap(True)
            layout.addWidget(self.sub_status_label)

        self.status_label = QLabel("就绪")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    # ── 统一接口 ─────────────────────────────────────────────
    def set_progress(self, current: int, total: int, message: str = "") -> None:
        """总进度更新：current/total → 百分比；message 非空时同时更新状态行。"""
        percent = int((current / total) * 100) if total else 0
        self.bar.setValue(max(0, min(100, percent)))
        self.percent_label.setText(f"{percent}%")
        if message:
            self.status_label.setText(message)

    def set_sub_progress(self, current: int, total: int, message: str = "") -> None:
        """子进度更新（dual 模式）。非 dual 模式下为空操作。"""
        if self.sub_bar is None:
            return
        percent = int((current / total) * 100) if total else 0
        self.sub_bar.setValue(max(0, min(100, percent)))
        self.sub_percent_label.setText(f"{percent}%")
        if message and self.sub_status_label is not None:
            self.sub_status_label.setText(message)

    def set_status(self, text: str) -> None:
        """仅更新状态文字。"""
        self.status_label.setText(text)

    def set_sub_status(self, text: str) -> None:
        """仅更新子进度状态文字（dual 模式）。"""
        if self.sub_status_label is not None:
            self.sub_status_label.setText(text)

    def reset(self, status_text: str = "就绪") -> None:
        """归零进度 + 状态复位（含子进度）。"""
        self.bar.setValue(0)
        self.percent_label.setText("0%")
        self.status_label.setText(status_text)
        if self.sub_bar is not None:
            self.sub_bar.setValue(0)
            self.sub_percent_label.setText("0%")
        if self.sub_status_label is not None:
            self.sub_status_label.setText("")
