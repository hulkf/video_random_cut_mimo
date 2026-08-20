# 架构评审报告（第三轮 · 2026-08-20）

> 评审对象：`D:\JR_project\video_random_cut_mimo`（PyQt5 单机桌面视频批处理工具，千川电商短视频素材生产链路）
> 评审方式：**只读、实地读代码**（交叉核对已完成并 push 的 P0/P1/P2，HEAD = `1ee2956`）
> 评审人：高见远（架构师）
> 用户问题：**"整个项目的架构需不需要再做一些优化？你的意见是什么？"**

---

## 一、结论先行（TL;DR + 总分）

**一句话结论**：地基已经稳了，**不需要做大拆大改**；剩下的是"减重 + 收口"层面的小修小补，外加几件**等您拍板**的大事。当前**没有会立刻出事故的危机型债务**，可以放心继续用它。

> 给完全不懂代码的您的大白话：这工具的结构骨架（"公共执行层 + 标签页注册表 + 统一后台线程"）已经搭得很结实，上一轮说的几个真 bug 也都修掉了。现在的问题不是"会塌"，而是"有点胖、有点散"——比如同样的模型路径在十几个地方各写一遍、依赖清单没收拾干净、5 套语音识别堆在一起。这些都不影响现在能用，但以后改功能/加功能会多花时间。

**三态描述**：

| 状态 | 判定 | 说明 |
|------|------|------|
| 🟢 地基稳 | **是** | 公共层（编码/ffmpeg/注册表/线程基类）设计扎实；编码铁律和 ffmpeg 统一接入守住；线程与停止协议基本守住 |
| 🟡 有技术债 | **是（脂肪型，非危机型）** | model_dirs 半截子、D:\Models 散落、crf 默认分裂、worker 约定未贯彻、依赖清单未校准、5 套 ASR 并存、下载巨石文件 |
| 🔴 有风险 | **几乎无** | 仅剩"字幕 SenseVoice 在新电脑上因缺 `sherpa-onnx` 跑不起来"这一处**安装/可复现**风险（非运行时崩溃） |

**综合打分（维度评分，10 分制）**：

| 维度 | 得分 | 一句话 |
|------|------|--------|
| 分层（gui → core → utils 方向） | **8** | utils→core 反向依赖已通过 `video_utils` 下沉 core 解决；gui→gui 常量耦合已切断。仅剩模型路径散落（属常量问题，非方向问题） |
| 线程与停止协议 | **8** | BaseWorker/BaseTab/tab_registry 统一；跨 tab 互杀已修；audio_mix 卡死已修；关窗逻辑稳妥。扣分：worker `run()`/`work()` 约定未贯彻、Whisper 进程未追踪 |
| 编码铁律（get_encoder + run_ffmpeg） | **9** | 全仓**无新硬编码 libx264、无裸 subprocess 重编码**；所有重编码走 `run_ffmpeg_with_fallback`。扣 1 分因 crf 默认 20/23 分裂 |
| 依赖与启动 | **5** | 启动重导入已靠 lazy import 改善（paddleocr 不再顶层）；但 `requirements.txt` **未校准**（torch/demucs 仍强制、`sherpa-onnx` 仍缺声明） |
| 可维护性 | **6** | 公共层消除重复；但 worker 约定不一、下载 1437 行巨石、ASR 双 SenseVoice 增加"改一处动多处"成本 |
| 一致性 | **6** | VIDEO_EXTS 收口好、wink 已改 dict；但 crf 分裂、D:\Models/端口 50051 散落、ASR 双实现仍在 |

**总分 ≈ 7.0 / 10**：地基稳，处于"减重+收口"阶段，无危机债。

---

## 二、P0 / P1 / P2 落地核查（实地读码验证，非看报告）

> 主理人核实 P0/P1/P2 已 commit 并 push（HEAD = `1ee2956`）。以下为本人逐文件核查结果，**标"⚠️未完全落地"的是与完成报告口径不一致、需您注意的点**。

### P0（首轮，已落地，本轮复核）

