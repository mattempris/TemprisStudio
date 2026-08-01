"""Pipeline job orchestration + progress streaming.

Adapted from the pattern proven in `jobMatching/backend/app/services/orchestrator.py`:
blocking work (GPU embedding, scipy clustering, thread-pool LLM fan-out) runs in
an executor while the coroutine yields heartbeats, so a long stage never looks
like a dead socket.

Divergences from that reference, both deliberate:
  - Jobs are keyed by (client, project) and registered in a process-level
    registry, so a client that reconnects mid-run attaches to the existing job
    rather than starting a second one.
  - Progress is pushed onto an asyncio.Queue from worker threads via
    `loop.call_soon_threadsafe`, which lets the synchronous `llm.pmap` fan-out
    (unchanged from the Insurance Demo original) report progress into an async
    consumer without either side knowing about the other.
"""
from __future__ import annotations

import asyncio
import time
import traceback
import uuid
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.models.messages import (
    ErrorMessage,
    HeartbeatMessage,
    JobCompleteMessage,
    ProgressMessage,
    StageCompleteMessage,
    StageStartMessage,
)

HEARTBEAT_SECONDS = 5.0


@dataclass
class PipelineJob:
    job_id: str
    client_slug: str
    project_slug: str
    stage: str
    status: str = "pending"
    # Messages emitted so far. Kept so a client that connects late (or
    # reconnects) can be replayed the history rather than silently missing it —
    # SSE/WS have no replay of their own.
    history: list[dict] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    _queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    _consumer_attached: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.client_slug, self.project_slug)

    def is_terminal(self) -> bool:
        return self.status in {"completed", "failed", "cancelled"}


