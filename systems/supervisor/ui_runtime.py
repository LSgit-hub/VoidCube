from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import webbrowser
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from VoidCube_core.utils import atomic_json_write
from systems.supervisor.observation_status import normalize_autonomous_status
from systems.supervisor.ui_assets import load_supervisor_ui_html
from systems.supervisor.ui_autonomous_projection import project_autonomous_observation
from systems.supervisor.ui_body_projection import project_body_slot_cards
from systems.supervisor.ui_cognition_projection import (
    project_cognition_judgement,
    project_cognition_uncertainty,
)
from systems.supervisor.ui_projection import (
    default_observation_input_snapshot,
    format_supervisor_ui_event,
    observation_count,
    project_observation_board,
    project_recent_autonomous_activity,
    runtime_activity_label,
)
from systems.supervisor.ui_trace_projection import (
    attach_observation_trace_details,
    project_trace_detail,
    recent_observation_trace_ids,
)
from systems.supervisor.ui_state_projection import (
    project_supervisor_scene,
    project_ui_metrics,
)

logger = logging.getLogger("supervisor")


class SupervisorUIMixin:
    """内置监督者小屋 UI 与 API-B 主视角状态映射。"""

    def _initialize_supervisor_ui_runtime(self) -> None:
        runtime_root = Path(
            getattr(self, "_runtime_root", None)
            or self.config.soul_store_path
        ).resolve()
        runtime_root.mkdir(parents=True, exist_ok=True)
        self._supervisor_ui_activity_path = runtime_root / "supervisor-ui-activity.json"
        self._supervisor_ui_events: Deque[Dict[str, Any]] = deque(
            self._load_supervisor_ui_activity(),
            maxlen=self.config.ui_activity_buffer_size,
        )
        self._supervisor_ui_observation_input_cache: Dict[str, Any] = {}
        self._supervisor_ui_memory_stats_cache: Dict[str, Any] = {}
        # 媒体播放是即时指令；revision 保证相同 URL 的重复播放也会重新推送。
        self._current_media: Optional[Dict[str, Any]] = None
        self._media_revision = 0

    def enqueue_media(self, media: Dict[str, Any]) -> None:
        """将媒体项加入播放队列，Web UI 自动弹出播放器。

        ``media`` 字段：
        - url (str, 必填): 媒体 URL（YouTube/B站/直链）
        - title (str): 标题，显示在播放器上
        - type (str): "youtube" | "bilibili" | "audio" | "video" | "auto"
        - auto_play (bool): 是否自动播放，默认 True
        """
        current = dict(media)
        current.setdefault("auto_play", True)
        current.setdefault("type", "auto")
        current.setdefault("title", current.get("url", "未知"))
        current["_enqueued_at"] = datetime.now(timezone.utc).isoformat()
        self._media_revision += 1
        current["_revision"] = self._media_revision
        self._current_media = current
        logger.info("Media enqueued: %s (%s)", current.get("title"), current.get("type"))

    def _record_supervisor_ui_activity(
        self,
        event_type: str,
        *,
        scene: str = "planning",
        summary: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # 按基线 §3.4/§3.6，Supervisor（API-B）只负责治理判断、
        # API-B 只观察判断在途与内生驱动，不直接执行学习或替身改进代码。
        # 因此凡是暗示执行面的 scene（如 `learning`、`execution`）
        # 都要被挡回 `planning`；那是 API-A 的职责域。
        from systems.supervisor.planning_runtime import SUPERVISOR_LEGAL_SCENES

        if scene not in SUPERVISOR_LEGAL_SCENES:
            logger.warning(
                "Refusing illegal supervisor scene=%r for event_type=%r; "
                "falling back to 'planning'. Legal supervisor scenes: %s",
                scene, event_type, sorted(SUPERVISOR_LEGAL_SCENES),
            )
            scene = "planning"

        event = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "scene": scene,
            "summary": summary or event_type.replace("_", " "),
            "metadata": dict(metadata or {}),
            "recorded_at": datetime.utcnow().isoformat(),
        }
        self._supervisor_ui_events.appendleft(event)
        self._persist_supervisor_ui_activity()
        self._record_supervisor_ui_activity_history(event)
        return event

    def _recent_supervisor_ui_activity(self, limit: int = 20) -> List[Dict[str, Any]]:
        events = getattr(self, "_supervisor_ui_events", None)
        if events is None:
            return []
        return list(events)[: max(limit, 0)]

    def _latest_drive_candidate_snapshot(self) -> List[Dict[str, Any]]:
        for event in self._recent_supervisor_ui_activity(limit=20):
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or "").strip().lower()
            metadata = dict(event.get("metadata") or {})
            if event_type == "endogenous_drive_idle":
                return []
            if event_type == "endogenous_drive_evaluated":
                candidates = metadata.get("candidates")
                if isinstance(candidates, list):
                    return [dict(item) for item in candidates if isinstance(item, dict)]
            if event_type == "endogenous_drive_planned":
                tasks = metadata.get("tasks")
                if isinstance(tasks, list):
                    return [dict(item) for item in tasks if isinstance(item, dict)]
        return []

    def _load_supervisor_ui_activity(self) -> List[Dict[str, Any]]:
        path = getattr(self, "_supervisor_ui_activity_path", None)
        if path is None or not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except Exception:
            return []
        events = raw.get("events") if isinstance(raw, dict) else None
        if not isinstance(events, list):
            return []
        normalized = [
            dict(event)
            for event in events
            if isinstance(event, dict)
        ]
        return normalized[: max(int(self.config.ui_activity_buffer_size), 0)]

    def _persist_supervisor_ui_activity(self) -> None:
        path = getattr(self, "_supervisor_ui_activity_path", None)
        events = getattr(self, "_supervisor_ui_events", None)
        if path is None or events is None:
            return
        payload = {
            "version": 1,
            "updated_at": datetime.utcnow().isoformat(),
            "events": list(events),
        }
        try:
            atomic_json_write(path, payload)
        except Exception:
            return

    def _clear_supervisor_ui_activity(self) -> None:
        events = getattr(self, "_supervisor_ui_events", None)
        if events is not None:
            events.clear()
        path = getattr(self, "_supervisor_ui_activity_path", None)
        if path is not None:
            payload = {
                "version": 1,
                "updated_at": datetime.utcnow().isoformat(),
                "events": [],
            }
            try:
                atomic_json_write(path, payload)
            except Exception:
                return

    def _record_supervisor_ui_activity_history(self, event: Dict[str, Any]) -> None:
        governor = getattr(self, "_governor", None)
        if governor is None or not hasattr(governor, "record_supervisor_activity"):
            return
        try:
            governor.record_supervisor_activity(event=event)
        except Exception:
            return

    async def get_supervisor_ui(self) -> HTMLResponse:
        return HTMLResponse(load_supervisor_ui_html())

    async def get_supervisor_identity_archive(self) -> Dict[str, Any]:
        """Proxy the canonical Mem archive without creating UI-owned identity state."""
        try:
            import aiohttp

            gateway_url = str(self.config.execution.gateway_address).rstrip("/")
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                memory_url = await self._resolve_ui_memory_service_url(
                    session, gateway_url
                )
                async with session.get(f"{memory_url}/identity/archive") as response:
                    if response.status != 200:
                        raise HTTPException(
                            status_code=503, detail="Memory identity archive unavailable"
                        )
                    return await response.json()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail=f"Memory identity archive unavailable: {type(exc).__name__}"
            ) from exc

    async def get_supervisor_identity_turns(self, limit: int = 20) -> Dict[str, Any]:
        """Return recent Tier 1 turns for explicit identity verification in the room UI."""
        try:
            import aiohttp

            bounded_limit = max(1, min(int(limit), 50))
            gateway_url = str(self.config.execution.gateway_address).rstrip("/")
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                memory_url = await self._resolve_ui_memory_service_url(
                    session, gateway_url
                )
                async with session.get(
                    f"{memory_url}/turns",
                    params={"limit": bounded_limit, "newest_first": "true"},
                ) as response:
                    if response.status != 200:
                        raise HTTPException(
                            status_code=503, detail="Memory turns unavailable"
                        )
                    payload = await response.json()
                    turns = list(payload.get("turns") or [])
                    return {"turns": turns, "count": len(turns)}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail=f"Memory turns unavailable: {type(exc).__name__}"
            ) from exc

    async def get_supervisor_evolution_promotion_audit(
        self,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Expose read-only evolution-to-companion promotion metadata to the owner UI."""
        bounded_limit = max(1, min(int(limit), 500))
        try:
            import aiohttp

            gateway_url = str(self.config.execution.gateway_address).rstrip("/")
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{gateway_url}/api/mem/promotions",
                    params={
                        "limit": 500,
                        "target_domain": "companion",
                    },
                    headers=self._gateway_memory_headers(
                        memory_actor="stellar_companion"
                    ),
                ) as response:
                    if response.status != 200:
                        raise HTTPException(
                            status_code=503,
                            detail="Memory promotion audit unavailable",
                        )
                    payload = await response.json()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Memory promotion audit unavailable: {type(exc).__name__}",
            ) from exc

        raw_promotions = payload.get("promotions") if isinstance(payload, dict) else None
        if not isinstance(raw_promotions, list):
            raise HTTPException(
                status_code=503,
                detail="Memory promotion audit returned an invalid payload",
            )

        allowed_fields = (
            "promotion_id",
            "source_type",
            "source_memory_id",
            "source_domain",
            "target_domain",
            "reason",
            "approved_by",
            "approval_ref",
            "created_by",
            "status",
            "created_at",
            "expires_at",
            "revoked_at",
            "revoked_by",
            "revoke_reason",
        )
        promotions = [
            {field: item.get(field) for field in allowed_fields}
            for item in raw_promotions
            if isinstance(item, dict)
            and str(item.get("source_domain") or "") == "evolution"
            and str(item.get("target_domain") or "") == "companion"
        ][:bounded_limit]
        status_counts = {"active": 0, "revoked": 0, "expired": 0}
        for item in promotions:
            status = str(item.get("status") or "").strip().lower()
            if status in status_counts:
                status_counts[status] += 1

        return {
            "direction": {
                "source_domain": "evolution",
                "target_domain": "companion",
            },
            "promotions": promotions,
            "count": len(promotions),
            "status_counts": status_counts,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def get_supervisor_evolution_promotion_candidates(
        self,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Return only pending evolution-to-companion consent metadata."""
        bounded_limit = max(1, min(int(limit), 100))
        try:
            import aiohttp

            gateway_url = str(self.config.execution.gateway_address).rstrip("/")
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{gateway_url}/api/mem/promotion-candidates",
                    params={
                        "limit": bounded_limit,
                        "status": "awaiting_user_consent",
                        "source_domain": "evolution",
                        "target_domain": "companion",
                    },
                    headers=self._gateway_memory_headers(memory_actor="governor"),
                ) as response:
                    payload = await response.json()
                    if response.status != 200:
                        detail = payload.get("detail") if isinstance(payload, dict) else None
                        raise HTTPException(
                            status_code=response.status,
                            detail=detail or "Memory promotion candidates unavailable",
                        )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Memory promotion candidates unavailable: {type(exc).__name__}",
            ) from exc

        raw_candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if not isinstance(raw_candidates, list):
            raise HTTPException(
                status_code=503,
                detail="Memory promotion candidates returned an invalid payload",
            )
        allowed_fields = (
            "candidate_id",
            "source_type",
            "source_memory_id",
            "source_domain",
            "target_domain",
            "reason",
            "proposed_by",
            "governance_ref",
            "status",
            "requested_at",
            "expires_at",
        )
        candidates = [
            {field: item.get(field) for field in allowed_fields}
            for item in raw_candidates
            if isinstance(item, dict)
            and str(item.get("source_domain") or "") == "evolution"
            and str(item.get("target_domain") or "") == "companion"
            and str(item.get("status") or "") == "awaiting_user_consent"
        ][:bounded_limit]
        return {
            "direction": {
                "source_domain": "evolution",
                "target_domain": "companion",
            },
            "candidates": candidates,
            "count": len(candidates),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def consent_supervisor_evolution_promotion_candidate(
        self,
        candidate_id: str,
        request: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Record the local owner's immutable decision through the Gateway."""
        try:
            import aiohttp

            from systems.memory.promotion import MemoryPromotionConsent

            consent = MemoryPromotionConsent.model_validate(
                {
                    "approved": request.get("approved"),
                    "reason": request.get("reason"),
                    "consented_by": "local-owner",
                    "memory_actor": "governor",
                }
            )
            gateway_url = str(self.config.execution.gateway_address).rstrip("/")
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{gateway_url}/api/mem/promotion-candidates/{candidate_id}/consent",
                    json=consent.model_dump(mode="json"),
                    headers=self._gateway_memory_headers(memory_actor="governor"),
                ) as response:
                    payload = await response.json()
                    if response.status != 200:
                        detail = payload.get("detail") if isinstance(payload, dict) else None
                        raise HTTPException(
                            status_code=response.status,
                            detail=detail or "Memory promotion consent failed",
                        )
                    return payload
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Memory promotion consent unavailable: {type(exc).__name__}",
            ) from exc

    async def verify_supervisor_identity_experience(
        self, request: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Proxy an explicit identity-experience decision to canonical Mem."""
        try:
            import aiohttp

            from systems.memory.memory_service import IdentityExperienceVerification

            payload = IdentityExperienceVerification.model_validate(request).model_dump()
            gateway_url = str(self.config.execution.gateway_address).rstrip("/")
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                memory_url = await self._resolve_ui_memory_service_url(
                    session, gateway_url
                )
                async with session.post(
                    f"{memory_url}/identity/experiences/verify",
                    json=payload,
                ) as response:
                    response_payload = await response.json()
                    if response.status != 200:
                        detail = response_payload.get("detail") if isinstance(response_payload, dict) else None
                        raise HTTPException(
                            status_code=response.status,
                            detail=detail or "Identity experience verification failed",
                        )
                    return response_payload
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Identity experience verification unavailable: {type(exc).__name__}",
            ) from exc

    @staticmethod
    async def _resolve_ui_memory_service_url(session: Any, gateway_url: str) -> str:
        async with session.get(f"{gateway_url}/admin/services") as response:
            if response.status != 200:
                raise HTTPException(
                    status_code=503, detail="Gateway service registry unavailable"
                )
            services_payload = (await response.json()).get("services", {})
        services = (
            list(services_payload.values())
            if isinstance(services_payload, dict)
            else list(services_payload)
            if isinstance(services_payload, list)
            else []
        )
        memory_url = next(
            (
                str(service.get("address") or "").rstrip("/")
                for service in services
                if isinstance(service, dict)
                and service.get("service_type") == "memory"
                and service.get("address")
            ),
            "",
        )
        if not memory_url:
            raise HTTPException(
                status_code=503, detail="Memory Service is not registered"
            )
        return memory_url

    async def get_supervisor_ui_events(self, request: Request) -> StreamingResponse:
        async def event_stream():
            while True:
                if await request.is_disconnected():
                    break
                state = await self.get_supervisor_ui_state()
                yield format_supervisor_ui_event("state", state)
                await asyncio.sleep(self.config.ui_event_interval_seconds)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    async def get_voice_level_events(self, request: Request) -> StreamingResponse:
        async def event_stream():
            while True:
                if await request.is_disconnected():
                    break
                yield format_supervisor_ui_event(
                    "level",
                    self._voice_manager.realtime_status(),
                )
                await asyncio.sleep(0.1)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    async def get_media_events(self, request: Request) -> StreamingResponse:
        """SSE 端点：快速推送媒体播放事件到 Web UI。

        当队列中有新媒体项时，立即推送给前端播放器。
        轮询间隔 500ms，兼顾响应速度与资源消耗。
        """
        _last_revision = 0

        async def event_stream():
            nonlocal _last_revision
            while True:
                if await request.is_disconnected():
                    break
                current = self._current_media
                if current:
                    revision = int(current.get("_revision") or 0)
                    if revision != _last_revision:
                        _last_revision = revision
                        yield format_supervisor_ui_event(
                            "play",
                            {
                                "url": current.get("url", ""),
                                "title": current.get("title", ""),
                                "type": current.get("type", "auto"),
                                "auto_play": current.get("auto_play", True),
                                "enqueued_at": current.get("_enqueued_at", ""),
                                "revision": revision,
                                "queue_remaining": 0,
                            },
                        )
                await asyncio.sleep(0.5)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    async def enqueue_media_endpoint(self, request: Request) -> Dict[str, Any]:
        """HTTP 端点：Agent 通过网关调用此接口播放媒体。

        POST /ui/media/enqueue
        Body: {"url": "...", "title": "...", "type": "youtube|audio|video|auto"}
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="请求体必须是 JSON")
        url = (body.get("url") or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="缺少 url 字段")
        self.enqueue_media({
            "url": url,
            "title": (body.get("title") or "").strip() or url,
            "type": (body.get("type") or "auto").strip(),
            "auto_play": body.get("auto_play", True),
        })
        return {"status": "ok", "queued": 1, "revision": self._media_revision}

    async def _load_ui_observation_input_snapshot(
        self,
        *,
        timeout_seconds: float = 0.8,
    ) -> tuple[Dict[str, Any], bool]:
        default_snapshot = default_observation_input_snapshot()
        try:
            payload = await asyncio.wait_for(
                self.get_runtime_observation_input(),
                timeout=max(float(timeout_seconds), 0.05),
            )
        except Exception:
            cached = dict(getattr(self, "_supervisor_ui_observation_input_cache", {}) or {})
            if cached:
                cached["snapshot_source"] = "cached"
                return cached, False
            return default_snapshot, False

        normalized = dict(payload.get("observation_input") or {})
        if not normalized:
            normalized = dict(default_snapshot)
            normalized["snapshot_source"] = "default"
        normalized["activity"] = dict(normalized.get("activity") or {})
        normalized["user_chain_signal"] = dict(normalized.get("user_chain_signal") or {})
        if not normalized["user_chain_signal"]:
            normalized["user_chain_signal"] = dict(default_snapshot["user_chain_signal"])
        normalized["user_chain_signal"]["scope"] = str(
            normalized["user_chain_signal"].get("scope") or "soft_signal_only"
        ).strip() or "soft_signal_only"
        self._supervisor_ui_observation_input_cache = dict(normalized)
        return normalized, True

    async def _load_ui_memory_stats(
        self,
        *,
        timeout_seconds: float = 0.8,
    ) -> Dict[str, Any]:
        try:
            stats = await asyncio.wait_for(
                self._fetch_tier1_stats(),
                timeout=max(float(timeout_seconds), 0.05),
            )
        except Exception:
            cached = dict(getattr(self, "_supervisor_ui_memory_stats_cache", {}) or {})
            if cached:
                cached["snapshot_source"] = "cached"
                return cached
            return {
                "memory_unavailable": True,
                "memory_unavailable_reason": "ui_snapshot_unavailable",
                "memory_active": False,
                "snapshot_source": "default",
            }

        normalized = dict(stats or {})
        normalized["snapshot_source"] = "live"
        self._supervisor_ui_memory_stats_cache = dict(normalized)
        return normalized

    def _load_ui_body_status(
        self,
        chain_history_projection: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Load body-owned snapshots before applying the pure slot projection."""
        integrity = self._body_registry.inspect_layout()
        registry = dict(integrity.get("registry") or {})
        status: Dict[str, Any] = {
            "active_slot": registry.get("active_slot"),
            "retired_slot": registry.get("retired_slot"),
            "shell_slot": registry.get("shell_slot"),
            "last_switch_result": dict(registry.get("last_switch_result") or {}),
            "integrity": integrity,
            "slot_cards": [],
        }
        if not registry:
            return status

        slot_metas: Dict[str, Dict[str, Any]] = {}
        top_level_entries_by_slot: Dict[str, List[str]] = {}
        for slot_id in list(registry.get("slot_ids") or []):
            try:
                meta = self._body_registry.load_slot_meta(slot_id).model_dump(mode="json")
            except Exception:
                continue
            slot_metas[slot_id] = meta
            worktree_path = str(meta.get("worktree_path") or "").strip()
            if not worktree_path:
                continue
            try:
                top_level_entries_by_slot[slot_id] = sorted(
                    child.name for child in Path(worktree_path).iterdir()
                )[:24]
            except Exception:
                continue
        status["slot_cards"] = project_body_slot_cards(
            registry=registry,
            slot_metas=slot_metas,
            chain_history_projection=chain_history_projection,
            integrity_report=integrity,
            top_level_entries_by_slot=top_level_entries_by_slot,
        )
        return status

    async def _load_ui_observation_timeline(
        self,
        *,
        limit: int = 12,
    ) -> List[Dict[str, Any]]:
        try:
            return self._recent_local_supervisor_observation_timeline(limit=limit)
        except Exception:
            return self._recent_supervisor_ui_activity(limit=limit)

    def _collect_ui_trace_records(
        self,
        *,
        trace_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        records.extend(self._collect_trace_records_from_tasks(trace_id=trace_id))
        records.extend(self._collect_trace_records_from_supervisor_activity(trace_id=trace_id))
        records.extend(
            self._collect_trace_records_from_governor_history(
                trace_id=trace_id,
                limit=max(int(limit), 1),
            )
        )
        return records

    def _recent_local_supervisor_observation_timeline(
        self,
        *,
        limit: int = 12,
    ) -> List[Dict[str, Any]]:
        records = self._collect_ui_trace_records(limit=max(int(limit) * 4, 24))
        timeline = [
            dict(record)
            for record in self._build_trace_timeline(records)
            if str(record.get("trace_id") or "").strip()
        ]
        timeline.reverse()
        return timeline[: max(int(limit), 0)]

    async def get_supervisor_ui_state(self) -> Dict[str, Any]:
        chain_projection_rows = list(
            self._autonomous_chain_store.list_chain_projection_tasks()
        )
        chain_projection = [
            self._serialize_autonomous_chain_task(task)
            for task in chain_projection_rows
        ]
        chain_projection.sort(
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )

        drive_candidates: List[Dict[str, Any]] = self._latest_drive_candidate_snapshot()

        # Extract metrics from gateway activity for richer UI expression
        (
            observation_input_snapshot_with_status,
            tier1_stats,
            observation_timeline,
        ) = await asyncio.gather(
            self._load_ui_observation_input_snapshot(),
            self._load_ui_memory_stats(),
            self._load_ui_observation_timeline(limit=12),
        )
        (
            observation_input_snapshot,
            observation_input_available,
        ) = observation_input_snapshot_with_status
        activity = dict(observation_input_snapshot.get("activity") or {})
        counts = dict(activity.get("counts") or {})
        error_count = int(counts.get("error_count") or 0)

        body_status = self._load_ui_body_status(chain_projection)

        # ── LLM token usage ──
        mem_usage: Dict[str, Any] = {}
        try:
            from memai.llm_client import get_memory_token_usage
            raw = get_memory_token_usage()
            ctx = raw.get("context_length", 65536)
            total = raw.get("total_tokens", 0)
            mem_usage = {
                "total_tokens": total,
                "prompt_tokens": raw.get("prompt_tokens", 0),
                "completion_tokens": raw.get("completion_tokens", 0),
                "request_count": raw.get("request_count", 0),
                "context_length": ctx,
                "context_percent": round((total / ctx) * 100) if ctx > 0 else 0,
            }
        except Exception:
            pass

        # ── Web 小屋 API-B 主视角观测模型 ──
        autonomous_observation = project_autonomous_observation(
            chain_projection,
            drive_candidates=drive_candidates,
            history_tasks=chain_projection,
            timeline=observation_timeline,
        )
        try:
            autonomous_observation = await asyncio.wait_for(
                self._attach_recent_trace_details_to_observation(autonomous_observation),
                timeout=2.0,
            )
        except Exception:
            pass
        metrics = project_ui_metrics(
            chain_projection,
            autonomous_observation=autonomous_observation,
            body_status=body_status,
            error_count=error_count,
        )

        scene, title, summary = project_supervisor_scene(
            autonomous_observation=autonomous_observation,
            observation_input_available=observation_input_available,
            error_count=error_count,
            memory_active=tier1_stats.get("memory_active", False),
        )
        stellar_mode = self._stellar_mode_status()
        voice_status = self._voice_manager.status()
        if stellar_mode.get("mode") == "daily_companion":
            scene = "idle"
            title = "日常陪伴中"
            latest_dialogue = dict(
                stellar_mode.get("latest_companion_dialogue") or {}
            )
            latest_observation = dict(
                stellar_mode.get("latest_companion_observation") or {}
            )
            if voice_status.get("active"):
                summary = "正在通过语音与你交流。"
            elif latest_dialogue:
                summary = "最近完成了一轮日常对话，继续保持陪伴。"
            elif latest_observation.get("intent_state") == "understood":
                summary = "已理解当前任务，在确有帮助前保持安静。"
            else:
                summary = "正在安静陪伴并观察 VoidCube 内部事件。"

        # ── LM Input info (for 🧠 panel) ──
        lm_input: Dict[str, Any] = {
            "generation_enabled": bool(
                getattr(
                    getattr(self.config, "service_runtime", None),
                    "endogenous_drive_lm_task_generation_enabled",
                    False,
                )
            ),
        }

        def _loaded_cognition_state() -> Dict[str, Any]:
            raw_snapshot = self._load_endogenous_cognition_state()
            if isinstance(raw_snapshot.get("state"), dict):
                return dict(raw_snapshot.get("state") or {})
            return dict(raw_snapshot or {})

        # Extract recent LM call metadata from drive history / cognition state
        try:
            cog_snapshot = _loaded_cognition_state()
            proposal_cog = cog_snapshot.get("proposal_cognition") or {}
            lm_trace = dict(proposal_cog.get("lm_trace") or {})
            if lm_trace.get("status"):
                lm_input["status"] = lm_trace["status"]
            if lm_trace.get("model_role"):
                lm_input["model_role"] = lm_trace["model_role"]
            if lm_trace.get("proposal_count") is not None:
                lm_input["proposal_count"] = lm_trace["proposal_count"]
            # Recent evidence nodes from uncertainty ledger
            ledger = cog_snapshot.get("uncertainty_ledger") or {}
            recent_nodes = ledger.get("recent_nodes") or []
            if recent_nodes:
                lm_input["recent_evidence_nodes"] = [
                    {"node": n.get("node_id", ""), "title": n.get("title", ""), "summary": n.get("summary", "")}
                    for n in recent_nodes[:20]
                ]
        except Exception:
            pass

        # ── Cognition state (for 📊 panel) ──
        cognition: Dict[str, Any] = {}
        try:
            cog_snapshot = _loaded_cognition_state()
            perception = cog_snapshot.get("perception") or {}
            world_model = cog_snapshot.get("world_model") or {}
            # Build perception summary
            cognition["perception"] = {
                "system_posture": perception.get("system_posture", "balanced"),
                "user_mode": perception.get("user_mode", "未识别"),
                "api_b_judgement_count": perception.get("api_b_judgement_count", 0),
                "api_a_handoff_count": perception.get("api_a_handoff_count", 0),
                "api_a_running_count": perception.get("api_a_running_count", 0),
                "active_sessions": perception.get("active_sessions", 0),
                "recent_errors": perception.get("recent_errors", 0),
                "learning_quality": perception.get("learning_quality", 0),
                "correction_signals": perception.get("correction_signals", 0),
                "idle_seconds": perception.get("idle_seconds", {}),
            }
            # Build world model summary
            cognition["world_model"] = {
                "governance_load_state": world_model.get("governance_load_state", "未识别"),
                "memory_pressure": world_model.get("memory_pressure", 0),
                "truthfulness_pressure": world_model.get("truthfulness_pressure", 0),
                "learning_momentum": world_model.get("learning_momentum", 0),
                "body_upgrade_readiness": world_model.get("body_upgrade_readiness", 0),
                "self_confidence": world_model.get("self_confidence", 0),
            }
            # Needs
            raw_needs = cog_snapshot.get("needs") or []
            cognition["needs"] = [
                {
                    "need_type": n.get("need_type", "未分类需求"),
                    "severity": n.get("severity", 0),
                    "urgency": n.get("urgency", 0),
                    "confidence": n.get("confidence", 0),
                    "rationale": str(n.get("rationale", ""))[:200],
                }
                for n in raw_needs[:8]
            ]
            # Intents
            raw_intents = cog_snapshot.get("intents") or []
            cognition["intents"] = [
                {
                    "intent_type": i.get("intent_type", "未命名意图"),
                    "priority": i.get("priority", 0),
                    "output_channel": i.get("output_channel", "task_candidates"),
                    "target_horizon": i.get("target_horizon", "当前轮"),
                    "rationale": str(i.get("rationale", ""))[:150],
                }
                for i in raw_intents[:6]
            ]
            # Signals
            raw_signals = cog_snapshot.get("signals") or []
            cognition["signals"] = [
                {
                    "signal_type": s.get("signal_type", "未命名信号"),
                    "priority": s.get("priority", 0),
                    "message": str(s.get("message", ""))[:200],
                }
                for s in raw_signals[:5]
            ]
            # Adaptive policy
            raw_policy = cog_snapshot.get("adaptive_policy") or {}
            cognition["adaptive_policy"] = {
                "learning_expansion_bias": raw_policy.get("learning_expansion_bias", 0),
                "truthfulness_bias": raw_policy.get("truthfulness_bias", 0),
                "memory_continuity_bias": raw_policy.get("memory_continuity_bias", 0),
                "governance_hygiene_bias": raw_policy.get("governance_hygiene_bias", 0),
                "body_growth_bias": raw_policy.get("body_growth_bias", 0),
                "observation_bias": raw_policy.get("observation_bias", 0),
                "candidate_throttle": raw_policy.get("candidate_throttle", 1.0),
                "candidate_budget": raw_policy.get("candidate_budget", 3),
                "exploratory_learning_quota": raw_policy.get("exploratory_learning_quota", 0),
                "body_growth_quota": raw_policy.get("body_growth_quota", 0),
                "preferred_focus": raw_policy.get("preferred_focus", "balanced"),
            }
            cognition["judgement"] = project_cognition_judgement(cog_snapshot)
            cognition["uncertainty"] = project_cognition_uncertainty(cog_snapshot)
        except Exception:
            pass

        recent_autonomous_activity = project_recent_autonomous_activity(
            dict(observation_input_snapshot.get("activity") or {})
        )
        autonomous_runtime = dict(autonomous_observation.get("runtime") or {})
        autonomous_runtime["user_chain_signal"] = dict(
            observation_input_snapshot.get("user_chain_signal") or {}
        )
        autonomous_runtime["snapshot_source"] = str(
            observation_input_snapshot.get("snapshot_source") or "default"
        )
        autonomous_counts = dict(autonomous_observation.get("counts") or {})
        autonomous_runtime["api_a_handoff_count"] = observation_count(
            autonomous_counts.get("api_a_handoff")
        )
        autonomous_runtime["api_a_running_count"] = observation_count(
            autonomous_counts.get("api_a_running")
        )
        autonomous_observation["runtime"] = autonomous_runtime
        autonomous_board = project_observation_board(
            autonomous_observation,
            recent_activity=recent_autonomous_activity,
        )
        autonomous_observation["board"] = autonomous_board
        autonomous_observation["metrics"] = metrics

        return {
            "status": "ok",
            "stellar_mode": stellar_mode,
            "voice": voice_status,
            "scene": scene,
            "title": title,
            "summary": summary,
            "generated_at": datetime.utcnow().isoformat(),
            "autonomous_observation": autonomous_observation,
            "mem_usage": mem_usage,
            "tier1_stats": tier1_stats,
            "body_status": body_status,
            "error_count": error_count,
            "timeline": observation_timeline[:10],
            "lm_input": lm_input,
            "cognition": cognition,
            "media": {
                "current": self._current_media,
                "queue_length": 1 if self._current_media else 0,
            },
        }


    async def _load_recent_trace_details(
        self,
        trace_ids: List[str],
        *,
        limit: int = 6,
    ) -> Dict[str, Dict[str, Any]]:
        normalized: List[str] = []
        for trace_id in trace_ids:
            candidate = str(trace_id or "").strip()
            if not candidate or candidate in normalized:
                continue
            normalized.append(candidate)
            if len(normalized) >= max(int(limit), 1):
                break

        async def _load(trace_id: str) -> tuple[str, Dict[str, Any]]:
            records = self._collect_ui_trace_records(trace_id=trace_id, limit=200)
            summary = self._summarize_single_trace(trace_id, records)
            timeline = [
                dict(event)
                for event in self._build_trace_timeline(records)
            ]
            return trace_id, project_trace_detail(
                trace_id=trace_id,
                summary=summary,
                timeline=timeline,
            )

        results = await asyncio.gather(*[_load(trace_id) for trace_id in normalized])
        return {trace_id: detail for trace_id, detail in results}

    async def _attach_recent_trace_details_to_observation(
        self,
        observation: Dict[str, Any],
    ) -> Dict[str, Any]:
        trace_ids = recent_observation_trace_ids(observation)
        if not trace_ids:
            return observation

        details = await self._load_recent_trace_details(trace_ids)
        return attach_observation_trace_details(observation, details=details)

    async def _fetch_tier1_stats(self) -> Dict[str, Any]:
        """Fetch Tier 1 stats + memory_service rule execution status."""
        try:
            import aiohttp
            gateway_url = str(self.config.execution.gateway_address).rstrip("/")
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{gateway_url}/admin/services", timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    if resp.status != 200:
                        return {
                            "memory_unavailable": True,
                            "memory_unavailable_reason": f"gateway_services_status_{resp.status}",
                            "memory_active": False,
                        }
                    services_payload = (await resp.json()).get("services", {})
                memory_url = None
                if isinstance(services_payload, dict):
                    services = list(services_payload.values())
                elif isinstance(services_payload, list):
                    services = list(services_payload)
                else:
                    services = []
                for svc in services:
                    if not isinstance(svc, dict):
                        continue
                    if svc.get("service_type") == "memory":
                        memory_url = svc.get("address")
                        break
                if not memory_url:
                    return {
                        "memory_unavailable": True,
                        "memory_unavailable_reason": "memory_service_not_registered",
                        "memory_active": False,
                    }
                # Fetch both stats and rules status in parallel
                stats_data = {}
                rules_data = {}
                async with session.get(
                    f"{memory_url}/tier1/stats", timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    if resp.status == 200:
                        stats_data = await resp.json()
                async with session.get(
                    f"{memory_url}/compressed/rules-status", timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    if resp.status == 200:
                        rules_data = await resp.json()
                result = dict(stats_data)
                result["rules"] = rules_data.get("rules", {})
                result["llm_healthy"] = rules_data.get("llm_healthy", False)
                result["llm_model"] = rules_data.get("llm_model")
                result["llm_error"] = rules_data.get("llm_error")
                result["effective_activity_at"] = rules_data.get("effective_activity_at")
                result["llm_health_checked_at"] = rules_data.get("llm_health_checked_at")
                # P0-4 健康信号: memory_active reflects REAL write work in the last
                # 2 cycles (effective_activity_at), not merely "a rule ran"
                # (last_run, which advances even on no-op cycles). A degraded /
                # idle / broken pipeline no longer shows "记忆活跃 ✅".
                from datetime import datetime, timedelta, timezone
                recent = datetime.now(timezone.utc) - timedelta(seconds=7200)
                memory_active = False
                eff = rules_data.get("effective_activity_at")
                if eff:
                    try:
                        t = datetime.fromisoformat(eff)
                        if t.tzinfo is None:
                            t = t.replace(tzinfo=timezone.utc)
                        memory_active = t > recent
                    except Exception:
                        memory_active = False
                result["memory_active"] = memory_active
                return result
        except Exception as exc:
            return {
                "memory_unavailable": True,
                "memory_unavailable_reason": type(exc).__name__,
                "memory_active": False,
            }

    def _maybe_open_supervisor_ui(self) -> None:
        if not self.config.ui_enabled or not self.config.ui_auto_open:
            return
        if os.getenv("PYTEST_CURRENT_TEST"):
            return
        url = f"http://{self.config.host}:{self.config.port}{self.config.ui_path}"
        delay = max(float(self.config.ui_auto_open_delay_seconds), 0.0)

        def open_later() -> None:
            try:
                webbrowser.open(url)
            except Exception:
                return

        timer = threading.Timer(delay, open_later)
        timer.daemon = True
        timer.start()





