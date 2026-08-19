# 增量重构 PRD（P0 + P1）— video_random_cut_mimo

> 版本：v1.0 ｜ 作者：许清楚（产品经理）｜ 日期：2026-08-19
> 文档类型：增量 PRD（只覆盖本次 P0+P1 变更范围，不含全量需求）

## 0. 项目信息

| 项 | 内容 |
|---|---|
| 语言 | 中文 |
| 技术栈 | Python 3.12（D:/Anaconda/python.exe）+ PyQt5 + qt-material + onnxruntime-directml；ffmpeg（系统 PATH） |
| 平台 | Windows；22 核 CPU + NVIDIA NVENC |
| 项目名 | video_random_cut_mimo（启动 `python main.py`） |
| 原始需求 | 修复标签页注册硬编码 bug（P0）；把「视频裂变」已验证的 NVENC 硬件编码/中断/并发经验抽成公共层（core/encoder.py、core/ffmpeg_runner.py、utils/media_utils.py、utils/path_utils.py），并将硬件编码推广到其余 5 个重编码模块（P1），实现全模块 2~6 倍提速 |
| 约束 | 只读分析 + 文档产出，**不修改任何源码**；重构期间项目必须保持可用；每次变更后立即 git commit + push |

---

## 1. 背景与目标

**一句话**：项目功能齐全但"功能先行、无公共层"——唯一工程化成熟的「视频裂变」模块（NVENC 自动探测 + ThreadPoolExecutor 并发 + 引擎级中断）能力被锁死在文件内部，其余模块仍在串行跑 libx264 软件编码，22 核 + 独显机器性能被浪费；同时 main_window 的标签页注册散落 4 处且已不同步，存在关窗清理遗漏的隐患。

**成功标准（可量化）**：

| # | 指标 | 目标值 | 测量方式 |
|---|---|---|---|
| G1 | 试点「视频尺寸」模块单文件处理耗时 | **≥ 2×**（NVENC vs libx264，同机同素材） | 迁移前后各跑同一批素材计时 |
| G2 | 全部 5 个重编码模块单文件提速 | **≥ 2×**（预期 2~6×，保守验收 ≥2×） | 同上，逐模块对比 |
| G3 | 迁移模块输出行为一致性 | 分辨率 / 宽高比 / **SAR=1:1** / 时长 / 帧率与迁移前一致，可播放 | ffprobe 逐项对比 |
| G4 | 裂变模块行为不倒退 | 同一批素材耗时在基线 ±10% 内；中断/partial_results/清元数据/随机时间戳全部保留 | 回归对照 |
| G5 | 可维护性 | 新增/删除标签页只改**一处**注册表；格式列表、路径工具、编码策略**单点维护** | 代码审查 |
| G6 | 可用性 | 重构每一步提交后 `python main.py` 可启动，16 个 tab 全部可用 | 冒烟 |

---

## 2. 范围清单

### 2.1 本次做（P0 + P1）

#### P0 — main_window 标签页注册重构（顺手修现存 bug）

**现状（已读码确认，gui/main_window.py）**：
- 一个 tab 要改 **4 处**：import（L3-18）、实例化（L118-133）、addTab（L135-150）、closeEvent 清理列表（L154-162）。
- closeEvent 清理列表 **15 项，漏了 `settings_tab`**（addTab 有 16 项）；且列表顺序与 addTab 不一致，纯手写平行列表必然漂移。
- 同类隐患：`video_download_tab` 有 **两个** QThread 属性 `self.worker` 与 `self.login_worker`（gui/video_download_tab.py:133-134），closeEvent 只取 `worker`，`login_worker` 会泄漏导致进程不退出。

**改动**：
- 收敛为单一注册表（如 `TABS = [(key, "标题", factory), ...]`），import/实例化/addTab/closeEvent 全部由注册表驱动。
- closeEvent 清理：遍历注册表内所有 tab 的**所有 QThread 类型属性**（`vars(tab)` 过滤 `isinstance(v, QThread)`），覆盖 `worker` 与 `login_worker` 等全部后台线程；保留 stop → wait(1500) → terminate 的既有顺序。
- 注意 `SettingsTab(app)` 需要 app 参数 → 注册表用工厂函数（lambda）而非裸类。

