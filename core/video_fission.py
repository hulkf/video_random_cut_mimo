import datetime
import os
import random
import subprocess

from core.video_resizer import collect_videos, probe_video


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
    """

    INTENSITY_FACTOR = {"mild": 1.0, "medium": 2.0, "strong": 3.5}

    def __init__(self, options=None):
        self.options = options or {}
        self.intensity = self.options.get("intensity", "mild")
        self.preset = self.options.get("preset", "ultrafast")
        self.crf = int(self.options.get("crf", 20))
        self.ifactor = self.INTENSITY_FACTOR.get(self.intensity, 1.0)

    def _build_filter(self, video_path, rng):
        """构建一份随机滤镜链。每次调用参数都不同。

        关键约束：滤镜链执行完毕后，输出尺寸必须精确等于源视频 width×height。
        """
        width, height = probe_video(video_path)
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

        # 3) 缩放重采样：仅当宽高均为偶数时执行。
        #    scale 放大约1%（取偶数尺寸）→ crop 精确裁回原宽高，最终分辨率严格不变。
        #    奇数尺寸源视频不做缩放（libx264 要求偶数尺寸，放大裁回会产生 1px 偏差），
        #    只做调色+噪点，分辨率天然不变。
        if width % 2 == 0 and height % 2 == 0:
            pct = 1.0 + max(0.006, rng.uniform(0.008, 0.025) * self.ifactor)
            nw = int(round(width * pct / 2) * 2)
            nh = int(round(height * pct / 2) * 2)
            filters.append("scale={}:{}".format(nw, nh))
            filters.append("crop={}:{}".format(width, height))

        return ",".join(filters)

    def fission_one(self, video_path, output_path, seed=None):
        """对单个视频做一次裂变，输出到指定路径。

        保证：输出分辨率/宽高比 == 源视频；可选清空元数据 + 随机化时间戳。
        """
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        rng = random.Random(seed)
        vf = self._build_filter(video_path, rng)

        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", self.preset, "-crf", str(self.crf),
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

        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=3600)
        if result.returncode != 0 or not os.path.exists(output_path):
            raise RuntimeError("裂变失败: " + (result.stderr or "")[-500:])

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

    def fission_folder(self, input_sources, output_folder, separate_folder=True, callback=None):
        """批量裂变：每个视频生成 count 个不同版本。

        支持多个输入源（文件夹 或 单个视频文件），共享同一个输出文件夹。
        路径首尾如果带双引号/单引号会自动剥离兼容。

        Args:
            input_sources:     [(路径, 裂变数量), ...] 列表；路径可为文件夹或单个视频文件，
                              每个输入源可独立指定裂变数量
            output_folder:    输出根目录
            separate_folder:  True=每个源视频的产物放独立子文件夹；
                             False=所有产物统一平铺在输出根目录下
            callback:         progress(current, total, rel_path) 回调

        Returns:
            [{"input": ..., "outputs": [path1, ...], "subfolder": ...}, ...]
        """
        # 收集所有输入源（文件夹递归 / 单个文件），并记录各自的根目录与裂变数量
        videos = []  # (video_path, source_root, count)
        for p, cnt in input_sources or []:
            p = (p or "").strip().strip('"').strip("'")
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
        total = len(videos)
        base_seed = self.options.get("seed")

        for index, ((video_path, source_root, count), rel_base) in enumerate(zip(videos, names)):
            # rel 用于进度回调展示（相对其来源根目录）
            rel = os.path.relpath(video_path, source_root)

            if separate_folder:
                # 规则A：每个源视频的产物放独立子文件夹
                subfolder = os.path.join(output_folder, rel_base + "_fissions")
            else:
                # 规则B：所有产物统一平铺在输出根目录
                subfolder = output_folder
            os.makedirs(subfolder, exist_ok=True)

            if callback:
                callback(index, total, rel)

            outputs = []
            for i in range(count):
                # 每份用不同的种子，保证参数互不相同
                seed = None if base_seed is None else (base_seed + i)
                out_name = "{}_{:03d}.mp4".format(rel_base, i + 1)
                out_path = os.path.join(subfolder, out_name)
                # 防重名保护：文件已存在则追加序号（理论上 rel_base 已唯一，双保险）
                k = 2
                while os.path.exists(out_path):
                    out_path = os.path.join(
                        subfolder, "{}_{:03d}_{}.mp4".format(rel_base, i + 1, k))
                    k += 1
                self.fission_one(video_path, out_path, seed=seed)
                outputs.append(out_path)

            results.append({
                "input": video_path,
                "outputs": outputs,
                "subfolder": subfolder,
            })

            if callback:
                callback(index + 1, total, rel)

        return results
