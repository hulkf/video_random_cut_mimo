import concurrent.futures
import datetime
import os
import random
import threading

from core.encoder import fallback_to_software, get_default_workers, get_encoder
from core.ffmpeg_runner import run_ffmpeg, terminate_owner, FFmpegError
from utils.media_utils import collect_videos, probe_video
from utils.path_utils import strip_quotes


class FissionStopped(Exception):
    """用户中断裂变时抛出。"""


class HardwareLimitError(Exception):
    """硬件编码会话受限（如 NVENC 同时编码数量超限），用于触发回退。"""


class VideoFission:
    """视频裂变（去重搬运）：一个视频 → N 个不同指纹的副本。

    原理：
      对画面做温和且随机的变换（调色/噪点/缩放重采样），每份用不同的随机参数，
      使平台感知哈希(pHash)各不相同，但人眼几乎看不出差别。

      不使用水平翻转(hflip)，避免文字/人脸镜像反转。

    分辨率保证：
      所有产物的分辨率、宽高比与原视频严格一致（此为硬性约束，不可改动）：
      - 偶数尺寸源视频：scale 放大 ~1% 后再 crop 精确裁回原宽高
      - 奇数尺寸源视频：跳过缩放重采样，只做调色+噪点，尺寸天然不变

    性能（V9 优化）：
      - 并发：ThreadPoolExecutor 并行跑多个 ffmpeg（默认按编码器策略自动选）
      - 硬件编码：自动探测 NVENC → libx264（QSV 实测慢于软件已排除），硬件会话受限时自动回退软件
      实测（640x360×5份）：NVENC并发3 = 1.33s，是原顺序 libx264(8.33s) 的 6.3 倍

    中断支持：
      调用 request_stop() 后，正在运行的所有 ffmpeg 子进程都会被终止，
      已完成的产物保留，partial_results 记录中断前完成的视频。
    """

    INTENSITY_FACTOR = {"mild": 1.0, "medium": 2.0, "strong": 3.5}

    def __init__(self, options=None):
        self.options = options or {}
        self.intensity = self.options.get("intensity", "mild")
        self.preset = self.options.get("preset", "ultrafast")
        self.crf = int(self.options.get("crf", 20))
        self.ifactor = self.INTENSITY_FACTOR.get(self.intensity, 1.0)
        # 中断控制
        self._stop_requested = False
        self.partial_results = []    # 中断前已完成的视频列表
        self._owner = "fission-{}".format(id(self))  # 进程分组标识（P1.6 跨 tab 互杀修复）

    # ── 编码器探测与并发策略（委托公共模块 core.encoder，模块级缓存 + 全局回退）──
    def encoder(self):
        """获取编码器配置（带模块级缓存）。"""
        return get_encoder(crf=self.crf, preset=self.preset)

    def default_workers(self):
        """按编码器选择默认并发数（NVENC=3 / 软件=min(核数,8)）。"""
        return get_default_workers()

    # ── 中断控制 ─────────────────────────────────────────────
    def request_stop(self):
        """请求中断：设置标志 + 仅终止本实例（owner 分组）追踪的 ffmpeg，不误杀其他 tab。"""
        self._stop_requested = True
        terminate_owner(self._owner)

    def _check_stop(self):
        if self._stop_requested:
            raise FissionStopped("用户中断")


    def _build_filter(self, video_path, rng):
        """构建一份随机滤镜链。每次调用参数都不同。

        输出尺寸策略：
          - 默认：严格等于源视频 width×height（分辨率铁保证）
          - force_1080x1920 开启：统一输出 1080×1920（9:16 竖屏，居中裁切不变形）
        """
        info = probe_video(video_path)
        width, height = info["width"], info["height"]
        filters = []

        # 1) 调色：色相微偏 + 饱和度/亮度/对比度轻微浮动
        hue = rng.randint(-8, 8)
        sat = rng.uniform(0.92, 1.08)
        bri = rng.uniform(-0.04, 0.04) * self.ifactor
        con = rng.uniform(0.96, 1.04)
        filters.append("hue=h={}".format(hue))
        filters.append(
            "eq=saturation={s:.3f}:brightness={b:.3f}:contrast={c:.3f}".format(
                s=sat, b=bri, c=con
            )
        )

        # 2) 轻微噪点：像素级扰动
        strength = max(0.5, rng.uniform(0.8, 2.0) * self.ifactor)
        filters.append("noise=alls={:.1f}:allf=t".format(strength))

        # 3) 尺寸策略
        if self.options.get("force_1080x1920"):
            # 可选：统一转 1080×1920（9:16）。
            # force_original_aspect_ratio=increase 保持原比例放大到覆盖目标，
            # crop 居中裁切 —— 9:16 源无损；其他比例源居中裁切不变形。
            filters.append("scale=1080:1920:force_original_aspect_ratio=increase")
            filters.append("crop=1080:1920")
        elif width % 2 == 0 and height % 2 == 0:
            # 默认：分辨率铁保证（偶数源）——scale 放大约1%（取偶数）→ crop 精确裁回原宽高
            pct = 1.0 + max(0.006, rng.uniform(0.008, 0.025) * self.ifactor)
            nw = int(round(width * pct / 2) * 2)
            nh = int(round(height * pct / 2) * 2)
            filters.append("scale={}:{}".format(nw, nh))
            filters.append("crop={}:{}".format(width, height))
        # 奇数尺寸源默认不缩放，只做调色+噪点，分辨率天然不变

        # 4) 强制 SAR=1:1（关键！）：
        #    scale 非整数倍放大会让 ffmpeg 调整 SAR 以"保持显示比例"，
        #    导致输出 DAR 偏离精确 9:16（实测 SAR=2943:2944、DAR=26487:47104），
        #    千川会判定"素材尺寸不符合规范"。setsar=1 锁死 SAR，DAR 恒等于像素比。
        filters.append("setsar=1")

        return ",".join(filters)

    def fission_one(self, video_path, output_path, seed=None):
        """对单个视频做一次裂变，输出到指定路径。

        保证：输出分辨率/宽高比 == 源视频；清空元数据 + 随机化时间戳。
        编码：自动选用 NVENC/QSV 硬件加速，会话受限时回退 libx264。
        """
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        rng = random.Random(seed)
        vf = self._build_filter(video_path, rng)
        codec, enc_preset, quality_args = self.encoder()

        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", vf,
            "-c:v", codec, "-preset", enc_preset,
        ] + quality_args + [
            "-c:a", "copy",
            "-map", "0:v:0", "-map", "0:a?",
            "-movflags", "+faststart",
            # 默认硬编码：清空元数据（-fflags +bitexact 阻止 ffmpeg 写回 Lavf），
            # 并写入一条随机注释抹掉工具痕迹
            "-fflags", "+bitexact",
            "-map_metadata", "-1",
            "-metadata", "comment=wb{}".format(rng.randint(100000, 999999)),
            "-y", output_path,
        ]

        # 统一执行器：CREATE_NO_WINDOW + 全局进程追踪（中断时可 terminate_all）+ 失败删半成品
        try:
            run_ffmpeg(cmd, track=True, timeout=3600, owner=self._owner,
                       output_path=output_path, error_message="裂变失败")
        except FFmpegError as e:
            # 用户中断：删除半成品文件并抛出 FissionStopped
            if self._stop_requested:
                if os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except Exception:
                        pass
                raise FissionStopped("用户中断") from None
            # 硬件编码失败（NVENC/QSV 可能被其他进程占用 GPU 会话、驱动限制等）
            # → 全局回退软件编码重试一次；软件也失败则不会再回退（codec 已是 libx264）
            if codec != "libx264":
                fallback_to_software()
                return self.fission_one(video_path, output_path, seed=seed)
            raise

        # 成功但中断标志置位（竞态：编码完成后才请求停止）→ 同样删除半成品并抛中断
        if self._stop_requested:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass
            raise FissionStopped("用户中断")

        # 默认硬编码：随机化产物时间戳（创建/修改/访问）
        self._set_random_file_times(output_path, rng)

        return output_path

    def _set_random_file_times(self, path, rng):
        """将文件的创建/修改/访问时间改为 2018~2026 间的随机值（Windows）。"""
        try:
            import ctypes
            from ctypes import wintypes

            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", wintypes.DWORD),
                            ("dwHighDateTime", wintypes.DWORD)]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateFileW.restype = wintypes.HANDLE
            kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
            ]
            kernel32.SetFileTime.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
            ]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

            # 随机时间：2018-01-01 ~ 2026-08-01 (UTC)
            start = datetime.datetime(2018, 1, 1)
            span = (datetime.datetime(2026, 8, 1) - start).total_seconds()
            dt = start + datetime.timedelta(seconds=rng.uniform(0, span))
            epoch = datetime.datetime(1601, 1, 1)
            ft_int = int((dt - epoch).total_seconds() * 10_000_000)
            ft = FILETIME(ft_int & 0xFFFFFFFF, ft_int >> 32)

            GENERIC_WRITE = 0x40000000
            FILE_SHARE_READ = 0x1
            FILE_SHARE_WRITE = 0x2
            OPEN_EXISTING = 3
            FILE_ATTRIBUTE_NORMAL = 0x80

            handle = kernel32.CreateFileW(
                path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None,
            )
            if handle in (None, wintypes.HANDLE(-1).value, 0):
                return False
            try:
                return bool(kernel32.SetFileTime(
                    handle, ctypes.byref(ft), ctypes.byref(ft), ctypes.byref(ft)))
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False

    def fission_folder(self, input_sources, output_folder, separate_folder=True,
                       callback=None, max_workers=None):
        """批量裂变：每个视频生成 count 个不同版本。

        支持多个输入源（文件夹 或 单个视频文件），共享同一个输出文件夹。
        路径首尾如果带双引号/单引号会自动剥离兼容。

        Args:
            input_sources:     [(路径, 裂变数量), ...] 列表；路径可为文件夹或单个视频文件，
                              每个输入源可独立指定裂变数量
            output_folder:    输出根目录
            separate_folder:  True=每个源视频的产物放独立子文件夹；
                             False=所有产物统一平铺在输出根目录下
            callback:         progress(done, total, rel_path) 回调（按"份"粒度，并发安全）
            max_workers:      并发数；None=按编码器策略自动（NVENC 3 / QSV 4 / 软件 min(核数,8)）

        Returns:
            [{"input": ..., "outputs": [path1, ...], "subfolder": ...}, ...]
        """
        # 收集所有输入源（文件夹递归 / 单个文件），并记录各自的根目录与裂变数量
        videos = []  # (video_path, source_root, count)
        for p, cnt in input_sources or []:
            p = strip_quotes(p or "")
            cnt = max(1, int(cnt or 1))
            if not p:
                continue
            if os.path.isdir(p):
                for v in collect_videos(p):
                    videos.append((v, p, cnt))
            elif os.path.isfile(p):
                videos.append((p, os.path.dirname(p), cnt))

        # 去重排序（不同输入源可能重复指向同一文件）
        videos = sorted(set(videos), key=lambda x: x[0])

        # 计算每个源视频的名字基础：相对路径中的目录用下划线拼进名字
        from collections import Counter
        bases = [
            os.path.splitext(os.path.relpath(v, root))[0].replace(os.sep, "_").replace("/", "_")
            for v, root, _c in videos
        ]
        # 出现重名（不同输入源有同名文件）时，加上来源文件夹名前缀区分
        dup = {b for b, c in Counter(bases).items() if c > 1}
        names = []
        for (v, root, _c), b in zip(videos, bases):
            if b in dup:
                src_name = os.path.basename(os.path.normpath(root)) or "src"
                names.append("{}_{}".format(src_name, b))
            else:
                names.append(b)

        results = []
        base_seed = self.options.get("seed")

        # ── 构建任务计划：展开所有 (视频 × 份) 任务，记录输出分组 ──
        plan = []       # [(video_path, rel_base, subfolder)]
        all_tasks = []  # [(video_index, video_path, out_path, seed)]
        for index, ((video_path, source_root, count), rel_base) in enumerate(zip(videos, names)):
            if separate_folder:
                # 规则A：每个源视频的产物放独立子文件夹
                subfolder = os.path.join(output_folder, rel_base + "_fissions")
            else:
                # 规则B：所有产物统一平铺在输出根目录
                subfolder = output_folder
            os.makedirs(subfolder, exist_ok=True)
            plan.append((video_path, rel_base, subfolder))
            for i in range(count):
                seed = None if base_seed is None else (base_seed + i)
                out_name = "{}_{:03d}.mp4".format(rel_base, i + 1)
                out_path = os.path.join(subfolder, out_name)
                # 防重名保护：文件已存在则追加序号（理论上 rel_base 已唯一，双保险）
                k = 2
                while os.path.exists(out_path):
                    out_path = os.path.join(
                        subfolder, "{}_{:03d}_{}.mp4".format(rel_base, i + 1, k))
                    k += 1
                all_tasks.append((index, video_path, out_path, seed))

        # ── 并发执行所有任务 ──────────────────────────────────
        workers = max_workers or self.default_workers()
        workers = max(1, min(workers, len(all_tasks)))
        lock = threading.Lock()
        done = [0]
        total = len(all_tasks)
        finished_outputs = {}  # video_index -> [out_path, ...]
        errors = []
        stop_raised = [False]

        def run_one(task):
            vi, video_path, out_path, seed = task
            self._check_stop()  # 线程内检查：已停止则不启动新任务
            self.fission_one(video_path, out_path, seed=seed)
            with lock:
                finished_outputs.setdefault(vi, []).append(out_path)
                done[0] += 1
                d = done[0]
            if callback:
                callback(d, total, os.path.basename(out_path))
            return vi

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run_one, t): t for t in all_tasks}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    fut.result()
                except FissionStopped:
                    stop_raised[0] = True
                    for f in futures:
                        f.cancel()
                    break
                except Exception as e:
                    errors.append(e)
            # with 块退出时会等待所有已提交任务结束
            # （中断场景下 request_stop 已 terminate 所有 ffmpeg，任务会快速结束）

        # ── 组装结果：按视频分组，保留已完成的部分产物 ─────────
        for vi, (video_path, rel_base, subfolder) in enumerate(plan):
            outs = sorted(finished_outputs.get(vi, []))
            if outs:
                results.append({
                    "input": video_path,
                    "outputs": outs,
                    "subfolder": subfolder,
                })

        self.partial_results = list(results)

        if errors:
            raise errors[0]
        if stop_raised[0]:
            raise FissionStopped("用户中断")

        return results