**验收**：见 4.1 AC-P0 组。

#### P1-1 — core/encoder.py（编码策略，核心收益来源）

- 从 `core/video_fission.py:68-116` 上提：NVENC 自动探测（`ffmpeg -encoders` 含 h264_nvenc + 0.3s testsrc 试编码）、软件回退、默认并发数（NVENC=3 / 软件=min(核数,8)）。
- 对外提供**模块级**接口（带缓存）：
  - `get_encoder(crf=20, preset="ultrafast") -> (codec, preset, quality_args)`
  - `get_default_workers() -> int`
  - `fallback_to_software()` / 供会话受限时强制回退（或返回策略对象 EncoderPolicy，由实现者定，接口语义不变）。
- `video_fission.py` 改为调用公共接口，删除自身 `_probe_encoder/_test_hardware/encoder/default_workers/fallback_to_software` 内联实现（行为不变）。
- **明确不做**：QSV 探测（本环境实测 QSV 慢于软件，裂变已注释排除）、多编码器轮询。

#### P1-2 — core/ffmpeg_runner.py（统一 subprocess 封装）

- 统一封装：`run_ffmpeg(cmd, *, timeout, on_progress=None, track_procs=None, ...)`。
- 能力清单：
  - Windows 下 `CREATE_NO_WINDOW`（杜绝黑窗闪烁，现状所有 subprocess 均未带）；
  - 返回码检查 + stderr 错误提取（保持各模块现有 RuntimeError 文案风格）；
  - 进程追踪与 `terminate()`（承接裂变的 `request_stop` → 杀全部 ffmpeg 语义）；
  - 可选 `-progress pipe:1` 进度回调（**P1 只提供能力，UI 进度接入属 P2**）；
  - 超时 kill + **清理半成品输出**（现状 video_utils 超时后不清理，属行为改进）；
  - 失败时删除残留半成品（对齐裂变现有行为）。
- `video_fission.py` 的 Popen 管理逻辑迁入 runner（`_track_proc/_untrack_proc/_procs_lock`），裂变保留自身中断编排。

#### P1-3 — utils/media_utils.py（媒体探测/收集/格式常量单点化）

- 合并项（已读码确认的现状分布）：
  - `VIDEO_EXTS`：core/video_resizer.py:6、core/keyword_remover.py:9、core/mixer.py:14、core/video_concatenator.py:34（inline）、core/screenshot.py:439（inline）、core/slicer.py:12/36/121（inline）、core/video_mixer.py:37/46（inline）、GUI 层 5 个文件 inline（face_detection_tab/screenshot_tab/slice_tab/subtitle_tab/text_recognition_tab）——评审统计全项目 **19 处重复**；
  - `probe_video`：core/video_resizer.py:28、core/video_concatenator.py:56（`_probe_video`）、utils/video_utils.py:96（`_probe_video_profile`，含宽高/编码/像素格式/帧率）；
  - `get_video_duration`：utils/video_utils.py:8（keyword_remover、video_concatenator 等依赖）；
  - `collect_videos`：core/video_resizer.py:19、core/keyword_remover.py:18、core/video_concatenator.py:32（`get_videos`）、core/screenshot.py:439。
- 新文件 `utils/media_utils.py` 单点定义以上四者；本次迁移 **core 层 5 处**（video_resizer / video_concatenator / keyword_remover / screenshot / mixer / video_fission 改 import）。
- 兼容策略：`utils/video_utils.get_video_duration` 保留为一行转发（`from utils.media_utils import get_video_duration`），避免波及未迁移调用方；不重复实现。
- **本次不迁移**：GUI 层 inline 元组与 `core/wink_enhancer.py:55` 的 8 元组扩展列表（改动面大且非本次提速路径，后置 P2；届时统一决策扩展列表是否并入公共常量）。

#### P1-4 — utils/path_utils.py（路径工具单点化）

- 合并项（已读码确认）：
  - `gui/video_fission_tab.py:57` `strip_quotes`；
  - `core/video_concatenator.py:12` `normalize_input_path`（同为 strip + 剥首尾引号，两份逻辑等价）；
  - `core/video_fission.py:339` inline 剥引号（`p.strip().strip('"').strip("'")`）。
