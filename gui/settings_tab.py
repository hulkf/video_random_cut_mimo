import os
import subprocess
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QGroupBox, QSpinBox, QLineEdit,
    QPushButton, QFileDialog, QMessageBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QToolButton
)
from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices, QColor
from gui.config import get_config, set_config
from gui.styles import apply_theme_and_font
from qt_material import list_themes


# Whisper 模型下载信息
WHISPER_MODEL_INFO = [
    ("tiny",           "39 MB",   "最快，精度最低",
     "https://openaipublic.azureedge.net/main/whisper/models/65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650d02622388/tiny.pt"),
    ("base",           "74 MB",   "快速，精度一般",
     "https://openaipublic.azureedge.net/main/whisper/models/ed3a0b6b1c0edf879ad9b11b1af5a0daa309c1d51f70541b7a5b29584f9d5749/base.pt"),
    ("small",          "244 MB",  "平衡",
     "https://openaipublic.azureedge.net/main/whisper/models/9ec52e50c0e7e021beaf0ae6b2e020c9bd0c775f010997b2f5907d4e0eb8257a/small.pt"),
    ("medium",         "1.5 GB",  "推荐，精度较高",
     "https://openaipublic.azureedge.net/main/whisper/models/345ae4da62f9b3d59415adc60127b97c711ccb65c7d425114c87408810d7a29a/medium.pt"),
    ("large-v2",       "3.1 GB",  "最慢，精度最高",
     "https://openaipublic.azureedge.net/main/whisper/models/81f7c96c852ee8fc83278c2507779fc39385d53ff7e1ba7031b2c670b6c90f01/large-v2.pt"),
    ("large-v3",       "3.1 GB",  "最新大模型，多语言改进",
     "https://openaipublic.azureedge.net/main/whisper/models/e5b1a55b89c1367d107ae236d3612dee885214c1f1d547d4cd6d6e6f8b6aa997/large-v3.pt"),
    ("large-v3-turbo", "1.5 GB",  "large-v3 加速版，速度接近 medium 精度接近 large",
     "https://openaipublic.azureedge.net/main/whisper/models/df15baf802ea2530c16c7f3e7eb0590caedf72acac2f3f9f66c97b3c1c1f3e3c/large-v3-turbo.pt"),
]


