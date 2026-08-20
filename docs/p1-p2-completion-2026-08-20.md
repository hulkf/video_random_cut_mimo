# P1 + P2 重构落地完成报告（2026-08-20）

> 执行指令：按优化路线图一次性解决 P1（全部 6 项）+ P2（全部 6 项），困难项暂缓。
> 状态：**全部 commit 并已 push 到 `origin/main`**（HEAD = `1ee2956`）。

## 已落地项

### P1（小而痛，6 项全做）
| 项 | 文件 | 关键修复 |
|----|------|---------|
| P1.1 | `core/encoder.py` `core/ffmpeg_runner.py` | NVENC fallback 收窄为「仅会话受限且非超时」才回退 libx264（推翻千问 2.1 盲目重试 P0） |
| P1.2 | `gui/face_detection_tab.py` | 裸 QLineEdit → PathRow，删 `browse_folder`（原调 `_browse` 必崩） |
| P1.3 | `utils/media_utils.py` | VIDEO_EXTS 常量单点化（13 处散落 → 一处）|
| P1.4 | `gui/audio_mix_tab.py` `gui/common/base_worker.py` | 停止时正确复位 UI；BaseWorker.run 兜底 emit finished 防 tab 卡死 |
| P1.5 | `.gitignore` `core/sherpa_asr_error.log` | 仓库卫生：忽略 secrets/logs/temp；停止跟踪运行时日志 |
| P1.6 | `core/video_fission.py` `core/ffmpeg_runner.py` | 进程追踪加 owner 分组 + `terminate_owner()`，根治跨 tab 互杀 |

### P2（中等风险，6 项全做）
| 项 | 文件 | 关键修复 |
|----|------|---------|
| P2.1 | `core/voice_clone.py` `gui/voice_clone_tab.py` | 新增 `CosyVoiceService.stop()` + try/finally 清理残留进程 |
| P2.2 | — | 开拍云移 worker：核查已完成，跳过 |
| P2.3 | `core/audio_utils.py` | demucs 由裸 subprocess 改为 Popen + track_proc + CREATE_NO_WINDOW |
| P2.4 | `core/text_detector.py` | paddleocr 改为 lazy import（规避重导入痛点）|
| P2.5 | `core/model_dirs.py` | 新建单点收敛 ASR 模型目录常量，切断 gui→gui 耦合 |
| P2.6 | `core/video_utils.py` | `utils/video_utils.py` git mv 下沉 core，8 处调用方引用更新 |

## 分组提交（均已 push）
```
1ee2956  chore(P1.5): 仓库卫生
4035826  refactor(P2): 常量收口/反向依赖下沉/lazy import/CosyVoice/demucs
b048c67  refactor: VIDEO_EXTS 常量单点化 (P1.3)
18dad08  fix(audio_mix): 停止时正确复位 UI + BaseWorker 兜底 (P1.4)
898918c  fix(face_detection): PathRow 替换裸 QLineEdit (P1.2)
3bf1100  fix(encoder): 收窄 NVENC 回退 + 修复跨 tab 进程互杀 (P1.1/P1.6)
```

## 验证
- ✅ 全部 P2 文件 `py_compile` 通过
- ✅ `grep` 全仓无 `utils.video_utils` 残留（仅 `media_utils.py` 注释引用）
- ✅ `import core.model_dirs` / `import core.video_utils` 冒烟 OK
- ✅ `origin/main` = `1ee2956`，与本地一致

## ⚠️ 注意事项
1. **运行中的 App 必须重启**：core/ 与 gui/ 大量源码改动，已经在跑的进程需重开窗口才能加载新模块。
2. `config.json` 的 79 行本地 UI 设置变更**有意保持未提交**（用户素材路径等），不覆盖仓库默认配置。
3. **P3 / P4 按规划暂缓**：统一 logging、大视频内存分块、taobao 巨石分区、冒烟脚本、路径全收敛、wink 8 元组标准化、crf 统一，均后置。
4. **待用户决策**：Q1（哪些 ASR 可删）/ Q2（torch 可选化）/ Q3（get_encoder 收紧 100%）答复后，再推进 ASR 收敛与短期优化。
