from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from systems.runtime_task_profile import derive_runtime_task_profile


class TraceRuntimeMixin:
    """Read-only trace aggregation across supervisor-local runtime stores."""

    @staticmethod
    def _trace_status_label(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized == "queued":
            normalized = "planned"
        return {
            "planned": "待审核",
            "awaiting_review": "待审查",
            "approved": "待执行",
            "running": "执行中",
            "retry": "重试中",
            "deferred": "已延后",
            "paused": "已暂停",
            "completed": "已写回",
            "failed": "执行失败",
            "cancelled": "已取消",
        }.get(normalized, str(value or "").strip() or "状态未识别")

    @staticmethod
    def _trace_event_label(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        return {
            "task_snapshot": "链路项快照",
            "task_decision": "治理裁决",
            "execution_request": "自主交接请求",
            "trace_marker": "回合标记",
            "tasks_planned": "治理规划",
            "tasks_reviewed": "批量复核",
            "endogenous_drive_planned": "候选进入治理",
            "endogenous_drive_evaluated": "内生驱动评估",
            "self_learning": "自主学习回报",
            "autonomous_chain": "自主链路活动",
            "autonomous_chain_plan": "治理规划回报",
            "autonomous_chain_execute": "执行回报",
            "memory_task": "记忆维护回报",
            "memory_write_failure": "记忆写回异常",
            "uncertainty_high": "高不确定性告警",
            "gateway_activity_unavailable": "网关活动暂不可见",
        }.get(normalized, str(value or "").strip() or "链路事件")

    @staticmethod
    def _trace_source_label(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        return {
            "autonomous_chain_store": "链路存储",
            "supervisor_activity": "监督者活动",
            "mem_governor_history": "治理历史",
            "gateway_activity_log": "网关回报",
            "gateway_activity": "网关状态",
        }.get(normalized, str(value or "").strip() or "未知记录侧")

    @staticmethod
    def _trace_runtime_label(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        return {
            "self_learning": "自主学习",
            "self_evolution": "自主改进",
            "general_self_evolution": "通用自主改进",
            "body_improvement": "替身改进",
            "body_switch": "身体切换",
            "body_upgrade": "替身升级",
            "memory_maintenance": "记忆维护",
        }.get(normalized, str(value or "").strip() or "")

    async def list_runtime_traces(self, limit: int = 20) -> Dict[str, Any]:
        records = await self._collect_trace_records(limit=max(limit, 1))
        summaries = self._summarize_trace_records(records)
        summaries.sort(key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)
        return {
            "status": "ok",
            "count": len(summaries[:limit]),
            "traces": summaries[:limit],
            "sources": self._trace_source_counts(records),
        }

    async def get_runtime_trace(self, trace_id: str) -> Dict[str, Any]:
        normalized_trace_id = str(trace_id or "").strip()
        records = await self._collect_trace_records(trace_id=normalized_trace_id)
        timeline = self._build_trace_timeline(records)
        summary = self._summarize_single_trace(normalized_trace_id, records)
        return {
            "status": "ok",
            "trace_id": normalized_trace_id,
            "found": summary["record_count"] > 0,
            "sources": self._trace_source_counts(records),
            "summary": summary,
            "timeline": timeline,
            "records": records,
        }

    async def get_runtime_timeline(self, limit: int = 20) -> Dict[str, Any]:
        normalized_limit = max(int(limit), 0)
        records = await self._collect_trace_records(limit=max(normalized_limit, 1))
        timeline = [
            record
            for record in self._build_trace_timeline(records)
            if str(record.get("trace_id") or "").strip()
        ]
        timeline.reverse()
        if normalized_limit:
            timeline = timeline[:normalized_limit]
        else:
            timeline = []
        return {
            "status": "ok",
            "count": len(timeline),
            "sources": self._trace_source_counts(timeline),
            "timeline": timeline,
        }

    async def _collect_trace_records(
        self,
        *,
        trace_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        records.extend(self._collect_trace_records_from_tasks(trace_id=trace_id))
        records.extend(self._collect_trace_records_from_supervisor_activity(trace_id=trace_id))
        records.extend(self._collect_trace_records_from_governor_history(trace_id=trace_id, limit=limit))
        records.extend(await self._collect_trace_records_from_gateway_activity(trace_id=trace_id))
        return records

    def _collect_trace_records_from_tasks(
        self,
        *,
        trace_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for task in self._autonomous_chain_store.list_chain_projection_tasks(
            include_cancelled=True,
        ):
            if trace_id and task.trace_id != trace_id:
                continue
            serialized = self._serialize_autonomous_chain_task(task)
            profile = self._trace_runtime_profile_from_payload(serialized)
            records.append(
                self._build_trace_record(
                    source="autonomous_chain_store",
                    event_type="task_snapshot",
                    trace_id=task.trace_id,
                    recorded_at=serialized.get("updated_at") or serialized.get("created_at"),
                    task_id=task.task_id,
                    decision_id=None,
                    profile=profile,
                    summary=(
                        f"监督者当前观察到链路项「{task.title}」处于"
                        f"{self._trace_status_label(task.status)}。"
                    ),
                    payload=serialized,
                )
            )
            for decision in task.decision_history:
                decision_payload = decision.model_dump(mode="json")
                records.append(
                    self._build_trace_record(
                        source="autonomous_chain_store",
                        event_type="task_decision",
                        trace_id=decision.trace_id,
                        recorded_at=decision_payload.get("decided_at"),
                        task_id=task.task_id,
                        decision_id=decision.decision_id,
                        profile=self._trace_runtime_profile_from_payload(decision_payload),
                        summary=(
                            f"API-B 已将链路项「{task.title}」裁决为"
                            f"{self._trace_status_label(decision.status)}。"
                        ),
                        payload=decision_payload,
                    )
                )
            if task.execution_request is not None:
                execution_payload = task.execution_request.model_dump(mode="json")
                records.append(
                    self._build_trace_record(
                        source="autonomous_chain_store",
                        event_type="execution_request",
                        trace_id=task.execution_request.trace_id,
                        recorded_at=execution_payload.get("created_at"),
                        task_id=task.task_id,
                        decision_id=task.execution_request.decision_id,
                        profile=self._trace_runtime_profile_from_payload(execution_payload),
                        summary=f"API-B 已为「{task.title}」准备交给执行面的交接请求。",
                        payload=execution_payload,
                    )
                )
        return records

    def _collect_trace_records_from_supervisor_activity(
        self,
        *,
        trace_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        events = self._recent_supervisor_ui_activity(
            limit=max(int(self.config.ui_activity_buffer_size), 1)
        )
        for event in events:
            metadata = dict(event.get("metadata") or {})
            event_trace_id = metadata.get("trace_id")
            if trace_id and event_trace_id != trace_id:
                continue
            if not event_trace_id:
                continue
            records.append(
                self._build_trace_record(
                    source="supervisor_activity",
                    event_type=str(event.get("event_type") or "supervisor_activity"),
                    trace_id=str(event_trace_id),
                    recorded_at=event.get("recorded_at"),
                    task_id=metadata.get("task_id"),
                    decision_id=metadata.get("decision_id"),
                    profile=self._trace_runtime_profile_from_payload(metadata),
                    summary=str(event.get("summary") or event.get("event_type") or "监督者活动"),
                    payload=event,
                )
            )
        return records

    def _collect_trace_records_from_governor_history(
        self,
        *,
        trace_id: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for record in self._governor.list_history(limit=limit):
            extracted_trace_id = self._extract_trace_value(record, "trace_id")
            if trace_id and extracted_trace_id != trace_id:
                continue
            if not extracted_trace_id:
                continue
            request = record.get("request") if isinstance(record.get("request"), dict) else {}
            lineage = (
                record.get("evolution_lineage")
                if isinstance(record.get("evolution_lineage"), dict)
                else {}
            )
            profile = self._trace_runtime_profile_from_payload({**lineage, **request})
            records.append(
                self._build_trace_record(
                    source="mem_governor_history",
                    event_type=str(record.get("kind") or request.get("event_type") or "governance_record"),
                    trace_id=str(extracted_trace_id),
                    recorded_at=record.get("created_at"),
                    task_id=request.get("task_id"),
                    decision_id=request.get("decision_id") or lineage.get("decision_id"),
                    profile=profile,
                    summary=str(request.get("summary") or record.get("kind") or "治理记录"),
                    payload=record,
                )
            )
        return records

    async def _collect_trace_records_from_gateway_activity(
        self,
        *,
        trace_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        try:
            activity_log = await self._fetch_gateway_activity_log(trace_id=trace_id)
        except Exception as exc:
            return [
                self._build_trace_record(
                    source="gateway_activity",
                    event_type="gateway_activity_unavailable",
                    trace_id=trace_id or "",
                    recorded_at=datetime.utcnow().isoformat(),
                    task_id=None,
                    decision_id=None,
                    profile={},
                    summary=f"网关活动快照暂不可用：{exc}",
                    payload={"available": False},
                )
            ]

        records: List[Dict[str, Any]] = []
        events = activity_log.get("events") or []
        if not isinstance(events, list):
            return records
        for event in events:
            if not isinstance(event, dict):
                continue
            if not self._gateway_activity_visible_to_supervisor_ui(event):
                continue
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            metadata_trace_id = metadata.get("trace_id")
            if trace_id and metadata_trace_id != trace_id:
                continue
            if not metadata_trace_id:
                continue
            records.append(
                self._build_trace_record(
                    source="gateway_activity_log",
                    event_type=str(event.get("activity_kind") or "gateway_activity"),
                    trace_id=str(metadata_trace_id),
                    recorded_at=event.get("recorded_at"),
                    task_id=metadata.get("task_id"),
                    decision_id=metadata.get("decision_id"),
                    profile=self._trace_runtime_profile_from_payload(metadata),
                    summary=self._trace_gateway_activity_summary(event),
                    payload=event,
                )
            )
        return records

    def _trace_gateway_activity_summary(self, event: Dict[str, Any]) -> str:
        activity_kind = str(event.get("activity_kind") or "").strip().lower()
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        subject = self._trace_gateway_activity_subject(metadata)
        label = self._trace_gateway_activity_label(metadata)
        if activity_kind == "self_learning":
            if subject:
                return f"网关记下了 {subject} 的自主学习回报。"
            return f"网关记下了一次 {label or '自主学习'} 回报。"
        if activity_kind == "autonomous_chain":
            if subject:
                return f"网关记下了会影响 {subject} 下一跳的一次自主链路回报。"
            return f"网关记下了一次 {label or '自主链路'} 活动回报。"
        if activity_kind == "autonomous_chain_plan":
            return f"网关记下了 {subject or (label or '自主链路项')} 的治理规划回报。"
        if activity_kind == "autonomous_chain_execute":
            return f"网关记下了 {subject or (label or '自主链路项')} 的执行回报。"
        if activity_kind == "memory_task":
            if subject:
                return f"网关记下了 {subject} 的记忆维护回报。"
            return f"网关记下了一次 {label or '记忆维护'} 回报。"
        if activity_kind == "memory_write_failure":
            return "网关记下了一次记忆写回异常。"
        if activity_kind == "uncertainty_high":
            return "网关记下了一次高不确定性告警。"
        return f"网关记下了一次 {label or '自主链路'} 相关回报。"

    @staticmethod
    def _trace_gateway_activity_label(metadata: Dict[str, Any]) -> str:
        task_identity = metadata.get("task_identity") if isinstance(metadata.get("task_identity"), dict) else {}
        return (
            str(task_identity.get("display_label") or "").strip()
            or str(metadata.get("execution_kind_label") or "").strip()
            or str(metadata.get("task_family_label") or "").strip()
            or str(metadata.get("governance_task_type_label") or "").strip()
        )

    def _trace_gateway_activity_subject(self, metadata: Dict[str, Any]) -> str:
        task_identity = metadata.get("task_identity") if isinstance(metadata.get("task_identity"), dict) else {}
        summary = str(task_identity.get("summary") or "").strip()
        title = str(task_identity.get("title") or metadata.get("title") or "").strip()
        if summary:
            return f"「{summary}」"
        if title:
            return f"「{title}」"
        label = self._trace_gateway_activity_label(metadata)
        if label:
            return label
        return ""

    @staticmethod
    def _gateway_activity_visible_to_supervisor_ui(event: Dict[str, Any]) -> bool:
        """Return whether a Gateway event belongs on the API-B Web monitor.

        Gateway records both user-chain and autonomous-chain activity for idle
        decisions. The supervisor room only visualizes API-B governance state
        and autonomous task reports, so user chat and API-A scene reports stay
        out of the Web timeline even when they carry a trace_id.
        """
        activity_kind = str(event.get("activity_kind") or "").strip().lower()
        if activity_kind in {"user_request", "agent_scene"}:
            return False
        if activity_kind in {
            "self_learning",
            "autonomous_chain",
            "autonomous_chain_plan",
            "autonomous_chain_execute",
            "memory_task",
            "memory_write_failure",
            "uncertainty_high",
        }:
            return True

        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        governance_task_type = str(metadata.get("governance_task_type") or "").strip().lower()
        task_family = str(metadata.get("task_family") or "").strip().lower()
        execution_kind = str(metadata.get("execution_kind") or "").strip().lower()
        if governance_task_type in {"self_learning", "self_evolution", "memory_maintenance"}:
            return True
        if task_family in {
            "self_learning",
            "memory_maintenance",
            "general_self_evolution",
            "body_upgrade",
            "body_switch",
        }:
            return True
        if execution_kind in {
            "self_learning",
            "memory_maintenance",
            "body_improvement",
            "body_switch",
        }:
            return True
        return False

    async def _fetch_gateway_activity_log(
        self,
        *,
        trace_id: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        import aiohttp

        execution_config = self.config.execution
        params: Dict[str, Any] = {"limit": limit}
        if trace_id:
            params["trace_id"] = trace_id
        async with aiohttp.ClientSession() as session:
            url = f"{execution_config.gateway_address}/admin/activity/log"
            async with session.get(url, params=params, timeout=2) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"网关活动日志接口返回状态 {response.status}"
                    )
                return await response.json()

    def _build_trace_record(
        self,
        *,
        source: str,
        event_type: str,
        trace_id: str,
        recorded_at: Any,
        task_id: Any,
        decision_id: Any,
        profile: Dict[str, Any],
        summary: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "source": source,
            "source_label": self._trace_source_label(source),
            "event_type": event_type,
            "event_label": self._trace_event_label(event_type),
            "trace_id": trace_id,
            "recorded_at": recorded_at,
            "task_id": task_id,
            "decision_id": decision_id,
            "governance_task_type": profile.get("governance_task_type"),
            "governance_task_type_label": self._trace_runtime_label(profile.get("governance_task_type")),
            "task_family": profile.get("task_family"),
            "task_family_label": self._trace_runtime_label(profile.get("task_family")),
            "execution_kind": profile.get("execution_kind"),
            "execution_kind_label": self._trace_runtime_label(profile.get("execution_kind")),
            "summary": summary,
            "payload": payload,
        }

    def _trace_runtime_profile_from_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return derive_runtime_task_profile(
            task_type=payload.get("task_type"),
            governance_task_type=payload.get("governance_task_type"),
            task_family=payload.get("task_family"),
            execution_kind=payload.get("execution_kind"),
            kind=payload.get("kind"),
            default_task_family="general_self_evolution",
        )

    def _build_trace_timeline(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        timeline = list(records)
        timeline.sort(key=lambda item: str(item.get("recorded_at") or ""))
        return timeline

    def _trace_source_counts(self, records: List[Dict[str, Any]]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for record in records:
            source = str(record.get("source") or "unknown")
            counts[source] = counts.get(source, 0) + 1
        return counts

    def _summarize_trace_records(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_trace: Dict[str, List[Dict[str, Any]]] = {}
        for record in records:
            trace_id = str(record.get("trace_id") or "").strip()
            if not trace_id:
                continue
            by_trace.setdefault(trace_id, []).append(record)
        return [
            self._summarize_single_trace(trace_id, trace_records)
            for trace_id, trace_records in by_trace.items()
        ]

    def _summarize_single_trace(
        self,
        trace_id: str,
        records: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        real_records = [
            record
            for record in records
            if record.get("source") != "gateway_activity"
            or record.get("event_type") != "gateway_activity_unavailable"
        ]
        if not real_records:
            return {
                "trace_id": trace_id,
                "record_count": 0,
                "first_seen_at": None,
                "last_seen_at": None,
                "sources": {},
                "source_labels": [],
                "task_ids": [],
                "decision_ids": [],
                "governance_task_types": [],
                "governance_labels": [],
                "task_families": [],
                "execution_kinds": [],
                "execution_labels": [],
            }

        timestamps = sorted(
            str(record.get("recorded_at"))
            for record in real_records
            if record.get("recorded_at")
        )
        return {
            "trace_id": trace_id,
            "record_count": len(real_records),
            "first_seen_at": timestamps[0] if timestamps else None,
            "last_seen_at": timestamps[-1] if timestamps else None,
            "sources": self._trace_source_counts(real_records),
            "source_labels": self._trace_unique_labels(real_records, "source_label"),
            "task_ids": self._unique_trace_values(real_records, "task_id"),
            "decision_ids": self._unique_trace_values(real_records, "decision_id"),
            "governance_task_types": self._unique_trace_values(real_records, "governance_task_type"),
            "governance_labels": self._trace_unique_labels(real_records, "governance_task_type_label"),
            "task_families": self._unique_trace_values(real_records, "task_family"),
            "execution_kinds": self._unique_trace_values(real_records, "execution_kind"),
            "execution_labels": self._trace_unique_labels(
                real_records,
                "execution_kind_label",
                fallback_key="task_family_label",
            ),
        }

    def _unique_trace_values(self, records: List[Dict[str, Any]], key: str) -> List[str]:
        return sorted(
            {
                str(record.get(key))
                for record in records
                if record.get(key) is not None
            }
        )

    def _trace_unique_labels(
        self,
        records: List[Dict[str, Any]],
        key: str,
        *,
        fallback_key: Optional[str] = None,
    ) -> List[str]:
        values: List[str] = []
        for record in records:
            raw = str(record.get(key) or "").strip()
            if not raw and fallback_key:
                raw = str(record.get(fallback_key) or "").strip()
            if not raw or raw in values:
                continue
            values.append(raw)
        return values

    def _extract_trace_value(self, payload: Any, key: str) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        value = payload.get(key)
        if value is not None:
            return str(value)
        for nested_key in (
            "request",
            "response",
            "execution_report",
            "registry",
            "evolution_lineage",
            "metadata",
            "runtime_task_profile",
        ):
            nested = payload.get(nested_key)
            extracted = self._extract_trace_value(nested, key)
            if extracted:
                return extracted
        return None


