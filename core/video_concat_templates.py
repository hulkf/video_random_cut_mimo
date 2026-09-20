"""视频拼接模板注册表。

模板只描述调用契约和默认参数；实际拼接仍由 VideoConcatenatorEngine 完成。
没有默认值的输入必须由调用方提供。
"""

from copy import deepcopy


_VIDEO_CONCAT_TEMPLATES = {
    "001": {
        "id": "001",
        "name": "模板001",
        "operation": "video_concat",
        "required_inputs": ["folder_a", "folder_b", "output_folder"],
        "default_options": {
            "cover_enabled": True,
            "cover_source": "video_b_frame",
            "cover_folder": "",
            "cover_mode": "front",
            "cover_duration_min": 0.2,
            "cover_duration_max": 0.5,
            "require_9x16": True,
            "require_cover": True,
        },
    },
}


def _normalize_template_id(template_id) -> str:
    value = str(template_id).strip()
    return value.zfill(3) if value.isdigit() else value


def get_video_concat_template(template_id) -> dict:
    normalized_id = _normalize_template_id(template_id)
    template = _VIDEO_CONCAT_TEMPLATES.get(normalized_id)
    if template is None:
        raise ValueError("未知的视频拼接模板: {}".format(template_id))
    return deepcopy(template)


def list_video_concat_templates() -> list:
    return [deepcopy(template) for template in _VIDEO_CONCAT_TEMPLATES.values()]


def resolve_video_concat_template(template_id, inputs=None, options=None) -> dict:
    template = get_video_concat_template(template_id)
    resolved_inputs = dict(inputs or {})
    missing = [
        key for key in template["required_inputs"]
        if not resolved_inputs.get(key)
    ]
    if missing:
        raise ValueError("缺少必要参数: {}".format(", ".join(missing)))

    resolved_options = dict(template["default_options"])
    resolved_options.update(options or {})
    return {
        "template_id": template["id"],
        "inputs": resolved_inputs,
        "options": resolved_options,
    }
