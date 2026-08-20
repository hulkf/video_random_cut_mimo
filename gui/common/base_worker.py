# -*- coding: utf-8 -*-
"""统一后台线程基类（P2-1）。

解决「17 个手写 QThread Worker 类散落各处」问题：所有页面后台任务统一继承本基类，
信号协议全项目一致，停止协议与 gui/tab_registry.stop_tab_threads 配合。

信号协议：
    progress(current, total, message)   # 进度（文件粒度；current 从 0 起，total 为总数）
    finished(results)                   # 全部完成（results 为结果列表）
    error(message)                      # 失败（含中断等异常）

停止协议：
    request_stop() 设置标志，work() 中通过 self.stopped() 轮询退出；
    stop() 为 request_stop() 别名（tab_registry 优先调用 stop()）；
    未及时退出的由 tab_registry 的 wait(1500) → terminate() 兜底。
"""
from PyQt5.QtCore import QThread, pyqtSignal


class BaseWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_requested = False

    # ── 线程入口（统一 try/except，子类不再需要重复样板）──
    def run(self):
        emitted = {"finished": False, "error": False}

        def _mark_finished(_r):
            emitted["finished"] = True

        def _mark_error(_m):
            emitted["error"] = True

        self.finished.connect(_mark_finished)
        self.error.connect(_mark_error)
        try:
            self.work()
        except Exception as e:  # noqa: BLE001 —— 所有异常统一走 error 信号
            if not emitted["error"]:
                self.error.emit(str(e))
        finally:
            # 兜底：work() 既没 emit finished 也没 emit error（如中途被停止）时，
            # 仍复位 UI，避免标签页卡在"正在停止/执行中"（P1.4 / Phase3）。
            if not emitted["finished"] and not emitted["error"]:
                self.finished.emit([])

    def work(self):
        """子类覆盖：业务逻辑主体（run() 已统一处理异常）。"""
        raise NotImplementedError("BaseWorker 子类必须实现 work()")

    # ── 停止协议 ─────────────────────────────────────────────
    def request_stop(self):
        self._stop_requested = True

    def stop(self):
        """别名：tab_registry.stop_tab_threads 优先调用 stop()。"""
        self.request_stop()

    def stopped(self) -> bool:
        return self._stop_requested

    # ── 便捷：进度/完成发射（message 留空时跳过进度消息）──
    def emit_progress(self, current: int, total: int, message: str = ""):
        self.progress.emit(current, total, message)

    def emit_finished(self, results: list):
        self.finished.emit(results)
