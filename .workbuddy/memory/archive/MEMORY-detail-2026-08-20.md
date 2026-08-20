# 项目长期记忆 - video_random_cut_mimo

## 运行环境
- Python: D:/Anaconda/python.exe (3.12.4)，启动方式 `python main.py`（见 启动工具.bat）
- 依赖：PyQt5 + paddleocr + moviepy + onnxruntime + qt-material
- ffmpeg: C:\Users\91682\AppData\Local\ffmpeg\...\bin\ffmpeg.exe (8.1)
- FireRedASR 模型目录: D:\Models\FireRed\fireredasr2-aed-large-zh-en-int8-onnx-selfcrosskv-offline-20260212
- **⚠️ 硬件（2026-08-20 修正）**：本机**无 NVIDIA GPU**（Win32_VideoController 仅 Intel Arc + 向日葵虚拟显示器，无 nvcuda.dll/nvEncodeAPI64.dll）——ffmpeg -encoders 虽列出 h264_nvenc 但实际编码报 "Cannot load nvcuda.dll"。QSV(h264_qsv) 可用但实测**慢于软件 3.4×**。本机唯一可用编码路径 = libx264。重构后 core/encoder.py 自动探测 NVENC→不可用则回退 libx264；**代码部署到有 NVIDIA 的机器即自动启用 NVENC（2~6× 提速）**