- 输出路径构建器：统一命名后缀 + 防重名（现状散落：video_resizer.py:146、gui/video_resize_tab.py:72-77、video_concatenator.py:389、video_fission.py:385-392 等）。P1 仅沉淀**公共函数**（如 `build_output_path(dir, rel_base, suffix, ext=".mp4", dedupe=True)`），试点模块接入；其余模块命名后置 P2 统一。

#### P1-5 — 替换 14 处硬编码 `libx264 -preset ultrafast -crf 23`

已读码确认全部位置（共 14 处）：

| 文件 | 行号 | 处数 | 说明 |
|---|---|---|---|
| utils/video_utils.py | 41, 58, 74, 133, 155, 198, 221, 296 | 8 | cut_video / cut_video_fast / cut_video_no_audio / _blur_pad_video ×2 / image_to_video ×2 / concat_videos |
| core/video_concatenator.py | 209, 232, 251 | 3 | concat_pair / _concat_video_only 归一化 / concat demuxer 合并 |
| core/video_resizer.py | 126 | 1 | resize_video |
| core/keyword_remover.py | 342 | 1 | remove_keyword_ranges |
| gui/subtitle_tab.py | 143 | 1 | _burn_subtitles |

替换方式：统一走 `encoder.get_encoder()` 注入 `-c:v` 段；**音频参数、滤镜链、其余参数一律不动**（-c:a aac 128k / copy、-movflags、-pix_fmt 等保持原样），把行为差异面缩到最小。

#### P1-6 — 试点 + 批量推广节奏

1. **试点 = 视频尺寸（video_resize）**：一次提交完整切换 encoder + ffmpeg_runner + media_utils + path_utils（engine.resize_video 与 worker 的 collect_videos/probe 路径）。
2. 试点回归通过（AC-P1-5/6/7）后，再按 **utils/video_utils → video_concatenator → keyword_remover → subtitle_tab** 顺序逐模块推广，每模块一个提交。

### 2.2 本次不做（后置 P2/P3，明确排除）

- GUI 基类化：BaseTab / BaseWorker / PathPicker（评审阶段 2，17 个手写线程类收敛）。
- 批量并发框架：把"串行循环"改 ThreadPoolExecutor（仅裂变保留并发；其他模块 P1 仍串行）。
- 各模块 UI 进度百分比接入（ffmpeg -progress 解析能力 P1 提供，UI 接入后置）。
- ASR 五合一（fireredasr / funasr / sensevoice / sherpa / onnx 收敛）。
- ConfigStore 重构（gui/config.py）。
- 元数据清理补全（非裂变模块是否清元数据）。
- 音频直拷优化、媒体探测缓存、断点续跑、消除双重编码（评审性能清单 #2/4/5/7）。
- GUI 层 19 处视频格式列表的剩余迁移、wink_enhancer 扩展列表统一。

---

## 3. 用户故事

1. 作为电商卖家/千川素材运营，我希望**拼接一批视频时不用等那么久**，这样我能更快地产出投放素材赶上投放节奏。
2. 作为用户，我希望**视频尺寸调整批量处理从"几分钟"降到"几十秒"**，批量 100 个素材不再需要盯着进度条干等。
3. 作为用户，我希望**关掉软件时后台任务能干净退出**，不会出现窗口关了但终端/进程还挂着、任务管理器里杀不掉的情况。
4. 作为用户，我希望**以后加新功能页面不再有"忘了改某处导致关窗卡死"的坑**，新增标签页只动一个地方。
5. 作为用户，我希望**显卡被占用或驱动不可用时工具自动改用 CPU 继续干活**，而不是直接报错中断我整批任务。

---

## 4. 验收标准（CRITICAL，逐条可测）

### 4.1 P0 验收（main_window 注册重构）