| 项 | 核查证据 | 结论 |
|----|---------|------|
| tab 单点注册表 | `gui/tab_registry.py`：16 tab 全部在 `TABS` 列表；`stop_tab_threads` 用 `vars(tab)` 全量扫 QThread | ✅ 落地 |
| 信号契约统一 | 16 个 `*Tab` 全部继承 `BaseTab`；worker 全部继承 `BaseWorker`；`progress/finished/error` 三信号一致 | ✅ 落地 |
| 停止协议 | `BaseWorker.stop/request_stop/stopped` + `tab_registry` 顺序 `stop→wait(1500)→terminate` | ✅ 落地 |
| 编码铁律（走 get_encoder，禁硬编码 libx264） | 全仓 grep `libx264`：仅 `encoder.py`/`ffmpeg_runner.py`/`video_fission.py` 的**合法回退逻辑**出现；tab 内零硬编码 | ✅ 严守 |
| ffmpeg 统一接入 run_ffmpeg | 所有重编码路径均经 `run_ffmpeg` / `run_ffmpeg_with_fallback` | ✅ 严守 |

### P1（6 项，本轮复核）

| 项 | 核查证据 | 结论 |
|----|---------|------|
| P1.1 NVENC 回退收窄 | `core/ffmpeg_runner.py:325`：`if params[0] != "libx264" and is_session_limit(e.stderr) and not e.timed_out` —— 仅会话受限且非超时/非用户中断才回退 | ✅ 落地（推翻了"盲目重试"） |
| P1.2 face_detection 崩溃修复 | `gui/face_detection_tab.py`：`folder_input` 已用 `PathRow(MODE_FOLDER)`，无裸 `QLineEdit._browse` | ✅ 落地 |
| P1.3 VIDEO_EXTS 单点化 | 全部 app 模块引用 `utils.media_utils.VIDEO_EXTS`；仅 `scripts/compare_media.py:215`（非 app 脚本）残留硬编码 | ✅ 落地 |
| P1.4 audio_mix 停止复位 + BaseWorker 兜底 | `gui/audio_mix_tab.py:50-62`：停止分支正确 emit `finished([])`；`base_worker.py:46-50`：run() finally 兜底 emit finished | ✅ 落地 |
| P1.5 仓库卫生 | `.gitignore` 已含 `config.local.json`/`taobao_auth.json`/`*.log`；`core/sherpa_asr_error.log` 已停止跟踪 | ✅ 卫生部分落地 |
| P1.5 仓库卫生（依赖校准） | ⚠️ **见下方"与完成报告不符"** | ⚠️ **未落地** |
| P1.6 跨 tab 进程互杀修复 | `core/video_fission.py:56` 设 `self._owner`；`:71` 调 `terminate_owner(self._owner)`；`:158` `run_ffmpeg(..., owner=self._owner)`；`ffmpeg_runner.py:89` `terminate_owner` 仅杀同组 | ✅ 落地 |

### P2（6 项，本轮复核）

| 项 | 核查证据 | 结论 |
|----|---------|------|
| P2.1 CosyVoiceService.stop() | `core/voice_clone.py:155` `def stop(self)` 终止服务进程 + `finally` 清残留 | ✅ 落地 |
| P2.2 开拍云移 worker | `gui/kaipai_cloud_tab.py:26` `class KaipaiWorker(BaseWorker)`：下载在 worker 线程，不阻塞 GUI | ✅ 落地 |
| P2.3 demucs 接入 track_proc | `core/audio_utils.py:41-52`：`subprocess.Popen` + `track_proc` + `CREATE_NO_WINDOW` + 超时 kill + `untrack_proc` | ✅ 落地 |
| P2.4 text_detector 改 lazy import | `core/text_detector.py:12,96`：`from paddleocr import PaddleOCR` 在函数内；顶层仅 `os/glob/ffmpeg_runner` | ✅ 落地（启动不再被 paddleocr 拖慢） |
| P2.5 model_dirs 常量收口 | `core/model_dirs.py` 新建；`keyword_remove_tab.py:17`、`subtitle_tab.py:23` 已改引用，切断 gui→gui 耦合 | ✅ **部分落地**（仅 3 个 ASR 目录；D:\Models 散落未根治，见问题 R2） |
| P2.6 video_utils 下沉 core | `core/video_utils.py` 存在；`keyword_remove_tab.py:93` `import core.video_utils as vu`；grep 全仓无 `utils.video_utils` 残留 | ✅ 落地（反向依赖已消除） |

