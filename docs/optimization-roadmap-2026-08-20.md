# 三份复盘总结分析 + 接下来主要规划（2026-08-20）

> 输入：① round-2 实地读代码评审（`arch-review-2026-08-20-round2.md`）② GLM 迁移完整度分析 ③ 千问体检报告 v2
> 交叉综述见 `arch-three-way-review-2026-08-20.md`。本文聚焦**总结三方观点 + 找出对的部分 + 给出详细执行规划**。

---

## 一、三份复盘各自在说什么

| 复盘 | 视角 | 核心结论 | 最大价值 | 盲区 |
|---|---|---|---|---|
| **round-2**（我实地读码） | 架构维度评分 + ROI 排序 | 地基已稳，剩「减重+收口」，**本轮无 P0** | 确认公共层做对、给债务排 ROI；明确反对过度工程 | 把 fallback 当正面 feature（漏看过度激进）；未深挖迁移半成品 bug |
| **GLM**（迁移完整度） | 12 项逐条对照迁移规范 | 迁移基本到位，但 4 个细节 bug/债漏修 | 挖出 round-2 漏看的 face_detection 崩溃、VIDEO_EXTS 散落 13 处、反向依赖、跨 tab 耦合 | 主张 ConfigStore 是缺口（过度设计）；偏「规范洁癖」 |
| **千问**（上轮验收+全量复查） | P0 验收 + 新问题排查 | 修了 3.5/6 个 P0，**新引入 1 个 P0（fallback 盲目重试）**，2.2/2.3/2.4/2.5 未修 | 推翻 round-2「无 P0」；最精准定位 fallback 过度激进 | 把 2.3（CosyVoice）列 P0 偏重（本机跑不起来） |

**一句话**：round-2 看「骨架稳不稳」、GLM 看「迁移细不细」、千问看「上轮修得对不对 + 有没有引入新坑」。三者互补，**没有一份是全对的**。

---

## 二、我认为对的部分（交叉验证后分级）

### A. 三份一致同意 = 确定要改的真问题

| 问题 | 三方口径 | 判定 |
|---|---|---|
| 5 个 ASR 并存（~2116 行）+ SenseVoice 双实现 | round-2 D2 / 千问 P2 | ✅ 技术债，收敛方向明确（onnx_asr 唯一分发器） |
| 硬编码路径（D:\Models / 50051 / VIDEO_EXTS 散落） | round-2 P1 / GLM / 千问 P1-P2 | ✅ 收敛进 settings |
| 仓库卫生（.gitignore 缺 taobao_auth.json、requirements 偏离、services 未入库、根目录杂物） | GLM / 千问（P0 级卫生） | ✅ 半小时快修 |
| 启动慢（paddleocr 顶层 import 传染 / tab 全量实例化） | round-2 P2 / 千问 P2 | ✅ 懒 import 解决 |
| taobao_downloader 1437 行巨石 | round-2 D7 / 千问 | ✅ 内部分区 + 抽抖音簇（不微服务拆分） |

### B. 两份认同、我站对的（round-2 漏看，被 GLM/千问纠正）

| 问题 | 谁先发现 | 核实结论 |
|---|---|---|
| **fallback 盲目重试**（千问 2.1） | 千问 | ✅ 属实，最该先修。`run_ffmpeg_with_fallback` 捕获**所有** FFmpegError（含超时/坏文件/磁盘满）就全局废 NVENC + 重跑，让超时/停止保护失效 |
| **face_detection `_browse` 崩溃** | GLM | ✅ 属实真 crash。`QLineEdit` 无 `_browse()`，点浏览必 AttributeError |
| **VIDEO_EXTS 散落 13 处** | GLM | ✅ 属实。13 处硬编码 5 元组字面量 |
| **utils→core 反向依赖** | GLM（round-2 D4 也提） | ✅ `video_utils.py:5` import ffmpeg_runner |
| **keyword_remove 跨 tab 耦合** | GLM（round-2 D5 也提） | ✅ import subtitle_tab 常量 |
| **audio_mix 停止不复位**（千问 2.5） | 千问 | ✅ 属实真 bug。根因比千问说的更深：`AudioMixWorker` **覆盖了 `run()` 绕过 BaseWorker 统一异常处理**，且停止时两分支都不 emit → BaseTab 复位全挂 finished/error 信号 → UI 卡死 |
| **跨 tab 进程互杀**（千问 2.2） | 千问 | ✅ `_procs` 全局 set，request_stop 调 terminate_all 杀所有 tab |

