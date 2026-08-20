# -*- coding: utf-8 -*-
"""标签页基类（P2-2）：统一 worker 生命周期 / 防重入 / 启停状态 / 配置骨架。

各 tab 继承本基类后可省去重复的：
  - 「worker 正在运行则弹窗拦截」防重入检查（start_worker 内置）
  - worker 信号连接与清理样板
  - 开始按钮禁用/恢复状态机（set_busy）
  - 统一错误弹窗（on_worker_error 默认实现）

与 gui/tab_registry.stop_tab_threads 配合：BaseWorker 提供 stop()/request_stop()，
tab_registry 通过 vars(tab) 扫描 QThread 属性自动调用，本基类无需额外注册。
"""
from PyQt5.QtWidgets import QWidget, QMessageBox

from gui.common.base_worker import BaseWorker


class BaseTab(QWidget):
    """标签页公共基类。

    约定：
      - self.worker 为当前运行的 BaseWorker 实例（未运行时为 None）
      - 子类调用 self.start_worker(worker) 统一启动（含防重入与信号连接）
      - 子类覆盖 on_worker_progress / on_worker_finished / on_worker_error 处理业务展示
      - 子类覆盖 set_busy 切换开始/停止按钮状态（默认空实现）
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None

    # ── worker 生命周期 ──────────────────────────────────────
    def start_worker(self, worker: BaseWorker) -> bool:
        """统一启动后台任务：防重入 + 连接信号 + 更新按钮状态。

        Returns:
            True 启动成功；False 已有任务在进行中（已弹窗提示）。
        """
        if self.is_busy():
            QMessageBox.warning(self, "警告", "任务正在执行中")
            return False
        self.worker = worker
        worker.progress.connect(self.on_worker_progress)
        worker.finished.connect(self.on_worker_finished)
        worker.error.connect(self.on_worker_error)
        # 兜底清理：任务结束后无论子类是否调用 super().on_worker_finished，
        # self.worker 都会置 None，is_busy 不再误判（AC-P0-契约）。
        worker.finished.connect(lambda _r, w=worker: self._worker_finished_cleanup(w))
        self.set_busy(True)
        worker.start()
        return True

    def _worker_finished_cleanup(self, worker: BaseWorker) -> None:
        """任务结束兜底清理：若当前 worker 正是结束的实例则置 None。"""
        if self.worker is worker:
            self.worker = None

    def is_busy(self) -> bool:
        """是否正在执行任务（worker 存在且线程运行中）。"""
        return self.worker is not None and self.worker.isRunning()

    def set_busy(self, busy: bool) -> None:
        """切换忙碌状态。子类覆盖：如 self.start_btn.setEnabled(not busy)。"""
        pass

    # ── 默认回调（子类覆盖扩展业务展示）──
    def on_worker_progress(self, current: int, total: int, message: str) -> None:
        pass

    def on_worker_finished(self, results: list) -> None:
        self.set_busy(False)
        self.worker = None

    def on_worker_error(self, message: str) -> None:
        self.set_busy(False)
        QMessageBox.critical(self, "错误", message)

    # ── 配置骨架（子类覆盖实现持久化）──
    def load_config(self) -> None:
        pass

    def save_config(self) -> None:
        pass
