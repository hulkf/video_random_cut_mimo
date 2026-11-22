"""Restore a confirmed SmartEdit name-card region from its paired original."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np


def restore_frame(source, rendered, box, sift):
    x0, y0, x1, y1 = box
    original_gray, render_gray = [
        cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), None, fx=.5, fy=.5)
        for frame in (source, rendered)
    ]
    search = np.zeros(render_gray.shape, np.uint8)
    middle = render_gray.shape[0]
    search[round(middle * .22):round(middle * .84), :] = 255
    search[max(0, y0 // 2 - 8):y1 // 2 + 8, max(0, x0 // 2 - 8):x1 // 2 + 8] = 0
    key_src, desc_src = sift.detectAndCompute(original_gray, None)
    key_dst, desc_dst = sift.detectAndCompute(render_gray, search)
    if desc_src is None or desc_dst is None:
        raise RuntimeError("名片附近缺少原片配准特征")
    pairs = cv2.BFMatcher().knnMatch(desc_src, desc_dst, k=2)
    matches = [m for pair in pairs if len(pair) == 2 for m, n in [pair] if m.distance < .73 * n.distance]
    if len(matches) < 12:
        raise RuntimeError("原片与初版的相同画面特征不足")
    src = np.float32([key_src[m.queryIdx].pt for m in matches])
    dst = np.float32([key_dst[m.trainIdx].pt for m in matches])
    transform, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=1.4)
    if transform is None or int(inliers.sum()) < 10:
        raise RuntimeError("原片与初版无法可靠配准")
    if not .4 <= float(np.linalg.norm(transform[:, 0])) <= 3:
        raise RuntimeError("名片区域的原片缩放比例无法确认")
    transform[:, 2] = transform[:, 2] * 2 - (x0, y0)
    size = (x1 - x0, y1 - y0)
    patch = cv2.warpAffine(source, transform, size, borderMode=cv2.BORDER_REPLICATE)
    covered = cv2.warpAffine(np.full(source.shape[:2], 255, np.uint8), transform, size)
    original = rendered[y0:y1, x0:x1]
    difference = cv2.absdiff(original, patch).max(axis=2)
    glyph = np.uint8(difference > 19) * 255
    if (glyph[covered < 250] > 0).any():
        raise RuntimeError("名片区域超出原片可恢复画面")
    glyph = cv2.morphologyEx(glyph, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    glyph = cv2.dilate(glyph, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    alpha = cv2.GaussianBlur(glyph, (7, 7), 1.2).astype(np.float32)[:, :, None] / 255
    original[:] = np.rint(original * (1 - alpha) + patch * alpha).astype(np.uint8)
    return int(np.count_nonzero(glyph))


def repair(source, rendered, output, regions, ffmpeg):
    with_audio = [
        str(ffmpeg), "-nostdin", "-v", "error", "-xerror", "-n", "-f", "rawvideo",
        "-pixel_format", "bgr24",
    ]
    cv2.setNumThreads(1)
    original = cv2.VideoCapture(str(source))
    draft = cv2.VideoCapture(str(rendered))
    try:
        width, height = (int(draft.get(prop)) for prop in (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT))
        original_fps, fps = (capture.get(cv2.CAP_PROP_FPS) for capture in (original, draft))
        if not (original.isOpened() and draft.isOpened() and width > 0 and height > 0 and fps > 0
                and abs(original_fps - fps) < .1):
            raise RuntimeError("原片与初版的画面时间线需要人工核对")
        prepared = []
        for region in regions:
            start, end = region["start"], region["end"]
            left, top, right, bottom = region["box"]
            if not (0 <= start < end and 0 <= left < right <= 1000 and 0 <= top < bottom <= 1000
                    and (right - left) * (bottom - top) <= 180_000):
                raise ValueError("名片位置或覆盖范围不适合精确局部修复")
            box = (max(0, left * width // 1000 - 6), max(0, top * height // 1000 - 6),
                   min(width, right * width // 1000 + 7), min(height, bottom * height // 1000 + 7))
            if box[2] - box[0] < 4 or box[3] - box[1] < 4:
                raise ValueError("名片区域过小")
            prepared.append((start, end, box))
        sift = cv2.SIFT_create(nfeatures=1800)
        changed, sample, sample_pixels = 0, None, 0
        frame_index = 0
        command = [*with_audio, "-video_size", f"{width}x{height}", "-framerate", str(fps),
                   "-i", "pipe:0", "-i", str(rendered), "-map", "0:v:0", "-map", "1:a?",
                   "-c:v", "libx264", "-preset", "fast", "-crf", "17", "-pix_fmt", "yuv420p",
                   "-c:a", "copy", "-movflags", "+faststart", str(output)]
        with subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE) as encoder:
            try:
                while True:
                    ok, frame = draft.read()
                    if not ok:
                        break
                    timestamp = frame_index / fps
                    boxes = [box for start, end, box in prepared if start <= timestamp < end]
                    raw_ok, raw = original.read()
                    if boxes:
                        if not raw_ok:
                            raise RuntimeError("名片时段无法读取配对原片")
                        before = frame.copy()
                        try:
                            pixels = sum(restore_frame(raw, frame, box, sift) for box in boxes)
                        except RuntimeError as exc:
                            raise RuntimeError(f"{timestamp:.2f} 秒：{exc}") from exc
                        if pixels:
                            changed += 1
                            if pixels > sample_pixels:
                                sample = (before, frame.copy(), timestamp)
                                sample_pixels = pixels
                    encoder.stdin.write(frame.tobytes())
                    frame_index += 1
                encoder.stdin.close()
                if encoder.wait(timeout=150) or not changed:
                    raise RuntimeError("局部修复未产出有效成片或未检测到需要覆盖的名片像素")
            finally:
                if encoder.poll() is None:
                    encoder.kill()
                    encoder.wait()
        before, after, timestamp = sample
        for label, frame in (("before", before), ("after", after)):
            ok, image = cv2.imencode(".jpg", frame)
            if not ok:
                raise RuntimeError("无法保存同帧验收图")
            output.with_suffix(f".{label}.jpg").write_bytes(image.tobytes())
        return {"changed_frames": changed, "frame_count": frame_index, "comparison_timestamp": timestamp}
    finally:
        original.release()
        draft.release()


def main():
    inputs = json.load(sys.stdin)
    try:
        result = repair(Path(inputs["source"]), Path(inputs["rendered"]), Path(inputs["output"]),
                        inputs["regions"], Path(inputs["ffmpeg"]))
        print(json.dumps({"success": True, **result}))
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
