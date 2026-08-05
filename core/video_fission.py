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
    """

    INTENSITY_FACTOR = {"mild": 1.0, "medium": 2.0, "strong": 3.5}

    def __init__(self, options=None):
        self.options = options or {}
        self.intensity = self.options.get("intensity", "mild")
        self.preset = self.options.get("preset", "ultrafast")
        self.crf = int(self.options.get("crf", 20))
        self.ifactor = self.INTENSITY_FACTOR.get(self.intensity, 1.0)

    def _build_filter(self, video_path, rng):
        """构建一份随机滤镜链。每次调用参数都不同。"""
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

        # 3) 缩放重采样：微调尺寸后裁回原尺寸，触发像素重采样
        pct = 1.0 + max(0.006, rng.uniform(0.008, 0.025) * self.ifactor)
        nw = int(round(width * pct / 2) * 2)
        nh = int(round(height * pct / 2) * 2)
        filters.append("scale={}:{}".format(nw, nh))
        filters.append("crop={}:{}".format(width, height))

        # 保证输出偶数尺寸
        filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

        return ",".join(filters)

    def fission_one(self, video_path, output_path, seed=None):
        """对单个视频做一次裂变，输出到指定路径。"""
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
            "-y", output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=3600)
        if result.returncode != 0 or not os.path.exists(output_path):
            raise RuntimeError("裂变失败: " + (result.stderr or "")[-500:])
        return output_path

    def fission_folder(self, input_folder, output_folder, count=1, callback=None):
        """批量裂变：每个视频生成 count 个不同版本。

        Args:
            input_folder:  输入视频文件夹
            output_folder: 输出根目录
            count:         每个源视频生成几个变种（默认 1）
            callback:      progress(current, total, rel_path) 回调

        Returns:
            [{"input": ..., "outputs": [path1, path2, ...], "subfolder": ...}, ...]
        """
        videos = collect_videos(input_folder)
        results = []
        total = len(videos)
        base_seed = self.options.get("seed")

        for index, video_path in enumerate(videos):
            rel = os.path.relpath(video_path, input_folder)
            rel_dir = os.path.dirname(rel)
            rel_base, _ = os.path.splitext(os.path.basename(rel))

            # 每个源视频一个子文件夹，存放它的所有裂变产物
            subfolder = os.path.join(output_folder, rel_dir, rel_base + "_fissions")
            os.makedirs(subfolder, exist_ok=True)

            if callback:
                callback(index, total, rel)

            outputs = []
            for i in range(count):
                # 每份用不同的种子，保证参数互不相同
                seed = None if base_seed is None else (base_seed + i)
                out_name = "{}_{:03d}.mp4".format(rel_base, i + 1)
                out_path = os.path.join(subfolder, out_name)
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
