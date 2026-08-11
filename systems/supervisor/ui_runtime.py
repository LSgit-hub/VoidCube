"""State, projection, stream, and lifecycle owner for the Supervisor web UI."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from systems.supervisor.ui_activity_adapters import (
    SupervisorUIActivityContext,
    clear_supervisor_ui_activity,
    latest_drive_candidate_snapshot,
    load_supervisor_ui_activity,
    persist_supervisor_ui_activity,
    recent_supervisor_ui_activity,
    record_supervisor_ui_activity,
)
from systems.supervisor.ui_assets import load_supervisor_ui_html
from systems.supervisor.ui_body_status_adapters import (
    SupervisorUIBodyStatusContext,
    load_body_status,
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
from systems.supervisor.ui_delivery_adapters import (
    control_delivery_request,
    delivery_events,
    push_delivery_request,
    remove_delivery_assets,
    serve_delivery_asset,
    upload_delivery_asset,
)
from systems.supervisor.ui_delivery_state_adapters import (
    SupervisorUIDeliveryStateContext,
    clear_delivery_state,
    load_delivery_state,
    persist_delivery_state,
    push_delivery_state,
    select_delivery_state,
    selected_delivery,
)
from systems.supervisor.ui_media_state_adapters import (
    SupervisorUIMediaStateContext,
    control_media_state,
    enqueue_media_state,
    enqueue_media_playlist_state,
    load_media_state,
    persist_media_state,
)
from systems.supervisor.ui_memory_status_adapters import (
    SupervisorUIMemoryStatusContext,
    fetch_tier1_stats,
)
from systems.supervisor.ui_open_lifecycle_adapters import (
    SupervisorUIOpenLifecycleContext,
    maybe_open_supervisor_ui,
)
from systems.supervisor.ui_projection import default_observation_input_snapshot
from systems.supervisor.ui_snapshot_adapters import (
    SupervisorUIMemorySnapshotContext,
    SupervisorUIObservationSnapshotContext,
    load_memory_stats,
    load_observation_input_snapshot,
)
from systems.supervisor.ui_state_orchestration import (
    SupervisorUIStateContext,
    build_supervisor_ui_state,
)
from systems.supervisor.ui_stream_adapters import (
    control_media_request,
    enqueue_media_request,
    media_events,
    supervisor_state_events,
    voice_level_events,
)
from systems.supervisor.ui_trace_adapters import (
    SupervisorUITraceContext,
    attach_recent_trace_details_to_observation,
    collect_ui_trace_records,
    load_recent_trace_details,
    recent_local_supervisor_observation_timeline,
)


JsonDict = Dict[str, Any]
logger = logging.getLogger("supervisor")


@dataclass(frozen=True, slots=True)
class SupervisorUIRuntimePorts:
    runtime_root: Path
    activity_buffer_size: int
    legal_scenes: Collection[str]
    record_activity_history: Callable[[JsonDict], None]
    load_gateway_url: Callable[[], str]
    gateway_memory_headers: Callable[..., Dict[str, str]]
    ui_event_interval_seconds: float
    voice_realtime_status: Callable[[], JsonDict]
    load_runtime_observation_input: Callable[[], Any]
    inspect_body_layout: Callable[[], JsonDict]
    load_body_slot_meta: Callable[[str], JsonDict]
    collect_trace_records_from_tasks: Callable[..., List[JsonDict]]
    collect_trace_records_from_supervisor_activity: Callable[..., List[JsonDict]]
    collect_trace_records_from_governor_history: Callable[..., List[JsonDict]]
    build_trace_timeline: Callable[..., List[JsonDict]]
    summarize_single_trace: Callable[..., JsonDict]
    load_runtime_config: Callable[[], Any]
    list_chain_projection_tasks: Callable[[], List[Any]]
    serialize_chain_task: Callable[[Any], JsonDict]
    load_cognition_state: Callable[[], JsonDict]
    stellar_mode_status: Callable[[], JsonDict]
    voice_status: Callable[[], JsonDict]
    ui_enabled: bool
    ui_auto_open: bool
    ui_url: str
    ui_auto_open_delay_seconds: float


class SupervisorUIRuntime:
    """Own all mutable state and HTTP-facing behavior of the web-room UI."""

    def __init__(self, ports: SupervisorUIRuntimePorts) -> None:
        self.ports = ports
        ports.runtime_root.mkdir(parents=True, exist_ok=True)
        self.activity_path = ports.runtime_root / "supervisor-ui-activity.json"
        self.events: deque[JsonDict] = deque(
            load_supervisor_ui_activity(
                path=self.activity_path,
                max_events=ports.activity_buffer_size,
            ),
            maxlen=ports.activity_buffer_size,
        )
        self.observation_input_cache: JsonDict = {}
        self.memory_stats_cache: JsonDict = {}
        self.current_media: JsonDict | None = None
        self.media_queue: deque[JsonDict] = deque()
        self.media_revision = 0
        self.media_state_path = ports.runtime_root / "supervisor-ui-media.json"
        current, queue, revision = load_media_state(self.media_state_path)
        self.current_media = current
        self.media_queue.extend(queue)
        self.media_revision = revision
        self.delivery_artifact_root = ports.runtime_root / "delivery-artifacts"
        self.delivery_artifact_root.mkdir(parents=True, exist_ok=True)
        self.delivery_state_path = ports.runtime_root / "supervisor-ui-deliveries.json"
        selected_id, deliveries, delivery_revision = load_delivery_state(
            self.delivery_state_path
        )
        self.delivery_items: deque[JsonDict] = deque(deliveries[:50], maxlen=50)
        retained_ids = {
            str(item.get("delivery_id") or "") for item in self.delivery_items
        }
        self.selected_delivery_id = (
            selected_id
            if selected_id in retained_ids
            else str((self.delivery_items[0] if self.delivery_items else {}).get("delivery_id") or "")
        )
        self.delivery_revision = delivery_revision

    def _media_context(self) -> SupervisorUIMediaStateContext:
        return SupervisorUIMediaStateContext(
            current_revision=self.media_revision,
            current_media=self.current_media,
            media_queue=self.media_queue,
            set_revision=lambda revision: setattr(self, "media_revision", revision),
            set_current_media=lambda value: setattr(self, "current_media", value),
        )

    def _delivery_context(self) -> SupervisorUIDeliveryStateContext:
        return SupervisorUIDeliveryStateContext(
            current_revision=self.delivery_revision,
            selected_id=self.selected_delivery_id,
            items=self.delivery_items,
            set_revision=lambda revision: setattr(self, "delivery_revision", revision),
            set_selected_id=lambda value: setattr(self, "selected_delivery_id", value),
        )

    def _persist_deliveries(self) -> None:
        persist_delivery_state(
            self.delivery_state_path,
            selected_id=self.selected_delivery_id,
            items=self.delivery_items,
            revision=self.delivery_revision,
        )

    def current_delivery(self) -> JsonDict | None:
        return selected_delivery(self._delivery_context())

    def list_deliveries(self) -> list[JsonDict]:
        return [dict(item) for item in self.delivery_items]

    def push_delivery(self, delivery: JsonDict) -> JsonDict:
        evicted = (
            dict(self.delivery_items[-1])
            if self.delivery_items.maxlen
            and len(self.delivery_items) >= self.delivery_items.maxlen
            else None
        )
        current = push_delivery_state(
            context=self._delivery_context(), delivery=delivery
        )
        self._persist_deliveries()
        if evicted is not None and not any(
            item.get("delivery_id") == evicted.get("delivery_id")
            for item in self.delivery_items
        ):
            remove_delivery_assets(
                [evicted], artifact_root=self.delivery_artifact_root
            )
        logger.info("Delivery pushed: %s (%s)", current.get("title"), current.get("type"))
        return current

    def select_delivery(self, delivery_id: str) -> JsonDict | None:
        current = select_delivery_state(
            context=self._delivery_context(), delivery_id=delivery_id
        )
        self._persist_deliveries()
        return current

    def clear_deliveries(self) -> None:
        cleared_items = self.list_deliveries()
        clear_delivery_state(context=self._delivery_context())
        self._persist_deliveries()
        remove_delivery_assets(
            cleared_items, artifact_root=self.delivery_artifact_root
        )

    def enqueue_media(self, media: JsonDict) -> JsonDict:
        current = enqueue_media_state(
            context=self._media_context(),
            media=media,
            queue_mode=str(media.get("queue_mode") or "replace"),
        )
        logger.info("Media enqueued: %s (%s)", current.get("title"), current.get("type"))
        persist_media_state(self.media_state_path, current=self.current_media, queue=self.media_queue, revision=self.media_revision)
        return current

    def enqueue_media_playlist(self, media: list[JsonDict], queue_mode: str = "replace") -> JsonDict | None:
        current = enqueue_media_playlist_state(
            context=self._media_context(), items=media, queue_mode=queue_mode
        )
        persist_media_state(self.media_state_path, current=self.current_media, queue=self.media_queue, revision=self.media_revision)
        logger.info("Media playlist enqueued: %s items", len(media))
        return current

    def control_media(self, action: str, media_id: str = "") -> JsonDict | None:
        current = control_media_state(
            context=self._media_context(),
            action=action,
            media_id=media_id,
        )
        logger.info("Media control: %s (current=%s)", action, (current or {}).get("title"))
        persist_media_state(self.media_state_path, current=self.current_media, queue=self.media_queue, revision=self.media_revision)
        return current

    def media_queue_length(self) -> int:
        return len(self.media_queue)

    def media_queue_items(self) -> list[JsonDict]:
        return [dict(item) for item in self.media_queue]

    def record_activity(
        self,
        event_type: str,
        *,
        scene: str = "planning",
        summary: str = "",
        metadata: JsonDict | None = None,
    ) -> JsonDict:
        return record_supervisor_ui_activity(
            context=SupervisorUIActivityContext(
                activity_path=self.activity_path,
                events=self.events,
                legal_scenes=self.ports.legal_scenes,
                record_history=self.ports.record_activity_history,
            ),
            event_type=event_type,
            scene=scene,
            summary=summary,
            metadata=metadata,
        )

    def recent_activity(self, limit: int = 20) -> List[JsonDict]:
        return recent_supervisor_ui_activity(events=self.events, limit=limit)

    def latest_drive_candidates(self) -> List[JsonDict]:
        return latest_drive_candidate_snapshot(events=self.events)

    def persist_activity(self) -> None:
        persist_supervisor_ui_activity(path=self.activity_path, events=self.events)

    def clear_activity(self) -> None:
        clear_supervisor_ui_activity(path=self.activity_path, events=self.events)

    async def get_ui(self) -> HTMLResponse:
        return HTMLResponse(load_supervisor_ui_html())

    def _identity_context(self) -> SupervisorUIIdentityProxyContext:
        return SupervisorUIIdentityProxyContext(
            gateway_url=self.ports.load_gateway_url(),
            gateway_memory_headers=self.ports.gateway_memory_headers,
        )

    async def get_identity_archive(self) -> JsonDict:
        return await get_identity_archive(context=self._identity_context())

    async def get_identity_turns(self, limit: int = 20) -> JsonDict:
        return await get_identity_turns(
            context=self._identity_context(),
            limit=limit,
        )

    async def get_evolution_promotion_audit(self, limit: int = 100) -> JsonDict:
        return await get_evolution_promotion_audit(
            context=self._identity_context(),
            limit=limit,
        )

    async def get_evolution_promotion_candidates(
        self,
        limit: int = 100,
    ) -> JsonDict:
        return await get_evolution_promotion_candidates(
            context=self._identity_context(),
            limit=limit,
        )

    async def consent_evolution_promotion_candidate(
        self,
        candidate_id: str,
        request: JsonDict,
    ) -> JsonDict:
        return await consent_evolution_promotion_candidate(
            context=self._identity_context(),
            candidate_id=candidate_id,
            request=request,
        )

    async def verify_identity_experience(self, request: JsonDict) -> JsonDict:
        return await verify_identity_experience(
            context=self._identity_context(),
            request=request,
        )

    async def get_events(self, request: Request) -> StreamingResponse:
        return supervisor_state_events(
            request,
            load_state=self.get_state,
            interval_seconds=self.ports.ui_event_interval_seconds,
        )

    async def get_voice_levels(self, request: Request) -> StreamingResponse:
        return voice_level_events(
            request,
            realtime_status=self.ports.voice_realtime_status,
        )

    async def get_media_events(self, request: Request) -> StreamingResponse:
        return media_events(
            request,
            current_media=lambda: self.current_media,
            current_revision=lambda: self.media_revision,
            queue_length=self.media_queue_length,
            queue_items=self.media_queue_items,
        )

    async def get_delivery_events(self, request: Request) -> StreamingResponse:
        return delivery_events(
            request,
            current_delivery=self.current_delivery,
            current_revision=lambda: self.delivery_revision,
            delivery_items=self.list_deliveries,
        )

    async def push_delivery_endpoint(self, request: Request) -> JsonDict:
        return await push_delivery_request(
            request,
            push_delivery=self.push_delivery,
            delivery_items=self.list_deliveries,
            current_revision=lambda: self.delivery_revision,
        )

    async def control_delivery_endpoint(self, request: Request) -> JsonDict:
        return await control_delivery_request(
            request,
            select_delivery=self.select_delivery,
            clear_deliveries=self.clear_deliveries,
            delivery_items=self.list_deliveries,
            current_revision=lambda: self.delivery_revision,
        )

    async def upload_delivery_asset_endpoint(self, request: Request) -> JsonDict:
        return await upload_delivery_asset(
            request, artifact_root=self.delivery_artifact_root
        )

    async def get_delivery_asset_endpoint(
        self, artifact_id: str, filename: str
    ):
        return serve_delivery_asset(
            artifact_id, filename, artifact_root=self.delivery_artifact_root
        )

    async def enqueue_media_endpoint(self, request: Request) -> JsonDict:
        return await enqueue_media_request(
            request,
            enqueue_media=self.enqueue_media,
            current_revision=lambda: self.media_revision,
            queue_length=self.media_queue_length,
            current_media=lambda: self.current_media,
            queue_items=self.media_queue_items,
        )

    async def control_media_endpoint(self, request: Request) -> JsonDict:
        return await control_media_request(
            request,
            control_media=self.control_media,
            current_revision=lambda: self.media_revision,
            queue_length=self.media_queue_length,
            queue_items=self.media_queue_items,
        )

    async def enqueue_media_playlist_endpoint(self, request: Request) -> JsonDict:
        from systems.supervisor.ui_stream_adapters import enqueue_media_playlist_request
        return await enqueue_media_playlist_request(
            request,
            enqueue_playlist=self.enqueue_media_playlist,
            current_revision=lambda: self.media_revision,
            queue_length=self.media_queue_length,
            current_media=lambda: self.current_media,
            queue_items=self.media_queue_items,
        )

    # ── 账号中心 ─────────────────────────────────────

    @property
    def accounts_revision(self) -> int:
        """每次账号变更自增，前端通过 SSE 感知变化后刷新 cookie。"""
        try:
            return self._accounts_revision
        except AttributeError:
            self._accounts_revision = 0
            return 0

    def _bump_accounts_revision(self) -> int:
        self._accounts_revision = self.accounts_revision + 1
        return self._accounts_revision

    async def list_accounts(self) -> JsonDict:
        from systems.supervisor.account_store import (
            account_for_api,
            load_accounts,
            SUPPORTED_PLATFORMS,
        )
        accounts = load_accounts()
        return {
            "accounts": [account_for_api(a) for a in accounts],
            "supported_platforms": SUPPORTED_PLATFORMS,
            "accounts_revision": self.accounts_revision,
        }

    async def add_account(self, request: Request) -> JsonDict:
        from systems.supervisor.account_store import (
            PLATFORM_PRESETS,
            account_for_api,
            load_accounts,
            missing_required_auth_cookies,
            parse_cookie_string,
            PlatformAccount,
            sanitize_account_label,
            save_account,
        )
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="请求体必须是 JSON")

        platform = str(body.get("platform") or "").strip()
        if platform not in PLATFORM_PRESETS:
            raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")

        cookies_raw = str(body.get("cookies_raw") or "").strip()
        if not cookies_raw:
            raise HTTPException(status_code=400, detail="缺少 cookies_raw 字段")

        label = sanitize_account_label(str(body.get("label") or ""))
        account_id = str(body.get("id") or "").strip()

        parsed = parse_cookie_string(cookies_raw, platform)
        if not parsed:
            raise HTTPException(status_code=400, detail="无法解析 cookie 字符串")
        missing = missing_required_auth_cookies(parsed, platform)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"缺少登录所需 Cookie: {', '.join(missing)}。"
                    "请重新点击账号中心的“在桌面应用中登录”完成登录。"
                ),
            )

        # 更新已有账号或新建
        existing_account: Optional[PlatformAccount] = None
        if account_id:
            for a in load_accounts():
                if a.id == account_id:
                    existing_account = a
                    break

        account = PlatformAccount(
            id=account_id or (existing_account.id if existing_account else uuid.uuid4().hex[:12]),
            platform=platform,
            label=label,
            cookies_raw=cookies_raw,
            parsed_cookies=parsed,
            status="active",
        )

        save_account(account)
        self._bump_accounts_revision()
        logger.info("Account saved: %s (%s)", platform, account.id)
        return {
            "status": "ok",
            "account": account_for_api(account),
            "accounts_revision": self.accounts_revision,
        }

    async def delete_account_endpoint(self, request: Request) -> JsonDict:
        from systems.supervisor.account_store import delete_account, load_accounts
        account_id = request.path_params.get("account_id", "").strip()
        if not account_id:
            raise HTTPException(status_code=400, detail="缺少 account_id")
        remaining = delete_account(account_id)
        self._bump_accounts_revision()
        return {
            "status": "ok",
            "accounts_remaining": len(remaining),
            "accounts_revision": self.accounts_revision,
        }

    async def verify_account_endpoint(self, request: Request) -> JsonDict:
        from systems.supervisor.account_store import (
            account_for_api,
            load_accounts,
            verify_account,
        )
        account_id = request.path_params.get("account_id", "").strip()
        if not account_id:
            raise HTTPException(status_code=400, detail="缺少 account_id")
        accounts = load_accounts()
        target: Optional[PlatformAccount] = None
        for a in accounts:
            if a.id == account_id:
                target = a
                break
        if target is None:
            raise HTTPException(status_code=404, detail="账号不存在")
        result = await verify_account(target)
        # 更新状态
        from systems.supervisor.account_store import save_account
        target.last_verified = datetime.now(timezone.utc).isoformat()
        target.status = result["status"]
        save_account(target)
        self._bump_accounts_revision()
        return {
            "status": "ok",
            "account": account_for_api(target),
            "verify_result": result,
            "accounts_revision": self.accounts_revision,
        }

    async def _load_observation_input_snapshot(
        self,
        *,
        timeout_seconds: float = 0.8,
    ) -> tuple[JsonDict, bool]:
        return await load_observation_input_snapshot(
            context=SupervisorUIObservationSnapshotContext(
                load_runtime_observation_input=self.ports.load_runtime_observation_input,
                default_snapshot=default_observation_input_snapshot,
                get_cached_snapshot=lambda: dict(self.observation_input_cache),
                set_cached_snapshot=lambda snapshot: setattr(
                    self,
                    "observation_input_cache",
                    snapshot,
                ),
            ),
            timeout_seconds=timeout_seconds,
        )

    async def _load_memory_stats(
        self,
        *,
        timeout_seconds: float = 0.8,
    ) -> JsonDict:
        return await load_memory_stats(
            context=SupervisorUIMemorySnapshotContext(
                fetch_tier1_stats=self._fetch_tier1_stats,
                get_cached_snapshot=lambda: dict(self.memory_stats_cache),
                set_cached_snapshot=lambda snapshot: setattr(
                    self,
                    "memory_stats_cache",
                    snapshot,
                ),
            ),
            timeout_seconds=timeout_seconds,
        )

    def _load_body_status(self, chain_history: List[JsonDict]) -> JsonDict:
        return load_body_status(
            context=SupervisorUIBodyStatusContext(
                inspect_layout=self.ports.inspect_body_layout,
                load_slot_meta=self.ports.load_body_slot_meta,
            ),
            chain_history_projection=chain_history,
        )

    async def _load_observation_timeline(self, *, limit: int = 12) -> List[JsonDict]:
        try:
            return self.recent_local_observation_timeline(limit=limit)
        except Exception:
            return self.recent_activity(limit=limit)

    def _trace_context(self) -> SupervisorUITraceContext:
        return SupervisorUITraceContext(
            collect_trace_records_from_tasks=self.ports.collect_trace_records_from_tasks,
            collect_trace_records_from_supervisor_activity=(
                self.ports.collect_trace_records_from_supervisor_activity
            ),
            collect_trace_records_from_governor_history=(
                self.ports.collect_trace_records_from_governor_history
            ),
            build_trace_timeline=self.ports.build_trace_timeline,
            summarize_single_trace=self.ports.summarize_single_trace,
        )

    def collect_trace_records(
        self,
        *,
        trace_id: str | None = None,
        limit: int = 200,
    ) -> List[JsonDict]:
        return collect_ui_trace_records(
            context=self._trace_context(),
            trace_id=trace_id,
            limit=limit,
        )

    def recent_local_observation_timeline(
        self,
        *,
        limit: int = 12,
    ) -> List[JsonDict]:
        return recent_local_supervisor_observation_timeline(
            context=self._trace_context(),
            limit=limit,
        )

    async def get_state(self) -> JsonDict:
        state = await build_supervisor_ui_state(
            context=SupervisorUIStateContext(
                runtime_config=self.ports.load_runtime_config(),
                list_chain_projection_tasks=self.ports.list_chain_projection_tasks,
                serialize_chain_task=self.ports.serialize_chain_task,
                latest_drive_candidates=self.latest_drive_candidates,
                load_observation_input_snapshot=self._load_observation_input_snapshot,
                load_memory_stats=self._load_memory_stats,
                load_observation_timeline=self._load_observation_timeline,
                load_body_status=self._load_body_status,
                attach_trace_details=self.attach_recent_trace_details,
                load_cognition_state=self.ports.load_cognition_state,
                stellar_mode_status=self.ports.stellar_mode_status,
                voice_status=self.ports.voice_status,
                current_media=lambda: self.current_media,
                media_queue_length=self.media_queue_length,
            )
        )
        state["accounts_revision"] = self.accounts_revision
        return state

    async def load_recent_trace_details(
        self,
        trace_ids: List[str],
        *,
        limit: int = 6,
    ) -> Dict[str, JsonDict]:
        return await load_recent_trace_details(
            context=self._trace_context(),
            trace_ids=trace_ids,
            limit=limit,
        )

    async def attach_recent_trace_details(self, observation: JsonDict) -> JsonDict:
        return await attach_recent_trace_details_to_observation(
            context=self._trace_context(),
            observation=observation,
        )

    async def _fetch_tier1_stats(self) -> JsonDict:
        return await fetch_tier1_stats(
            context=SupervisorUIMemoryStatusContext(
                gateway_url=self.ports.load_gateway_url()
            )
        )

    def maybe_open(self) -> None:
        maybe_open_supervisor_ui(
            context=SupervisorUIOpenLifecycleContext(
                ui_enabled=self.ports.ui_enabled,
                auto_open=self.ports.ui_auto_open,
                url=self.ports.ui_url,
                delay_seconds=self.ports.ui_auto_open_delay_seconds,
            )
        )


__all__ = ["SupervisorUIRuntime", "SupervisorUIRuntimePorts"]
