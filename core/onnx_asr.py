# -*- coding: utf-8 -*-
"""
统一 ONNX ASR 引擎
支持 SenseVoice / FunASR Paraformer / FireRedASR
共享降噪、人声分离、标点恢复、热词、字幕切分
"""
import os
import sys
import subprocess
import io
import wave
import re
import numpy as np
import onnxruntime as ort


class OnnxASR:
    """统一 ONNX 推理引擎"""

    MODEL_TYPES = ("SenseVoice", "FunASR (Paraformer)", "FireRedASR")

    def __init__(self, model_dir, model_type, lang="zh"):
        self.model_dir = model_dir
        self.model_type = model_type
        self.sr = 16000
        self.lang = lang

        self._base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self._models_dir = r"D:\Models\sherpa-onnx"

        # 加载模型
        self._init_model()

        # 共享组件
        self._denoiser = self._init_denoise()
        self._punctuator = self._init_punct()

    # ------------------------------------------------------------------ #
    # 模型初始化
    # ------------------------------------------------------------------ #
    def _init_model(self):
        if self.model_type == "SenseVoice":
            self._init_sensevoice()
        elif self.model_type == "FunASR (Paraformer)":
            self._init_paraformer()
        elif self.model_type == "FireRedASR":
            self._init_fireredasr()
        else:
            raise ValueError(f"不支持的模型类型: {self.model_type}")

    def _init_sensevoice(self):
        from core.sensevoice_onnx import SenseVoice_ONNX
        self._engine = SenseVoice_ONNX(self.model_dir, lang=self.lang)

    def _init_paraformer(self):
        from core.funasr_onnx import FunASR_ONNX
        self._engine = FunASR_ONNX(self.model_dir)

    def _init_fireredasr(self):
        from core.fireredasr import FireRedASR
        self._engine = FireRedASR(self.model_dir)

    # ------------------------------------------------------------------ #
    # 共享组件初始化
    # ------------------------------------------------------------------ #
    def _init_denoise(self):
        model_path = os.path.join(self._models_dir, "gtcrn_simple.onnx")
        if not os.path.exists(model_path):
            return None
        try:
            import sherpa_onnx
            cfg = sherpa_onnx.OfflineSpeechDenoiserConfig(
                model=sherpa_onnx.OfflineSpeechDenoiserModelConfig(
                    gtcrn=sherpa_onnx.OfflineSpeechDenoiserGtcrnModelConfig(model=model_path),
                    num_threads=2,
                ),
            )
            return sherpa_onnx.OfflineSpeechDenoiser(cfg)
        except Exception:
            return None

    def _init_punct(self):
        model_path = os.path.join(self._models_dir, "punct-ct-transformer", "model.onnx")
        if not os.path.exists(model_path):
            return None
        try:
            import sherpa_onnx
            cfg = sherpa_onnx.OfflinePunctuationConfig(
                model=sherpa_onnx.OfflinePunctuationModelConfig(
                    ct_transformer=model_path,
                    num_threads=2,
                ),
            )
            return sherpa_onnx.OfflinePunctuation(cfg)
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # 共享音频处理
    # ------------------------------------------------------------------ #
    def _extract_audio(self, video_path):
        """提取音频"""
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", str(self.sr), "-ac", "1",
            "-f", "wav", "-y", "pipe:1"
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=3600)
        if result.returncode != 0:
            return None
        wav_data = io.BytesIO(result.stdout)
        with wave.open(wav_data, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        return audio

    def _denoise(self, audio):
        if self._denoiser is None:
            return audio
        try:
            result = self._denoiser.run(audio, self.sr)
            return np.array(result.samples, dtype=np.float32)
        except Exception:
            return audio

    def _extract_vocals(self, audio):
        try:
            from core.audio_utils import separate_vocals
            return separate_vocals(audio, self.sr)
        except Exception:
            return audio

    def _add_punctuation(self, text):
        if self._punctuator is None or not text:
            return text
        try:
            return self._punctuator.add_punctuation(text)
        except Exception:
            return text

    # ------------------------------------------------------------------ #
    # 标点清理
    # ------------------------------------------------------------------ #
    @staticmethod
    def _clean_punctuation(text):
        text = re.sub(r'[。，、！？；：]{2,}', lambda m: m.group(0)[0], text)
        text = re.sub(r'[,\.!?;:]{2,}', lambda m: m.group(0)[0], text)
        text = text.strip("，,。.、！!？?；;：: ")
        return text

    @staticmethod
    def _is_reliable(text):
        if not text or len(text.strip()) < 2:
            return False
        alpha_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        if alpha_count == 0 and len(text) > 1:
            return False
        return True

    # ------------------------------------------------------------------ #
    # 字幕切分
    # ------------------------------------------------------------------ #
    def _split_subtitle(self, text, start_sec, end_sec, max_chars=20):
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

        merged = [m for m in merged if self._is_reliable(m)]
        if not merged:
            return []

        total_dur = end_sec - start_sec
        seg_dur = total_dur / len(merged)
        results = []
        for i, m in enumerate(merged):
            s = round(start_sec + i * seg_dur, 3)
            e = round(start_sec + (i + 1) * seg_dur, 3)
            results.append({"start": s, "end": e, "text": m})
        return results

    # ------------------------------------------------------------------ #
    # 公开接口
    # ------------------------------------------------------------------ #
    def transcribe(self, video_path, use_vocal_extraction=False, use_denoise=False):
        """统一识别接口

        Args:
            video_path: 视频路径
            use_vocal_extraction: 是否做人声分离（耗时较长）
            use_denoise: 是否做降噪（可能影响识别率）

        返回:
            [{"start": float(秒), "end": float(秒), "text": str}, ...]
        """
        import traceback as tb
        try:
            audio = self._extract_audio(video_path)
            if audio is None or audio.size == 0:
                return []

            # 人声分离（可选）
            if use_vocal_extraction:
                audio = self._extract_vocals(audio)

            # 降噪（可选，默认关闭，可能影响识别率）
            if use_denoise:
                audio = self._denoise(audio)

            # 调用具体引擎识别（传入已处理的 audio，跳过引擎内部重复处理）
            if self.model_type == "FireRedASR":
                raw_segments = self._engine.transcribe(video_path, audio=audio, skip_vocal_separation=True)
            else:
                raw_segments = self._engine.transcribe(video_path, audio=audio)
            if not raw_segments:
                return []

            # 后处理：标点恢复 + 去重 + 置信度过滤（保留引擎原始时间戳）
            results = []
            for seg in raw_segments:
                text = seg["text"].strip()
                if not text:
                    continue

                # 过滤 SenseVoice 特殊标签
                text = re.sub(r"<\|[^|]*\|>", "", text).strip()
                if not text:
                    continue

                # 标点恢复（对无标点的文本添加标点）
                has_punct = bool(re.search(r'[。！？!?；;，,]', text))
                if not has_punct:
                    text = self._add_punctuation(text)

                # 标点清理（不二次切分，保留引擎原始时间戳）
                text = self._clean_punctuation(text)

                results.append({
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": text,
                })

            return results
        except Exception:
            log_path = os.path.join(self._base_dir, "core", "onnx_asr_error.log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"video: {video_path}\nmodel: {self.model_type}\n")
                tb.print_exc(file=f)
            return []