| # | 验收项 | 判定标准 |
|---|---|---|
| AC-P0-1 | 单点注册 | 新增/删除一个 tab 只改注册表**一处**；import/实例化/addTab/closeEvent 全部由注册表派生，代码审查确认无平行手写列表残留 |
| AC-P0-2 | 清理自动跟随 | closeEvent 自动覆盖注册表内所有 tab 的**全部 QThread 属性**（含 video_download_tab 的 `login_worker`）；新增带 worker 的 tab 无需改 closeEvent |
| AC-P0-3 | 关窗干净退出 | 有任务运行时关窗：stop → wait(1500) → terminate 逻辑保留；关闭后进程退出、终端不残留（任务管理器确认无 python 进程悬挂） |
| AC-P0-4 | 功能不回归 | 16 个 tab 全部保留，顺序/标题/功能与迁移前一致；`python main.py` 可启动 |

### 4.2 P1 验收（公共层 + 提速）

| # | 验收项 | 判定标准 |
|---|---|---|
| AC-P1-1 | encoder 探测 | `get_encoder()` 首次调用在 1s 内返回；本机返回 `("h264_nvenc", "p1", ["-cq", ...])`；`get_default_workers()` 在 NVENC 下 =3、软件下 =min(核数,8) |
| AC-P1-2 | encoder 回退 | 强制禁用 NVENC（测试钩子/临时禁用）时返回 libx264；NVENC 运行中报会话受限（OpenEncodeSession/too many 等）时自动回退软件并重试**成功**（对齐裂变现有行为） |
| AC-P1-3 | runner 基础 | 所有 P1 迁移调用统一走 runner；Windows 下无黑窗闪烁；失败/超时后输出目录无半成品残留；超时能 kill 子进程并抛错 |
| AC-P1-4 | runner 中断 | 裂变 request_stop 后正在运行的 ffmpeg 全部 terminate；已完成的产物保留；partial_results 语义与迁移前一致 |
| AC-P1-5 | 行为一致性 | 对**每个迁移模块**：同一输入素材，迁移前后各产出一个输出，ffprobe 对比 `codec_name / width / height / sample_aspect_ratio / display_aspect_ratio / duration / r_frame_rate / pix_fmt`，断言：分辨率一致、**SAR=1:1**、宽高比一致、时长一致（±0.05s）、pix_fmt=yuv420p（如迁移前即 yuv420p）；输出可播放（ffprobe 无错误 + 抽帧成功） |
| AC-P1-6 | 提速 | 试点 video_resize：同素材同机，迁移后单文件耗时 ≥ 迁移前基线 **2×**（先记录基线再迁移）；推广后 5 个模块均 ≥2× |
| AC-P1-7 | 裂变不倒退 | 同一批 640x360×5 素材：迁移前后耗时差 ≤ ±10%（基线 1.33s）；清元数据（-map_metadata -1 + bitexact）、随机 comment、随机文件时间戳行为全部保留 |
| AC-P1-8 | 静态/启动回归 | `python -m py_compile` 全项目通过；`python -c "import core.encoder, core.ffmpeg_runner, utils.media_utils, utils.path_utils"` 通过；`python main.py` 启动无 traceback |
| AC-P1-9 | 提交纪律 | P0、P1-1~P1-5、试点、每个模块推广各一个独立 commit，全部 push；每步提交后 main.py 冒烟可启动 |

---

## 5. 回归风险清单

