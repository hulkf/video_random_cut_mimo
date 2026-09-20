"""Platform-neutral video task center.

The public interface in this package is shared by Hermes, Feishu, Web and
other local callers.  Presentation and transport adapters belong to callers.
"""

from .interface import (
    PUBLIC_CAPABILITIES,
    TASK_CENTER_NAME,
    TASK_CENTER_VERSION,
    dispatch,
)
from .contracts import TASK_CENTER_OPERATION_SPECS

__all__ = [
    "PUBLIC_CAPABILITIES",
    "TASK_CENTER_NAME",
    "TASK_CENTER_VERSION",
    "dispatch",
    "TASK_CENTER_OPERATION_SPECS",
]
