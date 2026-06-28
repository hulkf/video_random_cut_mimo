import subprocess
import json
import os
import tempfile
import shutil


def get_video_duration(video_path):
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def extract_frames(video_path, output_dir, frame_interval=1.0):
    """Extract frames from video at specified interval."""
    os.makedirs(output_dir, exist_ok=True)
    cmd = [
        "ffmpeg", "-i", video_path, "-vf", f"fps=1/{frame_interval}",
        "-q:v", "2", os.path.join(output_dir, "frame_%04d.jpg")
    ]
    subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore")
    return sorted([
        os.path.join(output_dir, f)
        for f in os.listdir(output_dir)
        if f.endswith(".jpg")
    ])


def cut_video(video_path, start_time, duration, output_path):
    """Cut a segment from video with accurate seeking."""
    cmd = [
        "ffmpeg", "-i", video_path,
        "-ss", str(start_time), "-t", str(duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path
    ]
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=3600)
    if result.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(f"cut_video failed: {result.stderr}")
    return output_path


def extract_audio(video_path, output_path):
    """Extract audio from video."""
    cmd = [
        "ffmpeg", "-i", video_path, "-vn", "-acodec", "copy",
        "-y", output_path
    ]
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(f"extract_audio failed: {result.stderr}")
    return output_path


def _probe_video_profile(path):
    """快速获取视频编码关键参数，用于格式一致性判断。
    
    Returns:
        dict with keys: codec_name, width, height, pix_fmt, r_frame_rate(float)
    """
    cmd = [
        "ffprobe", "-v", "quiet", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,pix_fmt,r_frame_rate",
        "-of", "json", path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                errors="ignore", timeout=10)
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        fps_str = stream.get("r_frame_rate", "30/1")
        num, den = fps_str.split("/")
        stream["r_frame_rate"] = float(num) / float(den)
        return stream
    except (subprocess.TimeoutExpired, Exception):
        return {"codec_name": "h264", "width": 1080, "height": 1920, "pix_fmt": "yuv420p", "r_frame_rate": 30.0}


def _blur_pad_video(input_path, output_path, target_w, target_h):
    """将单个视频用模糊背景填充到指定分辨率，保持原始比例居中。去掉音频。"""
    profile = _probe_video_profile(input_path)
    src_w, src_h = profile["width"], profile["height"]
    src_ratio = src_w / src_h
    target_ratio = target_w / target_h

    if abs(src_ratio - target_ratio) < 0.01:
        if src_w == target_w and src_h == target_h:
            return input_path
        cmd = [
            "ffmpeg", "-i", input_path,
            "-vf", f"scale={target_w}:{target_h},setsar=1",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-an", "-y", output_path
        ]
        subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=120)
        return output_path

    if src_ratio > target_ratio:
        fit_w, fit_h = target_w, int(target_w / src_ratio)
    else:
        fit_h = target_h
        fit_w = int(target_h * src_ratio)

    vf = (
        f"split[bg][fg];"
        f"[bg]scale={target_w}:{target_h},crop={target_w}:{target_h},boxblur=20:5[blurred];"
        f"[fg]scale={fit_w}:{fit_h}[fg_scaled];"
        f"[blurred][fg_scaled]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )

    cmd = [
        "ffmpeg", "-i", input_path,
        "-filter_complex", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-an", "-y", output_path
    ]
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                            errors="ignore", timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"blur pad failed: {result.stderr}")
    return output_path


def image_to_video(image_path, duration, output_path, target_w=1080, target_h=1920):
    """Convert a static image to a video with specified duration.
    
    If image aspect ratio is not 9:16, apply blur padding.
    No audio track - audio is handled separately.
    Uses FFmpeg for all operations to avoid OpenCV path encoding issues.
    """
    probe_cmd = [
        "ffprobe", "-v", "quiet", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json", image_path
    ]
    result = subprocess.run(probe_cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="ignore", timeout=10)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"Failed to probe image: {image_path}")
    
    probe_data = json.loads(result.stdout)
    stream = probe_data.get("streams", [{}])[0]
    w = stream.get("width", 0)
    h = stream.get("height", 0)
    
    if w == 0 or h == 0:
        raise RuntimeError(f"Failed to get image dimensions: {image_path}")
    
    src_ratio = w / h
    target_ratio = target_w / target_h
    
    if abs(src_ratio - target_ratio) < 0.01 and w == target_w and h == target_h:
        cmd = [
            "ffmpeg", "-loop", "1", "-i", image_path,
            "-t", str(duration),
            "-vf", f"scale={target_w}:{target_h},setsar=1",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-an",
            "-y", output_path
        ]
    else:
        if src_ratio > target_ratio:
            fit_w, fit_h = target_w, int(target_w / src_ratio)
        else:
            fit_h = target_h
            fit_w = int(target_h * src_ratio)
        
        vf = (
            f"split[bg][fg];"
            f"[bg]scale={target_w}:{target_h},crop={target_w}:{target_h},boxblur=20:5[blurred];"
            f"[fg]scale={fit_w}:{fit_h}[fg_scaled];"
            f"[blurred][fg_scaled]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
        
        cmd = [
            "ffmpeg", "-loop", "1", "-i", image_path,
            "-t", str(duration),
            "-filter_complex", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-an",
            "-y", output_path
        ]
    
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                            errors="ignore", timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"image_to_video failed: {result.stderr}")
    return output_path


