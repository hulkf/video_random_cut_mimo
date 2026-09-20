"""Legacy Hermes delivery adapter kept outside the public task-center seam."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from core.video_task_center import TaskCenter
from .interface import (
    PLATFORM_ADAPTER_FIELDS,
    TASK_CENTER_NAME,
    TASK_CENTER_VERSION,
    _dispatch_validated,
)


def dispatch(
    request: dict[str, Any],
    *,
    operation_catalog: Mapping[str, Any],
    validate_request: Callable[[dict[str, Any]], None],
    authorization_required: Callable[[dict[str, Any]], bool],
    db_path: str | None = None,
) -> dict[str, Any]:
    """Run a task command with installed Hermes card-delivery compatibility."""
    validate_request(request)
    operation = str(request.get("operation") or "")
    inputs = dict(request.get("inputs") or request)
    center = TaskCenter(db_path)

    if operation == "task_center_bind_card":
        result = center.bind_card(
            inputs["task_id"], inputs["chat_id"], inputs.get("card_message_id", ""),
            delivered_updated_at=inputs.get("delivered_updated_at", ""),
            delivered_revision=inputs.get("delivered_revision"),
            pending_kind=inputs.get("pending_card_kind"),
            pending_revision=inputs.get("pending_card_revision"),
            pending_uuid=inputs.get("pending_card_uuid"),
            pending_card_json=inputs.get("pending_card_json"),
            pending_updated_at=inputs.get("pending_card_updated_at"),
            pending_mode=inputs.get("pending_card_mode"),
            pending_target_message_id=inputs.get("pending_card_target_message_id"),
            ack_pending_revision=inputs.get("ack_pending_revision"),
            ack_pending_uuid=inputs.get("ack_pending_uuid"),
            next_delivery_mode=inputs.get("next_delivery_mode"),
        )
    elif operation == "task_center_confirm" and (
        inputs.get("expected_chat_id") or inputs.get("expected_card_message_id")
    ):
        result = center.confirm(
            inputs["task_id"], int(inputs["plan_version"]),
            confirmed_by=inputs.get("confirmed_by", ""),
            confirmation_message_id=inputs.get("confirmation_message_id", ""),
            expected_chat_id=inputs.get("expected_chat_id", ""),
            expected_card_message_id=inputs.get("expected_card_message_id", ""),
        )
    elif operation == "task_center_control" and (
        inputs.get("expected_chat_id") or inputs.get("expected_card_message_id")
    ):
        result = center.control(
            inputs["task_id"], inputs["action"],
            expected_chat_id=inputs.get("expected_chat_id", ""),
            expected_card_message_id=inputs.get("expected_card_message_id", ""),
        )
    else:
        direct_confirmation_phrase = inputs.get("direct_confirmation_phrase")
        clean_inputs = {
            key: value for key, value in inputs.items()
            if key not in PLATFORM_ADAPTER_FIELDS
        }
        if operation == "task_center_plan" and direct_confirmation_phrase:
            clean_inputs.pop("direct_confirmation_phrase", None)
        core_request = {**request, "inputs": clean_inputs}
        core = _dispatch_validated(
            core_request,
            operation_catalog=operation_catalog,
            validate_request=validate_request,
            authorization_required=authorization_required,
            db_path=db_path,
            public=False,
        )
        if operation == "task_center_plan" and inputs.get("chat_id"):
            result = center.bind_card(
                core["task"]["task_id"],
                str(inputs.get("chat_id") or ""),
                str(inputs.get("card_message_id") or ""),
            )
        else:
            result = core["task"]
        if operation == "task_center_plan" and direct_confirmation_phrase:
            result = center.confirm(
                core["task"]["task_id"], int(core["task"]["plan_version"]),
                confirmed_by=inputs.get("confirmed_by", ""),
                confirmation_message_id=inputs.get("confirmation_message_id", ""),
            )

    return {
        "success": True,
        "task_center": TASK_CENTER_NAME,
        "version": TASK_CENTER_VERSION,
        "operation": operation,
        "task": result,
    }