### ⚠️ 与完成报告口径不符之处（需您注意）

1. **P1.5「requirements 校准」实际未落地**：完成报告（`p1-p2-completion-2026-08-20.md`）声称已把 `torch/torchaudio/demucs` 移出主依赖、补 `sherpa-onnx`。但**实地读 `requirements.txt` 现状**仍是：
   ```
   paddlepaddle>=2.4.0      # 重
   paddleocr>=2.6.0
   onnxruntime-directml>=1.20.1
   demucs>=4.0.0            # 重，且依赖 torch
   torch>=2.0.0            # 重
   torchaudio>=2.0.0
   ```
   - `torch/torchaudio/demucs` **仍强制安装**（未移到 `requirements-optional.txt`）；
   - `sherpa-onnx` **仍缺声明**，但 `core/sherpa_asr.py:12` 在**模块顶层** `import sherpa_onnx` —— 新电脑照 `requirements.txt` 装会**直接 import 失败**，字幕页的 SenseVoice（SherpaASR 路径）跑不起来。
   - 根目录也**无** `requirements-optional.txt` 文件。
   → 这是 D1 债**仍开着**，且是唯一的"真实运行/安装风险"。

---

## 三、残余问题清单（文件:行号 + 根因 + 建议 + 风险 + ROI + 处置）

> 处置标签：**该做** / **暂缓** / **不该做**。所有"该做"均遵守"反对过度工程"边界（不碰业务铁律、不引入 ConfigStore/DI/插件化/Web/asyncio/重型 CI）。

### R1 · `requirements.txt` 未校准（依赖收口）—— 该做（中 ROI，阻塞于 Q2）

- **证据**：`requirements.txt:2-9`（见上）；`core/sherpa_asr.py:12` 顶层 `import sherpa_onnx`（缺声明）。
- **大白话**：换电脑重装时，要么卡在 torch 的 `shm.dll` 报错（您之前踩过的坑），要么字幕 SenseVoice 因缺 `sherpa-onnx` 直接打不开。
- **根因**：P1.5 的"依赖校准"子项只做了 .gitignore，漏做 requirements。
- **建议**：① 补 `sherpa-onnx` 到主依赖；② 把 `torch`/`torchaudio`/`demucs`/`paddlepaddle`/`paddleocr` 中"仅可选功能才用"的移到 `requirements-optional.txt` 并加注释（具体哪些移，取决于 Q2 您的答复）；③ 评估 `paddlepaddle/paddleocr` 是否也能降为可选。
- **风险**：低。**ROI**：高（直接决定"换机能不能一键装好"）。
- **处置**：**该做**，但等 Q2 答复后定"哪些移可选"。

### R2 · `D:\Models` 路径硬编码散落（约 10 处）—— 该做（低优先，低 ROI）

- **证据**：
  - `core/fireredasr.py:12`（重复定义 `FIREMODELS_DIR`）、`:400` `vad_path = r"D:\Models\sherpa-onnx\silero_vad.onnx"`
  - `core/onnx_asr.py:31`、`core/sherpa_asr.py:32`（各自重复 `_models_dir = r"D:\Models\sherpa-onnx"`）
  - `core/screenshot.py:376,454`、`gui/screenshot_tab.py:46`、`gui/face_detection_tab.py:105`（scrfd 模型路径）
  - `gui/settings_tab.py:302,306`、`gui/voice_clone_tab.py:19,236,238`、`core/voice_clone.py:82`（CosyVoice 根目录）
  - 端口 `50051` 重复：`core/voice_clone.py:82` 与 `services/cosyvoice_server.py:63`
- **大白话**：模型默认装在哪个盘、端口用哪个，现在在十几个文件里各写一遍。哪天您把模型挪到别的盘，得改十几个地方，漏一个就找不到模型。
- **根因**：P2.5 只把"3 个 ASR 目录"收口到 `model_dirs.py`，但**其余模型路径 + 端口仍散落**。
- **建议**：扩展 `core/model_dirs.py` 收口所有模型根目录常量 + 端口常量，散落处改为引用；并把"默认路径"与"用户在设置里填的路径"彻底分开（设置里有 UI 入口的优先用设置值）。
- **风险**：低（不影响当前能用，只影响挪模型/换端口时）。**ROI**：低-中。
- **处置**：**该做（低优先）**，可作为一轮轻量清理。

