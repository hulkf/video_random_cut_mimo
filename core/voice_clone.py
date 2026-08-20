# -*- coding: utf-8 -*-
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".flv", ".webm")


def _run(command, timeout=3600):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, creationflags=flags
    )


class VoiceLibrary:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def list_profiles(self):
        profiles = []
        for metadata_path in self.root.glob("*/profile.json"):
            try:
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
                data["directory"] = str(metadata_path.parent)
                profiles.append(data)
            except (OSError, ValueError):
                continue
        return sorted(profiles, key=lambda item: item.get("name", "").lower())

    def create(self, name, reference_audio, reference_text):
        name = name.strip()
        reference_text = reference_text.strip()
        if not name or not reference_text:
            raise ValueError("音色名称和参考音频文案不能为空")
        if not os.path.isfile(reference_audio):
            raise ValueError("参考音频不存在")
        slug = re.sub(r"[^0-9A-Za-z_-]+", "_", name).strip("_") or f"voice_{int(time.time())}"
        profile_dir = self.root / slug
        if profile_dir.exists():
            raise ValueError(f"音色名称已存在：{name}")
        profile_dir.mkdir(parents=True)
        target_audio = profile_dir / "reference.wav"
        result = _run([
            "ffmpeg", "-y", "-i", reference_audio, "-vn", "-ac", "1",
            "-ar", "16000", "-c:a", "pcm_s16le", str(target_audio)
        ])
        if result.returncode != 0:
            shutil.rmtree(profile_dir, ignore_errors=True)
            raise RuntimeError("参考音频转换失败：" + result.stderr[-500:])
        metadata = {
            "id": slug,
            "name": name,
            "reference_audio": str(target_audio),
            "reference_text": reference_text,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (profile_dir / "profile.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return metadata

    def delete(self, profile_id):
        target = (self.root / profile_id).resolve()
        if target.parent != self.root.resolve():
            raise ValueError("非法音色路径")
        shutil.rmtree(target, ignore_errors=True)


class CosyVoiceService:
    def __init__(self, conda_exe, env_name, server_script, model_dir,
                 host="127.0.0.1", port=50051):
        self.conda_exe = conda_exe
        self.env_name = env_name
        self.server_script = server_script
        self.model_dir = model_dir
        self.host = host
        self.port = port
        self.process = None
        self.log_path = os.path.join(tempfile.gettempdir(), "cosyvoice3_server.log")

    @property
    def base_url(self):
        return f"http://{self.host}:{self.port}"

    def request(self, path, payload=None, timeout=30):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path, data=data,
            headers={"Content-Type": "application/json"},
            method="POST" if data is not None else "GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(message or str(exc)) from exc

    def is_ready(self):
        try:
            return self.request("/health", timeout=2).get("ready", False)
        except Exception:
            return False

    def start(self, status_callback=None, timeout=600):
        if self.is_ready():
            return
        if not os.path.isfile(self.conda_exe):
            raise RuntimeError("未找到 Conda，请先执行 CosyVoice 3 安装脚本")
        command = [
            self.conda_exe, "run", "--no-capture-output", "-n", self.env_name,
            "python", self.server_script, "--model-dir", self.model_dir,
            "--host", self.host, "--port", str(self.port)
        ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        log = open(self.log_path, "a", encoding="utf-8")
        self.process = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT,
            creationflags=flags, cwd=os.path.dirname(self.server_script)
        )
        started = time.time()
        while time.time() - started < timeout:
            if self.is_ready():
                return
            if self.process.poll() is not None:
                raise RuntimeError(f"CosyVoice 服务启动失败，请查看 {self.log_path}")
            if status_callback:
                status_callback("正在加载 CosyVoice 3 模型...")
            time.sleep(2)
        raise TimeoutError(f"CosyVoice 服务启动超时，请查看 {self.log_path}")

    def synthesize(self, text, profile, output_path, speed=1.0, timeout=7200):
        result = self.request("/synthesize", {
            "text": text,
            "prompt_text": profile["reference_text"],
            "prompt_wav": profile["reference_audio"],
            "output_path": output_path,
            "speed": speed,
        }, timeout=timeout)
        if not result.get("success"):
            raise RuntimeError(result.get("error", "CosyVoice 合成失败"))
        return result


class VoiceClonePipeline:
    def __init__(self, service, asr_type, asr_model_dir, text_source="auto"):
        self.service = service
        self.asr_type = asr_type
        self.asr_model_dir = asr_model_dir
        self.text_source = text_source
        self.asr = None

    @staticmethod
    def find_videos(folder):
        videos = []
        for root, _, files in os.walk(folder):
            videos.extend(
                os.path.join(root, name) for name in files
                if name.lower().endswith(VIDEO_EXTENSIONS)
            )
        return sorted(videos)

    def _load_asr(self):
        if self.asr is None:
            from core.onnx_asr import OnnxASR
            self.asr = OnnxASR(self.asr_model_dir, self.asr_type)
        return self.asr

    def get_text(self, video_path):
        txt_path = os.path.splitext(video_path)[0] + ".txt"
        if self.text_source != "asr" and os.path.isfile(txt_path):
            text = Path(txt_path).read_text(encoding="utf-8-sig").strip()
            if text:
                return text
        if self.text_source == "txt":
            raise RuntimeError(f"缺少同名文案：{txt_path}")
        segments = self._load_asr().transcribe(video_path)
        text = "".join(item.get("text", "").strip() for item in segments)
        if not text:
            raise RuntimeError("未识别到口播文案")
        return text

    @staticmethod
    def _duration(path):
        """媒体时长（秒）。统一委托 utils.media_utils.get_video_duration。"""
        from utils.media_utils import get_video_duration
        try:
            return float(get_video_duration(path))
        except Exception as e:
            raise RuntimeError("无法读取媒体时长") from e

    def apply(self, video_path, output_path, profile, speed=1.0):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="voice_clone_") as temp_dir:
            generated = os.path.join(temp_dir, "generated.wav")
            fitted = os.path.join(temp_dir, "fitted.wav")
            text = self.get_text(video_path)
            self.service.synthesize(text, profile, generated, speed=speed)
            video_duration = self._duration(video_path)
            audio_duration = self._duration(generated)
            filters = []
            if audio_duration > video_duration and audio_duration / video_duration <= 1.35:
                filters.append(f"atempo={audio_duration / video_duration:.6f}")
            filters.append(f"apad=pad_dur={video_duration:.3f}")
            audio_result = _run([
                "ffmpeg", "-y", "-i", generated, "-af", ",".join(filters),
                "-t", f"{video_duration:.3f}", "-ar", "48000", fitted
            ])
            if audio_result.returncode != 0:
                raise RuntimeError("配音时长适配失败：" + audio_result.stderr[-500:])
            mux_result = _run([
                "ffmpeg", "-y", "-i", video_path, "-i", fitted,
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                "-shortest", output_path
            ])
            if mux_result.returncode != 0:
                raise RuntimeError("视频换音失败：" + mux_result.stderr[-700:])
            return {"video": video_path, "output": output_path, "text": text}
