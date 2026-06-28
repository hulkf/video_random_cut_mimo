# -*- coding: utf-8 -*-
"""
sherpa-onnx 统一 ASR 引擎
支持 SenseVoice / Paraformer / FireRedASR，内置 VAD + 降噪 + 标点 + 热词
"""
import os
import re
import subprocess
import io
import wave
import numpy as np
import sherpa_onnx


class SherpaASR:
    """基于 sherpa-onnx 的统一 ASR 引擎"""

    MODEL_TYPES = ("SenseVoice", "FunASR (Paraformer)")

    VAD_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"

    def __init__(self, model_dir, model_type, lang="zh",
                 enable_denoise=True, enable_punct=True, hotwords_file=None):
        self.model_dir = model_dir
        self.model_type = model_type
        self.sr = 16000
        self.lang = lang

        self._base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self._models_dir = r"D:\Models\sherpa-onnx"

        self._vad = self._init_vad()
        self._recognizer = self._init_recognizer()

        self._denoiser = self._init_denoise() if enable_denoise else None
        self._punctuator = self._init_punct() if enable_punct else None
        self._hotwords_file = hotwords_file or self._default_hotwords()

    # ------------------------------------------------------------------ #
    # 组件初始化
    # ------------------------------------------------------------------ #
    def _init_vad(self):
        vad_path = self._ensure_model("silero_vad.onnx", self.VAD_URL)
        cfg = sherpa_onnx.VadModelConfig(
            silero_vad=sherpa_onnx.SileroVadModelConfig(
                model=vad_path,
                threshold=0.5,
                min_silence_duration=0.5,
                min_speech_duration=0.25,
                window_size=512,
                max_speech_duration=30,
            ),
            sample_rate=self.sr,
            num_threads=1,
        )
        return sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=120)

    def _init_recognizer(self):
        if self.model_type == "SenseVoice":
            return self._init_sensevoice()
        elif self.model_type == "FunASR (Paraformer)":
            return self._init_paraformer()
        else:
            raise ValueError(f"不支持的模型类型: {self.model_type}")

    def _init_sensevoice(self):
        model_path = os.path.join(self.model_dir, "model.1.onnx")
        if not os.path.exists(model_path):
            model_path = os.path.join(self.model_dir, "model.onnx")
        tokens_path = os.path.join(self.model_dir, "tokens.txt")
        if not os.path.exists(tokens_path):
            self._generate_tokens_from_bpe()

        try:
            return sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=model_path,
                tokens=tokens_path,
                num_threads=4,
                sample_rate=self.sr,
                feature_dim=80,
                language=self.lang,
                use_itn=True,
                debug=False,
            )
        except Exception as e:
            raise RuntimeError(f"加载 SenseVoice 模型失败: {e}\n请检查模型文件是否完整")

    def _init_paraformer(self):
        model_path = os.path.join(self.model_dir, "model.onnx")
        tokens_path = os.path.join(self.model_dir, "tokens.txt")
        try:
            return sherpa_onnx.OfflineRecognizer.from_paraformer(
                paraformer=model_path,
                tokens=tokens_path,
                num_threads=4,
                sample_rate=self.sr,
                feature_dim=80,
                debug=False,
            )
        except Exception as e:
            raise RuntimeError(f"加载 Paraformer 模型失败: {e}\n请检查模型文件是否完整")

    def _init_denoise(self):
        model_path = os.path.join(self._models_dir, "gtcrn_simple.onnx")
        if not os.path.exists(model_path):
            return None
        cfg = sherpa_onnx.OfflineSpeechDenoiserConfig(
            model=sherpa_onnx.OfflineSpeechDenoiserModelConfig(
                gtcrn=sherpa_onnx.OfflineSpeechDenoiserGtcrnModelConfig(model=model_path),
                num_threads=2,
            ),
        )
        try:
            return sherpa_onnx.OfflineSpeechDenoiser(cfg)
        except Exception:
            return None

    def _init_punct(self):
        model_path = os.path.join(self._models_dir, "punct-ct-transformer", "model.onnx")
        if not os.path.exists(model_path):
            return None
        cfg = sherpa_onnx.OfflinePunctuationConfig(
            model=sherpa_onnx.OfflinePunctuationModelConfig(
                ct_transformer=model_path,
                num_threads=2,
            ),
        )
        try:
            return sherpa_onnx.OfflinePunctuation(cfg)
        except Exception:
            return None

    def _default_hotwords(self):
        path = os.path.join(self._models_dir, "hotwords.txt")
        if os.path.exists(path):
            return path
        return ""

    def _ensure_model(self, filename, url):
        path = os.path.join(self._models_dir, filename)
        if os.path.exists(path):
            return path
        os.makedirs(self._models_dir, exist_ok=True)
        try:
            import urllib.request
            urllib.request.urlretrieve(url, path)
        except Exception:
            pass
        return path

    # ------------------------------------------------------------------ #
    # BPE tokens 生成 (SenseVoice)
    # ------------------------------------------------------------------ #
    def _generate_tokens_from_bpe(self):
        import struct
        bpe_path = os.path.join(self.model_dir, "chn_jpn_yue_eng_ko_spectok.bpe.model")
        tokens_path = os.path.join(self.model_dir, "tokens.txt")

        with open(bpe_path, "rb") as f:
            data = f.read()

        pieces = []
        pos = 0
        while pos < len(data):
            try:
                tag, new_pos = self._read_varint(data, pos)
                field = tag >> 3
                wire = tag & 0x07
                if wire == 2:
                    length, new_pos = self._read_varint(data, new_pos)
                    if field == 1:
                        sub_data = data[new_pos:new_pos+length]
                        piece = self._parse_piece_str(sub_data)
                        pieces.append(piece)
                    new_pos += length
                elif wire == 0:
                    _, new_pos = self._read_varint(data, new_pos)
                elif wire == 5:
                    new_pos += 4
                else:
                    break
                pos = new_pos
            except Exception:
                break

        with open(tokens_path, "w", encoding="utf-8") as f:
            for i, p in enumerate(pieces):
                f.write(f"{p} {i}\n")

    @staticmethod
    def _read_varint(data, pos):
        result = 0
        shift = 0
        while pos < len(data):
            b = data[pos]
            result |= (b & 0x7f) << shift
            pos += 1
            if (b & 0x80) == 0:
                break
            shift += 7
        return result, pos

    @staticmethod
    def _parse_piece_str(sub_data):
        pos = 0
        while pos < len(sub_data):
            tag, new_pos = SherpaASR._read_varint(sub_data, pos)
            field = tag >> 3
            wire = tag & 0x07
            if wire == 2:
                length, new_pos = SherpaASR._read_varint(sub_data, new_pos)
                if field == 1:
                    return sub_data[new_pos:new_pos+length].decode("utf-8")
                new_pos += length
            elif wire == 0:
                _, new_pos = SherpaASR._read_varint(sub_data, new_pos)
            elif wire == 5:
                new_pos += 4
            else:
                break
            pos = new_pos
        return ""

    # ------------------------------------------------------------------ #
    # 音频提取
    # ------------------------------------------------------------------ #
    def _extract_audio(self, video_path):
        import shutil, tempfile
        # 处理中文路径：复制到临时目录
        use_path = video_path
        tmp_file = None
        try:
            if any(ord(c) > 127 for c in video_path):
                ext = os.path.splitext(video_path)[1]
                tmp_file = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
                tmp_file.close()
                shutil.copy2(video_path, tmp_file.name)
                use_path = tmp_file.name
        except Exception:
            pass

        cmd = [
            "ffmpeg", "-i", use_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", str(self.sr), "-ac", "1",
            "-f", "wav", "-y", "pipe:1"
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=3600)

        if tmp_file and os.path.exists(tmp_file.name):
            os.remove(tmp_file.name)

        if result.returncode != 0:
            return None
        wav_data = io.BytesIO(result.stdout)
        with wave.open(wav_data, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        return audio

    # ------------------------------------------------------------------ #
    # 降噪
    # ------------------------------------------------------------------ #
    def _denoise(self, audio):
        """对音频进行降噪处理"""
        if self._denoiser is None:
            return audio
        try:
            result = self._denoiser.run(audio, self.sr)
            return np.array(result.samples, dtype=np.float32)
        except Exception:
            return audio

    # ------------------------------------------------------------------ #
    # 标点恢复
    # ------------------------------------------------------------------ #
    def _add_punctuation(self, text):
        """对文本添加标点"""
        if self._punctuator is None or not text:
            return text
        try:
            return self._punctuator.add_punctuation(text)
        except Exception:
            return text

    # ------------------------------------------------------------------ #
    # VAD 分段
    # ------------------------------------------------------------------ #
    def _vad_segment(self, audio):
        """使用 Silero VAD 将音频切分为语音段"""
        self._vad.reset()
        chunk_size = 512
        for i in range(0, len(audio), chunk_size):
            chunk = audio[i:i + chunk_size]
            self._vad.accept_waveform(chunk)
        self._vad.flush()

        segments = []
        while not self._vad.empty():
            seg = self._vad.front
            samples = np.array(seg.samples, dtype=np.float32)
            segments.append({
                "start": seg.start,
                "samples": samples,
            })
            self._vad.pop()
        return segments

    # ------------------------------------------------------------------ #
    # 识别
    # ------------------------------------------------------------------ #
    def _recognize_segment(self, samples):
        """识别单个音频段，返回 (text, timestamps)"""
        stream = self._recognizer.create_stream()
        stream.accept_waveform(self.sr, samples)
        self._recognizer.decode_stream(stream)
        result = stream.result
        text = result.text.strip()
        # timestamps 是 token 级时间戳（秒）
        timestamps = list(result.timestamps) if result.timestamps else []
        return text, timestamps

    # ------------------------------------------------------------------ #
    # 标点清理
    # ------------------------------------------------------------------ #
    @staticmethod
    def _clean_punctuation(text):
        """去除重复标点"""
        import re
        text = re.sub(r'[。，、！？；：]{2,}', lambda m: m.group(0)[0], text)
        text = re.sub(r'[,\.!?;:]{2,}', lambda m: m.group(0)[0], text)
        text = text.strip("，,。.、！!？?；;：: ")
        return text

    # ------------------------------------------------------------------ #
    # 置信度过滤
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_reliable(text):
        """判断文本段是否可靠（过滤垃圾段）"""
        if not text or len(text.strip()) < 2:
            return False
        # 纯符号/数字占比过高
        alpha_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        if alpha_count == 0 and len(text) > 1:
            return False
        return True

    # ------------------------------------------------------------------ #
    # 人声提取
    # ------------------------------------------------------------------ #
    def _extract_vocals(self, audio):
        """用 demucs 提取人声（公共模块）"""
        try:
            from core.audio_utils import separate_vocals
            return separate_vocals(audio, self.sr)
        except Exception:
            return audio

    # ------------------------------------------------------------------ #
    # 字幕切分
    # ------------------------------------------------------------------ #
    def _split_subtitle(self, text, start_sec, end_sec, timestamps=None, max_chars=20):
        """按标点切分字幕，优先使用 token 时间戳"""
        text = self._clean_punctuation(text)

        parts = re.split(r'([。！？!?；;，,、\n])', text)
        clauses = []
        buf = ""
        for p in parts:
            if re.match(r'[。！？!?；;，,、\n]', p):
                buf += p
                if buf.strip():
                    clauses.append(buf.strip())
                buf = ""
            else:
                buf += p
        if buf.strip():
            clauses.append(buf.strip())

        if not clauses:
            return []

        # 合并过短的子句，使每段字幕长度适中
        merged = []
        buf = ""
        for c in clauses:
            if buf and len(buf) + len(c) > max_chars:
                merged.append(buf)
                buf = c
            else:
                buf += c
        if buf:
            merged.append(buf)

        # 过滤不可靠段
        merged = [m for m in merged if self._is_reliable(m)]
        if not merged:
            return []

        # 使用 token 时间戳精确切分
        if timestamps and len(timestamps) >= 2:
            return self._split_by_timestamps(merged, timestamps, start_sec)

        # 无时间戳时按字符比例分配
        total_dur = end_sec - start_sec
        total_chars = max(sum(len(c) for c in merged), 1)
        results = []
        char_pos = 0
        for m in merged:
            char_len = len(m)
            s = round(start_sec + total_dur * char_pos / total_chars, 3)
            e = round(start_sec + total_dur * (char_pos + char_len) / total_chars, 3)
            char_pos += char_len
            results.append({"start": s, "end": e, "text": m})
        return results

    def _split_by_timestamps(self, clauses, timestamps, chunk_start_sec):
        """根据 token 时间戳精确切分字幕"""
        if not timestamps or not clauses:
            return []

        total_text_len = max(sum(len(c) for c in clauses), 1)
        total_time = timestamps[-1] if timestamps else 1.0

        results = []
        char_pos = 0
        for c in clauses:
            char_len = len(c)
            start_rel = total_time * char_pos / total_text_len
            end_rel = total_time * (char_pos + char_len) / total_text_len
            char_pos += char_len

            results.append({
                "start": round(chunk_start_sec + start_rel, 3),
                "end": round(chunk_start_sec + end_rel, 3),
                "text": c,
            })
        return results

    # ------------------------------------------------------------------ #
    # 公开接口
    # ------------------------------------------------------------------ #
    def transcribe(self, video_path, use_vocal_extraction=False):
        """对视频进行语音识别，返回 segment 列表

        返回:
            [{"start": float(秒), "end": float(秒), "text": str}, ...]
        """
        import traceback as tb
        import re
        try:
            audio = self._extract_audio(video_path)
            if audio is None or audio.size == 0:
                return []

            # 0. 人声提取
            if use_vocal_extraction:
                audio = self._extract_vocals(audio)

            # 1. 降噪
            audio = self._denoise(audio)

            # 2. VAD 分段
            vad_segments = self._vad_segment(audio)
            if not vad_segments:
                return []

            # 3. 逐段识别
            results = []
            for seg in vad_segments:
                samples = seg["samples"]
                start_sec = seg["start"] / self.sr

                text, timestamps = self._recognize_segment(samples)

                # 过滤 SenseVoice 特殊标签
                text = re.sub(r"<\|[^|]*\|>", "", text).strip()

                if not text:
                    continue

                # 4. 标点恢复
                text = self._add_punctuation(text)

                end_sec = start_sec + len(samples) / self.sr

                # 5. 按标点切分为短字幕段（使用 token 时间戳）
                sub_segments = self._split_subtitle(text, start_sec, end_sec, timestamps)
                results.extend(sub_segments)

            return results
        except Exception:
            log_path = os.path.join(self._base_dir, "core", "sherpa_asr_error.log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"video: {video_path}\nmodel: {self.model_type}\n")
                tb.print_exc(file=f)
            return []
