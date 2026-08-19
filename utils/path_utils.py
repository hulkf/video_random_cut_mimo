# -*- coding: utf-8 -*-
"""路径工具公共模块：剥引号 / 规范化 / 输出路径构建。只依赖标准库。

单点化（P1-4）：
  - strip_quotes 从 gui/video_fission_tab.py 上提（core/video_concatenator.normalize_input_path
    与 core/video_fission 内联剥引号语义等价）；
  - build_output_path / unique_output_path 沉淀公共命名 + 防重名能力。
"""
import os


def strip_quotes(path: str) -> str:
    """剥离路径首尾空白与成对引号（' " 各一对）。

    行为对齐 video_fission_tab.strip_quotes（video_concatenator.normalize_input_path 等价）：
    p.strip() 后，若 len>=2 且 p[0]==p[-1] 且 p[0] in ('"', "'")，剥掉首尾并再 strip。
    - 空串 → ""
    - '"a/b"' → "a/b"；"'a/b'" → "a/b"；"a/b" → "a/b"；引号在中间（a"b）不误删
    """
    p = (path or "").strip()
    if len(p) >= 2 and p[0] == p[-1] and p[0] in ('"', "'"):
        return p[1:-1].strip()
    return p


def normalize_path(path: str) -> str:
    """输入路径规范化 = strip_quotes(path)。

    刻意不做 os.path.normpath：与现状 video_concatenator.normalize_input_path /
    video_fission 内联剥引号语义完全一致，避免改变相对路径/报错文案行为。
    """
    return strip_quotes(path)


def unique_output_path(output_path: str) -> str:
    """防重名：output_path 不存在则原样返回；存在则追加 _2/_3/... 直到不冲突。

    行为对齐 video_fission.fission_folder 防重名（k 从 2 起）：
    out.mp4 存在 → out_2.mp4 → out_3.mp4 ...
    """
    if not os.path.exists(output_path):
        return output_path
    base, ext = os.path.splitext(output_path)
    k = 2
    candidate = "{}_{}{}".format(base, k, ext)
    while os.path.exists(candidate):
        k += 1
        candidate = "{}_{}{}".format(base, k, ext)
    return candidate


def build_output_path(output_dir: str, rel_base: str, suffix: str,
                      ext: str = ".mp4", dedupe: bool = True) -> str:
    """构建输出路径：os.path.join(output_dir, f"{rel_base}_{suffix}{ext}")，suffix 为空则 f"{rel_base}{ext}"；
    dedupe=True 时套 unique_output_path。对齐 video_resize_tab.engine_target_rel_path 的命名形态。
    """
    name = "{}_{}{}".format(rel_base, suffix, ext) if suffix else "{}{}".format(rel_base, ext)
    output_path = os.path.join(output_dir, name)
    if dedupe:
        output_path = unique_output_path(output_path)
    return output_path
