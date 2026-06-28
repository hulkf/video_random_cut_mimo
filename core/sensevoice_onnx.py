# -*- coding: utf-8 -*-
"""
SenseVoice ONNX 离线推理引擎
支持 iic/SenseVoiceSmall 模型 (CTC-based, LFR + CMVN)
"""
import os
import subprocess
import io
import wave
import struct
import numpy as np
import onnxruntime as ort


class SenseVoice_ONNX:
    """SenseVoice ONNX 离线推理"""

    BLANK_ID = 0
    SOS_ID = 1
    EOS_ID = 2

    LANG_AUTO = 0
    LANG_ZH = 3
    LANG_EN = 4
    LANG_YUE = 7
    LANG_JA = 11
    LANG_KO = 12
    LANG_NOSPEECH = 13

    WITH_ITN = 14
    WITHOUT_ITN = 15

    def __init__(self, model_dir, lang="auto", use_itn=True):
        self.model_dir = model_dir
        self.sr = 16000
        self.n_mels = 80
        self.frame_length = 25
        self.frame_shift = 10
        self.lfr_m = 7
        self.lfr_n = 6
        self.n_fft = 512

        self.lang_id = self._parse_lang(lang)
        self.text_norm = self.WITH_ITN if use_itn else self.WITHOUT_ITN

        self._load_vocab()
        self._load_cmvn_from_metadata()
        self._load_model()
        self._build_mel_filterbank()

    def _parse_lang(self, lang):
        lang_map = {
            "auto": self.LANG_AUTO,
            "zh": self.LANG_ZH,
            "en": self.LANG_EN,
            "yue": self.LANG_YUE,
            "ja": self.LANG_JA,
            "ko": self.LANG_KO,
        }
        return lang_map.get(lang.lower(), self.LANG_AUTO)

    # ------------------------------------------------------------------ #
    # 模型加载
    # ------------------------------------------------------------------ #
    def _load_vocab(self):
        """加载词表：优先 tokens.txt，否则从 BPE 模型提取"""
        tokens_path = os.path.join(self.model_dir, "tokens.txt")
        if not os.path.exists(tokens_path):
            self._extract_tokens_from_bpe()
        self.token_list = []
        with open(tokens_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if parts:
                    self.token_list.append(parts[0])

    def _extract_tokens_from_bpe(self):
        """从 SentencePiece BPE 模型文件中提取词表"""
        bpe_path = os.path.join(self.model_dir, "chn_jpn_yue_eng_ko_spectok.bpe.model")
        if not os.path.exists(bpe_path):
            raise RuntimeError(f"找不到 BPE 模型: {bpe_path}")

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
                        piece, _ = self._parse_piece(sub_data)
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

        tokens_path = os.path.join(self.model_dir, "tokens.txt")
        with open(tokens_path, "w", encoding="utf-8") as f:
            for p in pieces:
                f.write(p + "\n")

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
    def _parse_piece(sub_data):
        piece = ""
        score = 0.0
        sp_pos = 0
        while sp_pos < len(sub_data):
            sp_tag, sp_new_pos = SenseVoice_ONNX._read_varint(sub_data, sp_pos)
            sp_field = sp_tag >> 3
            sp_wire = sp_tag & 0x07
            if sp_wire == 2:
                sp_len, sp_new_pos = SenseVoice_ONNX._read_varint(sub_data, sp_new_pos)
                if sp_field == 1:
                    piece = sub_data[sp_new_pos:sp_new_pos+sp_len].decode("utf-8")
                sp_new_pos += sp_len
            elif sp_wire == 5:
                if sp_field == 2:
                    score = struct.unpack("<f", sub_data[sp_new_pos:sp_new_pos+4])[0]
                sp_new_pos += 4
            elif sp_wire == 0:
                _, sp_new_pos = SenseVoice_ONNX._read_varint(sub_data, sp_new_pos)
            else:
                break
            sp_pos = sp_new_pos
        return piece, score

    def _load_cmvn_from_metadata(self):
        """从 ONNX 模型元数据中提取 CMVN 参数"""
        self._load_model_session()
        metadata = self._session.get_modelmeta().custom_metadata_map
        neg_mean_str = metadata.get("neg_mean", "")
        inv_stddev_str = metadata.get("inv_stddev", "")
        if not neg_mean_str or not inv_stddev_str:
            raise RuntimeError("ONNX 模型元数据中缺少 neg_mean/inv_stddev")
        self.cmvn_shift = np.array(neg_mean_str.split(","), dtype=np.float32)
        self.cmvn_scale = np.array(inv_stddev_str.split(","), dtype=np.float32)

    def _load_model_session(self):
        model_path = os.path.join(self.model_dir, "model.1.onnx")
        if not os.path.exists(model_path):
            model_path = os.path.join(self.model_dir, "model.onnx")
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        opts.inter_op_num_threads = 2
        opts.intra_op_num_threads = 2
        self._session = ort.InferenceSession(model_path, opts)
        self._model_path = model_path

    def _load_model(self):
        if not hasattr(self, "_session"):
            self._load_model_session()
        self.session = self._session

    def _build_mel_filterbank(self):
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
        return (feats + self.cmvn_shift) * self.cmvn_scale

    # ------------------------------------------------------------------ #
    # 模型推理
    # ------------------------------------------------------------------ #
    def _infer(self, features):
        feats = features[np.newaxis, :, :].astype(np.float32)
        feats_len = np.array([features.shape[0]], dtype=np.int32)
        lang = np.array([self.lang_id], dtype=np.int32)
        text_norm = np.array([self.text_norm], dtype=np.int32)
        outputs = self.session.run(None, {
            "x": feats,
            "x_length": feats_len,
            "language": lang,
            "text_norm": text_norm
        })
        return outputs[0] if isinstance(outputs, list) else outputs

    # ------------------------------------------------------------------ #
    # 解码
    # ------------------------------------------------------------------ #
    def _ctc_greedy_decode(self, logits):
        """CTC greedy 解码 + 去重，返回 token ID 列表"""
        token_ids = np.argmax(logits[0], axis=-1)
        prev = -1
        ids = []
        for tid in token_ids:
            tid = int(tid)
            if tid <= self.EOS_ID:
                prev = tid
                continue
            if tid != prev:
                ids.append(tid)
            prev = tid
        return ids

    def _ids_to_text(self, token_ids):
        """将 token ID 列表转为文本，用词表查表 + ▁ 转空格"""
        pieces = []
        for tid in token_ids:
            if tid < len(self.token_list):
                pieces.append(self.token_list[tid])
            else:
                pieces.append("")
        raw = "".join(pieces)
        # ▁ (U+2581) 是 SentencePiece 的空格标记
        text = raw.replace("\u2581", " ").strip()
        # 过滤 SenseVoice 特殊标签 <|xx|>
        import re
        text = re.sub(r"<\|[^|]*\|>", "", text).strip()
        return text

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
        import traceback as tb
        try:
            if audio is None:
                audio = self._extract_audio(video_path)
            if audio is None or audio.size == 0:
                return []

            audio_duration_sec = len(audio) / self.sr

            fbank = self._compute_fbank(audio)
            lfr_feats = self._apply_lfr(fbank)
            lfr_feats = self._apply_cmvn(lfr_feats)

            logits = self._infer(lfr_feats)
            token_ids = self._ctc_greedy_decode(logits)
            if not token_ids:
                return []

            text = self._ids_to_text(token_ids)
            if not text:
                return []

            segments = self._split_into_segments(text, audio_duration_sec)
            return segments
        except Exception:
            log_path = os.path.join(os.path.dirname(__file__), "sensevoice_error.log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"video: {video_path}\n")
                tb.print_exc(file=f)
            return []

    def _split_into_segments(self, text, total_duration):
        """按标点符号分句，均匀分配时间"""
        import re
        parts = re.split(r'([。！？!?.，,；;、\n])', text)
        sentences = []
        buf = ""
        for p in parts:
            if re.match(r'[。！？!?.，,；;、\n]', p):
                buf += p
                if buf.strip():
                    sentences.append(buf.strip())
                buf = ""
            else:
                buf += p
        if buf.strip():
            sentences.append(buf.strip())

        if not sentences:
            return [{"start": 0.0, "end": round(total_duration, 3), "text": text}]

        seg_duration = total_duration / len(sentences)
        segments = []
        for i, sent in enumerate(sentences):
            start = round(i * seg_duration, 3)
            end = round((i + 1) * seg_duration, 3)
            segments.append({"start": start, "end": end, "text": sent})
        return segments
