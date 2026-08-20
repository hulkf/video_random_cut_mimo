"""视频优化 Tab —— 调用美图 Wink 官方 CLI 做画质修复/超清增强。

处理在**美图云端**进行：整个文件上传上去，处理完再整个下载回来。
所以大文件的耗时大头是网络传输（实测上传约 1.2 MB/s），断网即失败。
需要本机装有 Wink 桌面版并且**已登录**，本 Tab 直接复用它的登录态。
"""

import os

from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QComboBox, QCheckBox, QSpinBox, QAbstractItemView
)
from PyQt5.QtCore import pyqtSignal

from core.wink_enhancer import (
    WinkEnhancer, LEVELS, IMAGE_ONLY_LEVELS,
    find_wink_exe, collect_media, is_video,
    build_output_path, human_size,
)
from gui.config import get_config, set_config
from gui.common.base_tab import BaseTab
from gui.common.base_worker import BaseWorker
from gui.common.path_row import PathRow, MODE_FOLDER


#: 每个档位给一句人话说明，省得用户对着"高糊图""演唱会"猜用途
LEVEL_HINTS = {
    1: "通用轻度增强，速度最快",
    2: "通用增强，去噪+锐化+提码率（推荐）",
    3: "有人物出镜的素材，优化肤质与五官",
    4: "最强档，画质提升明显但耗时更久",
    5: "仅支持图片：电商商品主图",
    6: "仅支持图片：文档、图表、截图",
    7: "游戏录屏画面",
    8: "动画、二次元片源",
    9: "仅支持图片：严重模糊的低清图",
    10: "舞台、演唱会等暗光复杂场景",
}


class VideoEnhanceWorker(BaseWorker):
    progress = pyqtSignal(int, int, str)
    file_done = pyqtSignal(dict)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, input_folder, output_folder, level, exe_path,
                 include_images, skip_existing, retry, timeout):
        super().__init__()
        self.input_folder = input_folder
        self.output_folder = output_folder
        self.level = level
        self.exe_path = exe_path
        self.include_images = include_images
        self.skip_existing = skip_existing
        self.retry = retry
        self.timeout = timeout

        self._stop = False
        self.engine = None

    def stop(self):
        """请求停止。当前文件会被中断，不会再开始下一个。"""
        self._stop = True
        if self.engine:
            self.engine.stop()

    def run(self):
        try:
            files = collect_media(self.input_folder, self.include_images)
            if not files:
                raise ValueError("输入文件夹里没有找到可处理的媒体文件")

            # 图片专用档位提前踢掉视频，免得白白上传一遍才失败
            skipped_by_level = 0
            if self.level in IMAGE_ONLY_LEVELS:
                before = len(files)
                files = [f for f in files if not is_video(f)]
                skipped_by_level = before - len(files)
                if not files:
                    raise ValueError(
                        f"档位「{LEVELS[self.level]}」仅支持图片，"
                        f"但文件夹里全是视频（{skipped_by_level} 个）"
                    )

            total = len(files)
            ok = fail = skipped = 0
            beans = 0

            self.engine = WinkEnhancer(
                exe_path=self.exe_path, level=self.level, timeout=self.timeout
            )
            err = self.engine.validate()
            if err:
                raise ValueError(err)

            for index, path in enumerate(files):
                if self._stop:
                    break

                name = os.path.relpath(path, self.input_folder)
                self.progress.emit(index, total, name)

                dest = build_output_path(
                    path, self.input_folder, self.output_folder, self.level
                )

                # 断点续跑：结果已经在就跳过，重跑不会重复花时间和美豆
                if self.skip_existing and os.path.isfile(dest):
                    skipped += 1
                    self.file_done.emit({
                        "name": name, "status": "跳过", "elapsed": 0,
                        "beans": 0, "output": dest, "error": "结果已存在",
                    })
                    self.progress.emit(index + 1, total, name)
                    continue

                size_text = human_size(os.path.getsize(path))
                result = None
                for attempt in range(self.retry + 1):
                    if self._stop:
                        break
                    result = self.engine.process(path, dest)
                    if result["success"]:
                        break

                if self._stop:
                    break

                beans += result["beans"]
                if result["success"]:
                    ok += 1
                    status = "成功"
                else:
                    fail += 1
                    status = "失败"

                self.file_done.emit({
                    "name": f"{name}  ({size_text})",
                    "status": status,
                    "elapsed": result["elapsed"],
                    "beans": result["beans"],
                    "output": result["output"] or "",
                    "error": result["error"],
                })
                self.progress.emit(index + 1, total, name)

            self.finished.emit({
                "total": total, "ok": ok, "fail": fail,
                "skipped": skipped, "beans": beans,
                "stopped": self._stop,
                "skipped_by_level": skipped_by_level,
            })
        except Exception as e:
            self.error.emit(str(e))