### C. 我反对 / 修正的

| 观点 | 来源 | 我的判定 |
|---|---|---|
| 「ConfigStore 是缺口」 | GLM | ❌ 反对。单机小工具，config.json + get_config/set_config 已够，引入 ConfigStore 是过度设计 |
| 2.3 CosyVoice 列 P0 | 千问 | ⚠️ 修正为 P1。voice_clone 需额外装 torch/conda，本机 torch 不可用基本跑不起来，日常不触发 |
| round-2「无 P0」 | round-2 | ⚠️ 被 fallback 推翻一处。整体「地基稳」判断仍成立，但 fallback 是真 P0 |

---

## 三、接下来的主要规划（分 4 阶段，详细执行）

> 原则：**先小而痛、后大而重**；每阶段改完即 commit+push；不碰业务铁律（9:16 / 1080×1920 / setsar=1 / 滤镜链 / get_encoder）。

### 阶段 1：立刻做（半天内，低风险高价值）

#### 1.1 fallback 收窄（千问 2.1，最该先修）
- **根因**：`core/ffmpeg_runner.py` 的 `run_ffmpeg_with_fallback` 用 `except FFmpegError` 捕获**所有**错误（含 `timed_out=True` 超时、坏文件、磁盘满），无差别 `fallback_to_software()`（进程级全局废 NVENC）+ 软件重跑整条命令。
- **修法**：except 内分流——
  - `timed_out=True` 或进程被用户终止 → **直接 raise，不重试不降级**；
  - stderr 命中 NVENC 会话受限特征（`session` / `Max sessions` / `Not enough` / `No capable devices`）→ 才 fallback；
  - 其余错误 → 直接 raise（让调用方处理）。
  - 新增 `core/encoder.py` 的 `is_nvenc_session_limit(stderr)` 判断函数（带缓存）。
- **涉及**：`core/ffmpeg_runner.py`、`core/encoder.py`
- **风险**：低。需构造 NVENC 会话满的 stderr 样本验证判断准确（本机无 NVENC，可构造字符串单测）
- **验收**：超时不再重跑；坏文件直接报错不降级全局 NVENC

#### 1.2 face_detection `_browse` 崩溃 bug（GLM）
- **根因**：`gui/face_detection_tab.py` 用裸 `QLineEdit`，浏览按钮调 `self.folder_input._browse()`，QLineEdit 无此方法 → AttributeError 崩溃。
- **修法**：`folder_input` 换 `PathRow(mode=MODE_FOLDER)`（与已迁移 tab 一致），删 `_browse` 调用，PathRow 内置浏览。
- **涉及**：`gui/face_detection_tab.py`
- **风险**：低
- **验收**：点浏览弹文件夹选择对话框，不崩

#### 1.3 VIDEO_EXTS 13 处收口（GLM）
- **根因**：13 处硬编码 `(".mp4",".avi",".mov",".mkv",".flv")` 字面量，与 `utils.media_utils.VIDEO_EXTS`（5 元组）重复。另注意 `core/voice_clone.py:14` 的 `VIDEO_EXTENSIONS` 是 6 元组（含 `.webm`），收口时需统一（建议 VIDEO_EXTS 加 `.webm` 成 6 元组，或 voice_clone 引用 VIDEO_EXTS 并确认是否要 .webm）。
- **修法**：13 处全部改 `from utils.media_utils import VIDEO_EXTS`，删字面量。
- **涉及**：`face_detection_tab` / `screenshot.py` / `screenshot_tab` / `slice_tab×3` / `subtitle_tab` / `slicer.py×3` / `text_recognition_tab` / `video_mixer.py×2`
- **风险**：低-中，需回归各功能确认扫描范围不变
- **验收**：`grep '(".mp4"'` 返回 0 处业务残留

#### 1.4 audio_mix 停止不复位（千问 2.5）
- **根因**：`AudioMixWorker` 覆盖了 `run()`（没遵循 BaseWorker 的 `work()` 约定），绕过统一 try/except；停止时 `self.stopped()` 为 True → 既不 emit finished 也不 emit error → BaseTab 复位（set_busy + self.worker=None）全挂 finished/error 信号 → start_btn 永久禁用、状态卡「正在停止…」。
- **修法（最小，不碰 BaseWorker 全局）**：worker 的 run() 停止分支补 `self.finished.emit([])` 走正常复位（不弹错误窗）；或改回实现 `work()` 让 `mix_folder` 回调 raise InterruptedError 被 BaseWorker.run() 捕获——但走 error 会弹窗，不理想。**推荐**：保持覆盖 run()，停止时 emit finished([])。
- **涉及**：`gui/audio_mix_tab.py`
- **风险**：低
- **验收**：点停止后 start_btn 恢复、状态归位

