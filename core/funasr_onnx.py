# -*- coding: utf-8 -*-
"""
FunASR Paraformer ONNX 离线推理引擎
支持 paraformer-large-zh-en-timestamp-onnx-offline 模型
"""
import os
import re
import subprocess
import io
import wave
import numpy as np
import onnxruntime as ort


class FunASR_ONNX:
    """FunASR Paraformer ONNX 离线推理"""

    # 特殊 token，不在输出文本中出现
    _SPECIAL_TOKENS = frozenset({
        "<blank>", "<s>", "</s>", "<unk>", "<pad>",
        "<sil>", "<noise>", "<mus>"
    })

    def __init__(self, model_dir):
        self.model_dir = model_dir
        self.sr = 16000
        self.n_mels = 80
        self.frame_length = 25   # ms
        self.frame_shift = 10    # ms
        self.lfr_m = 7
        self.lfr_n = 6
        self.n_fft = 512

        self._load_tokens()
        self._load_cmvn()
        self._load_model()
        self._build_mel_filterbank()

    # ------------------------------------------------------------------ #
    # 模型加载
    # ------------------------------------------------------------------ #
    def _load_tokens(self):
        path = os.path.join(self.model_dir, "tokens.txt")
        self.token_list = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.rsplit(" ", 1)
                self.token_list.append(parts[0])

    def _load_cmvn(self):
        path = os.path.join(self.model_dir, "am.mvn")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        shift_m = re.search(
            r"<AddShift>\s*\d+\s+\d+\s*\n\s*<LearnRateCoef>\s*\d+\s*\[([^\]]+)\]",
            content
        )
        scale_m = re.search(
            r"<Rescale>\s*\d+\s+\d+\s*\n\s*<LearnRateCoef>\s*\d+\s*\[([^\]]+)\]",
            content
        )
        if not shift_m or not scale_m:
            raise RuntimeError("无法解析 am.mvn CMVN 文件")
        self.cmvn_shift = np.array(shift_m.group(1).strip().split(), dtype=np.float32)
        self.cmvn_scale = np.array(scale_m.group(1).strip().split(), dtype=np.float32)

    def _load_model(self):
        model_path = os.path.join(self.model_dir, "model.onnx")
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        opts.inter_op_num_threads = 2
        opts.intra_op_num_threads = 2
        self.session = ort.InferenceSession(model_path, opts)

    def _build_mel_filterbank(self):
        """构建 80 维 Mel 滤波器组 (用于 hamming window 后的幅度谱)"""
        n_freqs = self.n_fft // 2 + 1
        low_mel = 0
        high_mel = 2595 * np.log10(1 + (self.sr / 2) / 700)
        mel_pts = np.linspace(low_mel, high_mel, self.n_mels + 2)
        hz_pts = 700 * (10 ** (mel_pts / 2595) - 1)
        bin_pts = np.floor((self.n_fft + 1) * hz_pts / self.sr).astype(int)

        fbank = np.zeros((self.n_mels, n_freqs))
        for m in range(1, self.n_mels + 1):
            fl, fc, fr = bin_pts[m - 1], bin_pts[m], bin_pts[m + 1]
            for k in range(fl, fc):
                if k < n_freqs and fc != fl:
                    fbank[m - 1, k] = (k - fl) / (fc - fl)
            for k in range(fc, fr):
                if k < n_freqs and fr != fc:
                    fbank[m - 1, k] = (fr - k) / (fr - fc)
        self.mel_filterbank = fbank

    # ------------------------------------------------------------------ #
    # 音频提取
    # ------------------------------------------------------------------ #
    def _extract_audio(self, video_path):
        """从视频中提取 16kHz 单声道 PCM 音频"""
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

    # ------------------------------------------------------------------ #
    # 前端特征提取
    # ------------------------------------------------------------------ #
    def _compute_fbank(self, audio):
        """计算 80 维 log Mel-filterbank 特征 (hamming 窗)"""
        frame_size = int(self.sr * self.frame_length / 1000)
        hop_size = int(self.sr * self.frame_shift / 1000)
        num_frames = 1 + (len(audio) - frame_size) // hop_size
        if num_frames <= 0:
            return np.zeros((1, self.n_mels), dtype=np.float32)

        window = np.hamming(frame_size)
        frames_data = np.zeros((num_frames, frame_size), dtype=np.float32)
        for i in range(num_frames):
            start = i * hop_size
            frames_data[i] = audio[start:start + frame_size] * window

        spec = np.maximum(np.abs(np.fft.rfft(frames_data, n=self.n_fft)), 1e-10)
        mel_spec = np.log(np.maximum(np.dot(spec, self.mel_filterbank.T), 1e-10))
        return mel_spec.astype(np.float32)

    def _apply_lfr(self, fbank):
        """LFR (Low Frame Rate): stack m=7 frames, skip n=6 → 560-dim"""
        m, n = self.lfr_m, self.lfr_n
        T = fbank.shape[0]
        lfr_frames = []
        for i in range(0, T, n):
            end = min(i + m, T)
            cur = fbank[i:end]
            if len(cur) < m:
                pad = np.zeros((m - len(cur), self.n_mels), dtype=np.float32)
                cur = np.vstack([cur, pad])
            lfr_frames.append(cur.reshape(-1))
        return np.array(lfr_frames, dtype=np.float32)

    def _apply_cmvn(self, feats):
        """应用均值和方差归一化 (全局 CMVN)"""
        return (feats + self.cmvn_shift) * self.cmvn_scale

    # ------------------------------------------------------------------ #
    # 模型推理
    # ------------------------------------------------------------------ #
    def _infer(self, features):
        """运行 ONNX 推理，返回 (logits, token_num, us_alphas, us_cif_peak)"""
        feats = features[np.newaxis, :, :].astype(np.float32)
        feats_len = np.array([features.shape[0]], dtype=np.int32)
        return self.session.run(None, {
            "speech": feats,
            "speech_lengths": feats_len
        })

    # ------------------------------------------------------------------ #
    # 解码
    # ------------------------------------------------------------------ #
    def _decode_tokens(self, logits):
        """argmax + CTC 合并，返回解码后的 token 文本列表"""
        token_ids = np.argmax(logits[0], axis=-1)
        prev = -1
        tokens = []
        for tid in token_ids:
            if tid <= 2:       # <blank>, <s>, </s>
                prev = tid
                continue
            if tid < len(self.token_list):
                token = self.token_list[tid]
                if token in self._SPECIAL_TOKENS:
                    prev = tid
                    continue
                if tid != prev:
                    tokens.append(token)
            prev = tid
        return tokens

    def _tokens_to_text(self, tokens):
        """将 token 列表合并为文本 (处理 @@ BPE 续写符)"""
        parts = []
        for t in tokens:
            if t.endswith("@@"):
                parts.append(t[:-2])
            else:
                parts.append(t)
        return "".join(parts).strip()

    def _get_cif_timestamps(self, us_alphas, num_tokens, audio_duration_ms):
        """基于 CIF alpha 累积和计算每个 token 的起止时间 (毫秒)"""
        alphas = us_alphas[0]
        cum_alphas = np.cumsum(alphas)
        ms_per_frame = audio_duration_ms / len(alphas) if len(alphas) > 0 else 10

        thresholds = np.arange(1, min(int(cum_alphas[-1]) + 1, num_tokens + 1))
        fire_pos = np.searchsorted(cum_alphas, thresholds, side="right")
        fire_pos = np.clip(fire_pos, 0, len(alphas) - 1)

        timestamps_ms = []
        for i in range(num_tokens):
            start = int(fire_pos[i] * ms_per_frame) if i < len(fire_pos) else 0
            end = int(fire_pos[i + 1] * ms_per_frame) if i + 1 < len(fire_pos) else int(audio_duration_ms)
            timestamps_ms.append((start, end))
        return timestamps_ms

    # ------------------------------------------------------------------ #
    # 公开接口
    # ------------------------------------------------------------------ #
    def transcribe(self, video_path, audio=None):
        """对视频进行语音识别，返回 segment 列表

        Args:
            video_path: 视频路径
            audio: 已提取的音频 (可选，跳过提取)

        返回:
            [{"start": float(秒), "end": float(秒), "text": str}, ...]
        """
        if audio is None:
            audio = self._extract_audio(video_path)
        if audio is None or len(audio) == 0:
            return []

        audio_duration_ms = len(audio) / self.sr * 1000

        # 特征提取并填充到 LFR 帧的整数倍
        fbank = self._compute_fbank(audio)
        lfr_feats = self._apply_lfr(fbank)
        lfr_feats = self._apply_cmvn(lfr_feats)

        # 推理
        logits, token_num, us_alphas, us_cif_peak = self._infer(lfr_feats)

        # 解码 token
        tokens = self._decode_tokens(logits)
        if not tokens:
            return []

        # CIF 时间戳
        timestamps_ms = self._get_cif_timestamps(
            us_alphas, len(tokens), audio_duration_ms
        )

        # 按时长和自然停顿分组为字幕片段 (最长 3.5s, 遇长停顿 >800ms 也切分)
        MAX_SEGMENT_SEC = 3.5
        MIN_PAUSE_MS = 800
        segments = []
        cur_tokens = []
        cur_start = timestamps_ms[0][0]

        for i, (token, (st, et)) in enumerate(zip(tokens, timestamps_ms)):
            if not cur_tokens:
                cur_start = st
            cur_tokens.append(token)

            # 判断是否需要切分
            cur_duration_sec = (et - cur_start) / 1000
            next_too_long = False
            if i + 1 < len(timestamps_ms):
                next_st = timestamps_ms[i + 1][0]
                next_too_long = (next_st - et > MIN_PAUSE_MS or
                                 (next_st - cur_start) / 1000 > MAX_SEGMENT_SEC)

            if cur_duration_sec >= MAX_SEGMENT_SEC or next_too_long:
                seg_text = self._tokens_to_text(cur_tokens).strip()
                if seg_text:
                    segments.append({
                        "start": round(cur_start / 1000, 3),
                        "end": round(et / 1000, 3),
                        "text": seg_text
                    })
                cur_tokens = []

        if cur_tokens:
            seg_text = self._tokens_to_text(cur_tokens).strip()
            if seg_text:
                segments.append({
                    "start": round(cur_start / 1000, 3),
                    "end": round(timestamps_ms[-1][1] / 1000, 3),
                    "text": seg_text
                })

        return segments

    def transcribe_with_timestamps(self, video_path):
        """返回带 token 级时间戳的详细结果

        返回:
            {"text": str, "tokens": [(token, start_ms, end_ms), ...]}
        """
        audio = self._extract_audio(video_path)
        if audio is None or len(audio) == 0:
            return {"text": "", "tokens": []}

        audio_duration_ms = len(audio) / self.sr * 1000
        fbank = self._compute_fbank(audio)
        lfr_feats = self._apply_lfr(fbank)
        lfr_feats = self._apply_cmvn(lfr_feats)

        logits, token_num, us_alphas, us_cif_peak = self._infer(lfr_feats)
        tokens = self._decode_tokens(logits)
        timestamps_ms = self._get_cif_timestamps(
            us_alphas, len(tokens), audio_duration_ms
        )

        token_list = list(zip(tokens, timestamps_ms))
        text = self._tokens_to_text(tokens)

        return {"text": text, "tokens": token_list}
