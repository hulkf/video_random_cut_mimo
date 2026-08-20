# -*- coding: utf-8 -*-
"""GUI 公共组件包（P2）：BaseWorker / BaseTab / PathRow / ProgressPanel。

新增标签页时优先复用本包组件，避免各 tab 重复实现线程/路径/进度逻辑。
"""
from gui.common.base_worker import BaseWorker
from gui.common.base_tab import BaseTab
from gui.common.path_row import PathRow, MODE_FOLDER, MODE_FILES
from gui.common.progress_panel import ProgressPanel

__all__ = [
    "BaseWorker",
    "BaseTab",
    "PathRow",
    "MODE_FOLDER",
    "MODE_FILES",
    "ProgressPanel",
]
