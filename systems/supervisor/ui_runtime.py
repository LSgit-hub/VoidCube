from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from fastapi import Request
from fastapi.responses import HTMLResponse, StreamingResponse

from systems.supervisor.ui_assets import load_supervisor_ui_html
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
from systems.supervisor.ui_body_status_adapters import (
    SupervisorUIBodyStatusContext,
    load_body_status,
)
from systems.supervisor.ui_snapshot_adapters import (
    SupervisorUIObservationSnapshotContext,
    SupervisorUIMemorySnapshotContext,
    load_memory_stats,
    load_observation_input_snapshot,
)
from systems.supervisor.ui_activity_adapters import (
    SupervisorUIActivityContext,
    clear_supervisor_ui_activity,
    latest_drive_candidate_snapshot,
    load_supervisor_ui_activity,
    persist_supervisor_ui_activity,
    recent_supervisor_ui_activity,
    record_supervisor_ui_activity,
)
from systems.supervisor.ui_media_state_adapters import (
    SupervisorUIMediaStateContext,
    enqueue_media_state,
)
from systems.supervisor.ui_open_lifecycle_adapters import (
    SupervisorUIOpenLifecycleContext,
    maybe_open_supervisor_ui,
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
        current = enqueue_media_state(
            context=SupervisorUIMediaStateContext(
                current_revision=self._media_revision,
                set_revision=lambda revision: setattr(
                    self, "_media_revision", revision
                ),
                set_current_media=lambda value: setattr(
                    self, "_current_media", value
                ),
            ),
            media=media,
        )
        logger.info("Media enqueued: %s (%s)", current.get("title"), current.get("type"))

    def _record_supervisor_ui_activity(
        self,
        event_type: str,
        *,
        scene: str = "planning",
        summary: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from systems.supervisor.planning_runtime import SUPERVISOR_LEGAL_SCENES

        return record_supervisor_ui_activity(
            context=SupervisorUIActivityContext(
                activity_path=self._supervisor_ui_activity_path,
                events=self._supervisor_ui_events,
                legal_scenes=SUPERVISOR_LEGAL_SCENES,
                record_history=self._record_supervisor_ui_activity_history,
            ),
            event_type=event_type,
            scene=scene,
            summary=summary,
            metadata=metadata,
        )

    def _recent_supervisor_ui_activity(self, limit: int = 20) -> List[Dict[str, Any]]:
        return recent_supervisor_ui_activity(
            events=getattr(self, "_supervisor_ui_events", None),
            limit=limit,
        )

    def _latest_drive_candidate_snapshot(self) -> List[Dict[str, Any]]:
        return latest_drive_candidate_snapshot(
            events=getattr(self, "_supervisor_ui_events", None)
        )

    def _load_supervisor_ui_activity(self) -> List[Dict[str, Any]]:
        return load_supervisor_ui_activity(
            path=getattr(self, "_supervisor_ui_activity_path", None),
            max_events=self.config.ui_activity_buffer_size,
        )

    def _persist_supervisor_ui_activity(self) -> None:
        persist_supervisor_ui_activity(
            path=getattr(self, "_supervisor_ui_activity_path", None),
            events=getattr(self, "_supervisor_ui_events", None),
        )

    def _clear_supervisor_ui_activity(self) -> None:
        clear_supervisor_ui_activity(
            path=getattr(self, "_supervisor_ui_activity_path", None),
            events=getattr(self, "_supervisor_ui_events", None),
        )

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
        return await load_observation_input_snapshot(
            context=SupervisorUIObservationSnapshotContext(
                load_runtime_observation_input=self.get_runtime_observation_input,
                default_snapshot=default_observation_input_snapshot,
                get_cached_snapshot=lambda: dict(
                    getattr(self, "_supervisor_ui_observation_input_cache", {}) or {}
                ),
                set_cached_snapshot=lambda snapshot: setattr(
                    self, "_supervisor_ui_observation_input_cache", snapshot
                ),
            ),
            timeout_seconds=timeout_seconds,
        )

    async def _load_ui_memory_stats(
        self,
        *,
        timeout_seconds: float = 0.8,
    ) -> Dict[str, Any]:
        return await load_memory_stats(
            context=SupervisorUIMemorySnapshotContext(
                fetch_tier1_stats=self._fetch_tier1_stats,
                get_cached_snapshot=lambda: dict(
                    getattr(self, "_supervisor_ui_memory_stats_cache", {}) or {}
                ),
                set_cached_snapshot=lambda snapshot: setattr(
                    self, "_supervisor_ui_memory_stats_cache", snapshot
                ),
            ),
            timeout_seconds=timeout_seconds,
        )

    def _load_ui_body_status(
        self,
        chain_history_projection: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return load_body_status(
            context=SupervisorUIBodyStatusContext(
                inspect_layout=self._body_registry.inspect_layout,
                load_slot_meta=self._body_registry.load_slot_meta,
            ),
            chain_history_projection=chain_history_projection,
        )

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
            load_cognition_state=self._endogenous_governance_state_persistence_service.load_cognition_state,
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
        maybe_open_supervisor_ui(
            context=SupervisorUIOpenLifecycleContext(
                ui_enabled=self.config.ui_enabled,
                auto_open=self.config.ui_auto_open,
                url=f"http://{self.config.host}:{self.config.port}{self.config.ui_path}",
                delay_seconds=self.config.ui_auto_open_delay_seconds,
            )
        )