class JobRegistry:
    """Process-level job registry.

    Single-process only, matching the reference implementation. A multi-instance
    deployment would need this moved to Redis or similar — flagged rather than
    pretended away, since the blob-backed project state is the durable record and
    this registry only tracks in-flight runs.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, PipelineJob] = {}
        self._active_by_project: dict[tuple[str, str], str] = {}

    def create(self, client_slug: str, project_slug: str, stage: str) -> PipelineJob:
        existing_id = self._active_by_project.get((client_slug, project_slug))
        if existing_id:
            existing = self._by_id.get(existing_id)
            if existing and not existing.is_terminal():
                raise JobAlreadyRunning(
                    f"a '{existing.stage}' job is already running for this project", existing
                )

        job = PipelineJob(
            job_id=f"job-{uuid.uuid4().hex[:12]}",
            client_slug=client_slug,
            project_slug=project_slug,
            stage=stage,
        )
        self._by_id[job.job_id] = job
        self._active_by_project[job.key] = job.job_id
        return job

    def get(self, job_id: str) -> PipelineJob | None:
        return self._by_id.get(job_id)

    def active_for_project(self, client_slug: str, project_slug: str) -> PipelineJob | None:
        job_id = self._active_by_project.get((client_slug, project_slug))
        if job_id is None:
            return None
        job = self._by_id.get(job_id)
        return job if job and not job.is_terminal() else None

    def release(self, job: PipelineJob) -> None:
        if self._active_by_project.get(job.key) == job.job_id:
            del self._active_by_project[job.key]


class JobAlreadyRunning(RuntimeError):
    def __init__(self, message: str, job: PipelineJob):
        super().__init__(message)
        self.job = job


_registry = JobRegistry()


def get_registry() -> JobRegistry:
    return _registry


class ProgressReporter:
    """Handed to synchronous worker code so it can report progress without
    knowing anything about asyncio or WebSockets.

    `llm.pmap`'s progress callback signature is (done, total, label), so
    `pmap_callback` adapts directly to it.
    """

    def __init__(self, job: PipelineJob, loop: asyncio.AbstractEventLoop, stage: str):
        self._job = job
        self._loop = loop
        self._stage = stage

    def _emit(self, payload: dict) -> None:
        self._job.history.append(payload)
        # called from worker threads — hop back onto the loop thread to touch the queue
        self._loop.call_soon_threadsafe(self._job._queue.put_nowait, payload)

    def stage_start(self, total: int, message: str) -> None:
        self._emit(StageStartMessage(stage=self._stage, total=total, message=message).model_dump())

    def progress(self, current: int, total: int, message: str = "") -> None:
        percent = int(100 * current / total) if total else 0
        self._emit(
            ProgressMessage(
                stage=self._stage, current=current, total=total, percent=percent, message=message
            ).model_dump()
        )

    def message(self, text: str) -> None:
        """A status line with no countable progress (e.g. "building index...").

        Emitted with total=0 so the UI shows the text without implying the bar
        has jumped back to zero.
        """
        self._emit(
            ProgressMessage(
                stage=self._stage, current=0, total=0, percent=0, message=text
            ).model_dump()
        )

    def stage_complete(self, summary: dict | None = None) -> None:
        self._emit(StageCompleteMessage(stage=self._stage, summary=summary or {}).model_dump())

    def pmap_callback(self) -> Callable[[int, int, str], None]:
        def _cb(done: int, total: int, label: str) -> None:
            self.progress(done, total, label)

        return _cb


async def run_job(
    job: PipelineJob,
    work: Callable[[ProgressReporter], dict],
    *,
    executor: ThreadPoolExecutor | None = None,
) -> None:
    """Run `work` in a thread, marking the job terminal on completion/failure.

    `work` is ordinary synchronous code (it may use llm.pmap, GPU inference,
    scipy) and receives a ProgressReporter to emit progress through.
    """
    loop = asyncio.get_running_loop()
    reporter = ProgressReporter(job, loop, job.stage)
    job.status = "running"
    own_executor = executor is None
    pool = executor or ThreadPoolExecutor(max_workers=1)

    try:
        summary = await loop.run_in_executor(pool, work, reporter)
        job.summary = summary or {}
        job.status = "completed"
        payload = JobCompleteMessage(job_id=job.job_id, summary=job.summary).model_dump()
    except asyncio.CancelledError:
        job.status = "cancelled"
        payload = ErrorMessage(stage=job.stage, message="job cancelled", recoverable=False).model_dump()
        job.history.append(payload)
        job._queue.put_nowait(payload)
        raise
    except Exception as e:  # noqa: BLE001 — surfaced to the client, not swallowed
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"
        traceback.print_exc()
        payload = ErrorMessage(stage=job.stage, message=job.error, recoverable=False).model_dump()
    finally:
        job.finished_at = time.time()
        _registry.release(job)
        if own_executor:
            pool.shutdown(wait=False)

    job.history.append(payload)
    job._queue.put_nowait(payload)


async def stream_job(job: PipelineJob, *, replay_history: bool = True) -> AsyncIterator[dict]:
    """Yield the job's messages, oldest first, then live ones until terminal.

    Replaying history first is what makes reconnect lossless: WS has no replay,
    so a client that drops mid-run would otherwise silently miss everything
    emitted during the gap.

    `job.history` is the single source of truth for both content and ordering;
    the queue is only a wake-up signal so we don't have to poll. Treating the
    queue as a second content source duplicates every message (each one is
    appended to history *and* pushed to the queue), which is exactly what an
    earlier version of this did.
    """
    sent = 0 if replay_history else len(job.history)
    last_beat = time.time()

    while True:
        while sent < len(job.history):
            yield job.history[sent]
            sent += 1

        if job.is_terminal():
            return

        try:
            await asyncio.wait_for(job._queue.get(), timeout=HEARTBEAT_SECONDS)
            # payload intentionally discarded — the next loop reads it from history
        except asyncio.TimeoutError:
            if job.is_terminal():
                continue  # let the drain-then-return path above handle it
            now = time.time()
            if now - last_beat >= HEARTBEAT_SECONDS:
                last_beat = now
                yield HeartbeatMessage(
                    stage=job.stage, elapsed_seconds=round(now - job.started_at, 1)
                ).model_dump()
