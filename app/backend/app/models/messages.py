"""WebSocket message schemas for pipeline progress streaming."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# Stages that involve long-running work worth streaming. The interactive
# decision steps (dedupe threshold preview, cluster k preview) are plain
# synchronous REST calls — there's no progress to report on a sub-second
# recompute, and making them WS-driven would add ceremony for nothing.
StageName = Literal[
    "strip",
    "normalize",
    "cluster",
    "name",
    "profile_gen",
    "je_vote",
    "review",
]

JobStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


class ProgressMessage(BaseModel):
    type: Literal["progress"] = "progress"
    stage: StageName
    current: int
    total: int
    percent: int
    message: str


class StageStartMessage(BaseModel):
    type: Literal["stage_start"] = "stage_start"
    stage: StageName
    total: int
    message: str


class StageCompleteMessage(BaseModel):
    type: Literal["stage_complete"] = "stage_complete"
    stage: StageName
    summary: dict[str, Any] = {}


class HeartbeatMessage(BaseModel):
    """Emitted every few seconds during blocking work so the client can tell the
    difference between 'still working' and 'connection dead'."""

    type: Literal["heartbeat"] = "heartbeat"
    stage: StageName | None = None
    elapsed_seconds: float = 0.0


class JobCompleteMessage(BaseModel):
    type: Literal["complete"] = "complete"
    job_id: str
    summary: dict[str, Any] = {}


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    stage: StageName | None = None
    message: str
    recoverable: bool = False
