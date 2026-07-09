# -*- coding: utf-8 -*-
"""
ONNX Runtime 执行提供者统一配置

自动检测并优先使用 DirectML（Intel Arc 核显）加速推理，
回退到 CPU 执行。所有 ONNX 引擎共用此模块，确保行为一致。
"""
import onnxruntime as ort


def get_providers():
    """返回可用的执行提供者列表，DirectML 优先。

    Returns:
        list: provider 列表，如 ['DmlExecutionProvider', 'CPUExecutionProvider']
    """
    available = ort.get_available_providers()
    providers = []
    if "DmlExecutionProvider" in available:
        providers.append("DmlExecutionProvider")
    providers.append("CPUExecutionProvider")
    return providers


def get_session_options(inter_op=2, intra_op=2):
    """创建预配置的 SessionOptions。

    Args:
        inter_op: 并行计算图之间的线程数
        intra_op: 单个计算图内部并行线程数

    Returns:
        ort.SessionOptions
    """
    opts = ort.SessionOptions()
    opts.log_severity_level = 3  # 只输出错误
    opts.inter_op_num_threads = inter_op
    opts.intra_op_num_threads = intra_op
    return opts
