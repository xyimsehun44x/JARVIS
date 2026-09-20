from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict


class JarvisState(TypedDict, total=False):
    messages: Annotated[list[dict[str, str]], operator.add]
    latest_user_text: str
    task_id: str
    task_status: Literal[
        "idle",
        "working",
        "clarifying",
        "awaiting_approval",
        "executing",
        "completed",
        "failed",
    ]
    active_domain: str | None
    last_domain: str | None
    task_summary: str | None
    delegated_task: dict[str, Any] | None
    agent_result: dict[str, Any] | None
    proposed_action: dict[str, Any] | None
    approval_status: Literal["pending", "approved", "rejected"] | None
    user_feedback: str | None
    execution_id: str | None
    execution_result: dict[str, Any] | None
    response_text: str | None
    recent_events: list[dict[str, Any]]
    weather_context: dict[str, Any] | None
