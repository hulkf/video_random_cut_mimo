# -*- coding: utf-8 -*-
"""统一 ffmpeg 执行封装：CREATE_NO_WINDOW / 进程追踪 / 超时 kill+清理 / 进度回调。

从 core/video_fission.py 的 Popen 管理逻辑上提（P1-2）：
  - Windows 下自动 creationflags=CREATE_NO_WINDOW（杜绝黑窗闪烁，纯增强，R4）；
  - 全局进程注册表 track_proc / untrack_proc / terminate_all（承接裂变 request_stop → 杀全部 ffmpeg）；
  - 超时 kill + 清理半成品输出（行为改进，R7）；
  - 失败时删除残留半成品（对齐裂变现有行为）；
  - 可选 -progress pipe:1 进度回调（P1 只提供能力，UI 接入属 P2）。

导入方向：本模块只允许 import core.encoder（单向），禁止 import 任何 core/ 业务模块与 gui/。
"""
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional

CREATE_NO_WINDOW = 0x08000000  # Windows 无黑窗（全项目现状均未带，属纯增强，R4）


class FFmpegError(RuntimeError):
    """ffmpeg 执行失败/超时。保留 returncode 与 stderr，供回退编排使用。

    timed_out=True 表示超时被 kill（裂变编排据此不把"超时"当作"硬件失败"而触发回退，行为不变）。
    """

    def __init__(self, message: str, *, cmd=None, returncode=None, stderr: str = "",
                 timed_out: bool = False):
        super().__init__(message)
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        self.timed_out = timed_out


@dataclass
class FFmpegResult:
    returncode: int
    stdout: str
    stderr: str
    output_path: Optional[str] = None


# ── 全局进程注册表（从 video_fission._track_proc/_untrack_proc/_procs_lock 上提）────
_procs: set = set()
_procs_lock = threading.Lock()


def track_proc(proc: subprocess.Popen) -> None:
    """将子进程注册到全局进程表（供 terminate_all 中断）。"""
    with _procs_lock:
        _procs.add(proc)


def untrack_proc(proc: subprocess.Popen) -> None:
    """将子进程从全局进程表移除。"""
    with _procs_lock:
        _procs.discard(proc)


def active_procs() -> list:
    """返回当前被追踪的活跃进程列表（快照）。"""
    with _procs_lock:
        return list(_procs)


def terminate_all() -> int:
    """终止所有被追踪的活跃进程，返回终止数量（裂变 request_stop 调用，AC-P1-4）。"""
    terminated = 0
    with _procs_lock:
        procs = list(_procs)
    for p in procs:
        if p.poll() is None:
            try:
                p.terminate()
                terminated += 1
            except Exception:
                pass
    return terminated


def _remove_output(output_path: Optional[str]) -> None:
    """删除半成品输出（失败/超时后清理，R7）。"""
    if output_path and os.path.exists(output_path):
        try:
            os.remove(output_path)
        except Exception:
            pass


def _read_progress_stdout(proc: subprocess.Popen,
                          on_progress: Callable[[dict], None]) -> str:
    """实时读取 stdout 进度行（-progress pipe:1 输出 key=value 行）并回调。

    stderr 在独立线程中读取，避免管道缓冲写满导致子进程阻塞。
    """
    stderr_chunks: List[str] = []

    def _read_stderr() -> None:
        try:
            while True:
                chunk = proc.stderr.readline()
                if chunk == "":
                    break
                stderr_chunks.append(chunk)
        except Exception:
            pass

    reader = threading.Thread(target=_read_stderr, daemon=True)
    reader.start()

    stdout_parts: List[str] = []
    try:
        while True:
            line = proc.stdout.readline()
            if line == "" and proc.poll() is not None:
                break
            if line == "":
                continue
            stdout_parts.append(line)
            stripped = line.strip()
            if "=" in stripped:
                key, _sep, value = stripped.partition("=")
                try:
                    on_progress({key.strip(): value.strip()})
                except Exception:
                    pass
    finally:
        reader.join(timeout=5)
    return "".join(stdout_parts)


