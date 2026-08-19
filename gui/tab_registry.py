# -*- coding: utf-8 -*-
"""标签页单点注册表。

新增/删除一个 tab：只改本文件（顶部 import + TABS 加一行），
实例化 / addTab / closeEvent 清理全部自动跟随，无需再改 main_window.py。

P0 重构目标（AC-P0-1~4）：
  - import / 实例化 / addTab / closeEvent 全部由本注册表派生，消灭 4 处手写平行列表；
  - closeEvent 清理用 vars(tab) 全量扫描 QThread 属性，自动覆盖 worker / login_worker
    及未来任何新增 *_worker 属性（修复 video_download_tab 双线程泄漏，AC-P0-2）；
  - 停止协议保留现状顺序：stop() → request_stop() → wait(1500) → terminate()（AC-P0-3）。
"""
from typing import Any, Callable, List, Tuple

from PyQt5.QtCore import QThread
from PyQt5.QtWidgets import QWidget

from gui.slice_tab import SliceTab
from gui.text_recognition_tab import TextRecognitionTab
from gui.audio_mix_tab import AudioMixTab
from gui.video_mix_tab import VideoMixTab
from gui.video_concat_tab import VideoConcatTab
from gui.video_resize_tab import VideoResizeTab
from gui.video_enhance_tab import VideoEnhanceTab
from gui.keyword_remove_tab import KeywordRemoveTab
from gui.face_detection_tab import FaceDetectionTab
from gui.screenshot_tab import ScreenshotTab
from gui.subtitle_tab import SubtitleTab
from gui.settings_tab import SettingsTab
from gui.kaipai_cloud_tab import KaipaiCloudTab
from gui.voice_clone_tab import VoiceCloneTab
from gui.video_fission_tab import VideoFissionTab
from gui.video_download_tab import VideoDownloadTab

# 元素：(属性名, 显示标题, 工厂函数)
# 工厂统一签名 factory(app) -> QWidget；不需要 app 的 tab 忽略参数即可。
# 属性名必须与现有 self.<name>_tab 一致（外部可能引用）。
TabFactory = Callable[[Any], QWidget]

TABS: List[Tuple[str, str, TabFactory]] = [
    ("slice_tab",            "视频切片", lambda app: SliceTab()),
    ("screenshot_tab",       "视频截图", lambda app: ScreenshotTab()),
    ("text_recognition_tab", "文字识别", lambda app: TextRecognitionTab()),
    ("face_detection_tab",   "人脸识别", lambda app: FaceDetectionTab()),
    ("audio_mix_tab",        "音频混剪", lambda app: AudioMixTab()),
    ("video_mix_tab",        "视频混剪", lambda app: VideoMixTab()),
    ("video_concat_tab",     "视频拼接", lambda app: VideoConcatTab()),
    ("video_resize_tab",     "视频尺寸", lambda app: VideoResizeTab()),
    ("video_enhance_tab",    "视频优化", lambda app: VideoEnhanceTab()),
    ("keyword_remove_tab",   "去关键词", lambda app: KeywordRemoveTab()),
    ("subtitle_tab",         "视频字幕", lambda app: SubtitleTab()),
    ("kaipai_cloud_tab",     "开拍云端", lambda app: KaipaiCloudTab()),
    ("video_fission_tab",    "视频裂变", lambda app: VideoFissionTab()),
    ("voice_clone_tab",      "音色复刻", lambda app: VoiceCloneTab()),
    ("video_download_tab",   "视频下载", lambda app: VideoDownloadTab()),
    ("settings_tab",         "设置",     lambda app: SettingsTab(app)),  # 唯一需要 app 参数
]


def stop_tab_threads(tab: QWidget) -> None:
    """停止一个 tab 上所有 QThread 类型属性（覆盖 worker / login_worker / 未来任意 *_worker）。

    协议：优先 stop()，其次 request_stop() → wait(1500) → 仍运行则 terminate() → wait(1500)。
    与现状 main_window.closeEvent 的顺序完全一致，只是由"手写属性名列表"改为"vars(tab) 全量扫描"。
    """
    for _name, obj in vars(tab).items():
        if not isinstance(obj, QThread):
            continue
        if not obj.isRunning():
            continue
        if hasattr(obj, "stop"):
            try:
                obj.stop()
            except Exception:
                pass
        elif hasattr(obj, "request_stop"):
            try:
                obj.request_stop()
            except Exception:
                pass
        if not obj.wait(1500):
            obj.terminate()
            obj.wait(1500)