#### 1.5 仓库卫生快修（GLM + 千问）
- `.gitignore` 补 `taobao_auth.json`（617KB 登录态裸奔）+ `*.log` + `__pycache__/`
- `git rm --cached core/sherpa_asr_error.log`
- 提交 `services/` 与 `scripts/install_cosyvoice3.ps1`（voice_clone_tab 引用，换机不缺文件）
- `requirements.txt` 补 `sherpa-onnx` / `requests` / `playwright`；`torch` / `torchaudio` / `demucs` 移到 `requirements-optional.txt`（标注「仅 Whisper 独立进程 / 音色复刻 / 人声分离需要」）
- 根目录杂物（`tb_*.html`×4 / `h5api_test.txt` / `check_output.py` / `Users/` / `v/`）评估是否 .gitignore 或删除
- **涉及**：`.gitignore` / `requirements.txt` / `services/`
- **风险**：低
- **验收**：`git status` 干净；新机器 `pip install -r requirements.txt` 能跑起主功能（不含可选）

#### 1.6 跨 tab 进程互杀（千问 2.2，可与 1.1 同批）
- **根因**：`ffmpeg_runner._procs` 是模块级全局 set，`video_fission.request_stop` 调 `terminate_all()` 杀所有 tab 的 ffmpeg。
- **修法**：`_procs` 改 `dict[token, set]`；`track_proc(proc, token)` 按组登记；新增 `terminate_group(token)` 只杀同组；`request_stop` 传自身 token 调 `terminate_group` 而非 `terminate_all`（terminate_all 保留给关窗兜底）。
- **涉及**：`core/ffmpeg_runner.py`、`core/video_fission.py`、其余调用 track_proc 的模块传 token
- **风险**：中（影响所有用 ffmpeg 的 tab 的停止逻辑，需回归停止功能）
- **验收**：两 tab 同时跑，A 点停止不杀 B 的 ffmpeg

---

### 阶段 2：本周（中等风险，需回归）

| 项 | 根因/修法 | 风险 |
|---|---|---|
| **2.3 CosyVoice stop + closeEvent** | `CosyVoiceService` 加 `stop()`（terminate self.process + 关 log）；`main_window.closeEvent` 在 stop_tab_threads 后遍历 tab 调 CosyVoice.stop（低优先，本机跑不起来） | 低 |
| **2.4 开拍云主线程网络阻塞** | `download_single` / `_download_batch` / `refresh_quota` 移到 worker 线程，流式下载避免冻结 GUI + 整读内存 | 中 |
| **audio_utils/demucs 接入 track + 异常兜底** | demucs 裸 subprocess.run → 补 CREATE_NO_WINDOW + 进 track_proc 注册表（停止能杀）+ 捕获 TimeoutExpired；失败路径清 tmp_dir | 中 |
| **CREATE_NO_WINDOW 补漏** | `media_utils.py:88/134`（ffprobe 循环调用）、`encoder.py:34-51` 补 CREATE_NO_WINDOW | 低 |
| **text_detector 懒 import** | `text_detector.py:1` 顶层 import paddleocr → 改函数内懒 import，斩断启动 3.5s 主因 | 低-中 |
| **keyword_remove 常量提公共** | `FIREMODELS_DIR/FUNASR_DIR/SENSEVOICE_DIR` 抽到 `core/` 或 `config/paths.py`，keyword_remove 改引用，切断 gui→gui 耦合 | 低 |
| **utils→core 反向依赖收口** | `video_utils.py:5` 不再 import ffmpeg_runner，改由调用方传 run 函数或下沉相关函数到 core | 中 |

---

### 阶段 3：短期（1-2 周，结构性改善）

#### 3.1 BaseWorker.run() 根治（2.5 的根治，防其他 tab 重蹈覆辙）
- **问题**：BaseWorker 设计 `work()` 约定，但 audio_mix 等仍覆盖 run()；且停止时无统一 emit → 复位靠不住。
- **修法**：`BaseWorker.run()` 改为统一保证「线程结束 emit finished（停止走 finished 不弹错），异常走 error」：
  ```
  run(): try: results = self.work() or []
         except StopRequested: results = []   # 停止 → 空结果走 finished 复位
         except Exception as e: self.error.emit(str(e)); return
         self.finished.emit(results)
  ```
  所有 worker 改实现 `work()` 返回 results，不再覆盖 run()、不再各自 emit finished。