### R3 · crf 默认值 20 / 23 分裂—— 该做（极低优先）

- **证据**：`core/encoder.py:14 DEFAULT_CRF = 20`；`core/ffmpeg_runner.py:309 run_ffmpeg_with_fallback(crf=20 默认)`；但真实调用方几乎全部显式传 `crf=23`（`subtitle_tab.py:153`、`keyword_remover.py:340`、`video_concatenator.py` 三处、`video_resizer.py:111`、`video_utils.py` 多处）；`config.json:123 video_fission.crf="20"`（裂变默认 20）。
- **大白话**：同样一个"压画质"的参数，有的地方默认 20（画质稍高、体积稍大），有的地方默认 23（画质稍低、体积稍小）。现在靠每个功能"手动写 23"才保持一致，万一哪个新功能忘了写，就偷偷用了 20。
- **根因**：历史遗留，裂变用 20、其余统一 23，但默认值常量没对齐。
- **建议**：把 `DEFAULT_CRF` 与 `run_ffmpeg_with_fallback` 默认值统一为 `23`（与绝大多数调用一致）；若裂变确实想保留 20，在裂变里显式传 `crf=20` 并加注释固化意图。
- **风险**：极低（仅影响输出体积/画质的微小一致性）。**ROI**：低。
- **处置**：**该做（极低优先）**，顺手改。

### R4 · worker 基类约定未贯彻（11/14 个 worker 覆盖 `run()` 而非 `work()`）—— 暂缓（维护性收益）

- **证据**：`BaseWorker.run()`（`base_worker.py:30-50`）设计意图是"子类实现 `work()`，run() 统一兜底异常 + 停止复位"。但实测仅 3 个 worker 用 `work()`（`screenshot_tab.py:37`、`slice_tab.py:67`、`video_resize_tab.py:33`）；其余 **11 个覆盖 `run()` 自管**（`audio_mix/face_detection/keyword_remove/kaipai/subtitle/text_recognition/video_concat/video_download/video_fission/video_enhance/voice_clone`）。
- **大白话**：线程基类的"安全网"（万一忘了发完成信号就自动复位 UI）只罩住了 3 个功能，其余 11 个功能得自己写全套"成功/失败/停止"的信号发送。好在它们现在都自己写对了，所以**目前没 bug**；但这套"有的用 A 写法、有的用 B 写法"不齐整，以后加新功能容易照错样板。
- **根因**：历史代码先写，基类后提炼；迁移时只把契约对齐到"都继承 BaseWorker + 都发三信号"，没强制统一到 `work()`。
- **建议（暂缓）**：不必立刻改；若日后动到某个 tab 的后台逻辑，顺手把它从"覆盖 run()"迁到"实现 work()"，让 P1.4 的安全网真正覆盖全部。或至少在 `BaseWorker` 文档里**固化"覆盖 run() 也必须自管 finished/error/停止复位"的约定**，避免后人踩坑。
- **风险**：当前低（自管正确）；长期维护性中等。**ROI**：中（纯维护性，不影响功能）。
- **处置**：**暂缓**（等自然迭代时顺手做，不必专门排期）。

### R5 · 字幕页 Whisper 独立进程未被进程表追踪—— 该做（低优先）

- **证据**：`gui/subtitle_tab.py:261 subprocess.run([sys.executable, _whisper_transcribe.py, ...])` 启动 Whisper 独立 Python 进程；未走 `track_proc`，停止 tab 时杀不到它（最长可跑 7200s）。
- **大白话**：用 Whisper 模型识别字幕时，会额外起一个后台 Python 进程；您点"停止"时，主线程停了，但这个 Whisper 子进程可能还在后台默默跑（直到跑完或超时）。
- **根因**：Whisper 走独立脚本进程，接入时没挂进全局进程表。
- **建议**：把该 `subprocess.Popen` 改用 `track_proc(proc)` 登记（owner 用本 tab 标识），停止时一并终止；或至少记录 pid 在 `stop_mixing` 类路径里 kill。
- **风险**：低（最多多跑一会儿，不崩溃）。**ROI**：低。
- **处置**：**该做（低优先）**。

