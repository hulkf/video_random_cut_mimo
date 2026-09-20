#!/usr/bin/env python3
"""JSON CLI for the platform-neutral video task center."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from typing import Any

import video_tool
from video_task_center import (
    PUBLIC_CAPABILITIES,
    TASK_CENTER_NAME,
    TASK_CENTER_VERSION,
    dispatch,
)
from video_task_center.hermes_compat import dispatch as dispatch_hermes


for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")


def _emit(payload: dict[str, Any], code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def _load_request(path: str) -> dict[str, Any]:
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as stream:
        return json.load(stream)


def run_request(request: dict[str, Any]) -> dict[str, Any]:
    return dispatch(
        request,
        operation_catalog=video_tool.CAPABILITIES["operations"],
        validate_request=video_tool._validate_request,
        authorization_required=video_tool._authorization_required,
    )


def run_hermes_request(request: dict[str, Any]) -> dict[str, Any]:
    """Compatibility entry for the installed Hermes delivery adapter only."""
    return dispatch_hermes(
        request,
        operation_catalog=video_tool.CAPABILITIES["operations"],
        validate_request=video_tool._validate_request,
        authorization_required=video_tool._authorization_required,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog=TASK_CENTER_NAME)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("capabilities")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--request", required=True, help="JSON path, or - for stdin")
    hermes_parser = subparsers.add_parser("hermes-run")
    hermes_parser.add_argument("--request", required=True, help="JSON path, or - for stdin")
    args = parser.parse_args(argv)
    try:
        if args.command == "capabilities":
            return _emit({"success": True, **PUBLIC_CAPABILITIES})
        with contextlib.redirect_stdout(sys.stderr):
            request = _load_request(args.request)
            result = run_hermes_request(request) if args.command == "hermes-run" else run_request(request)
        return _emit(result)
    except Exception as exc:
        return _emit({
            "success": False,
            "task_center": TASK_CENTER_NAME,
            "version": TASK_CENTER_VERSION,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }, 2)


if __name__ == "__main__":
    raise SystemExit(main())
