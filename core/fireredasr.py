import os
import sys
import json
import subprocess
import tempfile
import numpy as np
import onnxruntime as ort


FIREMODELS_DIR = r"D:\Models\FireRed"


class FireRedASR:
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
        self._load_models()

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
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        opts.inter_op_num_threads = 2
        opts.intra_op_num_threads = 2

        self.encoder = ort.InferenceSession(
            os.path.join(self.model_dir, "encoder.int8.onnx"), opts
        )
        self.ctc = ort.InferenceSession(
            os.path.join(self.model_dir, "ctc.int8.onnx"), opts
        )

    def _extract_audio(self, video_path):
        """提取音频"""
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

    def _mel_filterbank(self, n_freqs, n_mels, sample_rate, n_fft):
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
        return fbank

    def _compute_fbank(self, audio, sample_rate=16000, n_mels=80,
                       frame_length=25, frame_shift=10):
        frame_size = int(sample_rate * frame_length / 1000)
        hop_size = int(sample_rate * frame_shift / 1000)
        n_fft = 512
        n_freqs = n_fft // 2 + 1

        num_frames = 1 + (len(audio) - frame_size) // hop_size
        if num_frames <= 0:
            return np.zeros((1, n_mels), dtype=np.float32)

        frames = np.zeros((num_frames, frame_size), dtype=np.float32)
        for i in range(num_frames):
            start = i * hop_size
            frames[i] = audio[start:start + frame_size]

        window = np.hanning(frame_size)
        frames *= window

        spec = np.abs(np.fft.rfft(frames, n=n_fft))
        spec = np.maximum(spec, 1e-10)

        mel_fb = self._mel_filterbank(n_freqs, n_mels, sample_rate, n_fft)
        mel_spec = np.dot(spec, mel_fb.T)
        mel_spec = np.maximum(mel_spec, 1e-10)

        fbank = np.log(mel_spec).astype(np.float32)

        if self.cmvn_shift is not None and self.cmvn_scale is not None:
            fbank = (fbank + self.cmvn_shift) * self.cmvn_scale

        return fbank

    # CTC 解码时需要跳过的特殊 token（不写入字幕文本）
    _SPECIAL_TOKENS = frozenset({"<blank>", "<unk>", "<pad>", "<sos>", "<eos>",
                                  "<sil>", "<noise>", "<mus>"})

    def _ctc_greedy_decode(self, logits):
        tokens = []
        prev = -1
        for t in range(logits.shape[0]):
            idx = int(np.argmax(logits[t]))
            if idx != self.blank_id and idx != prev:
                if idx < len(self.token_list):
                    tok = self.token_list[idx]
                    # 跳过特殊控制 token，避免 SRT 中出现 <sil> 等 HTML 标签
                    if tok not in self._SPECIAL_TOKENS:
                        tokens.append(tok)
            prev = idx
        return tokens

    def _tokens_to_text(self, tokens):
        text = "".join(tokens)
        # 去掉 SentencePiece 空格前缀
        text = text.replace("▁", " ").strip()
        # 二次清理：移除残留的 <xxx> 格式特殊 token
        import re as _re
        text = _re.sub(r"<[^>]+>", "", text)
        return text.strip()

    def _ctc_decode_with_timestamps(self, logits, frame_shift_ms=10):
        """CTC 解码 + 帧级时间戳

        Args:
            logits: [frames, vocab_size]
            frame_shift_ms: 每帧对应毫秒数

        Returns:
            tokens: list of token strings
            timestamps: list of (start_ms, end_ms) for each token
        """
        tokens = []
        timestamps = []
        prev = -1
        for t in range(logits.shape[0]):
            idx = int(np.argmax(logits[t]))
            if idx != self.blank_id and idx != prev:
                if idx < len(self.token_list):
                    tok = self.token_list[idx]
                    if tok not in self._SPECIAL_TOKENS:
                        tokens.append(tok)
                        start_ms = t * frame_shift_ms
                        # 寻找下一个 blank 或重复帧作为结束
                        end_ms = start_ms + frame_shift_ms
                        for t2 in range(t + 1, logits.shape[0]):
                            idx2 = int(np.argmax(logits[t2]))
                            if idx2 == self.blank_id or idx2 == idx:
                                end_ms = t2 * frame_shift_ms
                                break
                        timestamps.append((start_ms, end_ms))
            prev = idx
        return tokens, timestamps

    def transcribe(self, video_path, audio=None, skip_vocal_separation=False):
        if audio is not None:
            sr = self.sr
        else:
            audio, sr = self._extract_audio(video_path)
        if audio is None or len(audio) == 0:
            return []

        if not skip_vocal_separation:
            audio = self._separate_vocals(audio, sr)

        # ③ VAD 分块
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

            ctc_logits, = self.ctc.run(None, {"encoder_outputs": enc_out})

            # ⑥ CTC 帧级时间戳
            tokens, token_timestamps = self._ctc_decode_with_timestamps(ctc_logits[0])

            if not tokens:
                continue

            text = self._tokens_to_text(tokens).strip()
            if not text:
                continue

            # 基于 token 时间戳切分字幕（每 3-5 秒一段）
            sub_segments = self._split_by_timestamps(
                text, token_timestamps, start_sec, sr
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

    def _split_by_timestamps(self, text, token_timestamps, chunk_start_sec, sr):
        """根据 token 时间戳将文本切分为字幕段"""
        if not token_timestamps:
            return [{"start": round(chunk_start_sec, 3),
                     "end": round(chunk_start_sec + len(text) * 0.1, 3),
                     "text": text}]

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

        # 按字符数比例分配 token 时间戳
        total_chars = max(sum(len(c) for c in clauses), 1)
        total_ms = token_timestamps[-1][1] - token_timestamps[0][0] if token_timestamps else 1000
        base_ms = token_timestamps[0][0] if token_timestamps else 0

        results = []
        char_pos = 0
        for c in clauses:
            char_len = len(c)
            start_ms = base_ms + total_ms * char_pos / total_chars
            end_ms = base_ms + total_ms * (char_pos + char_len) / total_chars
            char_pos += char_len

            results.append({
                "start": round(chunk_start_sec + start_ms / 1000, 3),
                "end": round(chunk_start_sec + end_ms / 1000, 3),
                "text": c,
            })
        return results

    def transcribe_with_decoder(self, video_path, max_len=200):
        audio, sr = self._extract_audio(video_path)
        if audio is None or len(audio) == 0:
            return []

        fbank = self._compute_fbank(audio, sr)
        fbank = fbank[np.newaxis, :, :]
        input_lengths = np.array([fbank.shape[1]], dtype=np.int64)

        enc_out, enc_lens, mask, *cross_kv = self.encoder.run(
            None,
            {"input": fbank, "input_lengths": input_lengths}
        )

        batch_size = 1
        cache_len = 0
        num_layers = 16
        dim = 1280

        self_k = [np.zeros((batch_size, cache_len, dim), dtype=np.float32) for _ in range(num_layers)]
        self_v = [np.zeros((batch_size, cache_len, dim), dtype=np.float32) for _ in range(num_layers)]

        token = np.array([[self.sos_id]], dtype=np.int64)
        step = np.array([0], dtype=np.int64)
        src_mask = mask

        generated = []
        for i in range(max_len):
            decoder_inputs = {
                "token": token,
                "step": step,
                "src_mask": src_mask,
            }
            for li in range(num_layers):
                decoder_inputs[f"self_k_cache_{li}"] = self_k[li]
                decoder_inputs[f"self_v_cache_{li}"] = self_v[li]
                decoder_inputs[f"cross_k_{li}"] = cross_kv[li * 2]
                decoder_inputs[f"cross_v_{li}"] = cross_kv[li * 2 + 1]

            outputs = self.decoder.run(None, decoder_inputs)
            logits = outputs[0]
            new_caches = outputs[1:]

            next_token = int(np.argmax(logits[0, -1]))
            if next_token == self.eos_id:
                break
            if next_token < len(self.token_list):
                generated.append(self.token_list[next_token])

            token = np.array([[next_token]], dtype=np.int64)
            step = np.array([step[0] + 1], dtype=np.int64)

            for li in range(num_layers):
                self_k[li] = new_caches[li * 2]
                self_v[li] = new_caches[li * 2 + 1]

        text = self._tokens_to_text(generated)
        audio_duration = len(audio) / sr
        return [{"start": 0.0, "end": round(audio_duration, 3), "text": text.strip()}]