### R6 · OllamaChecker 仍是裸 QThread（非 BaseWorker）—— 暂缓

- **证据**：`gui/subtitle_tab.py:504 class OllamaChecker(QThread)`、`:518 self._ollama_checker = OllamaChecker()`（作为 tab 属性，会被 `stop_tab_threads` 扫到）。无 `stop/request_stop` → 关窗时 `wait(1500)→terminate`，无害。
- **大白话**：字幕页里有个小检查线程没用统一基类，但它是一次性快速检查，关窗时会被安全终止，不影响。
- **建议**：暂缓；若顺手，可让它继承 `BaseWorker` 或直接用 `QThread` 的既有约定即可。
- **处置**：**暂缓**。

### R7 · 5 套 ASR 并存 + SenseVoice 双实现—— 该做（阻塞于 Q1）

- **证据（代码层）**：
  - `core/fireredasr.py`、`core/funasr_onnx.py`、`core/sensevoice_onnx.py`、`core/onnx_asr.py`（分发器）、`core/sherpa_asr.py` 共 5 个模块。
  - **两套 SenseVoice 并存且底层不同**：
    - 字幕页 `subtitle_tab.py:67-69` 走 `SherpaASR`（`core/sherpa_asr.py`，基于 `sherpa_onnx` 库）；
    - 去关键词页 `keyword_remove_tab.py:51-52` 走 `OnnxASR`（`core/onnx_asr.py` → `core/sensevoice_onnx.py`，纯 `onnxruntime`）。
  - `sherpa_asr.py` 仅在字幕页使用；`onnx_asr` 是统一分发器（FunASR/FireRed/SenseVoice 都走它）。
- **大白话**：您其实只想"把视频里的语音变成字幕/能找关键词"，但仓库里躺着 5 套识别引擎、其中"SenseVoice"还写了两遍（一套用 A 库、一套用 B 库）。以后想升级识别能力，得改好几个地方。
- **收敛可行性（代码层，不替您答 Q1）**：
  - 字幕页若改走 `OnnxASR`（与去关键词页统一），即可**删除 `sherpa_asr.py`**（约 250+ 行）并**卸掉 `sherpa-onnx` 依赖**（呼应 R1）；改动局限在 `subtitle_tab.py` 的模型分支 + 设置项 UI，影响面可控。
  - `funasr_onnx` / `fireredasr` 是否保留，取决于您日常用哪个模型识别（Q1）。
  - **技术风险**：中——必须做"模型路径 + 识别效果"回归，且**不碰**输出规格等业务铁律。
- **处置**：**该做，但等 Q1 答复后执行**。

### R8 · taobao_downloader.py 1437 行巨石—— 暂缓

- **证据**：`core/taobao_downloader.py`（文件仍约 1437 行，淘宝+抖音双站、URL 解析+浏览器会话+多种抽取+下载编排全塞一处）。
- **大白话**：下载功能的所有逻辑挤在一个大文件里，出 bug 时大海捞针。
- **建议**：暂缓；若做，仅"文件内分区注释 + 把抖音簇抽到 `core/douyin_downloader.py`"，**不做微服务式拆分**（Playwright 选择器很脆，先搬不动）。
- **处置**：**暂缓**（属重活，等前面轻量项落地）。

### R9 · 最小工程化保障（冒烟/自检脚本）—— 暂缓

- **证据**：无 `tests/`、无 `*.spec`、无 `pyproject.toml`、无 `scripts/smoke_import.py`/`check_env.bat`（规划里 P3 项）。
- **建议**：暂缓；可加一个轻量 `scripts/smoke_import.py`（import 所有 tab/core 验证不缺依赖）+ `check_env.bat`（查 ffmpeg/python/关键依赖）。**不引入 pytest 全套 / 不搭 CI**（过度工程）。
- **处置**：**暂缓**（按边界，不堆重型测试框架；轻量冒烟脚本可视情况补）。

### 已解决（本轮确认，不再计债）

