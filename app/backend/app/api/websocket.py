"""WebSocket endpoint for pipeline progress.

Connection lifecycle follows the pattern proven in
`jobMatching/backend/app/api/websocket.py`: accept, attach to the job (whether it
was just created or is already mid-run), forward every message as JSON, close on
disconnect. History is replayed on attach so a reconnect is lossless.
"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.orchestrator import get_registry, stream_job

router = APIRouter()


@router.websocket("/ws/pipeline/{job_id}")
async def pipeline_progress(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()
    registry = get_registry()
    job = registry.get(job_id)

    if job is None:
        await websocket.send_json({"type": "error", "message": f"unknown job {job_id}", "recoverable": False})
        await websocket.close()
        return

    try:
        async for payload in stream_job(job):
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        # The client went away. The job deliberately keeps running — its results
        # are persisted to blob storage, so the user can reconnect or just reload
        # the project rather than losing paid LLM work.
        return
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass  # already closed by the disconnect