## 公共层架构（2026-08-20 P0+P1 重构落地）
- **core/encoder.py**：get_encoder(crf,preset)→(codec,preset,quality_args) / get_default_workers()（NVENC=3、软件=min(核数,8)）/ fallback_to_software() 全局回退 / set_hardware_enabled() 测试钩子 / is_session_limit()；模块级缓存带锁
- **core/ffmpeg_runner.py**：run_ffmpeg(cmd, timeout, on_progress, output_path, track, error_message) / run_ffmpeg_with_fallback(build_cmd, crf, preset) / track_proc/untrack_proc/terminate_all() / FFmpegError(RuntimeError 子类，带 timed_out)；CREATE_NO_WINDOW=0x08000000
- **utils/media_utils.py**：VIDEO_EXTS 5 元组 / collect_videos（目录递归+单文件）/ probe_video（dict：codec_name/width/height/pix_fmt/r_frame_rate(float)/fps/duration/sample_aspect_ratio/display_aspect_ratio/has_audio）/ get_video_duration（video_utils 保留一行转发）
- **utils/path_utils.py**：strip_quotes / normalize_path(=strip_quotes，刻意不 normpath) / unique_output_path / build_output_path
- **gui/tab_registry.py**：TABS 16 项(属性名/标题/工厂) + stop_tab_threads(vars 扫描 QThread → stop/request_stop → wait(1500) → terminate)；**新增/删除 tab 只改此文件**；main_window 由注册表驱动
- **gui/common/（P2 落地，commit 2d81f0c）**：base_worker.py(BaseWorker 统一信号 progress(int,int,str)/finished(list)/error(str) + run() 统一 try/except + stop/request_stop 协议) / base_tab.py(BaseTab start_worker 防重入+信号连接+set_busy 状态机+默认错误弹窗+load/save_config 骨架) / path_row.py(PathRow 路径行 folder/file/files 三模式+浏览+on_change 回调；LINEEDIT_QSS 全项目唯一输入框修复) / progress_panel.py(ProgressPanel 进度条+百分比+状态行统一接口)；**全部 16 页迁移完成（2026-08-20）**：video_resize/slice/screenshot/text_recognition/face_detection（手工）+ audio_mix/video_mix/video_concat/keyword_remove/voice_clone/kaipai_cloud/fission/enhance/download/subtitle（子代理 3827614）；settings 无后台任务不迁移；mix_tab.py 废弃未注册不动
- **P2 迁移规范**：worker 改继承 BaseWorker（信号统一 (int,int,str)，emit 补空串；**run 保留子类实现**最小风险）；tab 改继承 BaseTab（start_worker 防重入+set_busy+on_worker_* 回调）；路径行按情况换 PathRow；特殊信号（(int,int,str,int)）与特殊页面逻辑保留
- **scripts/compare_media.py**：迁移前后 ffprobe 对比（codec/宽高/SAR/DAR/时长±0.05s/帧率/pix_fmt/可播放），--base/--new 或 --base-dir/--new-dir
- 约定：编码参数一律走 get_encoder()，禁止写死 libx264（残留仅在 encoder.py 回退路径）；滤镜链冻结（除非明确允许）；公共模块只依赖标准库（runner 单向依赖 encoder）；GUI 层格式列表/wink_enhancer 8 元组未迁移（P2 backlog）
- **坑：git add 新目录会混入 __pycache__/*.pyc**（项目无 .gitignore 覆盖新目录），需 git rm --cached + commit --amend 修正
- **push 已解决（2026-08-20）**：Windows 凭据管理器里 github 凭据是 LegacyGeneric 格式，GCM 读不了 → 用 `git -c credential.helper=wincred push origin main`（详见 ~/.workbuddy/MEMORY.md）；全部 16 个重构提交已推送，本地=远程=3827614

## 已知坑点
- **astor 包损坏会直接炸启动（2026-08-09 遇过）**：启动报 `No module named 'astor.node_util'`，Traceback 链 `main.py -> gui.slice_tab -> core.slicer -> core.text_detector -> paddleocr -> paddle -> astor`。原因：用户 site-packages（`C:\Users\91682\AppData\Roaming\Python\Python312\site-packages`）里 astor 缺 node_util.py（被误删/装坏）。修复：`D:/Anaconda/python.exe -m pip install --force-reinstall --no-deps astor==0.8.1`
- **faster_whisper 不可用**（ctranslate2 段错误）、**torch 不可用**（shm.dll WinError 127）：已换 FireRedASR ONNX 方案彻底绕开
- whisper 模型缓存仍在 ~/.cache/whisper/（tiny/base/medium），D:\whisper-models 有完整 7 模型
- **qt-material 主题下 QLineEdit 默认无可见边框**：仅显示 placeholder 文字，多个输入框视觉上挤在一起。已在 `gui/styles.py` 的 FIX_LAYOUT_QSS 中给 QLineEdit 加 1px 边框 + #1e1e1e 背景色 + 4d8fff focus 边框（**全用 !important** 防止被主题覆盖）。视频裂变 tab 内还**三保险**：① 抽常量 `LINEEDIT_QSS` 带 !important；② 每个 input/output edit **单独 setStyleSheet(LINEEDIT_QSS)**；③ tab 整体 setStyleSheet(LINEEDIT_QSS)。即使 qt-material 在非全屏状态重写 QSS 也会被强制覆盖
- **⚠️ 抖音风控（2026-08-08 发现）**：短时间频繁用 Playwright 无头浏览器访问抖音会触发风控——页面 HTML 出现 verify/captcha、"视频数据加载中"不渲染、video 标签缺失 → 解析失败。表现"先甜后苦"（前几次成功，连续访问后开始被拦）。**缓解方向未落地**：有头模式 + 窗口移屏幕外(`--window-position=-32000,-32000`) + 增强伪装 JS；或降频/失败重试。用户决定真实直链功能过几天风控缓解后再验证
- **抖音视频页 video 标签加载时序**：加载初期是 douyinvod 直链(2~3s 预览片段约200KB，HEVC无音频)，数秒后(~t=9s)切换为 `www.douyin.com/aweme/v1/play` 签名接口(完整视频，H264+音频)。**必须优先取 aweme/v1/play**，并做文件大小校验(<300KB 判定预览换源)

## 字幕功能（gui/subtitle_tab.py + core/fireredasr.py）
- **后端**：FireRedASR-AED-L INT8 ONNX（模型路径可配置，默认 D:\Models\FireRed\...）
- **架构**：GUI QThread 中直接 import onnxruntime + FireRedASR（ONNX 无 torch 依赖，无 DLL 冲突）
- FireRedASR.transcribe() 用 encoder+ctc 做 CTC greedy decode，按 10s 分块处理
- CTC 解码需过滤特殊 token：<blank>/<unk>/<pad>/<sos>/<eos>/<sil> 等（已在 _ctc_greedy_decode 中处理）
- decoder 方式 (transcribe_with_decoder) 输出全是 <blank>，**不可用**
- 字幕烧录：ffmpeg subtitles 滤镜，SRT 路径需 `replace("\\","/").replace(":","\\:")` + 单引号包裹
- 配置节：config.json 的 "subtitle" 段（字幕参数含 model_path）
- 设置页保留 Whisper 模型配置组（whisper_model_dir）供未来切换用

## 视频裂变（去重搬运）功能
- 入口：`gui/video_fission_tab.py` + `core/video_fission.py`
- 原理：温和随机变换改感知哈希 pHash（调色/噪点/1%缩放重采样），**不用水平翻转**（避免文字镜像反转），音频 `-c:a copy`、分辨率不变，编码 `libx264 -preset ultrafast`
- **坑：`eq` 滤镜不支持 `hue` 参数**，色相必须改用独立 `hue` 滤镜（与 eq 分开写）
- **坑：`-map_metadata -1` 会残留 `encoder: Lavf`，必须加 `-fflags +bitexact` 才能彻底清除**；`major_brand/compatible_brands` 是 MP4 必需字段去不掉
- 每条视频随机参数（random.Random），保证输出互不相同
- **核心参数：裂变数量 N（1~200）**——一个视频生成 N 个不同版本，产物放在 `原名_fissions/` 子文件夹
- **多输入源（V4/V6）**：输入支持 3 个源（文件夹或单文件）共享一个输出；**V6 起每个源可独立设置裂变数量**（数量 SpinBox 从"裂变参数"挪到每个输入行内，配置存 `input_counts` list）；`separate_folder` 可选每文件独立子文件夹或统一平铺；路径自动剥离首尾引号；不同输入源同名文件自动加来源前缀（dirA_aa/dirB_aa）
- **坑：core 的 fission_folder 里 `rel` 变量曾未定义**（V4 重构遗留），callback 非 None 时（GUI 必传）会 NameError——已在 V6 修复，`rel = os.path.relpath(video_path, source_root)` 加在循环开头
- **中断功能（V7）**：`FissionStopped` 异常 + `request_stop()`（设置标志 + terminate 当前 ffmpeg 子进程）；`fission_one` 用 Popen 而非 subprocess.run 便于 kill；中断时删除半成品；`partial_results` 保留已完成视频；GUI 有红色「停止」按钮（默认禁用，开始后启用）
- **性能优化（V9）**：并发执行（ThreadPoolExecutor）+ NVENC 硬件编码自动探测（0.3s 试编码实测，被占/不可用自动回退 libx264）；**QSV 在本机实测慢于软件（0.5x），已弃用**；默认并发 NVENC=3 / 软件=min(核数,8)；GUI「并行任务:」0=自动；实测 640x360×5份：NVENC并发3=1.33s（6.3x），libx264并发8=3.04s（2.7x）；硬件编码失败自动回退软件重试；进度回调按"份"粒度并发安全
- **分辨率铁保证（用户硬性要求）**：偶数尺寸源 scale+crop 精确裁回；奇数尺寸源跳过缩放只调色+噪点，尺寸天然不变
- **⚠️ SAR 坑（千川报"尺寸不符合规范"）**：scale 非整数倍放大会让 ffmpeg 调整 SAR（实测 2943:2944），导致 DAR 偏离精确 9:16（26487:47104），千川判异常。**已在滤镜链末尾强制 `setsar=1`**（V10 修复）——SAR=1:1 则 DAR 恒等于像素比。用户素材有 1080x1920 和 1440x2560 两种（都 9:16），千川若对 2K 高度有额外限制需另行确认
- **可选「统一转 1080×1920」（V11）**：GUI 存放规则处 checkbox（默认不勾选），开启后所有产物统一 1080×1920（`scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920` 居中裁切不变形，9:16 源无损）；配置存 `force_1080x1920`
- **文件层保险（默认开，V5 起硬编码不可关）**：`fission_one` 强制执行清空元数据(`-fflags +bitexact -map_metadata -1 -metadata comment=随机`) + 随机时间戳(ctypes SetFileTime)，不再读 options 判断
- 主窗口 tab 顺序：「视频裂变」在「开拍云端」之后，「设置」页已挪到最后

## 视频优化（美图 Wink 接入）
- 入口：`gui/video_enhance_tab.py` + `core/wink_enhancer.py`
- 后端：Wink 桌面版官方 CLI（`Wink.exe --cli picture_quality --level N --input X`），复用客户端本地登录态，无需自处理 token/签名
- **处理在云端**：整个文件上传到美图服务器，处理完再下载回来，实测上传 ~1.2 MB/s，断网即失败
- **⚠️ CLI 坑（已全部填平）**：
  - `--output` 参数完全失效，结果永远写到默认目录 → 解析 stdout 里"结果路径："拿到真实路径后 `shutil.move` 搬运
  - `--dry-run` 是陷阱，写着空跑其实照样真跑真扣费，引擎**不使用**
  - `--async` 是空壳，仍然同步阻塞，所以放进 QThread 跑
  - Windows 子进程默认弹黑窗，批量跑几十个会闪屏 → `subprocess.Popen(..., creationflags=CREATE_NO_WINDOW)`
- **Wink.exe 路径探测**：根目录那个 `Wink.exe` 只是启动器，`--cli` 不输出；真正能用的在版本子目录（如 `3.7.5\Wink.exe`）。引擎优先取版本目录下版本号最高的
- **档位（10 个）**：1=高清 2=超清 3=人像增强 4=AI超清 5=商品图 6=文字图表 7=游戏 8=动漫 9=高糊图 10=演唱会。**5/6/9 仅支持图片**，worker 提前拦截视频避免白跑
- **停止机制**：`request_stop()` + `taskkill /PID X /T /F` 杀整棵进程树（避免残留），无需管理员权限
- **结果路径解析坑**：`.split("：")[-1].split(":")[-1]` 会把盘符 `D:` 当分隔符吃掉，必须从标记后面切**第一个**冒号（半角/全角取最近的）
- **计费**：CLI 输出"消耗美豆：N"，引擎用 `BEAN_RE` 解析汇总。VIP=0 豆，非 VIP=6 豆/次（实测任务结果 JSON 含 `price.is_vip`/`vip_beans`/`non_vip_beans` 字段）
- **断点续跑**：worker 检测 `build_output_path` 对应目标文件已存在则跳过，默认开
- 主窗口 tab 顺序新增「视频优化」在「视频尺寸」之后、「去关键词」之前

## 工作流约定补充
- 项目其他源文件全是 LF 行尾，但 `gui/main_window.py` 原本是 CRLF —— **Edit/Write 工具不会自动改行尾**，所以替换后 git 会把整个文件判为全量重写。处理：发现后用 Python 一次性转 CRLF → LF，并单独提一个 chore 提交
