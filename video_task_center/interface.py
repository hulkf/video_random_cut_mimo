"""Small, platform-neutral interface for the video task center."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from core.video_task_center import TaskCenter
from .contracts import PUBLIC_OPERATION_NAMES, TASK_CENTER_OPERATION_SPECS


TASK_CENTER_NAME = "video-task-center"
TASK_CENTER_VERSION = "1.0.0"

# Deliberately excludes Feishu/Card Kit delivery state.  Platform adapters may
# use the compatibility delivery operation, but it is not part of the public
# task-management contract offered to new callers.
PUBLIC_CAPABILITIES: dict[str, Any] = {
    "name": TASK_CENTER_NAME,
    "version": TASK_CENTER_VERSION,
    "operations": TASK_CENTER_OPERATION_SPECS,
    "constraints": {
        "platform_neutral": True,
        "json_only": True,
        "presentation_owned_by_caller": True,
        "transport_owned_by_caller": True,
        "single_state_store": True,
    },
}


PLATFORM_ADAPTER_FIELDS = frozenset({
    "chat_id", "card_message_id", "card_delivered_updated_at",
    "card_delivered_revision", "delivered_updated_at", "delivered_revision",
    "expected_chat_id", "expected_card_message_id", "pending_card_kind",
    "pending_card_revision", "pending_card_uuid", "pending_card_json",
    "pending_card_updated_at", "pending_card_mode",
    "pending_card_target_message_id", "ack_pending_revision",
    "ack_pending_uuid", "next_delivery_mode", "card_next_delivery_mode",
    "confirmation_message_id",
})


def _inputs(request: Mapping[str, Any]) -> dict[str, Any]:
    raw = request.get("inputs") or request
    if not isinstance(raw, Mapping):
        raise ValueError("inputs must be an object")
    return dict(raw)


def dispatch(
    request: dict[str, Any],
    *,
    operation_catalog: Mapping[str, Any] | None = None,
    validate_request: Callable[[dict[str, Any]], None] | None = None,
    authorization_required: Callable[[dict[str, Any]], bool] | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Execute one task-center command through the stable public seam.

    Business-operation validation is injected by the video tool so the task
    center never duplicates media capability rules.  The returned value is
    transport-neutral data; callers decide whether to render HTML, Card 2.0 or
    another presentation.
    """

    if operation_catalog is None or validate_request is None or authorization_required is None:
        # Lazy import keeps video_tool -> task-center compatibility acyclic while
        # giving Web/Python callers one ready-to-use public function.
        import video_tool

        operation_catalog = video_tool.CAPABILITIES["operations"]
        validate_request = video_tool._validate_request
        authorization_required = video_tool._authorization_required
    validate_request(request)
    return _dispatch_validated(
        request,
        operation_catalog=operation_catalog,
        validate_request=validate_request,
        authorization_required=authorization_required,
        db_path=db_path,
        public=True,
    )


