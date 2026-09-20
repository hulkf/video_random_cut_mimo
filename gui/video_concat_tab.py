from datetime import datetime
import uuid

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel,
    QMessageBox, QGroupBox, QCheckBox, QDoubleSpinBox, QComboBox,
    QScrollArea
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from core.video_concatenator import VideoConcatenatorEngine
from core.video_concat_templates import (
    get_video_concat_template,
    resolve_video_concat_template,
)
from gui.config import get_config, set_config
from utils.path_utils import normalize_path as normalize_input_path
from gui.common.base_tab import BaseTab
from gui.common.base_worker import BaseWorker
from gui.common.path_row import PathRow, MODE_FOLDER
from gui.common.progress_panel import ProgressPanel


class Template001TaskCenterClient:
    """模板001界面到正式任务中心公共接口的薄适配层。"""

    def __init__(self, request_runner=None, task_id_factory=None):
        self.request_runner = request_runner
        self.task_id_factory = task_id_factory or self._new_task_id

    @staticmethod
    def _new_task_id():
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return "template001-{}-{}".format(timestamp, uuid.uuid4().hex[:8])

    def _run(self, request):
        if self.request_runner is None:
            from video_task_center_cli import run_request

            self.request_runner = run_request
        return self.request_runner(request)

    def submit(self, config):
        inputs = {
            "folder_a": config["folder_a"],
            "folder_b": config["folder_b"],
            "output_folder": config["output_folder"],
        }
        options = {
            key: value for key, value in config.items()
            if key not in inputs and not key.startswith("_")
        }
        task_id = self.task_id_factory()
        planned = self._run({
            "operation": "task_center_plan",
            "inputs": {
                "task_id": task_id,
                "title": "模板001 千川视频合成",
                "steps": [{
                    "id": "template001-concat",
                    "name": "模板001 千川视频合成",
                    "request": {
                        "operation": "video_concat",
                        "template_id": "001",
                        "inputs": inputs,
                        "options": options,
                    },
                }],
                "parameter_lines": [
                    "文件夹A：{}".format(inputs["folder_a"]),
                    "文件夹B：{}".format(inputs["folder_b"]),
                    "输出目录：{}".format(inputs["output_folder"]),
                    "非9:16素材：先转为9:16",
                    "成品任一边超过2000像素：转为1080×1920",
                ],
                "risk_note": "纯本地视频处理；不调用外部付费服务；不覆盖输入素材。",
            },
        })["task"]
        return self._run({
            "operation": "task_center_confirm",
            "inputs": {
                "task_id": task_id,
                "plan_version": int(planned["plan_version"]),
                "confirmed_by": "desktop-gui",
            },
            "authorization": {
                "confirmed": True,
                "scope": "task_center_confirm",
            },
        })["task"]

    def get(self, task_id):
        return self._run({
            "operation": "task_center_status",
            "inputs": {"task_id": task_id},
        })["task"]


class VideoConcatWorker(BaseWorker):
    # progress/finished/error 继承 BaseWorker（progress(int,int,str)）
    sub_progress = pyqtSignal(int)  # 单个任务的子进度（0-100）

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        try:
            config = dict(self.config)
            template_id = config.pop("_template_id", None)
            if template_id == "001":
                from core.video_concat_pipeline import run_template_001_concat

                results = run_template_001_concat(config, self._on_progress)
            else:
                engine = VideoConcatenatorEngine(config)
                results = engine.run(self._on_progress)
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))

    def _on_progress(self, cur, total, msg, sub):
        """引擎进度回调（worker 线程内执行）：轮询停止标志。"""
        if self.stopped():
            raise InterruptedError("用户停止")
        self.progress.emit(cur, total, msg)
        self.sub_progress.emit(sub)