- ✅ **wink 8 元组（原 D9）**：`core/wink_enhancer.py:286-292` 的 `process()` 现返回**字典**（`success/output/beans/elapsed/error`），非 8 元组。已解决。
- ✅ **启动慢（paddleocr 顶层传染）**：`text_detector.py` 已 lazy import。已解决。
- ✅ **VIDEO_EXTS 散落**：仅非 app 脚本 `scripts/compare_media.py:215` 残留，app 内已全收口。基本解决。
- ✅ **utils→core 反向依赖**：`video_utils` 已下沉 `core/`。已解决。

---

## 四、ASR 收敛可行性评估（代码层，仅给"答完后如何做"，不替您拍板 Q1）

- **当前调用面**：
  - 字幕页：`SenseVoice`→`SherpaASR`（sherpa_onnx 库）；`FunASR/FireRed`→`OnnxASR`；`Whisper`→独立进程 `_whisper_transcribe.py`。
  - 去关键词页：`FunASR/FireRed/SenseVoice`→`OnnxASR`（onnxruntime）。
  - 设置页（`settings_tab.py`）暴露 FireRed/FunASR/SenseVoice/Whisper 模型路径配置。
- **收敛路径（若 Q1 决定删 sherpa 系）**：
  1. `subtitle_tab.py` 的 `SenseVoice` 分支由 `SherpaASR` 改为 `OnnxASR`；
  2. 删除 `core/sherpa_asr.py`；
  3. `requirements.txt` 去掉 `sherpa-onnx`（并入 R1）；
  4. 设置页 UI 移除 Sherpa 相关字样（如有）。
  - 影响面：字幕页 + 设置页 + 依赖清单，约 3 处，可控。
- **若 Q1 还要删 FunASR/FireRed**：保留 `onnx_asr` 作为唯一分发器，删对应模块 + 设置项；但需确认您日常识别确实不用它们（否则误删会断功能）。
- **结论**：技术上收敛**完全可行且低风险**，瓶颈只在"您用哪几个"的业务决策（Q1），不在架构。

---

## 五、待您决策的 3 个问题（给出"等答复后再定"的建议，不替您拍板）

- **Q1（决定 R7 / ASR 收敛范围）**：您日常"字幕"和"去关键词"**实际用哪几个 ASR 模型**？FireRedASR / FunASR / SenseVoice / Whisper 里哪些可确认不用、允许删？尤其注意：字幕页的"SenseVoice"（sherpa_onnx 库）和去关键词页的"SenseVoice"（onnxruntime）**底层不是同一套**——您两个都在用吗？还是只用一个？**建议**：若只用一个，优先保留 `onnx_asr` 那套（纯 onnxruntime，依赖更轻），删掉另一套。
- **Q2（决定 R1 / 依赖收口）**：换电脑/重装时，`torch`（shm.dll 报错）、`demucs`、`paddlepaddle`（paddleocr）是不是主要痛点？您能否接受把"音色复刻、Whisper、人声分离、文字识别"标成**需单独装依赖的可选功能**（主依赖不强制装 torch/paddle）？**建议**：能接受的话，主依赖只留 PyQt5 + onnxruntime-directml + Pillow + numpy + qt-material，其余进 `requirements-optional.txt`，装机会省心很多。
- **Q3（决定 R3 / 铁律收口）**：是否把"所有**本地**重编码都走 `get_encoder(crf=23)`"继续收紧——**澄清**：`video_enhance`（视频优化）走的是美图 Wink **云端**处理，**本机不做 ffmpeg 重编码**，所以本质上不存在"没走 get_encoder"的问题（它压根不调 ffmpeg）。真正要收的是 `crf` 默认值对齐（R3）。**建议**：把 `DEFAULT_CRF` 与 `run_ffmpeg_with_fallback` 默认统一为 23（与绝大多数功能一致），裂变若想保留 20 就显式传参并注释。

---

## 六、明确不建议做的事（防止过度工程，与既有边界一致）