def _dispatch_validated(
    request: dict[str, Any],
    *,
    operation_catalog: Mapping[str, Any],
    validate_request: Callable[[dict[str, Any]], None],
    authorization_required: Callable[[dict[str, Any]], bool],
    db_path: str | None = None,
    public: bool,
) -> dict[str, Any]:
    """Execute a request which has already passed the outer request gate.

    This private seam lets compatibility adapters keep their raw delivery
    fields without validating or executing the same task-center operation a
    second time.
    """
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    operation = str(request.get("operation") or "")
    if operation not in PUBLIC_OPERATION_NAMES:
        raise ValueError(f"unsupported task center operation: {operation}")
    inputs = _inputs(request)
    if public:
        leaked = sorted(PLATFORM_ADAPTER_FIELDS.intersection(inputs))
        if leaked:
            raise ValueError(
                "public task center does not accept presentation or transport fields: "
                + ", ".join(leaked)
            )
    center = TaskCenter(db_path)

    if operation == "task_center_plan":
        declared_authorizations = {
            str(value) for value in inputs.get("authorized_operations") or []
        }
        required_authorizations: set[str] = set()
        for index, step in enumerate(inputs.get("steps") or []):
            inner = dict(step.get("request") or {}) if isinstance(step, dict) else {}
            inner_operation = str(inner.get("operation") or "")
            if (
                not inner_operation
                or inner_operation == "task_control"
                or inner_operation.startswith("task_center_")
            ):
                raise ValueError(f"步骤 {index + 1} 不是可执行的视频业务操作")
            if inner_operation in {"settings_secret_set", "settings_update"}:
                raise ValueError(
                    f"{inner_operation} 不允许进入持久化视频任务计划，请使用专用交互流程"
                )
            if inner_operation not in operation_catalog:
                raise ValueError(f"不支持的视频业务操作: {inner_operation}")
            validation_request = dict(inner)
            if step.get("items_from_step"):
                target_key = str(
                    step.get("input_key")
                    or ("items" if inner_operation == "kaipai_download" else "input_path")
                )
                required = operation_catalog[inner_operation].get("required") or []
                if target_key not in required:
                    raise ValueError(
                        f"步骤 {index + 1} 的 input_key={target_key} 不是该 operation 的必要输入"
                    )
                placeholder: Any = (
                    [{"url": "https://placeholder.invalid/result.mp4"}]
                    if target_key == "items"
                    else "D:/derived-input"
                )
                if isinstance(validation_request.get("inputs"), dict):
                    validation_request["inputs"] = {
                        **validation_request["inputs"], target_key: placeholder,
                    }
                else:
                    validation_request[target_key] = placeholder
            validation_request["authorization"] = {
                "confirmed": True,
                "scope": inner_operation,
            }
            validate_request(validation_request)
            if authorization_required(inner):
                required_authorizations.add(inner_operation)
        missing = sorted(required_authorizations - declared_authorizations)
        if missing:
            raise PermissionError(
                "计划包含需单独披露的风险操作但未声明授权范围: " + ", ".join(missing)
            )
        if required_authorizations and not str(inputs.get("risk_note") or "").strip():
            raise ValueError("计划包含外部服务或删除操作，risk_note 不能为空")
        if not [line for line in inputs.get("parameter_lines") or [] if str(line).strip()]:
            raise ValueError("parameter_lines 必须完整列出实际参数")
        result = center.create_plan(
            inputs["task_id"], inputs["title"], inputs["steps"],
            cargo_number=inputs.get("cargo_number", ""),
            parameter_lines=inputs.get("parameter_lines") or [],
            risk_note=inputs.get("risk_note", ""),
            authorized_operations=inputs.get("authorized_operations") or [],
            # Legacy presentation context is accepted only for existing Hermes
            # callers.  It is intentionally absent from PUBLIC_CAPABILITIES.
        )
        if inputs.get("direct_confirmation_phrase"):
            result = center.confirm(
                result["task_id"], int(result["plan_version"]),
                confirmed_by=inputs.get("confirmed_by", ""),
            )
    elif operation == "task_center_confirm":
        result = center.confirm(
            inputs["task_id"], int(inputs["plan_version"]),
            confirmed_by=inputs.get("confirmed_by", ""),
        )
    elif operation == "task_center_list":
        result = center.list_tasks(
            status=inputs.get("status", ""),
            cargo_number=inputs.get("cargo_number", ""),
            limit=int(inputs.get("limit", 20)),
            offset=int(inputs.get("offset", 0)),
            watchable_only=inputs.get("watchable_only", False),
            reconcile=inputs.get("reconcile", True),
        )
    elif operation == "task_center_status":
        center.reconcile_workers(inputs["task_id"])
        result = center.get(inputs["task_id"])
    elif operation == "task_center_control":
        result = center.control(
            inputs["task_id"], inputs["action"],
        )
    elif operation == "task_center_events":
        result = center.list_events(
            task_id=inputs.get("task_id", ""),
            after_event_id=int(inputs.get("after_event_id", 0)),
            limit=int(inputs.get("limit", 100)),
            tail=bool(inputs.get("tail", False)),
        )
    return {
        "success": True,
        "task_center": TASK_CENTER_NAME,
        "version": TASK_CENTER_VERSION,
        "operation": operation,
        "task": _public_result(result) if public else result,
    }


def _public_result(value: Any) -> Any:
    """Remove legacy adapter state from all public task-center responses."""
    if isinstance(value, dict):
        return {
            key: _public_result(item)
            for key, item in value.items()
            if key not in PLATFORM_ADAPTER_FIELDS
        }
    if isinstance(value, list):
        return [_public_result(item) for item in value]
    return value