class VideoConcatPage(BaseTab):
    def __init__(self, config_section, template_id=None):
        super().__init__()
        self.config_section = config_section
        self.template = (
            get_video_concat_template(template_id) if template_id is not None else None
        )
        self.active_task_id = ""
        self._task_center_client = None
        self.init_ui()
        self.task_poll_timer = QTimer(self)
        self.task_poll_timer.setInterval(1000)
        self.task_poll_timer.timeout.connect(self._poll_task_center)
        self.load_config()

    def init_ui(self):
        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        folder_a_group = QGroupBox("文件夹A（第一批视频）")
        folder_a_layout = QVBoxLayout()
        folder_a_layout.setSpacing(8)
        self.folder_a_input = PathRow("选择文件夹A...", mode=MODE_FOLDER,
                                      on_change=lambda p: self.save_config(), allow_file=True)
        folder_a_layout.addWidget(self.folder_a_input)
        folder_a_group.setLayout(folder_a_layout)

        folder_b_group = QGroupBox("文件夹B（第二批视频）")
        folder_b_layout = QVBoxLayout()
        folder_b_layout.setSpacing(8)
        self.folder_b_input = PathRow("选择文件夹B...", mode=MODE_FOLDER,
                                      on_change=lambda p: self.save_config(), allow_file=True)
        folder_b_layout.addWidget(self.folder_b_input)
        folder_b_group.setLayout(folder_b_layout)

        cover_group = QGroupBox("封面图设置")
        cover_layout = QVBoxLayout()

        self.cover_check = QCheckBox("启用封面图")
        self.cover_check.setMinimumHeight(26)
        self.cover_check.setChecked(False)
        self.cover_check.stateChanged.connect(self.on_cover_changed)
        cover_layout.addWidget(self.cover_check)

        cover_source_row = QHBoxLayout()
        cover_source_row.addWidget(QLabel("封面来源:"))
        self.cover_source_combo = QComboBox()
        self.cover_source_combo.addItem("封面图文件夹随机选图", "folder")
        self.cover_source_combo.addItem("从文件夹B当前视频抽帧", "video_b_frame")
        self.cover_source_combo.setEnabled(False)
        self.cover_source_combo.setMinimumHeight(28)
        self.cover_source_combo.currentIndexChanged.connect(self.on_cover_changed)
        cover_source_row.addWidget(self.cover_source_combo)
        cover_source_row.addStretch()
        cover_layout.addLayout(cover_source_row)

        cover_folder_row = QHBoxLayout()
        cover_folder_row.setSpacing(8)
        cover_folder_row.addWidget(QLabel("封面图文件夹:"))
        self.cover_folder_input = PathRow("选择封面图文件夹...", mode=MODE_FOLDER,
                                          on_change=lambda p: self.save_config(), allow_file=True)
        self.cover_folder_input.setEnabled(False)
        self.cover_folder_btn = self.cover_folder_input.browse_btn
        self.cover_folder_btn.setEnabled(False)
        cover_folder_row.addWidget(self.cover_folder_input, 1)
        cover_layout.addLayout(cover_folder_row)

        cover_mode_row = QHBoxLayout()
        cover_mode_row.addWidget(QLabel("封面位置:"))
        self.cover_mode_combo = QComboBox()
        self.cover_mode_combo.addItems(["开头", "结尾", "首尾都加"])
        self.cover_mode_combo.setEnabled(False)
        self.cover_mode_combo.setMinimumHeight(28)
        cover_mode_row.addWidget(self.cover_mode_combo)
        cover_mode_row.addStretch()
        cover_layout.addLayout(cover_mode_row)

        cover_dur_row = QHBoxLayout()
        cover_dur_row.addWidget(QLabel("封面时长(秒):"))
        self.cover_duration_min = QDoubleSpinBox()
        self.cover_duration_min.setRange(0.1, 10.0)
        self.cover_duration_min.setValue(0.5)
        self.cover_duration_min.setSingleStep(0.1)
        self.cover_duration_min.setDecimals(1)
        self.cover_duration_min.setMinimumHeight(28)
        self.cover_duration_min.setEnabled(False)
        cover_dur_row.addWidget(self.cover_duration_min)
        cover_dur_row.addWidget(QLabel("~"))
        self.cover_duration_max = QDoubleSpinBox()
        self.cover_duration_max.setRange(0.1, 10.0)
        self.cover_duration_max.setValue(1.0)
        self.cover_duration_max.setSingleStep(0.1)
        self.cover_duration_max.setDecimals(1)
        self.cover_duration_max.setMinimumHeight(28)
        self.cover_duration_max.setEnabled(False)
        cover_dur_row.addWidget(self.cover_duration_max)
        cover_layout.addLayout(cover_dur_row)

        cover_group.setLayout(cover_layout)

        output_group = QGroupBox("输出设置")
        output_layout = QVBoxLayout()
        output_layout.setSpacing(8)
        self.output_folder_input = PathRow("选择输出文件夹...", mode=MODE_FOLDER,
                                           on_change=lambda p: self.save_config())
        output_layout.addWidget(self.output_folder_input)
        output_group.setLayout(output_layout)

        self.start_btn = QPushButton("开始拼接")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self.start_concat)

        # P2-5：双进度统一用公共 ProgressPanel（总进度 + 子进度）
        self.progress_panel = ProgressPanel("处理进度", dual=True)
        self.global_progress_bar = self.progress_panel.bar
        self.global_progress_label = self.progress_panel.percent_label
        self.task_progress_bar = self.progress_panel.sub_bar
        self.task_progress_label = self.progress_panel.sub_percent_label

        self.status_label = QLabel("就绪")

        if self.template and self.template["id"] == "001":
            description = (
                "模板001（千川视频）处理逻辑：\n"
                "1. 文件夹A和B中不是9:16的素材，先按“视频尺寸”功能转为9:16\n"
                "2. A和B按文件名排序后依次配对；数量较少的一侧循环使用\n"
                "3. 从当前B视频抽帧，并在成品开头加入0.2~0.5秒封面\n"
                "4. 拼接成品任一边超过2000像素时，统一降为1080×1920"
            )
        else:
            description = (
                "拼接逻辑说明：\n"
                "1. 从文件夹A和文件夹B各取一个视频进行拼接\n"
                "2. A和B视频按文件名排序后依次配对（A1+B1, A2+B2, ...）\n"
                "3. 如果两个文件夹视频数量不同，较少的文件夹会循环使用\n"
                "4. 启用封面图时，可选择在拼接视频的开头/结尾/首尾添加图片\n"
                "5. 封面图可来自图片文件夹，也可从当前配对的文件夹B视频抽帧\n"
                "6. 封面图无音频，时长可设置区间随机"
            )
        desc_label = QLabel(description)
        desc_label.setStyleSheet("color: gray; padding: 5px;")
        desc_label.setWordWrap(True)

        layout.addWidget(folder_a_group)
        layout.addWidget(folder_b_group)
        layout.addWidget(cover_group)
        layout.addWidget(output_group)
        layout.addWidget(self.start_btn)
        layout.addWidget(self.progress_panel)
        layout.addWidget(self.status_label)
        layout.addWidget(desc_label)
        layout.addStretch()

        container.setLayout(layout)
        scroll.setWidget(container)
        outer_layout.addWidget(scroll)
        self.setLayout(outer_layout)

    def load_config(self):
        self.folder_a_input.setText(get_config(self.config_section, "folder_a", ""))
        self.folder_b_input.setText(get_config(self.config_section, "folder_b", ""))
        self.output_folder_input.setText(get_config(self.config_section, "output_folder", ""))
        defaults = self.template["default_options"] if self.template else {
            "cover_enabled": False,
            "cover_source": "folder",
            "cover_mode": "front",
            "cover_duration_min": 0.5,
            "cover_duration_max": 1.0,
        }
        self.cover_check.setChecked(
            get_config(self.config_section, "cover_enabled", defaults["cover_enabled"])
        )
        cover_source = get_config(
            self.config_section, "cover_source", defaults["cover_source"]
        )
        cover_source_index = self.cover_source_combo.findData(cover_source)
        self.cover_source_combo.setCurrentIndex(cover_source_index if cover_source_index >= 0 else 0)
        self.cover_folder_input.setText(get_config(self.config_section, "cover_folder", ""))
        default_mode = {"front": 0, "back": 1, "both": 2}[defaults["cover_mode"]]
        self.cover_mode_combo.setCurrentIndex(
            int(get_config(self.config_section, "cover_mode", str(default_mode)))
        )
        self.cover_duration_min.setValue(float(get_config(
            self.config_section, "cover_duration_min", str(defaults["cover_duration_min"])
        )))
        self.cover_duration_max.setValue(float(get_config(
            self.config_section, "cover_duration_max", str(defaults["cover_duration_max"])
        )))
        self.on_cover_changed(Qt.Checked if self.cover_check.isChecked() else Qt.Unchecked)
        self._restore_active_template_task()

    def save_config(self):
        set_config(self.config_section, "folder_a", normalize_input_path(self.folder_a_input.text()))
        set_config(self.config_section, "folder_b", normalize_input_path(self.folder_b_input.text()))
        set_config(self.config_section, "output_folder", normalize_input_path(self.output_folder_input.text()))
        set_config(self.config_section, "cover_enabled", str(self.cover_check.isChecked()).lower())
        set_config(self.config_section, "cover_source", self.cover_source_combo.currentData())
        set_config(self.config_section, "cover_folder", normalize_input_path(self.cover_folder_input.text()))
        set_config(self.config_section, "cover_mode", str(self.cover_mode_combo.currentIndex()))
        set_config(self.config_section, "cover_duration_min", str(self.cover_duration_min.value()))
        set_config(self.config_section, "cover_duration_max", str(self.cover_duration_max.value()))

    def on_cover_changed(self, state):
        enabled = self.cover_check.isChecked()
        folder_enabled = enabled and self.cover_source_combo.currentData() == "folder"
        self.cover_source_combo.setEnabled(enabled)
        self.cover_folder_input.setEnabled(folder_enabled)
        self.cover_folder_btn.setEnabled(folder_enabled)
        self.cover_mode_combo.setEnabled(enabled)
        self.cover_duration_min.setEnabled(enabled)
        self.cover_duration_max.setEnabled(enabled)

    def browse_folder_a(self):
        self.folder_a_input._browse()

    def browse_folder_b(self):
        self.folder_b_input._browse()

    def browse_cover_folder(self):
        self.cover_folder_input._browse()

    def browse_output_folder(self):
        self.output_folder_input._browse()

    def start_concat(self):
        folder_a = normalize_input_path(self.folder_a_input.text())
        folder_b = normalize_input_path(self.folder_b_input.text())
        output_folder = normalize_input_path(self.output_folder_input.text())

        if not folder_a or not folder_b or not output_folder:
            QMessageBox.warning(self, "警告", "请填写所有必填项")
            return

        self.save_config()

        inputs = {
            "folder_a": folder_a,
            "folder_b": folder_b,
            "output_folder": output_folder,
        }
        options = {
            "cover_enabled": self.cover_check.isChecked(),
            "cover_source": self.cover_source_combo.currentData(),
            "cover_folder": normalize_input_path(self.cover_folder_input.text()),
            "cover_mode": self.cover_mode_combo.currentIndex(),
            "cover_duration_min": self.cover_duration_min.value(),
            "cover_duration_max": self.cover_duration_max.value(),
        }
        if self.template:
            resolved = resolve_video_concat_template(
                self.template["id"], inputs, options
            )
            config = {**resolved["inputs"], **resolved["options"]}
            config["_template_id"] = resolved["template_id"]
        else:
            config = {**inputs, **options}

        if self.template and self.template["id"] == "001":
            self._start_template_001_task(config)
            return

        worker = VideoConcatWorker(config)
        worker.sub_progress.connect(self.on_sub_progress)
        if not self.start_worker(worker):
            return

    def set_busy(self, busy):
        self.start_btn.setEnabled(not busy)

    def is_busy(self):
        return bool(self.active_task_id) or super().is_busy()

    def _task_client(self):
        if self._task_center_client is None:
            self._task_center_client = Template001TaskCenterClient()
        return self._task_center_client

    def _start_template_001_task(self, config):
        if self.active_task_id:
            QMessageBox.warning(self, "警告", "模板001任务正在任务中心执行中")
            return
        summary = (
            "确认提交到任务中心执行？\n\n"
            "文件夹A：{}\n"
            "文件夹B：{}\n"
            "输出目录：{}\n\n"
            "处理：非9:16先转9:16；成品任一边超过2000像素时转为1080×1920。\n"
            "费用：纯本地处理，0外部费用。"
        ).format(config["folder_a"], config["folder_b"], config["output_folder"])
        if QMessageBox.question(
            self,
            "确认模板001任务",
            summary,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            task = self._task_client().submit(config)
        except Exception as exc:
            QMessageBox.critical(self, "任务中心提交失败", str(exc))
            return
        self.active_task_id = str(task["task_id"])
        set_config(self.config_section, "active_task_id", self.active_task_id)
        self.set_busy(True)
        self.global_progress_bar.setValue(5)
        self.global_progress_label.setText("5%")
        self.status_label.setText("已提交任务中心：{}".format(self.active_task_id))
        self.task_poll_timer.start()

    def _poll_task_center(self):
        if not self.active_task_id:
            self.task_poll_timer.stop()
            return
        try:
            task = self._task_client().get(self.active_task_id)
        except Exception as exc:
            self.status_label.setText("任务中心状态读取失败：{}".format(exc))
            return

        status = str(task.get("status") or "")
        labels = {
            "awaiting_confirmation": "等待确认",
            "queued": "排队中",
            "running": "执行中",
            "waiting_cloud": "等待云端",
            "pausing": "正在暂停",
            "paused": "已暂停",
            "cancelling": "正在取消",
        }
        if status not in {"completed", "partial_failed", "failed", "cancelled", "stopped_unknown"}:
            progress = 50 if status in {"running", "waiting_cloud", "pausing", "cancelling"} else 10
            self.global_progress_bar.setValue(progress)
            self.global_progress_label.setText("{}%".format(progress))
            self.status_label.setText(
                "任务中心{}：{}".format(labels.get(status, status or "处理中"), self.active_task_id)
            )
            return

        task_id = self.active_task_id
        self.task_poll_timer.stop()
        self.active_task_id = ""
        set_config(self.config_section, "active_task_id", "")
        self.set_busy(False)
        if status == "completed":
            self.global_progress_bar.setValue(100)
            self.global_progress_label.setText("100%")
            self.task_progress_bar.setValue(100)
            self.task_progress_label.setText("100%")
            self.status_label.setText("任务中心执行完成：{}".format(task_id))
            output_text = "\n".join(task.get("output_directories") or []) or "请在任务中心查看结果"
            QMessageBox.information(self, "完成", "模板001任务已完成\n\n{}".format(output_text))
        else:
            error = str(task.get("error") or "任务未完成")
            self.status_label.setText("任务中心执行失败：{}".format(task_id))
            QMessageBox.critical(self, "模板001任务失败", "{}\n\n{}".format(task_id, error))

    def _restore_active_template_task(self):
        if not self.template or self.template["id"] != "001" or self.active_task_id:
            return
        stored_task_id = str(
            get_config(self.config_section, "active_task_id", "") or ""
        ).strip()
        if not stored_task_id:
            return
        try:
            task = self._task_client().get(stored_task_id)
        except ValueError as exc:
            if "任务不存在" in str(exc):
                set_config(self.config_section, "active_task_id", "")
                return
            self.active_task_id = stored_task_id
            self.set_busy(True)
            self.status_label.setText("任务中心状态暂时无法读取，将自动重试")
            self.task_poll_timer.start()
            return
        except Exception:
            self.active_task_id = stored_task_id
            self.set_busy(True)
            self.status_label.setText("任务中心状态暂时无法读取，将自动重试")
            self.task_poll_timer.start()
            return
        self.active_task_id = stored_task_id
        self.set_busy(True)
        if task.get("status") in {"completed", "partial_failed", "failed", "cancelled", "stopped_unknown"}:
            self.status_label.setText("正在读取已结束任务：{}".format(stored_task_id))
            QTimer.singleShot(0, self._poll_task_center)
        else:
            self.status_label.setText("已恢复任务中心跟踪：{}".format(stored_task_id))
            self.task_poll_timer.start()

    def on_worker_progress(self, current, total, message):
        global_progress = int((current / total) * 100) if total > 0 else 0
        self.global_progress_bar.setValue(global_progress)
        self.global_progress_label.setText(f"{global_progress}%")
        self.status_label.setText(f"进度 {current}/{total} - {message}")

    def on_sub_progress(self, sub_progress):
        self.task_progress_bar.setValue(sub_progress)
        self.task_progress_label.setText(f"{sub_progress}%")

    def on_worker_finished(self, results):
        super().on_worker_finished(results)
        self.global_progress_bar.setValue(100)
        self.global_progress_label.setText("100%")
        self.task_progress_bar.setValue(100)
        self.task_progress_label.setText("100%")
        self.status_label.setText("拼接完成")
        QMessageBox.information(self, "完成", f"已完成 {len(results)} 个拼接视频")

    def on_worker_error(self, msg):
        super().on_worker_error(msg)
        self.status_label.setText("拼接失败")


class VideoConcatTab(VideoConcatPage):
    def __init__(self):
        super().__init__("video_concat")
