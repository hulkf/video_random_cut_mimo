# 项目长期记忆 - video_random_cut_mimo

## 运行环境
- Python: D:/Anaconda/python.exe (3.12.4)，启动方式 `python main.py`（见 启动工具.bat）
- 依赖：PyQt5 + paddleocr + moviepy + onnxruntime + qt-material
- ffmpeg: C:\Users\91682\AppData\Local\ffmpeg\...\bin\ffmpeg.exe (8.1)
- FireRedASR 模型目录: D:\Models\FireRed\fireredasr2-aed-large-zh-en-int8-onnx-selfcrosskv-offline-20260212

## 已知坑点
- **faster_whisper 不可用**（ctranslate2 段错误）、**torch 不可用**（shm.dll WinError 127）：已换 FireRedASR ONNX 方案彻底绕开
- whisper 模型缓存仍在 ~/.cache/whisper/（tiny/base/medium），D:\whisper-models 有完整 7 模型

## 字幕功能（gui/subtitle_tab.py + core/fireredasr.py）
- **后端**：FireRedASR-AED-L INT8 ONNX（模型路径可配置，默认 D:\Models\FireRed\...）
- **架构**：GUI QThread 中直接 import onnxruntime + FireRedASR（ONNX 无 torch 依赖，无 DLL 冲突）
- FireRedASR.transcribe() 用 encoder+ctc 做 CTC greedy decode，按 10s 分块处理
- CTC 解码需过滤特殊 token：<blank>/<unk>/<pad>/<sos>/<eos>/<sil> 等（已在 _ctc_greedy_decode 中处理）
- decoder 方式 (transcribe_with_decoder) 输出全是 <blank>，**不可用**
- 字幕烧录：ffmpeg subtitles 滤镜，SRT 路径需 `replace("\\","/").replace(":","\\:")` + 单引号包裹
- 配置节：config.json 的 "subtitle" 段（字幕参数含 model_path）
- 设置页保留 Whisper 模型配置组（whisper_model_dir）供未来切换用
