# 项目长期记忆 - video_random_cut_mimo

> 详细坑点/历史明细已归档：`archive/MEMORY-detail-2026-08-20.md`（遇具体模块问题去查）

## 环境
- Python `D:/Anaconda/python.exe` 3.12.4，`python main.py` 启动；PyQt5+paddleocr+onnxruntime+qt-material
- ffmpeg 8.1 `C:\Users\91682\AppData\Local\ffmpeg\...\bin\ffmpeg.exe`
- **本机无 NVIDIA GPU**（仅 Intel Arc）；QSV 实测比软件慢 3.4× → 唯一可用编码 = libx264。encoder.py 自动探测，换到 NVIDIA 机器自动启用 NVENC
- push 用 `git -c credential.helper=wincred push origin main`（GCM 读不了 LegacyGeneric 凭据）

## 架构（2026-08-20 P0+P1+P2 重构已落地，全部 commit+push 到 origin/main）
- `core/encoder.py` get_encoder/get_default_workers/fallback_to_software/is_session_limit —— **编码参数必须走这里，禁止写死 libx264**；NVENC fallback 已收窄为"仅会话受限且非超时"才回退
- `core/ffmpeg_runner.py` run_ffmpeg / run_ffmpeg_with_fallback / terminate_all / terminate_owner（owner 分组防跨 tab 互杀）+ track_proc(owner=) + FFmpegError(timed_out)；CREATE_NO_WINDOW
- `core/video_utils.py`（原 `utils/video_utils.py`，已 git mv 下沉收口反向依赖）/ `utils/media_utils.py` VIDEO_EXTS / collect_videos / probe_video；`utils/path_utils.py` strip_quotes / unique_output_path / build_output_path
- `core/model_dirs.py` 单点收敛 FIREMODELS_DIR / FUNASR_DIR / SENSEVOICE_DIR（切断 gui→gui 耦合）
- `gui/tab_registry.py` TABS 16 项 + stop_tab_threads —— **增删 tab 只改此文件**
- `gui/common/` BaseWorker(run 统一 try/except + finally 兜底 emit finished，信号 progress(int,int,str)/finished/error) + BaseTab(start_worker 防重入+set_busy) + PathRow + ProgressPanel；**16 页全部迁移完成**
- `core/text_detector.py` paddleocr 改为 lazy import（_init_worker/__init__ 内）；`core/voice_clone.py` 新增 CosyVoiceService.stop() 清理残留进程；`core/audio_utils.py` demucs 接入 track_proc
- 约定：滤镜链冻结；公共模块只依赖标准库；密钥存 config.local.json
- 未迁移 backlog：GUI 格式列表、wink_enhancer 8 元组、统一 logging、大视频内存分块（P3 暂缓）；taobao 巨石分区/冒烟脚本/路径全收敛/crf 统一（P4 暂缓）

## 关键坑（高频）
- qt-material 下 QLineEdit 无边框 → gui/styles.py FIX_LAYOUT_QSS + LINEEDIT_QSS 全带 !important
- `eq` 滤镜不支持 hue，色相要独立 `hue` 滤镜
- 清元数据必须 `-map_metadata -1` + `-fflags +bitexact`
- **SAR 坑（千川报尺寸异常）**：滤镜链末尾必须 `setsar=1`
- astor 包损坏会炸启动 → `pip install --force-reinstall --no-deps astor==0.8.1`
- torch/faster_whisper 不可用（DLL/段错误）→ 已换 FireRedASR ONNX
- 抖音风控：频繁无头访问触发 captcha；video 标签优先取 `aweme/v1/play`（<300KB 判定预览片段）
- Wink CLI：`--output`/`--dry-run`/`--async` 全是坑；真正可用的 exe 在版本子目录
- main_window.py 曾是 CRLF，改动会全量重写 diff

## 核心功能定位
- 视频裂变 `gui/video_fission_tab.py`+`core/video_fission.py`：3 输入源→1 输出，每源独立数量，温和随机变换改 pHash，不翻转，可选统一 1080×1920，支持中断+并发
- 字幕 `core/fireredasr.py`：FireRedASR-AED-L INT8 ONNX，CTC greedy（decoder 方式不可用）
- 视频优化 `core/wink_enhancer.py`：Wink 桌面版 CLI，云端处理，10 档位（5/6/9 仅图片）
