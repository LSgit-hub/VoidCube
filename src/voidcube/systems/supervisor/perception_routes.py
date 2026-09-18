"""Optional read-only perception routes for Supervisor."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..perception.query import PerceptionQueryService


class PerceptionContextRequest(BaseModel):
    level: str = "L0"
    user_query: str = ""
    start_at: datetime | None = None
    end_at: datetime | None = None
    limit: int = Field(default=24, ge=1, le=100)


class PerceptionStartRequest(BaseModel):
    consent: bool = False


def mount_perception_routes(
    app: FastAPI,
    *,
    get_service: Callable[[], PerceptionQueryService | None],
) -> None:
    """Mount only reads; this function never starts capture or invokes models."""

    def service() -> PerceptionQueryService:
        value = get_service()
        if value is None:
            raise HTTPException(status_code=503, detail="perception_unavailable")
        return value

    async def current_scene() -> dict[str, Any]:
        return {"status": "ok", "scene": service().current_scene()}

    async def timeline(start_at: datetime, end_at: datetime, limit: int = 100) -> dict[str, Any]:
        return {
            "status": "ok",
            "timeline": service().timeline(start_at=start_at, end_at=end_at, limit=max(1, min(100, limit))),
        }

    async def context(request: PerceptionContextRequest) -> dict[str, Any]:
        try:
            result = service().context(
                level=request.level,
                user_query=request.user_query,
                start_at=request.start_at,
                end_at=request.end_at,
                limit=request.limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "ok", "context": result}

    async def status() -> dict[str, Any]:
        return {"status": "ok", "runtime": asdict(service().runtime.status())}

    async def start(request: PerceptionStartRequest | None = None) -> dict[str, Any]:
        runtime = service().runtime
        if request is None or not request.consent:
            raise HTTPException(status_code=403, detail="perception_consent_required")
        runtime.authorize()
        runtime.start()
        return {"status": "ok", "runtime": asdict(runtime.status())}

    async def pause() -> dict[str, Any]:
        runtime = service().runtime
        runtime.pause()
        return {"status": "ok", "runtime": asdict(runtime.status())}

    async def resume() -> dict[str, Any]:
        runtime = service().runtime
        runtime.resume()
        return {"status": "ok", "runtime": asdict(runtime.status())}

    async def stop() -> dict[str, Any]:
        runtime = service().runtime
        runtime.stop()
        return {"status": "ok", "runtime": asdict(runtime.status())}

    async def clear() -> dict[str, Any]:
        runtime = service().runtime
        runtime.clear()
        return {"status": "ok", "runtime": asdict(runtime.status())}

    app.add_api_route("/runtime/perception/scene", current_scene, methods=["GET"])
    app.add_api_route("/runtime/perception/timeline", timeline, methods=["GET"])
    app.add_api_route("/runtime/perception/context", context, methods=["POST"])
    app.add_api_route("/runtime/perception/status", status, methods=["GET"])
    app.add_api_route("/runtime/perception/start", start, methods=["POST"])
    app.add_api_route("/runtime/perception/pause", pause, methods=["POST"])
    app.add_api_route("/runtime/perception/resume", resume, methods=["POST"])
    app.add_api_route("/runtime/perception/stop", stop, methods=["POST"])
    app.add_api_route("/runtime/perception/clear", clear, methods=["POST"])


__all__ = ["PerceptionContextRequest", "mount_perception_routes"]
