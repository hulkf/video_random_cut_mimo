"""Describe existing footage and export the verified original-frame ranges."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.error
import urllib.request


PROVIDERS = {
    "deepseek": ("https://api.deepseek.com/chat/completions", "deepseek-flash", "DEEPSEEK_API_KEY"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "qwen3.7-plus", "DASHSCOPE_API_KEY"),
}
PROMPTS = {
    "remove_side": "标 S 给身体腰胯明显侧面、侧后方或背面及仍叠有这些画面的帧。清楚正面、轻微倾斜但仍以正面为主为 F。",
    "keep_front": "仅在腰胯与所穿商品主要清楚展示正面时标 F。侧面、侧后方、背面或仍有它们的叠化为 S。",
    "visible_candidates": "以当前角度的目标商品为准。腰口、可见裤脚边缘和两侧可见外轮廓都在画面内且无明显遮挡才标 F；明确截断或挡住标 S。衣服另一面的自然不可见不算遮挡。"
}
ORIENTATION_GUIDE = (
    "用腰胯和商品的朝向判断，不凭手、肩膀、腿的姿势，也不因裁切和颜色变化改判。"
    "若叠化两层都明确为正面，仍标 F；若能看出任一层为侧面或背面，标 S；"
    "叠化使朝向看不清时标 U。"
)
OPERATION = "video_scene_edit"


def _run(program, args, timeout=100):
    process = subprocess.run(
        [program, *args], capture_output=True, check=False, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if process.returncode:
        raise RuntimeError(f"{program} failed ({process.returncode})")
    return process.stdout


def _probe(path):
    data = json.loads(_run("ffprobe", [
        "-v", "error", "-show_streams", "-show_frames", "-show_entries",
        "stream=codec_type,duration:frame=media_type,best_effort_timestamp_time",
        "-of", "json", str(path),
    ]))
    times = [float(f["best_effort_timestamp_time"]) for f in data["frames"]
             if f["media_type"] == "video"]
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    duration = float(video["duration"])
    if (not times or abs(times[0]) > .001 or duration > 120 or len(times) > 6000
            or times[-1] >= duration or any(a >= b for a, b in zip(times, times[1:]))):
        raise ValueError("需要零起点且时间戳递增的 120 秒以内视频")
    return times, duration, any(s["codec_type"] == "audio" for s in data["streams"])


def _read_keys(want_qwen):
    # The Workbench starts its native worker with a dedicated database. Other
    # video profiles use their own databases and may not opt into this provider.
    database = Path(os.environ.get("VIDEO_TASK_CENTER_DB", ""))
    if not database.is_absolute() or database.parts[-3:] != ("tools", "video-scene-edit", "tasks.db"):
        raise PermissionError("画面识别仅可由工作台专用视频任务调用")
    env_file = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / ".env"
    if not env_file.is_absolute() or not env_file.is_file():
        raise RuntimeError("未配置工作台视频识别凭据")
    values = {}
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.partition("=")
        if sep and key in {entry[2] for entry in PROVIDERS.values()}:
            values[key] = value.strip().strip('"\'')
    required = ("deepseek", "qwen") if want_qwen else ("deepseek",)
    if any(not values.get(PROVIDERS[p][2]) for p in required):
        raise RuntimeError("工作台视频识别缺少对应模型凭据")
    return values


def classify(provider, frames, mode, description, keys):
    url, model, key_name = PROVIDERS[provider]
    guidance = ORIENTATION_GUIDE if mode != "visible_candidates" else ""
    prompt = (f"这是电商服装剪辑任务。{PROMPTS[mode]}{guidance}\n用户描述：{description}\n"
              "根据当前帧画面分类，邻帧只作辅助。有明显转身而不能清楚确定的帧标 U；"
              "仅回答 JSON 对象 {\"labels\":[\"F\",\"S\",\"U\"]}，"
              f"按图片顺序给恰好 {len(frames)} 项。F=可保留/候选，S=不符合，U=证据不够。")
    content = [{"type": "text", "text": prompt}]
    for number, time, filename in frames:
        content.extend([
            {"type": "text", "text": f"原片帧 {number}，{time:.6f} 秒"},
            {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(filename.read_bytes()).decode("ascii"),
            }},
        ])
    body = {"model": model, "messages": [{"role": "user", "content": content}],
            "max_tokens": 768, "response_format": {"type": "json_object"}}
    if provider == "deepseek":
        body["thinking"] = {"type": "disabled"}
    else:
        body["enable_thinking"] = False
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + keys[key_name], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"{provider} 请求失败 HTTP {error.code}") from None
    labels = json.loads(result["choices"][0]["message"]["content"])["labels"]
    if not isinstance(labels, list) or len(labels) != len(frames) or any(l not in ("F", "S", "U") for l in labels):
        raise ValueError(f"{provider} 未返回逐帧有效的分类")
    return labels


def _frame_batch(source, indices, times, scratch):
    scratch.mkdir()
    expr = "+".join(f"eq(n\\,{i})" for i in indices)
    _run("ffmpeg", ["-nostdin", "-v", "error", "-i", str(source), "-vf",
         f"select={expr},scale=360:-2", "-fps_mode", "passthrough", "-q:v", "3", "-an",
         str(scratch / "f%03d.jpg")])
    images = sorted(scratch.glob("f*.jpg"))
    if len(images) != len(indices):
        raise ValueError("解码帧数与原始时间线不一致")
    return [(i, times[i], image) for i, image in zip(indices, images)]


def _merge(windows):
    merged = []
    for lo, hi in sorted(windows):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    return merged


def _ranges(labels, target):
    ranges = []
    for i, value in enumerate(labels):
        if value != target:
            continue
        if ranges and ranges[-1][1] == i:
            ranges[-1][1] += 1
        else:
            ranges.append([i, i+1])
    return ranges


def reconcile(indices, coarse, frame_count, dense):
    # An unobserved cut or contradictory boundary is never accepted just because a
    # model returned a plausible timestamp. Use decoded frame indices throughout.
    output = [None] * frame_count
    for n, lo in enumerate(indices):
        hi = indices[n+1] if n+1 < len(indices) else frame_count
        if n+1 < len(coarse) and coarse[n] != coarse[n+1]:
            continue
        if coarse[n] != "U":
            output[lo:hi] = [coarse[n]] * (hi-lo)
    for lo, labels in dense:
        hi = lo + len(labels)
        for i in range(lo, hi):
            output[i] = labels[i-lo] if labels[i-lo] != "U" else None
    return output


def _snapshot(path):
    st = path.stat()
    with path.open("rb") as file:
        return st.st_size, st.st_mtime_ns, hashlib.file_digest(file, "sha256").hexdigest()


def export(source, target, ranges, times, duration, audio):
    # One output per source, same ordering, audio follows the same original time.
    graph, pads = [], []
    for i, (lo, hi) in enumerate(ranges):
        graph.append(f"[0:v]trim=start_frame={lo}:end_frame={hi},setpts=PTS-STARTPTS[v{i}]")
        pads.append(f"[v{i}]")
        if audio:
            end = times[hi] if hi < len(times) else duration
            graph.append(f"[0:a]atrim=start={times[lo]:.9f}:end={end:.9f},asetpts=PTS-STARTPTS[a{i}]")
            pads.append(f"[a{i}]")
    graph.append("".join(pads) + f"concat=n={len(ranges)}:v=1:a={int(audio)}[v]" + ("[a]" if audio else ""))
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".scene-") as temp:
        staged = Path(temp) / "result.mp4"
        args = ["-nostdin", "-v", "error", "-i", str(source), "-filter_complex", ";".join(graph), "-map", "[v]"]
        if audio:
            args += ["-map", "[a]", "-c:a", "aac"]
        args += ["-c:v", "libx264", "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p",
                 "-fps_mode", "passthrough", "-movflags", "+faststart", str(staged)]
        _run("ffmpeg", args, timeout=180)
        decoded = int(_run("ffprobe", ["-v", "error", "-select_streams", "v:0", "-count_frames",
                    "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(staged)]).strip())
        if decoded != sum(hi-lo for lo, hi in ranges):
            raise RuntimeError("成片帧数与保留区间不一致")
        os.link(staged, target)


def _one(entry, mode, description, output, keys, check):
    source = Path(entry["path"])
    if not source.is_absolute() or source.resolve(strict=True) != source or source.suffix.lower() != ".mp4":
        raise ValueError("请选择有效的原片 MP4")
    stamp = _snapshot(source)
    if stamp[:2] != (entry["size"], int(entry["mtime_ns"])):
        raise ValueError("原片在工作台授权后发生变化")
    times, duration, audio = _probe(source)
    check()
    with tempfile.TemporaryDirectory(prefix="video-scene-edit-") as temp:
        scratch = Path(temp)
        # At least four samples per second for the first pass. The last frame is
        # sampled explicitly so a change at the end is eligible for refinement.
        indices = [0]
        for i, t in enumerate(times):
            if t - times[indices[-1]] >= .24:
                indices.append(i)
        if indices[-1] != len(times)-1:
            indices.append(len(times)-1)
        coarse = []
        for j in range(0, len(indices), 32):
            frames = _frame_batch(source, indices[j:j+32], times, scratch / f"coarse{j}")
            coarse.extend(classify("deepseek", frames, mode, description, keys))
            check()
        if mode == "visible_candidates":
            labels = reconcile(indices, coarse, len(times), [])
            review = [i for i, label in enumerate(labels) if label is None or label == "U"]
            spans = _ranges(labels, "F")
            return {"file": source.name, "status": "candidate_only", "candidate_frames": spans,
                    "candidate_seconds": [[round(times[a], 6), round(times[b] if b < len(times) else duration, 6)] for a, b in spans],
                    "review_frames": len(review)}

        windows = []
        for j, (a, b) in enumerate(zip(coarse, coarse[1:])):
            if a != b or a == "U":
                lo = indices[j]
                hi = indices[j+1]
                pad = max(4, hi - lo)
                windows.append((max(0, lo-pad), min(len(times), hi+pad+1)))

        def confirm(span):
            lo, hi = span
            frames = _frame_batch(source, list(range(lo, hi)), times, scratch / f"qwen{lo}")
            return lo, classify("qwen", frames, mode, description, keys)

        dense = []
        if windows:
            # Split long mutually overlapping windows and cover every intervening frame.
            cuts = [(start, min(start+32, hi)) for lo, hi in _merge(windows) for start in range(lo, hi, 32)]
            with ThreadPoolExecutor(max_workers=2) as pool:
                for result in pool.map(confirm, cuts):
                    check()
                    dense.append(result)
        else:
            # Uniform clips have no cut point. A second model sees a spread of
            # representative frames before an unchanged/empty decision is accepted.
            sentinel = sorted(set(indices[::max(1, len(indices)//12)] + [indices[-1]]))
            frames = _frame_batch(source, sentinel, times, scratch / "uniform")
            second = classify("qwen", frames, mode, description, keys)
            if set(second) != {coarse[0]} or "U" in coarse:
                return {"file": source.name, "status": "review_required", "reason": "整段方向两模型不一致"}
        labels = reconcile(indices, coarse, len(times), dense)
        confirmed = {lo+i for lo, part in dense for i, label in enumerate(part) if label in ("F", "S")}
        boundaries = [i for i in range(1, len(labels)) if labels[i] != labels[i-1]]
        ranges = _ranges(labels, "F")
        times_result = [[round(times[a], 6), round(times[b] if b < len(times) else duration, 6)] for a,b in ranges]
        result = {"file": source.name, "keep_frames": ranges, "keep_seconds": times_result,
                  "source_frames": len(times), "confirmed_cut_points": [i for i in boundaries if i in confirmed and i-1 in confirmed],
                  "deepseek_sample_indices": indices, "deepseek_labels": coarse,
                  "qwen_windows": [[lo, len(part)] for lo, part in dense]}
        if None in labels or any(i not in confirmed or i-1 not in confirmed for i in boundaries):
            return dict(result, status="review_required", reason="有未复核或方向不确定的切点")
        if not ranges:
            return dict(result, status="empty")
        if ranges == [[0, len(times)]]:
            return dict(result, status="unchanged")
        if _snapshot(source) != stamp:
            raise ValueError("源文件在识别过程中发生变化")
        check()
        target = output / (source.stem + "-正面.mp4")
        export(source, target, ranges, times, duration, audio)
        if _snapshot(source) != stamp:
            target.unlink()
            raise ValueError("原片在导出期间发生变化")
        return dict(result, status="exported", output=str(target))


def run_scene_edit(request):
    inputs = request["inputs"]
    mode = inputs["mode"]
    if mode not in PROMPTS:
        raise ValueError("不支持的剪辑模式")
    files = inputs["files"]
    description = inputs["description"]
    output = Path(inputs["output_folder"])
    if (not isinstance(files, list) or not 1 <= len(files) <= 10
            or not isinstance(description, str) or not 1 <= len(description.strip()) <= 500
            or not output.is_absolute() or output.resolve(strict=True) != output or not output.is_dir()):
        raise ValueError("视频剪辑输入不完整")
    keys = _read_keys(mode != "visible_candidates")
    controller = request.get("_task_controller")
    def check():
        if controller is not None:
            controller.check()
    results = []
    for entry in files:
        check()
        try:
            results.append(_one(entry, mode, description, output, keys, check))
        except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            from core.task_control import TaskControlSignal
            if isinstance(error, TaskControlSignal):
                raise
            results.append({"file": Path(entry["path"]).name, "status": "review_required", "reason": str(error)[:250]})
    receipt = output / "剪辑复核.json"
    with receipt.open("x", encoding="utf-8") as file:
        json.dump({"mode": mode, "results": results}, file, ensure_ascii=False, indent=2)
    return {"operation": OPERATION, "results": results,
            "outputs": [r["output"] for r in results if r.get("output")] + [str(receipt)]}
