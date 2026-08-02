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

from fastapi import Request
from fastapi.responses import HTMLResponse, StreamingResponse

from VoidCube_core.utils import atomic_json_write
from systems.supervisor.observation_status import normalize_autonomous_status
from systems.supervisor.ui_assets import load_supervisor_ui_html
from systems.supervisor.ui_body_projection import project_body_slot_cards
from systems.supervisor.ui_projection import (
    default_observation_input_snapshot,
    runtime_activity_label,
)
from systems.supervisor.ui_state_orchestration import (
    SupervisorUIStateContext,
    build_supervisor_ui_state,
)
from systems.supervisor.ui_stream_adapters import (
    enqueue_media_request,
    media_events,
    supervisor_state_events,
    voice_level_events,
)
from systems.supervisor.ui_identity_proxy_adapters import (
    SupervisorUIIdentityProxyContext,
    consent_evolution_promotion_candidate,
    get_evolution_promotion_audit,
    get_evolution_promotion_candidates,
    get_identity_archive,
    get_identity_turns,
    verify_identity_experience,
)
from systems.supervisor.ui_memory_status_adapters import (
    SupervisorUIMemoryStatusContext,
    fetch_tier1_stats,
)
from systems.supervisor.ui_trace_adapters import (
    SupervisorUITraceContext,
    attach_recent_trace_details_to_observation,
    collect_ui_trace_records,
    load_recent_trace_details,
    recent_local_supervisor_observation_timeline,
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

    def _supervisor_ui_identity_proxy_context(
        self,
    ) -> SupervisorUIIdentityProxyContext:
        return SupervisorUIIdentityProxyContext(
            gateway_url=str(self.config.execution.gateway_address).rstrip("/"),
            gateway_memory_headers=self._gateway_memory_headers,
        )

    async def get_supervisor_identity_archive(self) -> Dict[str, Any]:
        return await get_identity_archive(
            context=self._supervisor_ui_identity_proxy_context()
        )

    async def get_supervisor_identity_turns(self, limit: int = 20) -> Dict[str, Any]:
        return await get_identity_turns(
            context=self._supervisor_ui_identity_proxy_context(),
            limit=limit,
        )

    async def get_supervisor_evolution_promotion_audit(
        self,
        limit: int = 100,
    ) -> Dict[str, Any]:
        return await get_evolution_promotion_audit(
            context=self._supervisor_ui_identity_proxy_context(),
            limit=limit,
        )

    async def get_supervisor_evolution_promotion_candidates(
        self,
        limit: int = 100,
    ) -> Dict[str, Any]:
        return await get_evolution_promotion_candidates(
            context=self._supervisor_ui_identity_proxy_context(),
            limit=limit,
        )

    async def consent_supervisor_evolution_promotion_candidate(
        self,
        candidate_id: str,
        request: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await consent_evolution_promotion_candidate(
            context=self._supervisor_ui_identity_proxy_context(),
            candidate_id=candidate_id,
            request=request,
        )

    async def verify_supervisor_identity_experience(
        self, request: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await verify_identity_experience(
            context=self._supervisor_ui_identity_proxy_context(),
            request=request,
        )

    async def get_supervisor_ui_events(self, request: Request) -> StreamingResponse:
        return supervisor_state_events(
            request,
            load_state=self.get_supervisor_ui_state,
            interval_seconds=self.config.ui_event_interval_seconds,
        )

    async def get_voice_level_events(self, request: Request) -> StreamingResponse:
        return voice_level_events(
            request,
            realtime_status=self._voice_manager.realtime_status,
        )

    async def get_media_events(self, request: Request) -> StreamingResponse:
        return media_events(
            request,
            current_media=lambda: self._current_media,
        )

    async def enqueue_media_endpoint(self, request: Request) -> Dict[str, Any]:
        return await enqueue_media_request(
            request,
            enqueue_media=self.enqueue_media,
            current_revision=lambda: self._media_revision,
        )

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

    def _supervisor_ui_trace_context(self) -> SupervisorUITraceContext:
        return SupervisorUITraceContext(
            collect_trace_records_from_tasks=self._collect_trace_records_from_tasks,
            collect_trace_records_from_supervisor_activity=(
                self._collect_trace_records_from_supervisor_activity
            ),
            collect_trace_records_from_governor_history=(
                self._collect_trace_records_from_governor_history
            ),
            build_trace_timeline=self._build_trace_timeline,
            summarize_single_trace=self._summarize_single_trace,
        )

    def _collect_ui_trace_records(
        self,
        *,
        trace_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        return collect_ui_trace_records(
            context=self._supervisor_ui_trace_context(),
            trace_id=trace_id,
            limit=limit,
        )

    def _recent_local_supervisor_observation_timeline(
        self,
        *,
        limit: int = 12,
    ) -> List[Dict[str, Any]]:
        return recent_local_supervisor_observation_timeline(
            context=self._supervisor_ui_trace_context(),
            limit=limit,
        )

    async def get_supervisor_ui_state(self) -> Dict[str, Any]:
        context = SupervisorUIStateContext(
            runtime_config=getattr(self.config, "service_runtime", None),
            list_chain_projection_tasks=self._autonomous_chain_store.list_chain_projection_tasks,
            serialize_chain_task=self._serialize_autonomous_chain_task,
            latest_drive_candidates=self._latest_drive_candidate_snapshot,
            load_observation_input_snapshot=self._load_ui_observation_input_snapshot,
            load_memory_stats=self._load_ui_memory_stats,
            load_observation_timeline=self._load_ui_observation_timeline,
            load_body_status=self._load_ui_body_status,
            attach_trace_details=self._attach_recent_trace_details_to_observation,
            load_cognition_state=self._load_endogenous_cognition_state,
            stellar_mode_status=self._stellar_mode_status,
            voice_status=self._voice_manager.status,
            current_media=lambda: self._current_media,
        )
        return await build_supervisor_ui_state(context=context)


    async def _load_recent_trace_details(
        self,
        trace_ids: List[str],
        *,
        limit: int = 6,
    ) -> Dict[str, Dict[str, Any]]:
        return await load_recent_trace_details(
            context=self._supervisor_ui_trace_context(),
            trace_ids=trace_ids,
            limit=limit,
        )

    async def _attach_recent_trace_details_to_observation(
        self,
        observation: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await attach_recent_trace_details_to_observation(
            context=self._supervisor_ui_trace_context(),
            observation=observation,
        )

    async def _fetch_tier1_stats(self) -> Dict[str, Any]:
        return await fetch_tier1_stats(
            context=SupervisorUIMemoryStatusContext(
                gateway_url=str(self.config.execution.gateway_address).rstrip("/")
            )
        )

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