class VideoEnhanceTab(BaseTab):
    def __init__(self):
        super().__init__()
        self.init_ui()
        self.load_config()

    # ------------------------------------------------------------ UI

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        # ---------------- 输入输出 ----------------
        input_group = QGroupBox("输入设置")
        input_layout = QVBoxLayout()
        input_layout.setSpacing(8)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self.input_folder = PathRow("选择需要优化的视频文件夹...", mode=MODE_FOLDER,
                                    on_change=lambda p: self.save_config())
        folder_row.addWidget(self.input_folder, 1)
        input_layout.addLayout(folder_row)

        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        self.output_folder = PathRow("选择输出文件夹...", mode=MODE_FOLDER,
                                     on_change=lambda p: self.save_config())
        output_row.addWidget(self.output_folder, 1)
        input_layout.addLayout(output_row)
        input_group.setLayout(input_layout)

        # ---------------- 优化模式 ----------------
        mode_group = QGroupBox("优化模式")
        mode_layout = QVBoxLayout()
        mode_layout.setSpacing(8)

        level_row = QHBoxLayout()
        level_row.setSpacing(8)
        level_row.addWidget(QLabel("模式:"))
        self.level_combo = QComboBox()
        self.level_combo.setMinimumHeight(30)
        self.level_combo.setMinimumWidth(180)
        for key in sorted(LEVELS):
            suffix = "（仅图片）" if key in IMAGE_ONLY_LEVELS else ""
            self.level_combo.addItem(f"{key}. {LEVELS[key]}{suffix}", key)
        self.level_combo.currentIndexChanged.connect(self.on_level_changed)
        level_row.addWidget(self.level_combo)

        self.level_hint = QLabel("")
        self.level_hint.setStyleSheet("color: #26a69a;")
        level_row.addWidget(self.level_hint, 1)
        mode_layout.addLayout(level_row)

        opt_row = QHBoxLayout()
        opt_row.setSpacing(16)
        self.include_images_cb = QCheckBox("同时处理图片")
        self.include_images_cb.setToolTip("勾选后 jpg/png 等图片也会一起上传处理")
        opt_row.addWidget(self.include_images_cb)

        self.skip_existing_cb = QCheckBox("跳过已处理（断点续跑）")
        self.skip_existing_cb.setChecked(True)
        self.skip_existing_cb.setToolTip("输出目录已有同名结果时直接跳过，重跑不浪费时间和美豆")
        opt_row.addWidget(self.skip_existing_cb)

        opt_row.addWidget(QLabel("失败重试:"))
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(0, 5)
        self.retry_spin.setValue(1)
        self.retry_spin.setFixedWidth(60)
        opt_row.addWidget(self.retry_spin)

        opt_row.addWidget(QLabel("单文件超时(秒):"))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(60, 7200)
        self.timeout_spin.setSingleStep(60)
        self.timeout_spin.setValue(1800)
        self.timeout_spin.setFixedWidth(80)
        opt_row.addWidget(self.timeout_spin)

        opt_row.addStretch()
        mode_layout.addLayout(opt_row)
        mode_group.setLayout(mode_layout)

        # ---------------- Wink 客户端 ----------------
        wink_group = QGroupBox("Wink 客户端")
        wink_layout = QVBoxLayout()
        wink_layout.setSpacing(8)

        wink_row = QHBoxLayout()
        wink_row.setSpacing(8)
        self.wink_path = QLineEdit()
        self.wink_path.setMinimumHeight(30)
        self.wink_path.setPlaceholderText("Wink.exe 路径（一般会自动探测到）")
        wink_btn = QPushButton("浏览")
        wink_btn.setFixedWidth(80)
        wink_btn.clicked.connect(self.browse_wink)
        detect_btn = QPushButton("自动探测")
        detect_btn.setFixedWidth(90)
        detect_btn.clicked.connect(self.detect_wink)
        wink_row.addWidget(self.wink_path, 1)
        wink_row.addWidget(wink_btn)
        wink_row.addWidget(detect_btn)
        wink_layout.addLayout(wink_row)

        self.wink_status = QLabel("")
        wink_layout.addWidget(self.wink_status)
        wink_group.setLayout(wink_layout)

        # ---------------- 操作按钮 ----------------
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.start_btn = QPushButton("开始优化")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self.start_enhance)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setMinimumHeight(36)
        self.stop_btn.setFixedWidth(120)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_enhance)
        btn_row.addWidget(self.start_btn, 1)
        btn_row.addWidget(self.stop_btn)

        # ---------------- 进度 ----------------
        progress_group = QGroupBox("处理进度")
        progress_layout = QVBoxLayout()
        progress_row = QHBoxLayout()
        progress_row.addWidget(QLabel("进度:"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        progress_row.addWidget(self.progress_bar)
        self.progress_label = QLabel("0%")
        progress_row.addWidget(self.progress_label)
        progress_layout.addLayout(progress_row)
        self.status_label = QLabel("就绪")
        progress_layout.addWidget(self.status_label)
        progress_group.setLayout(progress_layout)

        # ---------------- 结果表 ----------------
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(5)
        self.result_table.setHorizontalHeaderLabels(
            ["文件", "状态", "耗时", "美豆", "输出/失败原因"]
        )
        self.result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.result_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.result_table.setColumnWidth(1, 70)
        self.result_table.setColumnWidth(2, 80)
        self.result_table.setColumnWidth(3, 60)
        self.result_table.setMinimumHeight(200)
        self.result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        hint = QLabel(
            "处理在美图云端进行：文件会整个上传到美图服务器，处理完再下载回来，"
            "因此需要联网且大文件较慢（上传约 1.2MB/s，一个 60MB 的视频光上传就要 1 分钟左右）。"
            "使用前请确保 Wink 桌面版已登录；计费按账号 VIP 状态走，"
            "非会员每次会扣美豆，批量跑前建议先确认余额。"
            "会递归处理子文件夹，并在输出目录保留原有子文件夹结构。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; padding: 5px;")

        layout.addWidget(input_group)
        layout.addWidget(mode_group)
        layout.addWidget(wink_group)
        layout.addLayout(btn_row)
        layout.addWidget(progress_group)
        layout.addWidget(self.result_table, 1)
        layout.addWidget(hint)
        self.setLayout(layout)

    # ------------------------------------------------------------ 配置

    def load_config(self):
        self.input_folder.setText(get_config("video_enhance", "input_folder", ""))
        self.output_folder.setText(get_config("video_enhance", "output_folder", ""))

        level = int(get_config("video_enhance", "level", "2"))
        idx = self.level_combo.findData(level)
        self.level_combo.setCurrentIndex(idx if idx >= 0 else 1)

        self.include_images_cb.setChecked(
            get_config("video_enhance", "include_images", "0") == "1"
        )
        self.skip_existing_cb.setChecked(
            get_config("video_enhance", "skip_existing", "1") == "1"
        )
        self.retry_spin.setValue(int(get_config("video_enhance", "retry", "1")))
        self.timeout_spin.setValue(int(get_config("video_enhance", "timeout", "1800")))

        saved_exe = get_config("video_enhance", "wink_exe", "")
        if saved_exe and os.path.isfile(saved_exe):
            self.wink_path.setText(saved_exe)
        else:
            self.wink_path.setText(find_wink_exe() or "")

        self.on_level_changed()
        self.refresh_wink_status()

    def save_config(self):
        set_config("video_enhance", "input_folder", self.input_folder.text())
        set_config("video_enhance", "output_folder", self.output_folder.text())
        set_config("video_enhance", "level", str(self.current_level()))
        set_config("video_enhance", "include_images",
                   "1" if self.include_images_cb.isChecked() else "0")
        set_config("video_enhance", "skip_existing",
                   "1" if self.skip_existing_cb.isChecked() else "0")
        set_config("video_enhance", "retry", str(self.retry_spin.value()))
        set_config("video_enhance", "timeout", str(self.timeout_spin.value()))
        set_config("video_enhance", "wink_exe", self.wink_path.text())

    # ------------------------------------------------------------ 交互

    def current_level(self):
        data = self.level_combo.currentData()
        return int(data) if data is not None else 2

    def on_level_changed(self):
        level = self.current_level()
        self.level_hint.setText(LEVEL_HINTS.get(level, ""))
        # 图片专用档位下，"同时处理图片"必须开着，否则一个文件都跑不了
        if level in IMAGE_ONLY_LEVELS:
            self.include_images_cb.setChecked(True)
            self.include_images_cb.setEnabled(False)
        else:
            self.include_images_cb.setEnabled(True)

    def refresh_wink_status(self):
        path = self.wink_path.text().strip()
        if not path:
            self.wink_status.setText("未找到 Wink 客户端，请手动指定 Wink.exe")
            self.wink_status.setStyleSheet("color: #e57373;")
        elif os.path.isfile(path):
            self.wink_status.setText(f"已就绪：{path}")
            self.wink_status.setStyleSheet("color: #81c784;")
        else:
            self.wink_status.setText(f"路径不存在：{path}")
            self.wink_status.setStyleSheet("color: #e57373;")

    def detect_wink(self):
        found = find_wink_exe()
        if found:
            self.wink_path.setText(found)
            self.save_config()
        else:
            QMessageBox.warning(
                self, "未找到",
                "没有探测到 Wink 客户端。\n\n"
                "请确认已安装美图 Wink 桌面版，或手动指定安装目录下\n"
                "版本子文件夹里的 Wink.exe（例如 3.7.5\\Wink.exe）。"
            )
        self.refresh_wink_status()

    def browse_input(self):
        self.input_folder._browse()

    def browse_output(self):
        self.output_folder._browse()

    def browse_wink(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Wink.exe", "", "Wink (Wink.exe);;可执行文件 (*.exe)"
        )
        if path:
            self.wink_path.setText(path)
            self.save_config()
            self.refresh_wink_status()

    # ------------------------------------------------------------ 执行

    def start_enhance(self):
        input_folder = self.input_folder.text().strip()
        output_folder = self.output_folder.text().strip()
        exe_path = self.wink_path.text().strip()

        if not input_folder or not output_folder:
            QMessageBox.warning(self, "警告", "请选择输入和输出文件夹")
            return
        if not os.path.isdir(input_folder):
            QMessageBox.warning(self, "警告", f"输入文件夹不存在：{input_folder}")
            return
        if not exe_path or not os.path.isfile(exe_path):
            QMessageBox.warning(
                self, "警告",
                "没有可用的 Wink 客户端。\n请点「自动探测」或手动指定 Wink.exe 路径。"
            )
            return

        level = self.current_level()
        include_images = self.include_images_cb.isChecked() or level in IMAGE_ONLY_LEVELS
        files = collect_media(input_folder, include_images)
        if not files:
            QMessageBox.warning(self, "警告", "输入文件夹里没有找到可处理的媒体文件")
            return

        os.makedirs(output_folder, exist_ok=True)

        self.save_config()
        self.result_table.setRowCount(0)
        self.progress_bar.setValue(0)
        self.progress_label.setText("0%")
        self.status_label.setText(f"准备处理 {len(files)} 个文件...")

        worker = VideoEnhanceWorker(
            input_folder, output_folder, level, exe_path,
            include_images, self.skip_existing_cb.isChecked(),
            self.retry_spin.value(), self.timeout_spin.value(),
        )
        worker.file_done.connect(self.on_file_done)
        if not self.start_worker(worker):
            return

    def set_busy(self, busy):
        self.start_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)

    def stop_enhance(self):
        if self.worker and self.worker.isRunning():
            self.stop_btn.setEnabled(False)
            self.status_label.setText("正在停止，等待当前文件中断...")
            self.worker.stop()

    def on_worker_progress(self, current, total, name):
        percent = int((current / total) * 100) if total else 0
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"{percent}%")
        self.status_label.setText(f"处理 {min(current + 1, total)}/{total}: {name}")

    def on_file_done(self, result):
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        self.result_table.setItem(row, 0, QTableWidgetItem(result["name"]))
        self.result_table.setItem(row, 1, QTableWidgetItem(result["status"]))
        self.result_table.setItem(row, 2, QTableWidgetItem(f"{result['elapsed']:.1f}s"))
        self.result_table.setItem(row, 3, QTableWidgetItem(str(result["beans"])))
        detail = result["output"] if result["status"] == "成功" else result["error"]
        self.result_table.setItem(row, 4, QTableWidgetItem(detail))
        self.result_table.scrollToBottom()

    def on_worker_finished(self, summary):
        super().on_worker_finished(summary)

        parts = [f"成功 {summary['ok']}"]
        if summary["fail"]:
            parts.append(f"失败 {summary['fail']}")
        if summary["skipped"]:
            parts.append(f"跳过 {summary['skipped']}")
        parts.append(f"消耗美豆 {summary['beans']}")
        text = "，".join(parts)

        if summary["stopped"]:
            self.status_label.setText(f"已停止（{text}）")
            QMessageBox.information(self, "已停止", f"任务已中止。\n\n{text}")
            return

        self.progress_bar.setValue(100)
        self.progress_label.setText("100%")
        self.status_label.setText(f"处理完成：{text}")

        extra = ""
        if summary.get("skipped_by_level"):
            extra = (
                f"\n\n注意：当前档位仅支持图片，已自动跳过 "
                f"{summary['skipped_by_level']} 个视频。"
            )
        QMessageBox.information(self, "完成", f"视频优化完成。\n\n{text}{extra}")

    def on_worker_error(self, msg):
        super().on_worker_error(msg)
        self.status_label.setText("处理失败")
