# -*- coding: utf-8 -*-
"""ffprobe 对比脚本：迁移前后输出参数一致性断言。

用法:
  python scripts/compare_media.py --base before.mp4 --new after.mp4
  python scripts/compare_media.py --base-dir out_before --new-dir out_after
  python scripts/compare_media.py --base-dir out_before --new-dir out_after --strict

对比字段: codec_name / width / height / sample_aspect_ratio / display_aspect_ratio /
         duration / r_frame_rate / pix_fmt
断言（默认）:
  - width/height 一致（分辨率）
  - SAR == "1:1"（千川硬校验；历史 2943:2944 事故防护）
  - DAR 一致（或比值一致）
  - |duration_new - duration_base| <= 0.05s
  - pix_fmt == "yuv420p"（若基线为 yuv420p）
  - 可播放性: ffmpeg -i new -frames:v 1 -f null - 返回 0（抽帧成功）
--strict: 全部字段逐项相等（含 codec_name 之外的 pix_fmt/r_frame_rate 也严格相等）
输出: 逐项对比表 + PASS/FAIL；任一断言失败 exit code != 0（便于脚本化）
依赖: 仅标准库 + ffprobe/ffmpeg（系统 PATH）
"""
import argparse
import json
import os
import subprocess
import sys

FIELDS = [
    ("codec_name", "编码"),
    ("width", "宽度"),
    ("height", "高度"),
    ("sample_aspect_ratio", "SAR"),
    ("display_aspect_ratio", "DAR"),
    ("duration", "时长(s)"),
    ("r_frame_rate", "帧率"),
    ("pix_fmt", "像素格式"),
]

DURATION_TOLERANCE = 0.05


def run_ffprobe(path):
    """提取视频流关键字段（对齐 AC-P1-5 字段清单）。"""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,width,height,sample_aspect_ratio,display_aspect_ratio,r_frame_rate,pix_fmt,duration",
        "-show_entries", "format=duration",
        "-of", "json", path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise RuntimeError("ffprobe failed on {}: {}".format(path, (r.stderr or "")[-500:]))
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams", [])
    if not streams:
        raise RuntimeError("No video stream in {}".format(path))
    s = streams[0]
    fmt = data.get("format", {}) or {}
    duration = s.get("duration") or fmt.get("duration") or 0.0
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        duration = 0.0
    fps = s.get("r_frame_rate", "0/1")
    try:
        if "/" in str(fps):
            num, den = str(fps).split("/", 1)
            fps = float(num) / max(1.0, float(den))
        else:
            fps = float(fps)
    except (TypeError, ValueError):
        fps = 0.0
    return {
        "codec_name": s.get("codec_name", "") or "",
        "width": int(s.get("width", 0) or 0),
        "height": int(s.get("height", 0) or 0),
        "sample_aspect_ratio": s.get("sample_aspect_ratio", "") or "0:1",
        "display_aspect_ratio": s.get("display_aspect_ratio", "") or "0:1",
        "duration": duration,
        "r_frame_rate": float(fps),
        "pix_fmt": s.get("pix_fmt", "") or "",
    }