- **风险**：中-高（影响 16 个 tab，需逐一迁移 + 回归停止/完成路径）
- **价值**：根治「停止不复位」类 bug，work() 实现更简单

#### 3.2 ASR 收敛（依赖 Q1 确认）
- `onnx_asr` 作为唯一分发器；SenseVoice 统一走 `sensevoice_onnx`（纯 onnxruntime）；`subtitle_tab` 的 SenseVoice 改用 OnnxASR 而非 sherpa_asr；**删除/冻结 `sherpa_asr.py`**；按 Q1 答复决定 funasr_onnx / fireredasr 去留。
- 删约 2000 行，依赖（sherpa_onnx）可减负。
- **风险**：中，必须做模型路径 + 识别效果回归

#### 3.3 其余结构改善
- 开拍云下载移 worker 流式（与 2.4 合并深化）
- `fireredasr.py` 音频 capture_output 全量读内存 → 分块处理（1h 视频 ≈ 460MB 峰值）
- 统一 logging 框架（替代 60+ 处 except Exception 静默吞）
- 输出覆盖策略统一（resizer 去重命名 vs concat(-y)/mixer(move)）

---

### 阶段 4：中期（重活，等前面落地）

| 项 | 修法 | 备注 |
|---|---|---|
| taobao_downloader 内部分区 + 抽抖音簇 | 文件内分区注释 + 抖音簇抽到 `core/douyin_downloader.py`，不做微服务拆分 | Playwright 选择器脆，先搬不动 |
| 最小工程化 | `scripts/smoke_import.py`（import 全模块验证不缺依赖）+ `check_env.bat`（ffmpeg/python/关键依赖） | 不引入 pytest/CI |
| 硬编码路径全量收敛 | D:\Models / 50051 / 模型目录全进 settings UI | SenseVoice/SCRFD 目前无 UI 入口 |
| wink_enhancer 8 元组标准化 | 返回改 dataclass/NamedTuple | round-2 D9 |
| crf 默认值统一 | `run_ffmpeg_with_fallback` 默认 20 → 23 | round-2 D10 |
| get_encoder 收紧 100%（依赖 Q3） | video_enhance 等尚未接入的补齐 | 当前已基本贯彻 |

---

## 四、不建议做（防止过度工程）

1. **不引入 ConfigStore**（GLM 主张）—— config.json + get_config/set_config 对单机工具已够
2. **不引入 DI 框架 / 插件化 tab** —— tab_registry 单点注册足够
3. **不改造 Web / 微服务 / 多进程** —— 它是单机桌面程序
4. **不引入重型测试框架 / 完整 CI** —— 冒烟脚本 + 自检 bat 比 pytest/Jenkins 实用
5. **不用 asyncio 重写线程** —— BaseWorker(QThread) + 协作式停止工作良好
6. **不碰业务铁律**：9:16 / 1080×1920 / setsar=1 / 滤镜链 / 编码走 get_encoder

---

## 五、待你确认的 3 个问题（决定阶段 3/4 范围）

- **Q1（决定 ASR 收敛范围）**：字幕 / 去关键词实际用哪几个 ASR 模型？FireRedASR / FunASR / SenseVoice / Whisper 哪些可删？两套 SenseVoice（sherpa_asr vs sensevoice_onnx）是否都在用？
- **Q2（决定依赖收口）**：torch（shm.dll 报错）/ demucs 是否重装痛点？能否把「音色复刻、Whisper 字幕」标为需单独装依赖的可选功能（主依赖不强制装 torch）？
- **Q3（决定铁律收口）**：是否把「所有重编码必须走 get_encoder(crf=23)」收紧到 100%（含 video_enhance）？

---

## 六、一句话总结

三份复盘交叉验证后：**地基稳（公共层 + 注册表 + 线程标准化 + 编码铁律守住）**，当前最该先修的是阶段 1 的 6 项「小而痛」问题（fallback 收窄、face_detection 崩溃、VIDEO_EXTS 收口、audio_mix 复位、仓库卫生、进程互杀），半天能全搞定；重活（ASR 收敛 / BaseWorker 根治 / taobao 分区）等阶段 1 落地 + 你答完 3 问后再推进。
