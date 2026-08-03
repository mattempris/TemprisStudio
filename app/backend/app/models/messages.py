"""WebSocket message schemas for pipeline progress streaming."""
from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel

# Stages that involve long-running work worth streaming. The interactive
# decision steps (dedupe threshold preview, cluster k preview) are plain
# synchronous REST calls — there's no progress to report on a sub-second
# recompute, and making them WS-driven would add ceremony for nothing.
#
# These MUST match the frontend's wizard step ids, because the client uses the
# stage on a streamed message to decide which step's progress bar to render, and
# to re-attach a running job to its step after a reload.
#
# Keep STAGE_NAMES in step with the Literal: routes validate their label against
# it at job creation (see orchestrator.JobRegistry.create). Previously the
# Literal was the only check and it happened deep inside message construction,
# so an unlisted label killed the job mid-run *and* killed the error report that
# should have explained why — the UI just saw the progress bar vanish.
StageName = Literal[
    "strip",
    "dedupe",
    "normalize",
    "cluster",
    "categories",
    "families",
    "profiles",
    "evaluation",
    "skills",
    "tasks",
    "matching",
    "workforce",
]

STAGE_NAMES: frozenset[str] = frozenset(get_args(StageName))

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
