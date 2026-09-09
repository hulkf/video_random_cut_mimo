# Project Memory

This file records project-specific preferences for `video_random_cut_mimo`.
Follow it when working in this repository.

## CodeGraph

If a `.codegraph/` directory exists at the repo root, use CodeGraph before
grep/find or ad hoc file reads when locating or understanding code. If there is
no `.codegraph/` directory, skip CodeGraph.

## User Preferences

- Work in Chinese by default when reporting progress or results to the user.
- Prefer direct implementation, narrow verification, and concrete usable
  entrypoints over abstract plans.
- Keep edits narrowly scoped to the requested feature or bug. Do not touch
  unrelated dirty files, generated caches, or local config unless needed.
- This is a PyQt desktop video tool. For UI/tab changes, verify the real tab or
  at least run a focused syntax/import/offscreen check when full GUI testing is
  not practical.
- For every code change, default to committing and pushing the narrow task
  files to Git after verification, because the user wants work synced in case
  the machine shuts down. If the worktree contains unrelated changes, do not
  stage them.
- After every code change, explicitly tell the user whether the running project
  needs to be restarted.

## Restart Guidance

- Usually needs restart: Python source changes under `gui/`, `core/`, `utils/`,
  `services/`, `main.py`, startup/import wiring, dependency changes, packaged
  runtime hooks, or anything that affects already-loaded PyQt classes.
- Usually does not need restart: documentation-only changes, comments only,
  files not loaded by the running app, or changes to future packaging scripts
  that are not part of the current running process.
- If unsure, say so plainly and recommend restart. For this desktop app, a
  restart means closing the current app window/process and launching it again
  so Python reloads changed modules.

## 任务调用与临时产物（2026-09-09）

- 业务视频任务统一通过 `video_task_plan` 提交结构化步骤，确认后由任务中心执行；状态和结果读取 `video_task_status`，控制使用 `video_task_control`。工具不可用时报告故障，不能退回 Shell 批处理。
- 不在视频项目根目录写入请求 JSON、日志、能力快照、探测视频或一次性 Python/Shell 脚本；不复用历史请求文件作为新任务参数来源。
- 请求和结果由任务中心数据库持久化，后台日志由执行器写入 `.task_center/logs/`；多步骤通过计划及 `items_from_step` 传递结果，不通过截取混合日志或生成解析脚本接力。
- 开发者获授权诊断 CLI 时使用 `run --request -`，通过进程 stdin 传 JSON，分别捕获 stdout JSON 和 stderr 诊断信息；不要合并流后再解析。确需磁盘临时文件时使用系统临时目录下的独立任务目录，并在本次诊断结束时清理。
- 正式日志用于故障追踪，不属于无用文件；保留任务数据库、恢复状态、登录态、模型、素材及仍被调用的 `_whisper_transcribe.py`，不能按下划线前缀批量删除源码。