class SettingsTab(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.init_ui()
        self.load_config()

    def init_ui(self):
        layout = QVBoxLayout()

        # ===== 主题设置 =====
        theme_group = QGroupBox("主题设置")
        theme_layout = QHBoxLayout()
        theme_layout.addWidget(QLabel("界面主题:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list_themes())
        self.theme_combo.currentTextChanged.connect(self.on_theme_changed)
        theme_layout.addWidget(self.theme_combo)
        theme_layout.addStretch()
        theme_group.setLayout(theme_layout)

        # ===== 字体设置 =====
        font_group = QGroupBox("字体设置")
        font_layout = QHBoxLayout()
        font_layout.addWidget(QLabel("字体大小:"))
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 24)
        self.font_size_spin.setValue(10)
        self.font_size_spin.setSuffix(" pt")
        self.font_size_spin.valueChanged.connect(self.on_font_size_changed)
        font_layout.addWidget(self.font_size_spin)
        font_layout.addStretch()
        font_group.setLayout(font_layout)

        # ===== 模型路径配置 =====
        path_group = QGroupBox("模型路径配置")
        path_layout = QVBoxLayout()

        # FireRedASR
        fire_layout = QHBoxLayout()
        fire_layout.addWidget(QLabel("FireRedASR:"))
        self.fire_path_input = QLineEdit()
        self.fire_path_input.setPlaceholderText("FireRedASR ONNX 模型目录...")
        fire_btn = QPushButton("浏览")
        fire_btn.setFixedWidth(80)
        fire_btn.clicked.connect(self.browse_fire_path)
        fire_layout.addWidget(self.fire_path_input, 1)
        fire_layout.addWidget(fire_btn)
        path_layout.addLayout(fire_layout)

        # FunASR
        funasr_layout = QHBoxLayout()
        funasr_layout.addWidget(QLabel("FunASR:"))
        self.funasr_path_input = QLineEdit()
        self.funasr_path_input.setPlaceholderText("FunASR Paraformer ONNX 模型目录...")
        funasr_btn = QPushButton("浏览")
        funasr_btn.setFixedWidth(80)
        funasr_btn.clicked.connect(self.browse_funasr_path)
        funasr_layout.addWidget(self.funasr_path_input, 1)
        funasr_layout.addWidget(funasr_btn)
        path_layout.addLayout(funasr_layout)

        # Whisper
        whisper_dir_layout = QHBoxLayout()
        whisper_dir_layout.addWidget(QLabel("Whisper:"))
        self.model_dir_input = QLineEdit()
        self.model_dir_input.setPlaceholderText("存放 .pt 模型文件的文件夹...")
        self.model_dir_input.textChanged.connect(self.refresh_model_status)
        wdir_btn = QPushButton("浏览")
        wdir_btn.setFixedWidth(80)
        wdir_btn.clicked.connect(self.browse_model_dir)
        whisper_dir_layout.addWidget(self.model_dir_input, 1)
        whisper_dir_layout.addWidget(wdir_btn)
        path_layout.addLayout(whisper_dir_layout)

        path_group.setLayout(path_layout)

        save_row = QHBoxLayout()
        save_row.addStretch()
        save_btn = QPushButton("保存路径配置")
        save_btn.setFixedWidth(120)
        save_btn.clicked.connect(self.save_config)
        save_row.addWidget(save_btn)
        path_layout.addLayout(save_row)

        # ===== Whisper 模型下载 (折叠) =====
        whisper_group = QGroupBox("Whisper 模型下载")
        whisper_layout = QVBoxLayout()

        # 折叠按钮
        self.toggle_dl_btn = QToolButton()
        self.toggle_dl_btn.setCheckable(True)
        self.toggle_dl_btn.setChecked(False)
        self.toggle_dl_btn.setArrowType(Qt.RightArrow)
        self.toggle_dl_btn.setText("▶ 展开下载列表")
        self.toggle_dl_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_dl_btn.setStyleSheet(
            "QToolButton { border: none; font-weight: bold; padding: 4px; }"
            "QToolButton:hover { color: #1976D2; }"
        )
        self.toggle_dl_btn.toggled.connect(self._toggle_dl_section)
        whisper_layout.addWidget(self.toggle_dl_btn)

        # 折叠的内容
        self.dl_container = QWidget()
        dl_box = QVBoxLayout(self.dl_container)
        dl_box.setContentsMargins(0, 0, 0, 0)

        hint = QLabel(
            "说明：在此文件夹中放入对应模型的 .pt 文件（如 base.pt、medium.pt）。\n"
            "字幕功能会优先从此目录加载，找不到才回退到默认缓存目录。\n"
            "下载方法：点击下表中的「下载」按钮，浏览器会下载对应 .pt 文件，下载完成后放入上面的目录。"
        )
        hint.setStyleSheet("color: gray; padding: 4px;")
        hint.setWordWrap(True)
        dl_box.addWidget(hint)

        self.model_table = QTableWidget()
        self.model_table.setColumnCount(5)
        self.model_table.setHorizontalHeaderLabels(["模型", "大小", "说明", "本地状态", "操作"])
        self.model_table.setMinimumHeight(180)
        self.model_table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self.model_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        for name, size, desc, url in WHISPER_MODEL_INFO:
            self._add_model_row(name, size, desc, url)

        dl_box.addWidget(self.model_table)

        refresh_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新本地状态")
        self.refresh_btn.clicked.connect(self.refresh_model_status)
        refresh_layout.addStretch()
        refresh_layout.addWidget(self.refresh_btn)
        dl_box.addLayout(refresh_layout)

        self.dl_container.setVisible(False)
        whisper_layout.addWidget(self.dl_container)
        whisper_group.setLayout(whisper_layout)

        layout.addWidget(theme_group)
        layout.addWidget(font_group)
        layout.addWidget(path_group)
        layout.addWidget(whisper_group)
        layout.addStretch()

        self.setLayout(layout)

    def _toggle_dl_section(self, checked):
        self.dl_container.setVisible(checked)
        self.toggle_dl_btn.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.toggle_dl_btn.setText(
            "▼ 收起下载列表" if checked else "▶ 展开下载列表"
        )

    def _add_model_row(self, name, size, desc, url):
        row = self.model_table.rowCount()
        self.model_table.insertRow(row)
        self.model_table.setItem(row, 0, QTableWidgetItem(name))
        self.model_table.setItem(row, 1, QTableWidgetItem(size))
        self.model_table.setItem(row, 2, QTableWidgetItem(desc))
        self.model_table.setItem(row, 3, QTableWidgetItem("未检测"))
        dl_btn = QPushButton("下载")
        dl_btn.setFixedWidth(60)
        dl_btn.clicked.connect(lambda _, u=url: self._open_url(u))
        self.model_table.setCellWidget(row, 4, dl_btn)

    def _open_url(self, url):
        QDesktopServices.openUrl(QUrl(url))

    def browse_fire_path(self):
        folder = QFileDialog.getExistingDirectory(self, "选择 FireRedASR 模型目录")
        if folder:
            self.fire_path_input.setText(folder)
            self.save_config()

    def browse_funasr_path(self):
        folder = QFileDialog.getExistingDirectory(self, "选择 FunASR 模型目录")
        if folder:
            self.funasr_path_input.setText(folder)
            self.save_config()

    def browse_model_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "选择 Whisper 模型目录")
        if folder:
            self.model_dir_input.setText(folder)
            self.save_config()

    def refresh_model_status(self):
        model_dir = self.model_dir_input.text().strip()
        for row in range(self.model_table.rowCount()):
            name_item = self.model_table.item(row, 0)
            if not name_item:
                continue
            name = name_item.text()
            status_item = self.model_table.item(row, 3)

            found_path = None
            if model_dir:
                p = os.path.join(model_dir, f"{name}.pt")
                if os.path.isfile(p):
                    found_path = p
            if not found_path:
                default_cache = os.path.join(os.path.expanduser("~"), ".cache", "whisper")
                p = os.path.join(default_cache, f"{name}.pt")
                if os.path.isfile(p):
                    found_path = p

            if found_path:
                size_mb = os.path.getsize(found_path) / (1024 * 1024)
                status_item.setText(f"已下载 ({size_mb:.0f} MB)")
                status_item.setForeground(QColor("#2e7d32"))
            else:
                status_item.setText("未下载")
                status_item.setForeground(QColor("#c62828"))

    def load_config(self):
        saved_theme = get_config("settings", "theme", "dark_teal.xml")
        idx = self.theme_combo.findText(saved_theme)
        self.theme_combo.blockSignals(True)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self.theme_combo.blockSignals(False)

        saved_size = int(get_config("settings", "font_size", "10"))
        self.font_size_spin.blockSignals(True)
        self.font_size_spin.setValue(saved_size)
        self.font_size_spin.blockSignals(False)

        self.fire_path_input.setText(
            get_config("settings", "fireredasr_model_path",
                       r"D:\Models\FireRed")
        )
        self.funasr_path_input.setText(
            get_config("settings", "funasr_model_path",
                       r"D:\Models\FunASR\paraformer-large-zh-en-timestamp-onnx-offline")
        )

        self.model_dir_input.blockSignals(True)
        self.model_dir_input.setText(get_config("settings", "whisper_model_dir", ""))
        self.model_dir_input.blockSignals(False)
        self.refresh_model_status()

    def save_config(self):
        set_config("settings", "fireredasr_model_path", self.fire_path_input.text())
        set_config("settings", "funasr_model_path", self.funasr_path_input.text())
        set_config("settings", "whisper_model_dir", self.model_dir_input.text())

    def on_theme_changed(self, theme):
        font_size = self.font_size_spin.value()
        apply_theme_and_font(self.app, theme, font_size)
        set_config("settings", "theme", theme)

    def on_font_size_changed(self, size):
        theme = self.theme_combo.currentText()
        apply_theme_and_font(self.app, theme, size)
        set_config("settings", "font_size", str(size))
