# 视频工具 Agent 无界面接口

## 入口

```powershell
python video_tool.py health
python video_tool.py capabilities
python video_tool.py run --request REQUEST.json
```

请求和响应均为 JSON。`capabilities` 是唯一权威能力清单；Agent 不应根据 GUI 名称猜测 operation。

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
| 开拍云端 | `kaipai_process` | 擦除/画质修复等开拍任务 |
| 开拍云端 | `kaipai_download`、`kaipai_quota` | 下载结果、查询配置/额度信息 |
| 视频裂变 | `video_fission` | 一个视频生成多个不同指纹版本 |
| 音色复刻 | `voice_profile_list/create/delete` | 管理本地音色 |
| 音色复刻 | `voice_clone_apply`、`voice_synthesize` | 批量换音或合成试听音频 |
| 视频下载 | `video_download`、`download_auth_status`、`download_login` | 下载淘宝/抖音视频及管理登录态 |
| 设置 | `settings_get`、`settings_update`、`settings_secret_set` | 管理普通配置和密钥 |
| 通用校验 | `validate` | 返回媒体尺寸、时长和可解析状态 |

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

- `video_enhance` 依赖 Wink 登录态并可能消耗云端额度。
- `kaipai_*` 依赖开拍凭据并可能产生付费任务。
- `video_download` 可能依赖淘宝登录态。
- `download_login` 会打开交互式登录流程。
- `voice_*` 依赖本机 CosyVoice/ASR 模型环境。
- `delete_face_images`、`delete_face_videos`、`auto_delete`、`voice_profile_delete`、`settings_update` 和 `settings_secret_set` 可能删除或改变本地状态。

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
