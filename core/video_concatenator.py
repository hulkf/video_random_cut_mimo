import os
import random
import tempfile
import shutil
import subprocess
import json
from utils.video_utils import (
    get_video_duration, image_to_video
)


class VideoConcatenatorEngine:
    def __init__(self, config):
        self.config = config
        self.folder_a = config["folder_a"]
        self.folder_b = config["folder_b"]
        self.output_folder = config["output_folder"]
        self.cover_enabled = config.get("cover_enabled", False)
        self.cover_folder = config.get("cover_folder", "")
        self.cover_duration_min = config.get("cover_duration_min", 0.5)
        self.cover_duration_max = config.get("cover_duration_max", 1.0)
        self.cover_mode = config.get("cover_mode", "front")  # front, back, both

    def get_videos(self, folder):
        video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
        videos = []
        for root, dirs, files in os.walk(folder):
            for f in sorted(files):
                if f.lower().endswith(video_exts):
                    videos.append(os.path.join(root, f))
        return videos

    def get_cover_images(self):
        if not self.cover_enabled or not self.cover_folder:
            return []
        image_exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
        images = []
        for root, dirs, files in os.walk(self.cover_folder):
            for f in sorted(files):
                if f.lower().endswith(image_exts):
                    images.append(os.path.join(root, f))
        return images

    def _probe_video(self, path):
        """获取视频信息"""
        cmd = [
            "ffprobe", "-v", "quiet", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate",
            "-of", "json", path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        fps_str = stream.get("r_frame_rate", "30/1")
        num, den = fps_str.split("/")
        return {
            "width": stream["width"],
            "height": stream["height"],
            "fps": float(num) / float(den)
        }

    def concat_pair(self, video_a, video_b, output_path, cover_img=None):
        """拼接两个视频，保留音频，封面图用静音"""
        tmp_dir = tempfile.mkdtemp()
        try:
            ref = self._probe_video(video_a)
            ref_w, ref_h = ref["width"], ref["height"]

            # 获取两个视频的时长
            dur_a = get_video_duration(video_a)
            dur_b = get_video_duration(video_b)

            # 封面图处理
            cover_duration = 0
            cover_video = None
            if self.cover_enabled and cover_img:
                cover_duration = random.uniform(self.cover_duration_min, self.cover_duration_max)
                cover_video = os.path.join(tmp_dir, "cover.mp4")
                image_to_video(cover_img, cover_duration, cover_video, ref_w, ref_h)
                if not os.path.exists(cover_video):
                    cover_video = None
                    cover_duration = 0

            # 构建 ffmpeg 命令：直接拼接视频和音频
            cmd = ["ffmpeg"]

            # 输入视频
            if cover_video:
                cmd.extend(["-i", cover_video])
            cmd.extend(["-i", video_a, "-i", video_b])

            # 静音时长
            total_silence = cover_duration

            # 构建 filter_complex
            filter_parts = []
            video_idx = 0

            # 封面图视频（如果有）
            if cover_video:
                filter_parts.append(f"[{video_idx}:v]scale={ref_w}:{ref_h},setsar=1[v_cover]")
                video_idx += 1

            # 视频A和B
            filter_parts.append(f"[{video_idx}:v]scale={ref_w}:{ref_h},setsar=1[v_a]")
            video_idx += 1
            filter_parts.append(f"[{video_idx}:v]scale={ref_w}:{ref_h},setsar=1[v_b]")
            video_idx += 1

            # 拼接视频流
            if cover_video:
                filter_parts.append("[v_cover][v_a][v_b]concat=n=3:v=1:a=0[outv]")
            else:
                filter_parts.append("[v_a][v_b]concat=n=2:v=1:a=0[outv]")

            # 音频处理：静音 + 音频A + 音频B
            audio_filter_parts = []
            audio_idx = 1  # 从第2个输入开始是音频

            if total_silence > 0:
                filter_parts.append(f"anullsrc=channel_layout=stereo:sample_rate=44100[silence]")
                filter_parts.append(f"[silence]atrim=0:{total_silence},asetpts=PTS-STARTPTS[silence_padded]")
                audio_filter_parts.append("[silence_padded]")

            # 音频A
            filter_parts.append(f"[{audio_idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a_a]")
            audio_filter_parts.append("[a_a]")
            audio_idx += 1

            # 音频B
            filter_parts.append(f"[{audio_idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a_b]")
            audio_filter_parts.append("[a_b]")

            # 合并音频
            concat_audio = "".join(audio_filter_parts)
            filter_parts.append(f"{concat_audio}concat=n={len(audio_filter_parts)}:v=0:a=1[outa]")

            filter_str = ";".join(filter_parts)

            cmd.extend([
                "-filter_complex", filter_str,
                "-map", "[outv]", "-map", "[outa]",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-shortest",
                "-y", output_path
            ])

            result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=600)
            if result.returncode != 0:
                raise RuntimeError(f"concat failed: {result.stderr}")

            return output_path
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _concat_video_only(self, input_paths, output_path, ref_w, ref_h, ref_fps):
        """拼接视频（仅视频流，无音频）- 使用 concat demuxer"""
        # 先统一所有视频格式
        normalized_paths = []
        for i, p in enumerate(input_paths):
            norm_path = os.path.join(os.path.dirname(output_path), f"norm_{i}.mp4")
            cmd = [
                "ffmpeg", "-i", p,
                "-vf", f"scale={ref_w}:{ref_h},fps={ref_fps},setsar=1",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-an", "-y", norm_path
            ]
            result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=120)
            if result.returncode == 0 and os.path.exists(norm_path):
                normalized_paths.append(norm_path)

        if not normalized_paths:
            raise RuntimeError("No valid video paths to concatenate")

        # 使用 concat demuxer
        concat_list = os.path.join(os.path.dirname(output_path), "concat_list.txt")
        with open(concat_list, "w", encoding="utf-8") as f:
            for p in normalized_paths:
                f.write(f"file '{p}'\n")

        cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_list,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-an", "-y", output_path
        ]
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=300)

        # 清理
        if os.path.exists(concat_list):
            os.remove(concat_list)
        for p in normalized_paths:
            if os.path.exists(p):
                os.remove(p)

        if result.returncode != 0:
            raise RuntimeError(f"concat video failed: {result.stderr}")

    def _extract_audio(self, video_path, audio_path):
        """提取音频"""
        cmd = [
            "ffmpeg", "-i", video_path, "-vn", "-acodec", "copy",
            "-y", audio_path
        ]
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=60)
        # 如果提取失败（可能没有音频），返回False
        return result.returncode == 0 and os.path.exists(audio_path)

    def _merge_audio(self, audio_parts, output_path):
        """合并音频：支持静音和文件"""
        if not audio_parts:
            # 没有音频，创建静音
            cmd = [
                "ffmpeg", "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t", "1", "-c:a", "aac", "-y", output_path
            ]
            subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore")
            return

        filter_parts = []
        input_idx = 0
        concat_inputs = []

        for part_type, part_data in audio_parts:
            if part_type == "silence":
                # 静音段
                filter_parts.append(f"anullsrc=channel_layout=stereo:sample_rate=44100[s{input_idx}]")
                filter_parts.append(f"[s{input_idx}]atrim=0:{part_data},asetpts=PTS-STARTPTS[a{input_idx}]")
                concat_inputs.append(f"[a{input_idx}]")
                input_idx += 1
            else:
                # 音频文件
                cmd_pre = ["ffmpeg", "-i", part_data]
                # 需要先添加输入，这里用 filter_complex 的方式
                pass

        # 简化方案：用concat方式
        # 生成静音文件
        silence_parts = []
        file_parts = []
        for part_type, part_data in audio_parts:
            if part_type == "silence":
                silence_parts.append(part_data)
            else:
                file_parts.append(part_data)

        # 创建静音段
        total_silence = sum(silence_parts)
        if total_silence > 0:
            silence_path = output_path.replace(".m4a", "_silence.m4a")
            cmd = [
                "ffmpeg", "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t", str(total_silence), "-c:a", "aac", "-b:a", "128k",
                "-y", silence_path
            ]
            subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=60)
            if os.path.exists(silence_path):
                file_parts.insert(0, silence_path)

        if len(file_parts) == 0:
            return
        elif len(file_parts) == 1:
            shutil.copy(file_parts[0], output_path)
            return

        # 使用 concat demuxer 合并
        concat_list = os.path.join(os.path.dirname(output_path), "concat_list.txt")
        with open(concat_list, "w", encoding="utf-8") as f:
            for p in file_parts:
                f.write(f"file '{p}'\n")

        cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_list,
            "-c:a", "aac", "-b:a", "128k",
            "-y", output_path
        ]
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=60)

        # 清理
        if os.path.exists(concat_list):
            os.remove(concat_list)
        silence_path = output_path.replace(".m4a", "_silence.m4a")
        if os.path.exists(silence_path):
            os.remove(silence_path)

    def _add_audio(self, video_path, audio_path, output_path):
        """给视频添加音频"""
        cmd = [
            "ffmpeg", "-i", video_path, "-i", audio_path,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-map", "0:v:0", "-map", "1:a:0",
            "-shortest",
            "-y", output_path
        ]
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"add audio failed: {result.stderr}")

    def run(self, callback=None):
        os.makedirs(self.output_folder, exist_ok=True)

        videos_a = self.get_videos(self.folder_a)
        videos_b = self.get_videos(self.folder_b)

        if not videos_a:
            raise ValueError("文件夹A中没有视频文件")
        if not videos_b:
            raise ValueError("文件夹B中没有视频文件")

        cover_images = self.get_cover_images()

        total = max(len(videos_a), len(videos_b))
        results = []

        for i in range(total):
            va = videos_a[i % len(videos_a)]
            vb = videos_b[i % len(videos_b)]

            name_a = os.path.splitext(os.path.basename(va))[0]
            name_b = os.path.splitext(os.path.basename(vb))[0]
            output_name = f"{name_a}+{name_b}.mp4"
            output_path = os.path.join(self.output_folder, output_name)

            cover_img = random.choice(cover_images) if cover_images else None

            if callback:
                callback(i, total, f"拼接: {name_a} + {name_b}", 0)

            self.concat_pair(va, vb, output_path, cover_img)
            results.append(output_path)

            if callback:
                callback(i + 1, total, f"完成: {name_a} + {name_b}", 100)

        return results