def check_playable(path):
    """抽帧验证可解码：ffmpeg -i path -frames:v 1 -f null - 返回 0。"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            timeout=60,
        )
        return r.returncode == 0
    except Exception:
        return False


def compare_one(base_path, new_path, strict):
    """对比单个文件对，返回 (rows, failures)。"""
    base = run_ffprobe(base_path)
    new = run_ffprobe(new_path)
    failures = []
    rows = []

    # 逐字段对比表
    for key, label in FIELDS:
        bv = base[key]
        nv = new[key]
        if key == "duration":
            ok = abs(nv - bv) <= DURATION_TOLERANCE
            status = "PASS" if ok else "FAIL"
            b_s = "{:.3f}".format(bv)
            n_s = "{:.3f}".format(nv)
        elif key == "r_frame_rate":
            ok = abs(nv - bv) <= max(0.01, abs(bv) * 0.01)
            status = "PASS" if ok else "FAIL"
            b_s = "{:.3f}".format(bv)
            n_s = "{:.3f}".format(nv)
        elif key == "sample_aspect_ratio":
            # SAR 必须为 1:1（千川硬校验）
            ok = (nv == "1:1")
            status = "PASS" if ok else "FAIL"
            b_s, n_s = str(bv), str(nv)
        else:
            ok = (str(nv) == str(bv))
            status = "PASS" if ok else "FAIL"
            b_s, n_s = str(bv), str(nv)
        if strict and key != "codec_name":
            ok = ok and (str(nv) == str(bv))
            status = "PASS" if ok else "FAIL"
        rows.append((label, b_s, n_s, status))
        if not ok:
            failures.append("{}: base={} new={}".format(label, b_s, n_s))

    # DAR 一致（或比值一致：避免 9:16 与 1080x1920 的 DAR 表示差异）
    b_dar = base["display_aspect_ratio"]
    n_dar = new["display_aspect_ratio"]
    dar_ok = _dar_equal(b_dar, n_dar, base["width"], base["height"],
                        new["width"], new["height"])
    rows.append(("DAR(比值)", b_dar, n_dar, "PASS" if dar_ok else "FAIL"))
    if not dar_ok:
        failures.append("DAR mismatch: base={} new={}".format(b_dar, n_dar))

    # pix_fmt：基线为 yuv420p 时，新输出必须仍为 yuv420p
    if base["pix_fmt"] == "yuv420p" and new["pix_fmt"] != "yuv420p":
        failures.append("pix_fmt regression: base=yuv420p new={}".format(new["pix_fmt"]))

    # 可播放性
    playable = check_playable(new_path)
    rows.append(("可播放", "-", "-", "PASS" if playable else "FAIL"))
    if not playable:
        failures.append("new output not playable: {}".format(new_path))

    return rows, failures


def _dar_equal(b_dar, n_dar, b_w, b_h, n_w, n_h):
    """DAR 相等判断：字符串相同，或与像素宽高比等价（容忍 9:16 vs 1080x1920 表示差异）。"""
    if b_dar == n_dar:
        return True
    if not b_dar or not n_dar or ":" not in b_dar or ":" not in n_dar:
        return False
    try:
        def _ratio(s):
            a, b = s.split(":")
            return float(a) / max(1.0, float(b))
        br = _ratio(b_dar)
        nr = _ratio(n_dar)
        if abs(br - nr) < 0.001:
            return True
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    # 兜底：像素比等价（DAR 缺失时）
    if b_w > 0 and b_h > 0 and n_w > 0 and n_h > 0:
        return abs((b_w / b_h) - (n_w / n_h)) < 0.001
    return False


def print_report(rows, failures, base_path, new_path):
    print("=" * 78)
    print("对比: base={}".format(base_path))
    print("      new ={}".format(new_path))
    print("=" * 78)
    header = "{:<12}{:<16}{:<16}{}".format("字段", "基线", "新输出", "状态")
    print(header)
    print("-" * 78)
    for label, b_s, n_s, status in rows:
        print("{:<12}{:<16}{:<16}{}".format(label, b_s, n_s, status))
    print("-" * 78)
    if failures:
        print("FAIL ({} 项):".format(len(failures)))
        for f in failures:
            print("  - " + f)
        return False
    print("PASS")
    return True


def collect_pairs(base, new, base_dir, new_dir):
    """收集待对比的文件对。"""
    pairs = []
    if base and new:
        pairs.append((base, new))
        return pairs
    if not base_dir or not new_dir:
        raise SystemExit("必须提供 --base/--new 或 --base-dir/--new-dir")

    def _walk(dirpath):
        found = []
        for root, _dirs, files in os.walk(dirpath):
            for f in sorted(files):
                if f.lower().endswith((".mp4", ".avi", ".mov", ".mkv", ".flv")):
                    found.append(os.path.join(root, f))
        return sorted(found)

    base_files = _walk(base_dir)
    if not base_files:
        raise SystemExit("base-dir 下没有视频文件: {}".format(base_dir))
    for bf in base_files:
        rel = os.path.relpath(bf, base_dir)
        nf = os.path.join(new_dir, rel)
        if os.path.isfile(nf):
            pairs.append((bf, nf))
        else:
            pairs.append((bf, None))
    return pairs


def main():
    parser = argparse.ArgumentParser(description="ffprobe 对比脚本：迁移前后输出参数一致性断言")
    parser.add_argument("--base", help="迁移前输出文件")
    parser.add_argument("--new", help="迁移后输出文件")
    parser.add_argument("--base-dir", help="迁移前输出目录（递归）")
    parser.add_argument("--new-dir", help="迁移后输出目录（递归）")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式：pix_fmt/r_frame_rate 也逐项严格相等")
    args = parser.parse_args()

    pairs = collect_pairs(args.base, args.new, args.base_dir, args.new_dir)
    all_ok = True
    for base_path, new_path in pairs:
        if new_path is None:
            print("MISSING: {} 在 new-dir 中无对应文件".format(base_path))
            all_ok = False
            continue
        try:
            rows, failures = compare_one(base_path, new_path, args.strict)
        except Exception as e:
            print("ERROR comparing {} vs {}: {}".format(base_path, new_path, e))
            all_ok = False
            continue
        if not print_report(rows, failures, base_path, new_path):
            all_ok = False
        print()

    print("=" * 78)
    print("总体: {}".format("PASS" if all_ok else "FAIL"))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