1. **不引入 ConfigStore**——`config.json` + `get_config/set_config` 对单机小工具已够；引入是过度设计。
2. **不引入 DI 框架 / 插件化 tab**——`tab_registry` 单点注册已足够。
3. **不改造 Web / 微服务 / 多进程**——它就是一台电脑上跑的桌面程序。
4. **不引入重型测试框架 / 完整 CI**——最多加个轻量冒烟脚本（R9），不堆 pytest/Jenkins/GitHub Actions。
5. **不用 asyncio 重写线程**——`BaseWorker(QThread)` + 协作式停止工作良好。
6. **不碰业务铁律**：9:16 / 1080×1920 / `setsar=1` / 滤镜链冻结 / 编码必须走 `get_encoder`。本报告所有建议均不涉及它们。

---

## 七、给主理人的汇总（用于回传用户）

**核心结论（TL;DR）**：架构**地基已稳，不需要大改**；当前是"减重+收口"阶段，**没有会立刻出事的危机债**。上一轮修的真 bug（跨 tab 互杀、NVENC 盲目重试、face_detection 崩溃、audio_mix 卡死）经实地读码**确认都已落地**。

**该做清单（按 ROI，多数低优先、可一轮轻量清理）**：
1. R1 校准 `requirements.txt`（补 `sherpa-onnx`、把 torch/demucs/paddle 按需降为可选）—— 阻塞于 Q2，中 ROI；
2. R2 把 `D:\Models` 与端口 `50051` 散落的约 10 处硬编码收口到 `model_dirs.py` —— 低优先；
3. R3 把 `crf` 默认 20/23 对齐为 23 —— 极低优先、顺手改；
4. R5 字幕 Whisper 独立进程纳入进程表追踪（停止能杀）—— 低优先；
5. R7 ASR 收敛（删 sherpa 系）—— 阻塞于 Q1，技术可行低风险。

**暂缓清单**：R4 worker 约定贯彻（自然迭代顺手做）、R6 OllamaChecker、R8 taobao 巨石分区、R9 轻量冒烟脚本。

**不该做清单（违反边界的过度工程，一律不做）**：ConfigStore、DI/插件化、Web/微服务化、重型测试/CI、asyncio 重写线程、改动业务铁律（9:16/1080×1920/setsar=1/滤镜链/get_encoder）。

**一句话给用户的建议**：可以放心继续用；真要优化，先做"依赖清单收拾干净"（决定换机能不能一键装好）这一件最有价值，其余都是锦上添花。**3 个待决策问题（Q1 用哪几个语音模型 / Q2 能否把重依赖降为可选 / Q3 crf 默认值）答复后，再推进对应清理。**

---

## 八、附录：本轮实地核查要点

- ✅ 编码铁律严守：全仓 grep `libx264` 仅出现在 `encoder.py`/`ffmpeg_runner.py`/`video_fission.py` 的**合法回退逻辑**；无任何 tab 裸 `subprocess` 调 ffmpeg 做重编码。
- ✅ 线程/停止协议：16 tab 全继承 `BaseTab`、worker 全继承 `BaseWorker`；`video_fission` 已用 owner 分组终止（P1.6）；`closeEvent` = `stop_tab_threads` + `kill_all_ffmpeg` 稳妥。
- ✅ 反向依赖已消除：`video_utils` 下沉 `core/`，无 `utils.video_utils` 残留；`keyword_remove` 已改引用 `core.model_dirs`（gui→gui 耦合切断）。
- ✅ VIDEO_EXTS 已单点化（app 内零硬编码 5 元组）。
- ✅ lazy import 已落地：paddleocr（text_detector）、sherpa_onnx（onnx_asr/fireredasr 内）、torch（cosyvoice_server/_whisper_transcribe 内）均延迟加载，启动不吃重。
- ✅ wink 8 元组已改为字典返回。
- ⚠️ **P1.5 的 requirements 校准实际未落地**（torch/demucs 仍强制、`sherpa-onnx` 仍缺声明）—— 唯一真实"安装/可复现"风险，对应 R1。
- ⚠️ `D:\Models` 与端口 `50051` 仍硬编码约 10 处（P2.5 只收口了 3 个 ASR 目录）—— 对应 R2。
- ⚠️ worker `run()`/`work()` 约定未贯彻：11/14 worker 覆盖 `run()` 自管，BaseWorker 的 finished 兜底安全网只覆盖 3 个 —— 当前无 bug，对应 R4。