def concat_videos(input_paths, output_path):
    """拼接视频，使用 filter_complex concat，自动统一视频参数。
    
    非9:16视频不拉伸，而是用模糊背景填充到9:16。
    只输出视频，不保留音频。
    """
    if not input_paths:
        raise RuntimeError("No input paths")

    try:
        ref = _probe_video_profile(input_paths[0])
    except Exception:
        ref = {"width": 1080, "height": 1920, "r_frame_rate": 30.0}
    ref_w, ref_h = ref["width"], ref["height"]
    ref_fps = ref["r_frame_rate"]

    processed_paths = []
    tmp_dir = None
    for p in input_paths:
        try:
            profile = _probe_video_profile(p)
            src_w, src_h = profile["width"], profile["height"]
            src_ratio = src_w / src_h
            ref_ratio = ref_w / ref_h

            if src_w == ref_w and src_h == ref_h:
                processed_paths.append(p)
            elif abs(src_ratio - ref_ratio) < 0.01:
                processed_paths.append(p)
            else:
                if tmp_dir is None:
                    tmp_dir = tempfile.mkdtemp()
                pad_path = os.path.join(tmp_dir, f"padded_{len(processed_paths)}.mp4")
                try:
                    _blur_pad_video(p, pad_path, ref_w, ref_h)
                    processed_paths.append(pad_path)
                except Exception:
                    continue
        except Exception:
            continue

    if not processed_paths:
        raise RuntimeError("No valid video paths to concatenate")

    try:
        cmd = ["ffmpeg"]
        for p in processed_paths:
            cmd.extend(["-i", p])

        filters = []
        input_parts = []
        for i in range(len(processed_paths)):
            filters.append(f"[{i}:v]scale={ref_w}:{ref_h},fps={ref_fps},setsar=1[v{i}]")
            input_parts.append(f"[v{i}]")

        concat_in = "".join(input_parts)
        filters.append(f"{concat_in}concat=n={len(processed_paths)}:v=1:a=0[outv]")

        filter_str = ";".join(filters)
        cmd.extend([
            "-filter_complex", filter_str,
            "-map", "[outv]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-an", "-y", output_path
        ])

        result = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                                errors="ignore", timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"concat failed: {result.stderr}")
        return output_path
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def remove_audio(video_path, output_path):
    """Remove audio from video."""
    if not os.path.exists(video_path):
        raise RuntimeError(f"Input file not found: {video_path}")
    cmd = [
        "ffmpeg", "-i", video_path, "-an",
        "-c", "copy",
        "-y", output_path
    ]
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=60)
    if result.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(f"remove_audio failed: {result.stderr}")
    return output_path


def add_audio(video_path, audio_path, output_path):
    """Add audio track to video with sync."""
    if not os.path.exists(video_path):
        raise RuntimeError(f"Video file not found: {video_path}")
    if not os.path.exists(audio_path):
        raise RuntimeError(f"Audio file not found: {audio_path}")
    cmd = [
        "ffmpeg", "-i", video_path, "-i", audio_path,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        "-y", output_path
    ]
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=3600)
    if result.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(f"add_audio failed: {result.stderr}")
    return output_path


def add_audio_with_silence(video_path, audio_path, output_path, silence_duration):
    """Add audio to video with silence padding at the beginning.
    
    Audio = silence(silence_duration) + audio_from_audio_path
    """
    if not os.path.exists(video_path):
        raise RuntimeError(f"Video file not found: {video_path}")
    if not os.path.exists(audio_path):
        raise RuntimeError(f"Audio file not found: {audio_path}")
    
    if silence_duration <= 0:
        return add_audio(video_path, audio_path, output_path)
    
    cmd = [
        "ffmpeg", "-i", video_path, "-i", audio_path,
        "-filter_complex",
        f"anullsrc=channel_layout=stereo:sample_rate=44100[silence];"
        f"[silence]atrim=0:{silence_duration},asetpts=PTS-STARTPTS[silence_padded];"
        f"[silence_padded][1:a]concat=n=2:v=0:a=1[outa]",
        "-map", "0:v:0", "-map", "[outa]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-y", output_path
    ]
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore", timeout=3600)
    if result.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(f"add_audio_with_silence failed: {result.stderr}")
    return output_path