| # | 风险 | 影响 | 对策 |
|---|---|---|---|
| R1 | **SAR 偏离**（千川硬校验 SAR=1:1，历史上 2943:2944 事故） | 素材被千川判"尺寸不符合规范" | 迁移不改动滤镜链（setsar 位置不变）；试点在 AC-P1-5 用 ffprobe 断言 SAR=1:1；若发现某模块现状 SAR≠1（如 cut 系列无 setsar），本次一并显式补 `setsar=1` 并纳入回归 |
| R2 | **编码参数变化 → 文件大小/画质差异**（NVENC p1+cq vs x264 ultrafast+crf23 标度不同） | 输出体积/画质变化，用户感知 | 试点先做 3~5 个代表素材的 体积/SSIM/肉眼 三方对比，确认可接受；不可接受则调 NVENC 质量参数（-cq 降低 / -rc vbr + -b:v），或映射保持近似的码率档 |
| R3 | **元数据/encoder 标签变化**（NVIDIA 编码器标签写入） | 理论上不影响千川；但属可感知差异 | 裂变路径参数原样保留（清元数据行为不变）；其他模块 ffprobe -show_format 对比元数据清单，确认无新增异常项 |
| R4 | **黑窗闪烁 / 控制台行为变化**（新增 CREATE_NO_WINDOW） | 部分用户习惯了弹窗？通常是负面体验 | CREATE_NO_WINDOW 为纯增强，不改变 stdout/stderr 捕获；验证无黑窗、无输出丢失 |
| R5 | **NVENC 会话并发受限**（消费卡 3~5 session，多标签页同时跑） | 编码失败 | 默认并发=3（裂变已有）；runner 全局保留"硬件失败→自动回退软件重试一次"；回退后功能不坏（AC-P1-2） |
| R6 | **奇数尺寸源**（NVENC 可能要求偶数宽高） | 新报错 | 保留软件回退兜底（AC-P1-2）；若 NVENC 因奇数失败自动降级 libx264，不中断整批 |
| R7 | **超时语义变化**（新 runner 超时 kill+清理半成品，现状 video_utils 不清理） | 行为改进但属变更 | 验收中显式测超时路径（短 timeout 模拟），确认抛错信息可读、无半成品残留 |
| R8 | **import 改动引入循环依赖/遗漏**（media_utils 被 core/gui 双向引用） | 启动崩溃 | media_utils/path_utils 只依赖标准库；encoder/ffmpeg_runner 不依赖业务模块；验收 AC-P1-8 全项目导入检查 |
| R9 | **试点失败牵连日常使用**（用户靠工具处理素材） | 断档 | 试点前记录基线；试点提交独立可回滚；验收全过才推广；每步 commit 后可 `git revert` 单个提交 |
| R10 | **GUI 层格式列表暂不迁移导致"单点维护"不彻底**（本次只迁移 core 层） | 重复仍在 | 明确 P1 边界（core 层 5 处 + 试点依赖）；剩余 19 处中的 GUI 层与 wink_enhancer 记入 P2 backlog，PRD 明示，避免实现者误以为全量清零 |

---

## 6. 验证策略（QA 执行）

**静态检查**
1. `python -m py_compile` 全项目（或用 `compileall`）。
2. 导入冒烟：`python -c "import core.encoder; import core.ffmpeg_runner; import utils.media_utils; import utils.path_utils; import core.video_fission; import core.video_resizer; import core.video_concatenator; import core.keyword_remover"`。

**P0 验证**
3. 代码审查：注册表单点、closeEvent 由注册表派生、无平行列表残留（grep `self.xxx_tab =` 与 `addTab` 只出现在注册表驱动处）。
4. 行为：启动 main.py → 16 个 tab 顺序/标题一致 → 开一个任务 → 关窗 → 进程退出无残留。
5. 场景测试（可写一次性脚本，不提交）：模拟注册表增删一个 tab，断言 closeEvent 覆盖集合自动变化。

**P1 单元/能力验证（脚本，不依赖 GUI）**
6. encoder：`get_encoder()` 返回 NVENC；`get_default_workers()` 数值正确；注入测试钩子禁用 NVENC → 返回 libx264。
7. media_utils：对同一批素材，`probe_video / get_video_duration / collect_videos` 输出与原实现逐项一致。
8. path_utils：`strip_quotes('"a/b"') == 'a/b'`、空串、单引号、无引号、引号在中间不误删。

**行为回归（真实小批量任务冒烟 + ffprobe 对比）**
9. 每个迁移模块挑 3~5 个代表素材（9:16 竖屏、横屏、含音频/无音频、偶数/奇数尺寸各至少 1 个）：
   - 迁移前跑一遍出基线输出 → 迁移后同参数跑一遍；
   - ffprobe 对比表（见 AC-P1-5 字段清单）逐项一致；
   - 用 ffmpeg 抽帧验证可解码（如 `ffmpeg -i out.mp4 -frames:v 1 -f null -` 返回 0）。
10. 裂变回归：同批 640x360×5 素材，耗时与基线 1.33s 对比 ≤±10%；中断按钮 → partial_results 保留已完成项。

**性能对比**
11. 记录基线（迁移前）：video_resize 处理 N 个素材总耗时与单文件耗时。
12. 迁移后同素材同机复测 → 单文件提速 ≥2×（AC-P1-6）；推广模块逐模块复测。

