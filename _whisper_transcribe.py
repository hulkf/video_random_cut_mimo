"""独立进程的 whisper 转录脚本 - 避免 GUI 进程中的 DLL 冲突"""
# 用法: python _whisper_transcribe.py <audio_path> <model_name> <output_srt_path> [model_dir]
# 输出: SRT 文件写入 output_srt_path，stdout 输出 JSON 结果供父进程读取

# 国内环境优先使用 HuggingFace 镜像，避免下载超时/连接重置
import os
if os.environ.get("HF_ENDPOINT") is None:
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
if os.environ.get("HF_HUB_DISABLE_SYMLINKS_WARNING") is None:
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import sys
import json
import tempfile
import shutil


def ensure_accessible(path):
    """如果路径含中文/特殊字符导致 ffmpeg 不可访问，复制到临时目录返回新路径"""
    try:
        if os.path.isfile(path):
            with open(path, 'rb') as f:
                f.read(1)
            return path, False
        return path, False
    except Exception:
        tmp = tempfile.mkdtemp(prefix="whisper_in_")
        dst = os.path.join(tmp, os.path.basename(path))
        shutil.copy2(path, dst)
        return dst, True


def segments_to_srt(segments, srt_path):
    d = os.path.dirname(os.path.abspath(srt_path))
    os.makedirs(d, exist_ok=True)
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            start = seg["start"]
            end = seg["end"]
            text = seg["text"].strip()
            sh = int(start // 3600)
            sm = int((start % 3600) // 60)
            ss = int(start % 60)
            sms = int((start % 1) * 1000)
            eh = int(end // 3600)
            em = int((end % 3600) // 60)
            es = int(end % 60)
            ems = int((end % 1) * 1000)
            f.write(f"{i}\n")
            f.write(f"{sh:02d}:{sm:02d}:{ss:02d},{sms:03d} --> {eh:02d}:{em:02d}:{es:02d},{ems:03d}\n")
            f.write(f"{text}\n\n")


def load_whisper_model(model_name, model_dir=None):
    """
    加载 whisper 模型。优先级：
    1. 若指定 model_dir 且存在 {model_dir}/{model_name}.pt，直接从该路径加载
    2. 否则用 whisper.load_model(name, download_root=model_dir)，让 whisper 自行下载/缓存
    """
    import whisper
    import torch

    # 优先用本地文件路径
    if model_dir:
        local_pt = os.path.join(model_dir, f"{model_name}.pt")
        if os.path.isfile(local_pt):
            # openai-whisper 的 load_model 也支持直接传文件路径
            return whisper.load_model(local_pt)

    return whisper.load_model(model_name, download_root=model_dir if model_dir else None)


def main():
    if len(sys.argv) < 4:
        print(json.dumps({"error": f"Usage: {sys.argv[0]} <audio> <model> <srt_output> [model_dir]"}))
        sys.exit(1)

    audio_path = sys.argv[1]
    model_name = sys.argv[2]
    srt_output = sys.argv[3]
    model_dir = sys.argv[4] if len(sys.argv) >= 5 else None

    temp_files = []

    try:
        audio_path, is_temp = ensure_accessible(audio_path)
        if is_temp:
            temp_files.append(audio_path)

        model = load_whisper_model(model_name, model_dir)
        result = model.transcribe(audio_path, language="zh")

        segments = []
        for seg in result.get("segments", []):
            segments.append({
                "start": round(seg["start"], 2),
                "end": round(seg["end"], 2),
                "text": seg["text"].strip()
            })

        if not segments:
            print(json.dumps({"error": "No speech detected"}))
            sys.exit(1)

        segments_to_srt(segments, srt_output)

        print(json.dumps({
            "success": True,
            "segment_count": len(segments),
            "srt_path": srt_output,
            "duration": round(result.get("segments", [])[-1]["end"], 2) if result.get("segments") else 0
        }))

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(json.dumps({
            "success": False,
            "error": f"{type(e).__name__}: {e}",
            "traceback": tb[-2000:] if len(tb) > 2000 else tb
        }))
        sys.exit(1)
    finally:
        for tf in temp_files:
            try:
                if os.path.exists(tf):
                    os.remove(tf)
                d = os.path.dirname(tf)
                if d and os.path.isdir(d) and d.startswith(tempfile.gettempdir()):
                    os.rmdir(d)
            except Exception:
                pass


if __name__ == "__main__":
    main()
