"""Screen perception queries and explicit sampling lifecycle controls."""

from __future__ import annotations

from dataclasses import asdict
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..perception.query import PerceptionQueryService
from .perception_control import PerceptionControl


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
    control: PerceptionControl | None = None,
    is_auto: Callable[[], bool] = lambda: False,
    mode_lock: asyncio.Lock | None = None,
) -> None:
    """Mount queries and explicit start/stop; mounting never starts capture."""

    def service() -> PerceptionQueryService:
        if is_auto():
            raise HTTPException(status_code=409, detail="perception_disabled_in_auto")
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
        if control is not None:
            return {"status": "ok", "runtime": control.status(), "allowed": not is_auto()}
        return {"status": "ok", "runtime": asdict(service().runtime.status())}

    @asynccontextmanager
    async def mode_guard():
        if mode_lock is None:
            yield
        else:
            async with mode_lock:
                yield

    async def start(request: PerceptionStartRequest | None = None) -> dict[str, Any]:
        if request is None or not request.consent:
            raise HTTPException(status_code=403, detail="perception_consent_required")
        if control is not None:
            async with mode_guard():
                if is_auto():
                    raise HTTPException(status_code=409, detail="perception_disabled_in_auto")
                try:
                    result = await asyncio.to_thread(control.start)
                except Exception as exc:
                    raise HTTPException(status_code=503, detail="perception_start_failed") from exc
                return {"status": "ok", "runtime": result, "allowed": True}
        runtime = service().runtime
        runtime.authorize()
        runtime.start()
        return {"status": "ok", "runtime": asdict(runtime.status())}

    async def pause() -> dict[str, Any]:
        if control is not None:
            return {"status": "ok", "runtime": control.stop(), "allowed": not is_auto()}
        runtime = service().runtime
        runtime.pause()
        return {"status": "ok", "runtime": asdict(runtime.status())}

    async def resume() -> dict[str, Any]:
        if control is not None:
            if not control.status()["authorized"]:
                raise HTTPException(status_code=403, detail="perception_consent_required")
            return await start(PerceptionStartRequest(consent=True))
        runtime = service().runtime
        runtime.resume()
        return {"status": "ok", "runtime": asdict(runtime.status())}

    async def stop() -> dict[str, Any]:
        if control is not None:
            return {"status": "ok", "runtime": control.stop(), "allowed": not is_auto()}
        runtime = service().runtime
        runtime.stop()
        return {"status": "ok", "runtime": asdict(runtime.status())}

    async def clear() -> dict[str, Any]:
        runtime = service().runtime
        await asyncio.to_thread(runtime.clear)
        return {"status": "ok", "runtime": control.status() if control else asdict(runtime.status())}

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
