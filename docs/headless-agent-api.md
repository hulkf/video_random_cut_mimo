# 视频工具 Agent 无界面接口

## 入口

```powershell
python video_tool.py health
python video_tool.py capabilities
python video_tool.py run --request -
```

请求和响应均为 JSON。`capabilities` 是唯一权威能力清单；Agent 不应根据 GUI 名称猜测 operation。

## 任务调用与临时产物（2026-09-09）

- 业务视频任务统一通过 `video_task_plan` 提交结构化步骤，确认后由任务中心执行；状态和结果读取 `video_task_status`，控制使用 `video_task_control`。工具不可用时报告故障，不能退回 Shell 批处理。
- 不在视频项目根目录写入请求 JSON、日志、能力快照、探测视频或一次性 Python/Shell 脚本；不复用历史请求文件作为新任务参数来源。
- 请求和结果由任务中心数据库持久化，后台日志由执行器写入 `.task_center/logs/`；多步骤通过计划及 `items_from_step` 传递结果，不通过截取混合日志或生成解析脚本接力。
- 开发者获授权诊断 CLI 时使用 `run --request -`，通过进程 stdin 传 JSON，分别捕获 stdout JSON 和 stderr 诊断信息；不要合并流后再解析。确需磁盘临时文件时使用系统临时目录下的独立任务目录，并在本次诊断结束时清理。
- 正式日志用于故障追踪，不属于无用文件；保留任务数据库、恢复状态、登录态、模型、素材及仍被调用的 `_whisper_transcribe.py`，不能按下划线前缀批量删除源码。

## 标签页与 operation

| 标签页 | operation | 主要用途 |
|---|---|---|
| 视频切片 | `video_slice` | 按随机时长批量切片 |
| 视频截图 | `video_screenshot` | 随机抽帧，可选人脸检测与删除 |
| 文字识别 | `text_recognition` | 检测视频画面中是否包含文字 |
| 人脸识别 | `face_detection` | 检测视频是否含人脸 |
| 音频混剪 | `audio_mix` | 按音频/视频时长组合素材片段 |
| 视频混剪 | `video_mix` | 按工具既有混剪规则生成视频 |
| 视频拼接 | `video_concat` | A/B 两目录配对拼接，可从 B 抽帧 |
| 千川拼接闭环 | `qianchuan_concat` | 自动标准化 A/B 为 9:16 后再拼接 |
| 视频尺寸 | `video_resize` | 转为 9:16、3:4、1:1 等预设 |
| 视频优化 | `video_enhance` | 调用 Wink 云端增强 |
| 去关键词 | `keyword_remove` | ASR 定位关键词并删除对应时间段 |
| 视频字幕 | `subtitle_generate` | ASR 生成字幕并烧录 |
| 开拍云端 | `kaipai_process` | 擦除/画质修复等开拍任务；目录多文件默认有界批量提交 |
| 开拍云端 | `kaipai_download`、`kaipai_quota` | 下载结果、查询配置/额度信息 |
| 视频裂变 | `video_fission` | 一个视频生成多个不同指纹版本 |
| 音色复刻 | `voice_profile_list/create/delete` | 管理本地音色 |
| 音色复刻 | `voice_clone_apply`、`voice_synthesize` | 批量换音或合成试听音频 |
| 视频下载 | `video_download`、`download_auth_status`、`download_login` | 下载淘宝/抖音视频及管理登录态 |
| 设置 | `settings_get`、`settings_update`、`settings_secret_set` | 管理普通配置和密钥 |
| 素材归档 | `material_organize` | 解压/收集视频并按货号归入模特素材或平铺素材 |
| 通用校验 | `validate` | 返回媒体尺寸、时长和可解析状态 |
| 任务控制 | `task_control` | 查询、暂停、继续或取消已登记的长任务 |

## 示例：转为 9:16

```json
{
  "operation": "video_resize",
  "inputs": {
    "input_path": "D:/素材/模特视频",
    "output_folder": "D:/素材/模特视频/916"
  },
  "options": {
    "target_ratio": "9:16",
    "process_mode": "mismatched",
    "blur_strength": 6
  }
}
```

## 示例：标准化后拼接

千川流程不能把 `video_concat` 的内部缩放当成比例转换。正确顺序是：

优先直接调用 `qianchuan_concat`。调用时只需提供 A、B 目录；工具会从二者共同上级目录取得货号，并按执行当天日期生成或复用 `<货号> 千川素材 <MMDD>`。如果显式提供 `output_folder`，它必须与该推导结果完全一致。该 operation 在工具内部依次完成：检查 A/B、转换所有非 9:16 输入、调用原有 `video_concat`、校验输出。`video_resize` 和 `video_concat` 仍保留为独立基础能力。

## 外部服务与高风险动作

`material_organize` 接受本地 ZIP、视频、目录或可直接下载的 HTTP(S) 地址。默认从文件名中“视频”之前识别货号，在 `D:\千川素材\<货号>\模特素材` 下归档；指定 `material_type=flat` 时使用 `平铺素材`。ZIP 只提取视频文件，成功提取后默认删除已复制到货号目录的压缩包；`delete_archive=false` 可保留。该 operation 会移动/写入/删除素材，必须携带 `authorization.confirmed=true` 且 scope 为 `material_organize`。

平铺归档只负责落盘和分类；后续智能全消、画质修复仍由 Agent 按确认卡分别调用 `kaipai_process` 与 `kaipai_download`，两个结果应保存到独立目录。

`kaipai_process` 的目录输入默认启用批量模式，并发数为 9。可通过 `options.batch_mode`（已启用/未启用）和 `options.max_workers` 调整；这是多个独立开拍任务的有界并发，结果按输入文件顺序返回，单个文件失败不会丢弃同批结果。

长任务请求可在顶层增加 `task_id`，工具会在 `.task_control/<task_id>.json` 保存状态。控制示例：

```json
{
  "operation": "task_control",
  "inputs": {"task_id": "QC-20260822-001", "action": "pause"}
}
```

本地拼接会在当前视频对完成后停止后续处理；继续会重新启动原请求并跳过已完成且可解析的本地输出。已提交的开拍云端任务无法由本地工具撤回。

- `video_enhance` 依赖 Wink 登录态并可能消耗云端额度。
- `kaipai_*` 依赖开拍凭据并可能产生付费任务。
- `video_download` 可能依赖淘宝登录态。
- `download_login` 会打开交互式登录流程。
- `voice_*` 依赖本机 CosyVoice/ASR 模型环境。
- `material_organize`、`delete_face_images`、`delete_face_videos`、`auto_delete`、`voice_profile_delete`、`settings_update` 和 `settings_secret_set` 可能删除或改变本地状态。

Agent 必须在用户明确授权相应外部调用、付费动作、登录动作或删除动作后才执行。工具本身也会拒绝缺少授权凭证的请求；授权格式如下：

```json
{
  "authorization": {
    "confirmed": true,
    "scope": "kaipai_process"
  }
}
```

`scope` 必须等于当前 operation，也可以使用 `*` 表示本次请求已取得通配授权。仅查询 `capabilities`、`health`、登录状态和普通媒体信息不需要授权。
