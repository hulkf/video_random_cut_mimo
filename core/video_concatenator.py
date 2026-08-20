import os
import random
import tempfile
import shutil

from core.ffmpeg_runner import (
    run_ffmpeg, run_ffmpeg_with_fallback, FFmpegError
)
from utils.media_utils import collect_videos, probe_video
from utils.path_utils import normalize_path
from core.video_utils import (
    get_video_duration, image_to_video
)


class VideoConcatenatorEngine:
    def __init__(self, config):
        self.config = config
        self.folder_a = normalize_path(config["folder_a"])
        self.folder_b = normalize_path(config["folder_b"])
        self.output_folder = normalize_path(config["output_folder"])
        self.cover_enabled = config.get("cover_enabled", False)
        self.cover_source = config.get("cover_source", "folder")
        self.cover_folder = normalize_path(config.get("cover_folder", ""))
        self.cover_duration_min = config.get("cover_duration_min", 0.5)
        self.cover_duration_max = config.get("cover_duration_max", 1.0)
        self.cover_mode = config.get("cover_mode", "front")  # front, back, both

    def get_videos(self, folder):
        return collect_videos(normalize_path(folder))

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
        """获取视频信息（委托 utils.media_utils.probe_video）。"""
        return probe_video(path)

    def _cover_mode_name(self):
        modes = {0: "front", 1: "back", 2: "both"}
        if isinstance(self.cover_mode, int):
            return modes.get(self.cover_mode, "front")
        if isinstance(self.cover_mode, str) and self.cover_mode.isdigit():
            return modes.get(int(self.cover_mode), "front")
        return self.cover_mode if self.cover_mode in ("front", "back", "both") else "front"

    def _extract_cover_frame(self, video_path, output_path, duration):
        # ffmpeg 8.x image2 muxer 在 -ss 接近 duration（浮点边界）时会拒绝；留 0.5s 余量
        timestamp = 0
        if duration > 0.5:
            timestamp = random.uniform(0.1, max(0.1, duration - 0.5))
        cmd = [
            "ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", video_path,
            "-frames:v", "1",
            # ffmpeg 8.x image2 muxer 单文件名必须显式 -update 1，否则可能 throw
            # "At least one output file must be specified"（用户报错根因）
            "-update", "1",
            # ffmpeg 8.x 默认 strict_std_compliance 提高，mjpeg 拒绝 TV-range YUV；
            # 视频源 h264 标记为 yuv420p(tv, bt709) 时需 -strict unofficial
            "-strict", "unofficial",
            "-q:v", "2", output_path
        ]
        result = run_ffmpeg(cmd, timeout=60, error_message="extract cover frame failed",
                            output_path=output_path)
        if result.returncode != 0 or not os.path.exists(output_path):
            raise RuntimeError(f"extract cover frame failed: {result.stderr}")
        return output_path

    def concat_pair(self, video_a, video_b, output_path, cover_img=None):
        """拼接两个视频，保留音频，封面图用静音"""
        tmp_dir = tempfile.mkdtemp()
        try:
            ref = self._probe_video(video_a)
            ref_w, ref_h = ref["width"], ref["height"]
            ref_fps = round(ref["fps"], 3)

            # 获取两个视频的时长
            dur_a = get_video_duration(video_a)
            dur_b = get_video_duration(video_b)

            # 封面图处理
            cover_duration = 0
            cover_video = None
            if self.cover_enabled and self.cover_source == "video_b_frame":
                cover_img = self._extract_cover_frame(
                    video_b, os.path.join(tmp_dir, "cover_from_b.jpg"), dur_b
                )

            if self.cover_enabled and cover_img:
                cover_duration = random.uniform(self.cover_duration_min, self.cover_duration_max)
                cover_video = os.path.join(tmp_dir, "cover.mp4")
                image_to_video(cover_img, cover_duration, cover_video, ref_w, ref_h)
                if not os.path.exists(cover_video):
                    cover_video = None
                    cover_duration = 0

            # 构建 filter_complex
            total_silence = cover_duration
            filter_parts = []
            video_idx = 0
            cover_mode = self._cover_mode_name()
            cover_at_front = bool(cover_video and cover_mode in ("front", "both"))
            cover_at_back = bool(cover_video and cover_mode in ("back", "both"))

            # 封面图视频（如果有）
            if cover_video:
                cover_filter = f"[{video_idx}:v]scale={ref_w}:{ref_h},fps={ref_fps},setsar=1"
                if cover_at_front and cover_at_back:
                    filter_parts.append(f"{cover_filter},split=2[v_cover_front][v_cover_back]")
                elif cover_at_back:
                    filter_parts.append(f"{cover_filter}[v_cover_back]")
                else:
                    filter_parts.append(f"{cover_filter}[v_cover_front]")
                video_idx += 1

            # 音频起始索引 = 视频A的输入索引（有封面时=1，无封面时=0）
            audio_idx = video_idx

            # 视频A和B
            filter_parts.append(f"[{video_idx}:v]scale={ref_w}:{ref_h},fps={ref_fps},setsar=1[v_a]")
            video_idx += 1
            filter_parts.append(f"[{video_idx}:v]scale={ref_w}:{ref_h},fps={ref_fps},setsar=1[v_b]")
            video_idx += 1

            # 拼接视频流
            video_parts = []
            if cover_at_front:
                video_parts.append("[v_cover_front]")
            video_parts.extend(["[v_a]", "[v_b]"])
            if cover_at_back:
                video_parts.append("[v_cover_back]")
            filter_parts.append(f"{''.join(video_parts)}concat=n={len(video_parts)}:v=1:a=0[outv]")

            # 音频处理：静音 + 音频A + 音频B
            audio_filter_parts = []

            if cover_at_front and total_silence > 0:
                filter_parts.append("anullsrc=channel_layout=stereo:sample_rate=44100[silence_front]")
                filter_parts.append(f"[silence_front]atrim=0:{total_silence},asetpts=PTS-STARTPTS[silence_front_padded]")
                audio_filter_parts.append("[silence_front_padded]")

            # 音频A
            filter_parts.append(f"[{audio_idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a_a]")
            audio_filter_parts.append("[a_a]")
            audio_idx += 1

            # 音频B
            filter_parts.append(f"[{audio_idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a_b]")
            audio_filter_parts.append("[a_b]")

            if cover_at_back and total_silence > 0:
                filter_parts.append("anullsrc=channel_layout=stereo:sample_rate=44100[silence_back]")
                filter_parts.append(f"[silence_back]atrim=0:{total_silence},asetpts=PTS-STARTPTS[silence_back_padded]")
                audio_filter_parts.append("[silence_back_padded]")

            # 合并音频
            concat_audio = "".join(audio_filter_parts)
            filter_parts.append(f"{concat_audio}concat=n={len(audio_filter_parts)}:v=0:a=1[outa]")

            filter_str = ";".join(filter_parts)

            # 编码参数统一走 run_ffmpeg_with_fallback（硬件失败自动回退软件）
            def _build_concat_cmd(params):
                _codec, _enc_preset, _quality_args = params
                _cmd = ["ffmpeg"]
                # 必须按 filter_complex 中 [N:v]/[N:a] 引用的顺序添加 -i 输入：
                #   有封面：cover_video(0) + video_a(1) + video_b(2)
                #   无封面：video_a(0) + video_b(1)
                # P0/P1 重构（4608376）时漏写，导致 ffmpeg 报
                # "Error binding filtergraph inputs/outputs: Invalid argument"。
                if cover_video:
                    _cmd.extend(["-i", cover_video])
                _cmd.extend(["-i", video_a, "-i", video_b])
                _cmd.extend([
                    "-filter_complex", filter_str,
                    "-map", "[outv]", "-map", "[outa]",
                    "-c:v", _codec, "-preset", _enc_preset, *_quality_args,
                    "-c:a", "aac", "-b:a", "128k",
                    "-shortest",
                    "-y", output_path
                ])
                return _cmd

            run_ffmpeg_with_fallback(
                _build_concat_cmd, crf=23,
                timeout=600, error_message="concat failed", output_path=output_path,
            )

            return output_path
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _concat_video_only(self, input_paths, output_path, ref_w, ref_h, ref_fps):
        """拼接视频（仅视频流，无音频）- 使用 concat demuxer"""
        # 先统一所有视频格式
        normalized_paths = []
        for i, p in enumerate(input_paths):
            norm_path = os.path.join(os.path.dirname(output_path), f"norm_{i}.mp4")

            def _build_norm_cmd(params):
                _codec, _enc_preset, _quality_args = params
                return [
                    "ffmpeg", "-i", p,
                    "-vf", f"scale={ref_w}:{ref_h},fps={ref_fps},setsar=1",
                    "-c:v", _codec, "-preset", _enc_preset, *_quality_args,
                    "-pix_fmt", "yuv420p",
                    "-an", "-y", norm_path
                ]

            try:
                run_ffmpeg_with_fallback(
                    _build_norm_cmd, crf=23,
                    timeout=120, error_message="normalize video failed",
                    output_path=norm_path,
                )
                if os.path.exists(norm_path):
                    normalized_paths.append(norm_path)
            except FFmpegError:
                continue

        if not normalized_paths:
            raise RuntimeError("No valid video paths to concatenate")

        # 使用 concat demuxer
        concat_list = os.path.join(os.path.dirname(output_path), "concat_list.txt")
        with open(concat_list, "w", encoding="utf-8") as f:
            for p in normalized_paths:
                f.write(f"file '{p}'\n")

        def _build_concat_demux_cmd(params):
            _codec, _enc_preset, _quality_args = params
            return [
                "ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_list,
                "-c:v", _codec, "-preset", _enc_preset, *_quality_args,
                "-pix_fmt", "yuv420p",
                "-an", "-y", output_path
            ]

        concat_error = None
        try:
            run_ffmpeg_with_fallback(
                _build_concat_demux_cmd, crf=23,
                timeout=300, error_message="concat video failed", output_path=output_path,
            )
        except FFmpegError as e:
            concat_error = e

        # 清理
        if os.path.exists(concat_list):
            os.remove(concat_list)
        for p in normalized_paths:
            if os.path.exists(p):
                os.remove(p)

        if concat_error is not None:
            raise RuntimeError(f"concat video failed: {concat_error.stderr}") from concat_error

    def _extract_audio(self, video_path, audio_path):
        """提取音频"""
        cmd = [
            "ffmpeg", "-i", video_path, "-vn", "-acodec", "copy",
            "-y", audio_path
        ]
        try:
            run_ffmpeg(cmd, timeout=60, error_message="extract audio failed",
                       output_path=audio_path)
        except FFmpegError:
            return False
        # 如果提取失败（可能没有音频），返回False
        return os.path.exists(audio_path)

    def _merge_audio(self, audio_parts, output_path):
        """合并音频：支持静音和文件"""
        if not audio_parts:
            # 没有音频，创建静音
            cmd = [
                "ffmpeg", "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t", "1", "-c:a", "aac", "-y", output_path
            ]
            try:
                run_ffmpeg(cmd, error_message="create silence failed",
                           output_path=output_path)
            except FFmpegError:
                pass
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
            try:
                run_ffmpeg(cmd, timeout=60, error_message="create silence failed",
                           output_path=silence_path)
            except FFmpegError:
                pass
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
        try:
            run_ffmpeg(cmd, timeout=60, error_message="merge audio failed",
                       output_path=output_path)
        except FFmpegError:
            pass

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
        run_ffmpeg(cmd, timeout=300, error_message="add audio failed",
                   output_path=output_path)

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

            if self.cover_source == "video_b_frame":
                cover_img = None
            else:
                cover_img = random.choice(cover_images) if cover_images else None

            if callback:
                callback(i, total, f"拼接: {name_a} + {name_b}", 0)

            self.concat_pair(va, vb, output_path, cover_img)
            results.append(output_path)

            if callback:
                callback(i + 1, total, f"完成: {name_a} + {name_b}", 100)

        return results
