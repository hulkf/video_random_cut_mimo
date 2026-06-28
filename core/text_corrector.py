# -*- coding: utf-8 -*-
"""
Ollama LLM 字幕纠错模块
通过本地 Ollama (MiniCPM3-4B) 对 ASR 识别结果进行内容纠错
"""
import json
import urllib.request
import urllib.error


OLLAMA_ENDPOINT = "http://localhost:11434"
DEFAULT_MODEL = "shibing624/minicpm3_4b:latest"

# 电商内裤类目纠错提示词
CORRECTION_PROMPT = """你是一个电商语音识别纠错助手，专注内裤类目商品推广场景。
请修正以下文本中的语音识别错误（如同音字、行业术语错误、数字错误等）。

注意：
- 上下文是电商内裤商品描述/推广内容，涉及面料、尺码、款式、穿着体验等
- 常见的行业术语：内裤、冰丝、无痕、高腰、中腰、低腰、莫代尔、纯棉、蕾丝、透气、弹力、收腹、塑形、三角、平角
- 修正同音字错误：内裤(不是内库/内酷)、冰丝(不是冰撕/冰丝)、莫代尔(不是莫代儿/莫带儿)
- 保持原意和语气，不要改写，只修正识别错误的词
- 只输出修正后的文本，不要添加任何解释

原文：{text}
修正后："""


class TextCorrector:
    """通过 Ollama API 调用本地 LLM 进行文本纠错"""

    def __init__(self, endpoint=None, model=None):
        self.endpoint = (endpoint or OLLAMA_ENDPOINT).rstrip("/")
        self.model = model or DEFAULT_MODEL

    def correct(self, text):
        """对单段文本进行纠错，返回修正后的文本"""
        if not text or not text.strip():
            return text

        prompt = CORRECTION_PROMPT.replace("{text}", text.strip())

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 512
            }
        }

        try:
            req = urllib.request.Request(
                f"{self.endpoint}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                corrected = result.get("response", "").strip()

            if not corrected:
                return text

            # 清理可能多余的引号或前缀
            corrected = corrected.strip("\"'「」")

            return corrected if corrected else text

        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, OSError) as e:
            print(f"[TextCorrector] Ollama 纠错失败: {e}，使用原文")
            return text

    def correct_segments(self, segments):
        """对 segments 列表进行全文纠错（一次请求，保留上下文）"""
        if not segments:
            return segments

        # 合并所有文本为带序号的全文
        numbered_text = "\n".join(
            f"[{i+1}] {seg['text']}" for i, seg in enumerate(segments)
        )

        batch_prompt = f"""你是一个电商语音识别纠错助手，专注内裤类目商品推广场景。
请修正以下文本中的语音识别错误（如同音字、行业术语错误、数字错误等）。

注意：
- 上下文是电商内裤商品描述/推广内容，涉及面料、尺码、款式、穿着体验等
- 常见的行业术语：内裤、冰丝、无痕、高腰、中腰、低腰、莫代尔、纯棉、蕾丝、透气、弹力、收腹、塑形、三角、平角
- 修正同音字错误
- 保持原意和语气，不要改写，只修正识别错误的词
- 保持 [序号] 格式不变，每行一个序号
- 只输出修正后的文本，不要添加任何解释

原文：
{numbered_text}
修正后："""

        payload = {
            "model": self.model,
            "prompt": batch_prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 2048
            }
        }

        try:
            req = urllib.request.Request(
                f"{self.endpoint}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                corrected_full = result.get("response", "").strip()

            if not corrected_full:
                return segments

            # 解析带序号的纠错结果
            corrected_map = {}
            for line in corrected_full.split("\n"):
                line = line.strip()
                import re
                m = re.match(r'\[(\d+)\]\s*(.*)', line)
                if m:
                    idx = int(m.group(1)) - 1
                    corrected_map[idx] = m.group(2).strip()

            # 应用纠错结果
            result_segments = []
            for i, seg in enumerate(segments):
                new_text = corrected_map.get(i, seg["text"])
                result_segments.append({
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": new_text if new_text else seg["text"]
                })
            return result_segments

        except Exception as e:
            print(f"[TextCorrector] 批量纠错失败: {e}，使用原文")
            return segments
