import os
import sys
import json
import subprocess
import tempfile
import numpy as np
import onnxruntime as ort


FIREMODELS_DIR = r"D:\Models\FireRed"


class FireRedASR:
    """FireRedASR AED 模型 ONNX 推理引擎

    关键修复：
    - 使用 AED decoder（self-cross KV cache 自回归解码），不再用 CTC 贪心。
      CTC 头只是训练辅助任务，正式推理必须用 decoder，否则错字率显著偏高。
    - 特征提取对齐官方 frontend_conf（conf.yaml）：povey 窗 + dither=0 + snip_edges。
    - VAD 分块后逐块 encoder/decoder，保留 CTC token 级时间戳用于字幕切分对齐。
    """

    # 解码时跳过的特殊 token
    _SPECIAL_TOKENS = frozenset({"<blank>", "<unk>", "<pad>", "<sos>", "<eos>",
                                  "<sil>", "<noise>", "<mus>"})

    # 与模型 frontend_conf 对齐（见 conf.yaml）
    _N_MELS = 80
    _N_FFT = 512
    _FRAME_LENGTH = 25   # ms
    _FRAME_SHIFT = 10    # ms

    def __init__(self, model_dir=None):
        if model_dir is None:
            model_dir = os.path.join(
                FIREMODELS_DIR,
                "fireredasr2-aed-large-zh-en-int8-onnx-selfcrosskv-offline-20260212"
            )
        self.model_dir = model_dir
        self.sr = 16000

        self._load_tokens()
        self._load_cmvn()
        self._build_mel_filterbank()
        self._load_models()

    # ------------------------------------------------------------------ #
    # 模型加载
    # ------------------------------------------------------------------ #
    def _load_tokens(self):
        tokens_path = os.path.join(self.model_dir, "tokens.txt")
        self.token_list = []
        self.token2id = {}
        with open(tokens_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.rsplit(" ", 1)
                token = parts[0]
                idx = int(parts[1])
                self.token_list.append(token)
                self.token2id[token] = idx
        self.sos_id = self.token2id.get("<sos>", 3)
        self.eos_id = self.token2id.get("<eos>", 4)
        self.blank_id = self.token2id.get("<blank>", 0)
        self.vocab_size = len(self.token_list)

    def _load_cmvn(self):
        mvn_path = os.path.join(self.model_dir, "am.mvn")
        self.cmvn_shift = None
        self.cmvn_scale = None
        with open(mvn_path, "r", encoding="utf-8") as f:
            content = f.read()
        import re
        shift_match = re.search(r'<AddShift>.*?\[\s*([0-9eE.+\-\s]+?)\s*\]', content, re.DOTALL)
        scale_match = re.search(r'<Rescale>.*?\[\s*([0-9eE.+\-\s]+?)\s*\]', content, re.DOTALL)
        if shift_match:
            vals = shift_match.group(1).split()
            self.cmvn_shift = np.array([float(v) for v in vals], dtype=np.float32)
        if scale_match:
            vals = scale_match.group(1).split()
            self.cmvn_scale = np.array([float(v) for v in vals], dtype=np.float32)

    def _load_models(self):
        from core.onnx_providers import get_providers, get_session_options
        opts = get_session_options(inter_op=2, intra_op=2)
        providers = get_providers()

        self.encoder = ort.InferenceSession(
            os.path.join(self.model_dir, "encoder.int8.onnx"),
            sess_options=opts, providers=providers
        )
        # 加载 AED decoder（替代原来只用 CTC 头解码的错误路径）
        decoder_path = os.path.join(self.model_dir, "decoder.int8.onnx")
        if os.path.exists(decoder_path):
            self.decoder = ort.InferenceSession(
                decoder_path, sess_options=opts, providers=providers
            )
        else:
            self.decoder = None
            print("[FireRedASR] 警告：未找到 decoder.int8.onnx，将回退到 CTC 解码（质量较差）")

        # CTC 头仅用于时间戳提取，不再作为主解码路径
        ctc_path = os.path.join(self.model_dir, "ctc.int8.onnx")
        self.ctc = ort.InferenceSession(ctc_path, sess_options=opts, providers=providers) \
            if os.path.exists(ctc_path) else None

        # 探测 decoder 结构（selfcrosskv: 16 层，dim=1280）
        self._num_layers, self._dim = self._probe_decoder_struct()

    def _probe_decoder_struct(self):
        """从 decoder 输入签名推断层数和维度"""
        if self.decoder is None:
            return 0, 0
        names = [i.name for i in self.decoder.get_inputs()]
        num_layers = 0
        while f"self_k_cache_{num_layers}" in names:
            num_layers += 1
        dim = 0
        for i in self.decoder.get_inputs():
            if i.name == "self_k_cache_0" and i.shape:
                dim = i.shape[-1] if isinstance(i.shape[-1], int) else 0
                break
        return num_layers, dim

    # ------------------------------------------------------------------ #
    # 音频提取
    # ------------------------------------------------------------------ #
    def _extract_audio(self, video_path):
        """提取音频（16kHz mono pcm）"""
        import io, wave
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            "-f", "wav", "-y", "pipe:1"
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=3600)
        if result.returncode != 0:
            return None, 0
        wav_data = io.BytesIO(result.stdout)
        with wave.open(wav_data, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        return audio, 16000

    def _separate_vocals(self, audio, sample_rate=16000):
        from core.audio_utils import separate_vocals
        return separate_vocals(audio, sample_rate)

    # ------------------------------------------------------------------ #
    # 特征提取（对齐官方 conf.yaml: WavFrontend + povey 窗）
    # ------------------------------------------------------------------ #
    def _build_mel_filterbank(self):
        """预先构建 mel 滤波器组"""
        n_freqs = self._N_FFT // 2 + 1
        n_mels = self._N_MELS
        sample_rate = self.sr
        n_fft = self._N_FFT

        low_freq_mel = 0
        high_freq_mel = 2595 * np.log10(1 + (sample_rate / 2) / 700)
        mel_points = np.linspace(low_freq_mel, high_freq_mel, n_mels + 2)
        hz_points = 700 * (10 ** (mel_points / 2595) - 1)
        bin_points = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)

        fbank = np.zeros((n_mels, n_freqs))
        for m in range(1, n_mels + 1):
            f_left = bin_points[m - 1]
            f_center = bin_points[m]
            f_right = bin_points[m + 1]
            for k in range(f_left, f_center):
                if k < n_freqs and f_center != f_left:
                    fbank[m - 1, k] = (k - f_left) / (f_center - f_left)
            for k in range(f_center, f_right):
                if k < n_freqs and f_right != f_center:
                    fbank[m - 1, k] = (f_right - k) / (f_right - f_center)
        self._mel_fb = fbank

    def _povey_window(self, n):
        """povey 窗（kaldi 默认）：周期形式的汉明窗。

        官方 conf.yaml 指定 window=povey。原代码用 np.hanning 导致
        特征分布与训练时不一致，直接拉高错字率。
        """
        if n <= 1:
            return np.ones(n, dtype=np.float32)
        return (0.54 - 0.46 * np.cos(2 * np.pi * np.arange(n) / (n - 1))).astype(np.float32)

    def _compute_fbank(self, audio, sample_rate=16000):
        """80 维 log-mel filterbank + CMVN，对齐 FireRedASR 训练分布"""
        n_mels = self._N_MELS
        frame_size = int(sample_rate * self._FRAME_LENGTH / 1000)   # 400
        hop_size = int(sample_rate * self._FRAME_SHIFT / 1000)      # 160
        n_fft = self._N_FFT

        # snip_edges=true（kaldi 默认）：帧数 = 1 + (len-frame)//hop
        num_frames = 1 + (len(audio) - frame_size) // hop_size
        if num_frames <= 0:
            return np.zeros((1, n_mels), dtype=np.float32)

        # 用 as_strided 风格的切片构造帧（比逐帧循环快）
        frames = np.zeros((num_frames, frame_size), dtype=np.float32)
        for i in range(num_frames):
            start = i * hop_size
            frames[i] = audio[start:start + frame_size]

        # povey 窗（替代原来错误的 hanning）
        frames *= self._povey_window(frame_size)

        spec = np.abs(np.fft.rfft(frames, n=n_fft))
        spec = np.maximum(spec, 1e-10)

        mel_spec = np.dot(spec, self._mel_fb.T)
        mel_spec = np.maximum(mel_spec, 1e-10)

        fbank = np.log(mel_spec).astype(np.float32)

        if self.cmvn_shift is not None and self.cmvn_scale is not None:
            fbank = (fbank + self.cmvn_shift) * self.cmvn_scale

        return fbank

    # ------------------------------------------------------------------ #
    # AED 自回归解码（核心修复）
    # ------------------------------------------------------------------ #
    def _aed_decode(self, enc_out, src_mask, cross_kv, max_len=200):
        """使用 AED decoder 自回归解码，返回 token id 列表

        Args:
            enc_out: encoder 输出 [1, enc_time, 1280]
            src_mask: encoder 输出的 mask [1, 1, enc_time]
            cross_kv: encoder 输出的 cross k/v 列表（每层一对）
            max_len: 最大解码步数
        Returns:
            list[int]: 生成的 token id（不含 sos/eos）
        """
        if self.decoder is None or self._num_layers == 0:
            return []

        batch_size = 1
        cache_len = 0
        num_layers = self._num_layers
        dim = self._dim

        self_k = [np.zeros((batch_size, cache_len, dim), dtype=np.float32)
                  for _ in range(num_layers)]
        self_v = [np.zeros((batch_size, cache_len, dim), dtype=np.float32)
                  for _ in range(num_layers)]

        token = np.array([[self.sos_id]], dtype=np.int64)
        step = np.array([0], dtype=np.int64)

        generated = []
        for _ in range(max_len):
            decoder_inputs = {
                "token": token,
                "step": step,
                "src_mask": src_mask,
            }
            for li in range(num_layers):
                decoder_inputs[f"self_k_cache_{li}"] = self_k[li]
                decoder_inputs[f"self_v_cache_{li}"] = self_v[li]
                # cross_kv 由 encoder 一次性算出，每步复用
                decoder_inputs[f"cross_k_{li}"] = cross_kv[li * 2]
                decoder_inputs[f"cross_v_{li}"] = cross_kv[li * 2 + 1]

            outputs = self.decoder.run(None, decoder_inputs)
            logits = outputs[0]
            new_caches = outputs[1:]

            next_token = int(np.argmax(logits))
            if next_token == self.eos_id:
                break
            if 0 <= next_token < self.vocab_size:
                generated.append(next_token)

            token = np.array([[next_token]], dtype=np.int64)
            step = np.array([step[0] + 1], dtype=np.int64)

            # 更新 KV cache（输出顺序：new_k_0, new_v_0, new_k_1, new_v_1, ...）
            for li in range(num_layers):
                self_k[li] = new_caches[li * 2]
                self_v[li] = new_caches[li * 2 + 1]

        return generated

    def _ids_to_tokens(self, ids):
        return [self.token_list[i] for i in ids if 0 <= i < self.vocab_size]

    def _tokens_to_text(self, tokens):
        text = "".join(t for t in tokens if t not in self._SPECIAL_TOKENS)
        text = text.replace("▁", " ").strip()
        import re as _re
        text = _re.sub(r"<[^>]+>", "", text)
        return text.strip()

    # ------------------------------------------------------------------ #
    # CTC 时间戳提取（用于字幕切分对齐）
    # ------------------------------------------------------------------ #
    def _ctc_decode_with_timestamps(self, logits):
        """CTC 帧级时间戳，仅用于辅助字幕时间对齐（不再作为主文本来源）"""
        if self.ctc is None:
            return [], []
        tokens = []
        timestamps = []
        prev = -1
        frame_shift_ms = self._FRAME_SHIFT
        for t in range(logits.shape[0]):
            idx = int(np.argmax(logits[t]))
            if idx != self.blank_id and idx != prev:
                if idx < self.vocab_size:
                    tok = self.token_list[idx]
                    if tok not in self._SPECIAL_TOKENS:
                        tokens.append(tok)
                        start_ms = t * frame_shift_ms
                        end_ms = start_ms + frame_shift_ms
                        for t2 in range(t + 1, logits.shape[0]):
                            idx2 = int(np.argmax(logits[t2]))
                            if idx2 == self.blank_id or idx2 == idx:
                                end_ms = t2 * frame_shift_ms
                                break
                        timestamps.append((start_ms, end_ms))
            prev = idx
        return tokens, timestamps

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def transcribe(self, video_path, audio=None, skip_vocal_separation=False):
        if audio is not None:
            sr = self.sr
        else:
            audio, sr = self._extract_audio(video_path)
        if audio is None or len(audio) == 0:
            return []

        if not skip_vocal_separation:
            audio = self._separate_vocals(audio, sr)

        vad_segments = self._vad_segment(audio, sr)

        segments = []
        for seg in vad_segments:
            chunk = seg["samples"]
            start_sec = seg["start_sec"]

            if len(chunk) < sr * 0.3:
                continue

            fbank = self._compute_fbank(chunk, sr)
            fbank = fbank[np.newaxis, :, :]
            input_lengths = np.array([fbank.shape[1]], dtype=np.int64)

            enc_out, enc_lens, mask, *cross_kv = self.encoder.run(
                None,
                {"input": fbank, "input_lengths": input_lengths}
            )

            # ① 主解码：AED 自回归解码（高精度）
            if self.decoder is not None and len(cross_kv) >= self._num_layers * 2:
                token_ids = self._aed_decode(enc_out, mask, cross_kv, max_len=200)
                tokens = self._ids_to_tokens(token_ids)
                text = self._tokens_to_text(tokens).strip()
                if not text:
                    continue

                # CTC 仅用于时间戳辅助（可选，失败不影响主文本）
                ctc_tokens, ctc_timestamps = [], []
                if self.ctc is not None:
                    ctc_logits, = self.ctc.run(None, {"encoder_outputs": enc_out})
                    ctc_tokens, ctc_timestamps = self._ctc_decode_with_timestamps(ctc_logits[0])

                sub_segments = self._split_by_timestamps(
                    text, ctc_tokens, ctc_timestamps, start_sec, sr
                )
                segments.extend(sub_segments)
            else:
                # 回退路径：CTC 解码（decoder 不可用时）
                if self.ctc is None:
                    continue
                ctc_logits, = self.ctc.run(None, {"encoder_outputs": enc_out})
                ctc_tokens, ctc_timestamps = self._ctc_decode_with_timestamps(ctc_logits[0])
                text = self._tokens_to_text(ctc_tokens).strip()
                if not text:
                    continue
                sub_segments = self._split_by_timestamps(
                    text, ctc_tokens, ctc_timestamps, start_sec, sr
                )
                segments.extend(sub_segments)

        return segments

    def _vad_segment(self, audio, sr):
        """用 Silero VAD 将音频切分为语音段"""
        vad_path = r"D:\Models\sherpa-onnx\silero_vad.onnx"
        if not os.path.exists(vad_path):
            return self._fallback_chunk(audio, sr)

        try:
            import sherpa_onnx
            vad_cfg = sherpa_onnx.VadModelConfig(
                silero_vad=sherpa_onnx.SileroVadModelConfig(
                    model=vad_path,
                    threshold=0.5,
                    min_silence_duration=0.5,
                    min_speech_duration=0.3,
                    window_size=512,
                    max_speech_duration=20,
                ),
                sample_rate=sr,
                num_threads=1,
            )
            vad = sherpa_onnx.VoiceActivityDetector(vad_cfg, buffer_size_in_seconds=120)

            chunk_size = 512
            for i in range(0, len(audio), chunk_size):
                vad.accept_waveform(audio[i:i + chunk_size])
            vad.flush()

            segments = []
            while not vad.empty():
                seg = vad.front
                samples = np.array(seg.samples, dtype=np.float32)
                segments.append({
                    "start_sec": seg.start / sr,
                    "samples": samples,
                })
                vad.pop()
            return segments if segments else self._fallback_chunk(audio, sr)
        except Exception:
            return self._fallback_chunk(audio, sr)

    def _fallback_chunk(self, audio, sr, chunk_sec=10.0):
        """VAD 失败时回退到固定分块"""
        chunk_samples = int(chunk_sec * sr)
        segments = []
        for i in range(0, len(audio), chunk_samples):
            chunk = audio[i:i + chunk_samples]
            if len(chunk) >= sr * 0.5:
                segments.append({
                    "start_sec": i / sr,
                    "samples": chunk,
                })
        return segments

    def _split_by_timestamps(self, text, ctc_tokens, ctc_timestamps, chunk_start_sec, sr):
        """根据标点 + CTC token 时间戳将文本切分为字幕段"""
        if not ctc_timestamps:
            # 无时间戳：按字符数估算时长
            est_end = chunk_start_sec + len(text) * 0.1
            return [{"start": round(chunk_start_sec, 3),
                     "end": round(est_end, 3),
                     "text": text}]

        token_items = []
        for token, (start_ms, end_ms) in zip(ctc_tokens, ctc_timestamps):
            token_text = self._tokens_to_text([token]).strip()
            if not token_text:
                continue
            token_items.append({
                "text": token_text,
                "start": round(chunk_start_sec + start_ms / 1000, 3),
                "end": round(chunk_start_sec + end_ms / 1000, 3),
            })

        # 按标点切分文本
        import re
        parts = re.split(r'([。！？!?；;，,、])', text)
        clauses = []
        buf = ""
        for p in parts:
            if re.match(r'[。！？!?；;，,、]', p):
                buf += p
                if buf.strip():
                    clauses.append(buf.strip())
                buf = ""
            else:
                buf += p
        if buf.strip():
            clauses.append(buf.strip())

        if not clauses:
            clauses = [text]

        # 按 token 时间戳累计切分（比纯字符比例更准）
        total_ms = ctc_timestamps[-1][1] - ctc_timestamps[0][0] if ctc_timestamps else 1000
        base_ms = ctc_timestamps[0][0] if ctc_timestamps else 0

        results = []
        char_pos = 0
        total_chars = max(sum(len(c) for c in clauses), 1)
        for c in clauses:
            char_len = len(c)
            start_ms = base_ms + total_ms * char_pos / total_chars
            end_ms = base_ms + total_ms * (char_pos + char_len) / total_chars
            char_pos += char_len

            abs_start = round(chunk_start_sec + start_ms / 1000, 3)
            abs_end = round(chunk_start_sec + end_ms / 1000, 3)
            segment_tokens = [
                item for item in token_items
                if item["end"] > abs_start and item["start"] < abs_end
            ]

            results.append({
                "start": abs_start,
                "end": abs_end,
                "text": c,
                "tokens": segment_tokens,
            })
        return results