**回退演练**
13. 临时强制软件路径（测试钩子或临时隐藏 nvenc），跑一个完整小批量：功能不坏、输出有效（AC-P1-2 / AC-P1-7）。

**提交纪律（与用户习惯一致）**
14. 每步独立 `git commit` + `git push`；每次提交后 `python main.py` 冒烟；任何一步失败允许单独 revert。

---

## 7. Open Questions（需确认）

1. **NVENC 质量参数口径**：NVENC `-cq` 数值与 x264 `crf` 不直接等价。默认用 `p1 + -cq crf` 映射还是需要用户可调？建议 P1 不做 UI 开关（保持与裂变一致：自动策略 + 自动回退），若试点画质对比不达标再议。
2. **video_utils 中无 setsar 的 cut 系列**：现状未显式 setsar，切 NVENC 后建议统一补 `setsar=1`（千川合规加固）。是否纳入本次 P1（默认纳入，纳入则须跑 AC-P1-5 回归）。
3. **wink_enhancer 扩展格式列表（webm/m4v/wmv 等 8 元组）**：公共 VIDEO_EXTS 是否扩为 8 元组？P1 默认保持 5 元组不动，P2 统一决策。
4. **runner 超时默认值**：现状各模块 timeout 3600/600/300/120/60 不一，统一默认值取多少（建议保留调用方显式传参，不强制统一）。

---

## 附录 A：代码事实核对表（读码确认，2026-08-19）

| 事实 | 证据 |
|---|---|
| 16 个 tab 注册散落 4 处 | gui/main_window.py：import L3-18；实例化 L118-133；addTab L135-150；closeEvent L154-162 |
| closeEvent 漏 settings_tab | main_window.py:154-162 列表 15 项 vs addTab 16 项；settings_tab 实例化于 L130 但不在清理列表 |
| video_download_tab 双 worker | gui/video_download_tab.py:133 `self.worker`、:134 `self.login_worker`；closeEvent 只处理 `worker` |
| 裂变编码器块 | core/video_fission.py:68-116（`_probe_encoder` 68-84、`_test_hardware` 86-97、`encoder` 99-103、`default_workers` 105-112、`fallback_to_software` 114-116） |
| 裂变 Popen 管理 | video_fission.py:223-236（Popen + _track_proc + communicate(timeout=3600) + TimeoutExpired→kill）；238-260（中断删半成品、硬件失败回退重试、失败删残留） |
| 裂变性能基线（代码注释） | video_fission.py:44：640x360×5 份 NVENC 并发3=1.33s，原顺序 libx264=8.33s（6.3×） |
| libx264 硬编码 14 处 | utils/video_utils.py:41,58,74,133,155,198,221,296；core/video_concatenator.py:209,232,251；core/video_resizer.py:126；core/keyword_remover.py:342；gui/subtitle_tab.py:143 |
| VIDEO_EXTS 重复（≥13 文件） | core/video_resizer.py:6、keyword_remover.py:9、mixer.py:14、video_concatenator.py:34、screenshot.py:439、slicer.py:12/36/121、video_mixer.py:37/46、wink_enhancer.py:55（8 元组）、gui/face_detection_tab.py:120、screenshot_tab.py:37、slice_tab.py:62/437/459、subtitle_tab.py:53、text_recognition_tab.py:33 |
| probe 实现 ≥3 份 | video_resizer.py:28、video_concatenator.py:56、video_utils.py:96（另有裂变 import video_resizer 的） |
| 时长工具 1 份（多依赖） | utils/video_utils.py:8；keyword_remover.py:6、video_concatenator.py:7-9 依赖 |
| strip 引号实现 3 份 | gui/video_fission_tab.py:57、core/video_concatenator.py:12、core/video_fission.py:339 |
| 试点模块形态 | gui/video_resize_tab.py:18-69 VideoResizeWorker 串行循环调 engine.resize_video；engine 内单次 subprocess.run（video_resizer.py:117-136），无 Popen/无中断 → 最简迁移对象 |
| 无黑窗处理 | 全项目 grep 无 CREATE_NO_WINDOW / creationflags |
| 运行环境 | main.py + requirements.txt：Python 3.12 / PyQt5≥5.15 / qt-material / onnxruntime-directml；ffmpeg 系统 PATH |
