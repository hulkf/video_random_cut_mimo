"""Canonical task-center operation contracts shared by every adapter."""

from __future__ import annotations

from typing import Any


TASK_CENTER_OPERATION_SPECS: dict[str, dict[str, Any]] = {
    "task_center_templates": {
        "description": "读取视频工具注册的模板目录，供飞书等入口生成模板选择项",
        "required": [],
        "options": ["template_id"],
    },
    "task_center_validate_media": {
        "description": "通过任务中心入口执行只读媒体校验",
        "required": ["path"],
        "options": [],
    },
    "task_center_plan": {
        "description": "创建或更新视频任务计划；仅在用户明确说“直接确认/直接执行”时可同时确认并启动",
        "required": ["task_id", "title"],
        "options": [
            "cargo_number", "risk_note", "authorized_operations",
            "direct_confirmation_phrase", "confirmed_by", "steps", "parameter_lines",
            "template_id", "template_inputs", "template_options",
        ],
    },
    "task_center_confirm": {
        "description": "确认指定计划版本并启动独立后台执行器",
        "required": ["task_id", "plan_version"],
        "options": ["confirmed_by"],
    },
    "task_center_list": {
        "description": "查询本地、云端和混合视频任务总览及当日开拍额度台账",
        "required": [],
        "options": [
            "status", "cargo_number", "limit", "offset",
            "watchable_only", "reconcile",
        ],
    },
    "task_center_status": {
        "description": "查询一个视频任务及各步骤、逐文件云端进度",
        "required": ["task_id"],
        "options": [],
    },
    "task_center_control": {
        "description": "暂停、继续或取消一个视频任务，不影响其他任务",
        "required": ["task_id", "action"],
        "action_values": ["pause", "resume", "cancel"],
        "options": [],
    },
    "task_center_events": {
        "description": "按游标读取持久化任务事件，供 Web、飞书等平台增量同步",
        "required": [],
        "options": ["task_id", "after_event_id", "limit", "tail"],
    },
}


# Existing Hermes deployments still persist delivery acknowledgements in the
# task database.  This is isolated from the public contract and can be retired
# after installed profiles migrate to a dedicated delivery store.
HERMES_COMPAT_OPERATION_SPECS: dict[str, dict[str, Any]] = {
    "task_center_bind_card": {
        "description": "Hermes 兼容：绑定已发送的 Card 2.0 消息",
        "required": ["task_id", "chat_id"],
        "options": [
            "card_message_id", "delivered_updated_at", "delivered_revision",
            "pending_card_kind", "pending_card_revision", "pending_card_uuid",
            "pending_card_json", "pending_card_updated_at", "pending_card_mode",
            "pending_card_target_message_id", "ack_pending_revision",
            "ack_pending_uuid", "next_delivery_mode",
        ],
        "internal_adapter": "hermes",
    },
}


PUBLIC_OPERATION_NAMES = frozenset(TASK_CENTER_OPERATION_SPECS)
