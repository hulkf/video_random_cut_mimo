import os
import random
import subprocess

from core.video_resizer import collect_videos, probe_video


class VideoFission:
    """视频裂变（去重搬运）：对画面做温和且随机的变换，改变感知哈希(pHash)，
    同时尽量保持画面观感不变。每条视频使用随机参数，保证输出互不相同。

    设计目标：
      - 看不出差别：变换幅度控制在人眼几乎无感的范围（±6°色相、±3%亮度、1%缩放等）
      - 改掉指纹：水平翻转 / 像素重采样对 pHash 极其敏感
      - 速度快：仅轻量滤镜 + libx264 -preset ultrafast，音频直接 copy 不重编码
    """

    INTENSITY_FACTOR = {"mild": 1.0, "medium": 2.0, "strong": 3.5}

    def __init__(self, options=None, seed=None):
        self.options = options or {}
        self.intensity = self.options.get("intensity", "mild")
        self.random_params = self.options.get("random_params", True)
        self.preset = self.options.get("preset", "ultrafast")
        self.crf = int(self.options.get("crf", 20))
        self.ifactor = self.INTENSITY_FACTOR.get(self.intensity, 1.0)
        # seed=None 时每条视频随机；给定种子则参数可复现
        self._rng = random.Random(seed)

    def _rand(self, lo, hi):
        return self._rng.uniform(lo, hi) if self.random_params else (lo + hi) / 2.0

    def _rand_int(self, lo, hi):
        return self._rng.randint(lo, hi) if self.random_params else (lo + hi) // 2

    def build_filter(self, video_path):
        width, height = probe_video(video_path)
        filters = []
        o = self.options

        # 水平翻转：对 pHash 极有效，对无文字/人脸朝向不敏感的产品视频几乎无感
        if o.get("flip") and self._rand(0, 1) < 0.9:
            filters.append("hflip")

        # 调色：色相 / 饱和度 / 亮度 / 对比度，温和随机
        if o.get("color"):
            hue = self._rand_int(-6, 6)
            sat = self._rand(0.94, 1.06)
            bri = self._rand(-0.03, 0.03) * self.ifactor
            con = self._rand(0.97, 1.03)
            # 色相用 hue 滤镜，亮度/饱和度/对比度用 eq 滤镜（eq 不支持 hue 参数）
            filters.append("hue=h={}".format(hue))
            filters.append(
                "eq=saturation={s:.3f}:brightness={b:.3f}:contrast={c:.3f}".format(
                    s=sat, b=bri, c=con
                )
            )

        # 轻微噪点：改变像素，几乎无感
        if o.get("noise"):
            strength = max(0.3, self._rand(0.5, 1.5) * self.ifactor)
            filters.append("noise=alls={:.1f}".format(strength))

        # 缩放重采样 + 裁回原尺寸：触发像素重采样，零视觉差别但强烈扰动 pHash
        if o.get("resample"):
            pct = 1.0 + max(0.004, self._rand(0.005, 0.02) * self.ifactor)
            nw = int(round(width * pct / 2) * 2)
            nh = int(round(height * pct / 2) * 2)
            filters.append("scale={}:{}".format(nw, nh))
            filters.append("crop={}:{}".format(width, height))

        # 兜底：至少做一次翻转，避免完全没改到指纹
        if not filters:
            filters.append("hflip")

        # 保证输出尺寸为偶数（libx264 要求），偶数尺寸时此步无实际变化
        filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

        return ",".join(filters)

    def fission_video(self, video_path, output_path):
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        vf = self.build_filter(video_path)
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

    def fission_folder(self, input_folder, output_folder, callback=None):
        videos = collect_videos(input_folder)
        results = []
        total = len(videos)
        seed = self.options.get("seed")
        for index, video_path in enumerate(videos):
            rel = os.path.relpath(video_path, input_folder)
            rel_base, _ = os.path.splitext(rel)
            out = os.path.join(output_folder, "{}_fission.mp4".format(rel_base))
            if callback:
                callback(index, total, rel)
            # 每条视频独立随机，保证输出互不相同
            engine = VideoFission(self.options, seed=seed)
            out_path = engine.fission_video(video_path, out)
            results.append({"input": video_path, "output": out_path})
            if callback:
                callback(index + 1, total, rel)
        return results