def run_ffmpeg(
    cmd: List[str],
    *,
    timeout: float = 3600,                    # OQ4：默认 3600，调用方显式传原值
    on_progress: Optional[Callable[[dict], None]] = None,  # P1 只提供能力，UI 接入属 P2
    output_path: Optional[str] = None,        # 失败/超时后删除该半成品
    track: bool = True,                       # 是否注册到全局进程表
    error_message: str = "ffmpeg failed",
) -> FFmpegResult:
    """执行 ffmpeg 命令。行为契约：
    1. Windows 下自动 creationflags=CREATE_NO_WINDOW；
    2. cmd 含 `-progress pipe:1` 且 on_progress 非空时，实时解析 stdout 进度行并回调
       （progress dict: {frame, fps, out_time_us, out_time_ms, out_time, speed, progress}）；
    3. 超时：kill 子进程 → 若 output_path 存在则删除 → 抛 FFmpegError("...超时...")；
    4. 失败（returncode != 0）：提取 stderr 尾部（约 500 字符）→ 若 output_path 存在则删除
       → 抛 FFmpegError(f"{error_message}: {stderr_tail}")（RuntimeError 子类，兼容现有 except）；
    5. 成功：返回 FFmpegResult；
    6. track=True 时自动注册/注销；on_progress 回调异常不致命（吞掉并继续）。
    """
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = CREATE_NO_WINDOW

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="ignore",
        **kwargs,
    )
    if track:
        track_proc(proc)
    try:
        if on_progress is not None and "-progress" in cmd and "pipe:1" in cmd:
            stdout_data = _read_progress_stdout(proc, on_progress)
            # stderr 由 _read_progress_stdout 的线程收集；等进程结束后再聚合
            stderr_data = ""
        else:
            try:
                stdout_data, stderr_data = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    _out, err = proc.communicate()
                except Exception:
                    _out, err = "", ""
                _remove_output(output_path)
                raise FFmpegError(
                    "{}: 超时（{}s）".format(error_message, timeout),
                    cmd=cmd, returncode=None, stderr=(err or "")[-500:], timed_out=True,
                ) from None
    finally:
        if track:
            untrack_proc(proc)

    if proc.returncode != 0:
        stderr_tail = (stderr_data or "")[-500:]
        _remove_output(output_path)
        raise FFmpegError(
            "{}: {}".format(error_message, stderr_tail),
            cmd=cmd, returncode=proc.returncode, stderr=stderr_tail,
        )

    return FFmpegResult(
        returncode=proc.returncode,
        stdout=stdout_data or "",
        stderr=stderr_data or "",
        output_path=output_path,
    )


def run_ffmpeg_with_fallback(
    build_cmd: Callable[[tuple], List[str]],
    *,
    crf: int = 20,
    preset: str = "ultrafast",
    **run_kwargs,
) -> FFmpegResult:
    """先按硬件编码参数执行；硬件失败（FFmpegError 且 codec != libx264）则全局回退软件重试一次。

    供试点/裂变之外的模块复用回退能力（R5/R6）；裂变保留自身编排，不使用本函数。
    """
    from core.encoder import get_encoder, fallback_to_software

    params = get_encoder(crf=crf, preset=preset)
    try:
        return run_ffmpeg(build_cmd(params), **run_kwargs)
    except FFmpegError as e:
        if params[0] != "libx264":
            fallback_to_software()
            return run_ffmpeg(build_cmd(get_encoder(crf=crf, preset=preset)), **run_kwargs)
        raise


class FFmpegCommand:
    """轻量命令构建器（可选；工程师也可直接用 list + run_ffmpeg）。"""

    def __init__(self, executable: str = "ffmpeg"):
        self._cmd: List[str] = [executable]

    def add(self, *args) -> "FFmpegCommand":
        self._cmd.extend(str(a) for a in args)
        return self

    def input(self, path: str) -> "FFmpegCommand":
        """-i path"""
        self._cmd.extend(["-i", path])
        return self

    def encoder(self, codec, preset, quality_args) -> "FFmpegCommand":
        """-c:v codec -preset preset + quality_args"""
        self._cmd.extend(["-c:v", codec, "-preset", preset])
        self._cmd.extend(quality_args)
        return self

    def output(self, path: str, *, overwrite: bool = True) -> "FFmpegCommand":
        """[-y] path"""
        if overwrite:
            self._cmd.append("-y")
        self._cmd.append(path)
        return self

    def build(self) -> List[str]:
        return list(self._cmd)

    def __str__(self) -> str:
        return " ".join(self._cmd)
