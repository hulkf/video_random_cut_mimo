# 三份架构评审交叉验证结论（2026-08-20）

> 范围：round-2 实地评审（bdb7b94）+ GLM 迁移完整度分析 + 千问体检报告 v2
> 结论速览：**round-2「无 P0、地基稳」整体成立，但被千问推翻一处**（fallback 盲目重试是真 P0，round-2 误当正面 feature）；**GLM 补了 round-2 漏看的 4 个细节 bug/债**（face_detection 崩溃、VIDEO_EXTS 散落、utils→core 反向依赖、keyword_remove 跨 tab 耦合）。三份交叉验证后，**真正该立刻修的是 5 项低风险高价值修复**，重活（ASR 收敛 / taobao 拆分）先按住。

---

## 一、2.3 / 2.5 核实结果（本次落定）

| 项 | 千问指控 | 核实证据 | 判定 |
|---|---|---|---|
| **2.3 CosyVoice 进程泄漏** | `CosyVoiceService` 只有 start 无 stop；`closeEvent` 不清理；端口写死 50051 | `core/voice_clone.py:80-153` 确无 `stop` 方法；`gui/main_window.py:111-123` 只 `stop_tab_threads`+`kill_all_ffmpeg`，无 CosyVoice 清理；`port=50051` 为 `__init__` 默认参数 | **属实**，但严重度打折 → 列 P1 而非 P0 |
| **2.5 audio_mix 停止不复位** | 停止路径不 emit finished/error，BaseTab 清理只挂 finished → start_btn 永久禁用 | `gui/audio_mix_tab.py:50-54` 在 `self.stopped()` 为 True 时**既不 emit finished 也不 emit error**；`gui/common/base_tab.py:44-77` 的复位（set_busy + self.worker=None）全挂在 finished/error 信号上 → 停止后 UI 卡死 | **属实，真 bug** |

> 2.5 根因比「audio_mix 缺 partial_results」更本质：**停止路径不 emit 任何信号**。所有继承 BaseTab 的 worker 都不该这么写；根治应在 `BaseWorker.run` 层用 try/finally 兜底 emit 清理，而非每页各加一个 partial_results。

---

## 二、三份评审交叉对照

| 问题 | round-2 口径 | GLM 口径 | 千问口径 | 我的判定 |
|---|---|---|---|---|
| fallback 盲目重试（千问 2.1） | 当正面 feature（NVENC 探测+回退） | 未专门提 | 新引入 P0（捕获**所有** FFmpegError 含超时，全局废 NVENC+超时重跑） | **千问更准**，round-2 漏看过度激进面 |
| 跨 tab 进程互杀（千问 2.2） | 未报（把 terminate_all 当关窗兜底） | 未提 | 未修 P0（_procs 全局 set，request_stop 杀所有 tab） | **属实** |
| face_detection 崩溃 bug | 漏看（半成品未识别为 bug） | 挖出：`_browse()` 在 QLineEdit 上必 AttributeError | 未提 | **GLM 挖得对**，实打实 crash |
| VIDEO_EXTS 散落 13 处 | 漏看 | 提出并要求收口 | 未提 | **属实**，单一可信源收口 |
| utils→core 反向依赖 | 提过（分层 6 分） | 点名 `video_utils.py` import ffmpeg_runner | 未提 | **属实**，分层违规 |
| keyword_remove 跨 tab 耦合 | 未报 | 点名 import subtitle_tab 常量 | 未提 | **属实**，解耦到公共层 |
| 5 个 ASR 并存（~2116 行） | P1 | 认同 | P2 | 一致认为债，收敛方向明确 |
| 硬编码路径（D:\Models / 50051 / VIDEO_EXTS） | 部分（P1 提模型路径） | 部分 | P1/P2 都提 | **三份一致**，收敛进 settings |
| 仓库卫生（.gitignore / requirements / services 未入库 / 根目录杂物） | 未细看 | 提过 | 提过（P0 级卫生） | 快修，半小时搞定 |
| 启动慢（paddleocr 顶层 import 传染 / tab 全量实例化） | P2 | 未提 | P2 | 一致，懒 import 解决 |
| ConfigStore 缺口 | 未提 | **主张是缺口** | 未提 | **反对**：单机小工具过度设计 |
| 开拍云主线程网络阻塞（千问 2.4） | 未报 | 未提 | 未修 | 体验债，用户日常高频 |

---

## 三、建议行动顺序

### 立刻做（半小时，低风险高价值）
1. **2.1 fallback 收窄**：仅在 NVENC 会话受限（`is_session_limit` 类错误）时回退；`timed_out=True` 与被用户终止的进程**绝不重试**（当前最伤，让超时/停止保护失效）
2. **face_detection `_browse` 崩溃 bug**：换 `PathRow` 或补 `_browse`（点浏览必崩）
3. **VIDEO_EXTS 13 处收口**：统一 `from utils.media_utils import VIDEO_EXTS`，删硬编码字面量
4. **2.5 audio_mix 停止不复位**：worker 停止时也 emit（或在 `BaseWorker` 层 try/finally 兜底）
5. **仓库卫生**：`.gitignore` 补 `taobao_auth.json` + `*.log`、`git rm --cached` 日志、提交 `services/` 与安装脚本、`requirements.txt` 校准（补 sherpa-onnx / requests / playwright）

### 本周（中等风险）
- **2.2 跨 tab 进程互杀**：`_procs` 任务级隔离（每任务传 token/组，terminate 只杀自己的）
- **2.3 CosyVoice stop + closeEvent 接入**（本机 torch 不可用基本跑不起来，优先级低，整理 voice_clone 时一并做）
- **2.4 开拍云主线程网络阻塞**：下载移 worker 流式，避免批量冻结 GUI

### 重活（先按住，别一次全做）
- 5 个 ASR 收敛到 1 个（删约 2000 行）
- `taobao_downloader.py` 1437 行巨石拆分（提取器表）
- 统一 logging 框架、fireredasr 内存分块、输出覆盖策略统一

---

## 四、反对 / 修正的点
- **GLM「ConfigStore 是缺口」**：反对。config.json + get_config/set_config 对单机小工具已够，引入 ConfigStore 是过度设计。
- **千问把 2.3 列 P0**：修正为 P1。voice_clone 是需额外装依赖（conda+torch）的可选功能，本机 torch 不可用基本跑不起来，日常不触发。
- **round-2 把 fallback 当 feature**：已被千问推翻，站千问。

---

## 五、待用户确认的 3 个问题（仍未答）
- **Q1**：字幕 / 去关键词实际用哪几个 ASR 模型？哪些可删？两套 SenseVoice（sherpa_asr vs sensevoice_onnx）是否都在用？
- **Q2**：torch / demucs 是否重装痛点？音色复刻 / Whisper 能否标为「需额外装依赖的可选功能」？
- **Q3**：是否把「所有重编码必须走 get_encoder」收紧到 100%（含 video_enhance）？

---

## 六、一句话总结
地基稳（信号契约 / 注册表 / BaseTab 清理 / 编码铁律 0 处硬编码 libx264 / 0 处裸 subprocess 调 ffmpeg 都守住了）；但 **fallback 盲目重试、face_detection 崩溃、VIDEO_EXTS 散落、audio_mix 停止卡死** 这 4 个是当前最该先修的「小而痛」问题，半小时能全搞定。重活（ASR 收敛 / taobao 拆分）等上述落地后再说。
