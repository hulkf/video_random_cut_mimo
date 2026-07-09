# -*- coding: utf-8 -*-
"""
ASR 字幕后处理纠错器

加载 assets/hotword_corrections.txt，对识别结果做整词替换。
适用所有 ASR 引擎（FireRedASR / FunASR / SenseVoice / Whisper），
因为 ASR 层热词 biasing 在当前 sherpa-onnx API 上对各引擎支持不一致，
后处理纠错是覆盖面最广、最可控的方案。
"""
import os
import re


class HotwordCorrector:
    """基于字典的整词同音字/形近字纠错"""

    _instance = None
    _dict_path = None
    _mtime = None

    def __init__(self, dict_path=None):
        if dict_path is None:
            dict_path = os.path.join(
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
                "assets", "hotword_corrections.txt"
            )
        self.dict_path = dict_path
        self._rules = []          # [(pattern, replacement)]
        self._load()

    @classmethod
    def shared(cls):
        """进程内单例 + 文件热更新（字典改了自动重载）"""
        path = os.path.join(
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
            "assets", "hotword_corrections.txt"
        )
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        if cls._instance is None or cls._dict_path != path or cls._mtime != mtime:
            cls._instance = cls(path)
            cls._dict_path = path
            cls._mtime = mtime
        return cls._instance

    def _load(self):
        """解析字典文件，构建正则规则"""
        if not os.path.exists(self.dict_path):
            self._rules = []
            return

        rules = []
        with open(self.dict_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) != 2:
                    continue
                wrongs, right = parts
                # wrongs 形如 "A|B|C"，按 | 拆成多个错误形式
                wrong_list = [w for w in wrongs.split("|") if w]
                if not wrong_list or not right:
                    continue
                # 跳过自映射（错误词 == 正确词，避免误伤）
                wrong_list = [w for w in wrong_list if w.lower() != right.lower()]
                if not wrong_list:
                    continue
                # 整词匹配，按长度降序避免短词先吃掉长词
                wrong_list.sort(key=len, reverse=True)
                escaped = "|".join(re.escape(w) for w in wrong_list)
                pattern = re.compile(escaped, re.IGNORECASE)
                rules.append((pattern, right))

        # 全局也按规则左侧最大长度降序，保证"防走光"先于"走光"被处理
        rules.sort(key=lambda r: len(r[0].pattern), reverse=True)
        self._rules = rules

    def correct(self, text):
        """对一段文本应用所有纠错规则"""
        if not text or not self._rules:
            return text
        out = text
        for pattern, right in self._rules:
            out = pattern.sub(right, out)
        return out

    def correct_segments(self, segments):
        """对 segment 列表逐条纠错，原地保留时间戳"""
        if not segments:
            return segments
        results = []
        for seg in segments:
            new_text = self.correct(seg.get("text", ""))
            new_seg = dict(seg)
            new_seg["text"] = new_text if new_text else seg.get("text", "")
            results.append(new_seg)
        return results
