# -*- coding: utf-8 -*-
"""ASR 模型目录常量（原散落在 gui/subtitle_tab，导致 keyword_remove 跨 tab 依赖）。

gui 各 tab 统一从此处引用，切断 gui→gui 耦合（D5/D6）。
"""
FIREMODELS_DIR = r"D:\Models\FireRed"
FUNASR_DIR = r"D:\Models\FunASR\paraformer-large-zh-en-timestamp-onnx-offline"
SENSEVOICE_DIR = r"D:\Models\SenseVoiceSmall"
