from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


CORE_VALUES: Dict[str, str] = {
    "continuity": "Preserve VoidCube's long-term memory, lineage, and service continuity.",
    "truthfulness": "Surface uncertainty, correction signals, and evidence gaps before they harden.",
    "creativity": "Turn idle capacity into bounded learning and improvement proposals.",
}


@dataclass(frozen=True, slots=True)
class EndogenousTaskCandidate:
    stable_key: str
    title: str
    summary: str
    priority: str
    governance_task_type: str
    task_family: str
    execution_kind: Optional[str]
    value_tags: List[str]
    utility: float
    evidence: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)

    def to_queue_item(self) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
            "source": "endogenous_drive",
            "endogenous_drive_key": self.stable_key,
            "core_values": list(self.value_tags),
            "utility": self.utility,
            "governance_task_type": self.governance_task_type,
            "task_family": self.task_family,
        }
        if self.execution_kind is not None:
            metadata["execution_kind"] = self.execution_kind
        return {
            "title": self.title,
            "summary": self.summary,
            "source": "endogenous_drive",
            "priority": self.priority,
            "governance_task_type": self.governance_task_type,
            "task_family": self.task_family,
            "execution_kind": self.execution_kind,
            "metadata": metadata,
            "evidence": {
                "endogenous_drive": {
                    "stable_key": self.stable_key,
                    "core_values": list(self.value_tags),
                    "core_value_definitions": {
                        key: CORE_VALUES[key] for key in self.value_tags if key in CORE_VALUES
                    },
                    "utility": self.utility,
                },
                **dict(self.evidence),
            },
            "constraints": dict(self.constraints),
        }


class EndogenousDriveEngine:
    """Supervisor drive loop — deterministic core + optional LLM intelligence.

    The drive engine does not execute work. It turns system facts, core values,
    and (when available) LLM-analyzed memory context into auditable queue
    candidates that still pass through supervisor review.

    Without LLM: uses deterministic text extraction (first 80 chars).
    With LLM: reads compressed memory context to generate intelligent,
    context-aware learning topics.
    """

    def generate_candidates(
        self,
        *,
        idle_window: Dict[str, Any],
        existing_drive_keys: Iterable[str],
        max_candidates: int = 3,
    ) -> List[EndogenousTaskCandidate]:
        existing_keys = set(existing_drive_keys)
        candidates = self._candidate_stream(idle_window, existing_keys=existing_keys)
        candidates.sort(key=lambda candidate: candidate.utility, reverse=True)
        return candidates[:max(max_candidates, 0)]

    def _candidate_stream(
        self, idle_window: Dict[str, Any], *, existing_keys: set[str] = None
    ) -> List[EndogenousTaskCandidate]:
        if existing_keys is None:
            existing_keys = set()
        activity = dict(idle_window.get("activity") or {})
        counts = dict(activity.get("counts") or {})
        decisions_by_family = dict(idle_window.get("task_family_decisions") or {})
        decisions_by_governance = dict(idle_window.get("governance_task_type_decisions") or {})

        memory_plan = self._decision_for(
            "memory_maintenance",
            decisions_by_family,
            decisions_by_governance,
        )
        self_learning_plan = self._decision_for(
            "self_learning",
            decisions_by_family,
            decisions_by_governance,
        )
        self_evolution_plan = self._decision_for(
            "general_self_evolution",
            decisions_by_family,
            decisions_by_governance,
        )

        candidates: List[EndogenousTaskCandidate] = []
        if memory_plan.get("eligible_for_planning") and "continuity:memory_maintenance_sweep" not in existing_keys:
            candidates.append(
                EndogenousTaskCandidate(
                    stable_key="continuity:memory_maintenance_sweep",
                    title="Maintain long-term memory continuity",
                    summary=(
                        "Inspect memory-maintenance needs during an idle window so long-term "
                        "identity, summaries, and governance traces stay usable."
                    ),
                    priority="high",
                    governance_task_type="memory_maintenance",
                    task_family="memory_maintenance",
                    execution_kind="memory_maintenance",
                    value_tags=["continuity"],
                    utility=0.92,
                    evidence={
                        "idle_window_checks": dict(idle_window.get("checks") or {}),
                        "idle_seconds": dict(idle_window.get("idle_seconds") or {}),
                    },
                )
            )

        recent_errors = int(counts.get("error_count") or counts.get("recent_errors") or 0)
        uncertainty_count = int(
            counts.get("uncertainty_high_count")
            or counts.get("high_uncertainty")
            or 0
        )
        correction_signals = recent_errors + uncertainty_count
        if correction_signals > 0 and self_learning_plan.get("eligible_for_planning") and "truthfulness:review_correction_signals" not in existing_keys:
            candidates.append(
                EndogenousTaskCandidate(
                    stable_key="truthfulness:review_correction_signals",
                    title="Review recent uncertainty and correction signals",
                    summary=(
                        "Turn recent errors or high-uncertainty answers into a bounded "
                        "self-learning follow-up instead of letting them remain invisible."
                    ),
                    priority="high" if correction_signals >= 3 else "normal",
                    governance_task_type="self_learning",
                    task_family="self_learning",
                    execution_kind=None,
                    value_tags=["truthfulness"],
                    utility=min(0.65 + correction_signals * 0.08, 0.95),
                    evidence={
                        "recent_errors": recent_errors,
                        "uncertainty_high_count": uncertainty_count,
                    },
                )
            )

        active_sessions = int(activity.get("active_sessions") or 0)
        if active_sessions == 0 and self_learning_plan.get("eligible_for_planning"):
            # Try LLM-generated topics first; fall back to mechanical extraction
            llm_topics = self._llm_generate_learning_topics(activity, max_topics=3)
            generated_count = 0
            for topic in (llm_topics if llm_topics else []):
                topic_key = _stable_key_for_topic(topic["title"])
                if topic_key in existing_keys:
                    continue  # Skip duplicate topic
                title = f"Research: {topic['title']}"
                summary = topic['summary']
                candidates.append(
                    EndogenousTaskCandidate(
                        stable_key=topic_key,  # Dynamic key: "creativity:idle_learning:{hash}"
                        title=title,
                        summary=summary,
                        priority="normal",
                        governance_task_type="self_learning",
                        task_family="self_learning",
                        execution_kind=None,
                        value_tags=["creativity"],
                        utility=0.72,
                        evidence={
                            "active_sessions": active_sessions,
                            "trigger": "idle_capacity",
                            "learning_topic": topic['title'],
                            "llm_generated": True,
                        },
                        constraints={
                            "execution_policy": "learn_only",
                            "must_not_modify_active_body": True,
                        },
                    )
                )
                existing_keys.add(topic_key)
                generated_count += 1
                if generated_count >= 2:
                    break

            # Fallback: mechanical extraction if no LLM topics
            if generated_count == 0:
                learning_topic = self._extract_learning_topic(activity)
                topic_key = (
                    _stable_key_for_topic(learning_topic)
                    if learning_topic
                    else "creativity:idle_learning:fallback"
                )
                if topic_key not in existing_keys:
                    title = (
                        f"Research: {learning_topic}"
                        if learning_topic
                        else "Explore one unresolved learning thread"
                    )
                    summary = (
                        f"Use idle capacity to research '{learning_topic}' — the most recent "
                        f"user-discussed topic that may benefit from deeper investigation."
                        if learning_topic
                        else "Use idle capacity to ask the agent for one evidence-producing "
                             "learning pass over unresolved recent topics."
                    )
                    utility = 0.68 if learning_topic else 0.58
                    candidates.append(
                        EndogenousTaskCandidate(
                            stable_key=topic_key,
                            title=title,
                    summary=summary,
                    priority="normal",
                    governance_task_type="self_learning",
                    task_family="self_learning",
                    execution_kind=None,
                    value_tags=["creativity"],
                    utility=utility,
                    evidence={
                        "active_sessions": active_sessions,
                        "trigger": "idle_capacity",
                        "learning_topic": learning_topic or "",
                        "llm_generated": bool(llm_topics),
                    },
                    constraints={
                        "execution_policy": "learn_only",
                        "must_not_modify_active_body": True,
                    },
                )
            )

        if self_evolution_plan.get("eligible_for_planning") and "continuity:queue_hygiene_review" not in existing_keys:
            candidates.append(
                EndogenousTaskCandidate(
                    stable_key="continuity:queue_hygiene_review",
                    title="Review self-evolution queue hygiene",
                    summary=(
                        "Check whether planned, deferred, or paused self-evolution work still "
                        "has enough evidence and clear rollback constraints."
                    ),
                    priority="normal",
                    governance_task_type="self_evolution",
                    task_family="general_self_evolution",
                    execution_kind="general_self_evolution",
                    value_tags=["continuity", "truthfulness"],
                    utility=0.52,
                    evidence={
                        "trigger": "supervisor_queue_governance",
                    },
                    constraints={
                        "must_not_execute_without_review": True,
                    },
                )
            )

        return candidates

    def _extract_learning_topic(self, activity: Dict[str, Any]) -> str:
        """Extract a concise learning topic from recent gateway activity metadata.

        Looks at the most recent user_request and agent_work metadata to find
        topics that were discussed but may benefit from deeper research.
        Returns empty string if no meaningful topic can be extracted.
        """
        recent = dict(activity.get("recent_metadata") or {})
        user_req = recent.get("user_request") or {}
        agent_work = recent.get("agent_work") or {}

        # Try to extract a topic from the user's last request
        user_text = str(user_req.get("text") or user_req.get("query") or "")
        if not user_text:
            user_text = str(user_req.get("summary") or "")
        if user_text and len(user_text) > 10:
            # Take first sentence or first 80 chars as the topic
            topic = user_text.split(".")[0].split("\n")[0].strip()
            if len(topic) > 80:
                topic = topic[:77] + "..."
            if len(topic) >= 10:
                return topic

        # Fall back to agent's last response summary
        agent_text = str(agent_work.get("summary") or agent_work.get("title") or "")
        if agent_text and len(agent_text) > 10:
            topic = agent_text.split(".")[0].strip()
            if len(topic) > 80:
                topic = topic[:77] + "..."
            if len(topic) >= 10:
                return topic

        return ""

    def _llm_generate_learning_topics(
        self, activity: Dict[str, Any], max_topics: int = 3
    ) -> List[Dict[str, str]]:
        """Use LLM to generate intelligent learning topics from memory context.

        Unlike _extract_learning_topic (mechanical string slicing), this reads
        the compressed memory state and recent activity to produce genuinely
        useful research directions grounded in the system's actual history.

        Returns list of {"title": ..., "summary": ...} dicts.
        Falls back to empty list if LLM is unavailable.
        """
        try:
            import os
            api_key = (
                os.environ.get("DEEPSEEK_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or ""
            ).strip()
            if not api_key:
                return []

            from memai.llm_client import OpenAICompatibleLLMClient
            model = os.environ.get("MEMAI_LLM_MODEL", "deepseek-chat")
            base_url = os.environ.get("MEMAI_LLM_BASE_URL", "https://api.deepseek.com/v1")
            client = OpenAICompatibleLLMClient(model=model, api_key=api_key, base_url=base_url)

            # ── Fetch real memory context from memory_service ──
            memory_context = self._fetch_memory_context()

            recent = dict(activity.get("recent_metadata") or {})
            user_req = str(recent.get("user_request", {}).get("text", ""))[:500]
            agent_resp = str(recent.get("agent_work", {}).get("summary", ""))[:500]
            errors = int(activity.get("counts", {}).get("error_count", 0))
            uncertainty = int(activity.get("counts", {}).get("uncertainty_high_count", 0))

            prompt = (
                f"基于以下 VoidCube 系统状态和长期记忆，生成 {max_topics} 个值得探索的学习方向。\n\n"
                f"【最近用户请求】{user_req if user_req else '无'}\n"
                f"【最近 Agent 响应】{agent_resp if agent_resp else '无'}\n"
                f"【系统错误】{errors}  【高不确定性】{uncertainty}\n\n"
                f"【压缩记忆上下文 — 最近的活跃弧线和场景】\n"
                f"{memory_context if memory_context else '(暂无压缩记忆)'}\n\n"
                f"基于以上所有信息生成学习方向。不要泛泛而谈——"
                f"基于记忆中的实际问题、未解决的疑问、代码改进机会来生成。"
                f"输出JSON数组: [{{\"title\": \"...\", \"summary\": \"...\"}}]"
            )
            result = client.complete_json(
                system_prompt=(
                    "你是 VoidCube 的内生驱动器。你有权访问系统的压缩长期记忆。"
                    "基于记忆中的实际问题、架构讨论、代码改进机会和未解决的疑问，"
                    "生成有实质价值的学习方向——具体的、可操作的、基于真实上下文。"
                ),
                user_payload={"context": prompt},
                task="extractor.events",
            )
            if isinstance(result, list) and len(result) > 0:
                topics = []
                for item in result[:max_topics]:
                    if isinstance(item, dict):
                        title = str(item.get("title", "")).strip()
                        summary = str(item.get("summary", "")).strip()
                        if title:
                            topics.append({"title": title, "summary": summary or title})
                if topics:
                    return topics
            return []
        except Exception:
            return []

    def _fetch_memory_context(self) -> str:
        """Fetch recent compressed memory summaries from memory_service for LLM context."""
        try:
            import urllib.request, json as _json
            # Resolve memory service URL via gateway (same as ui_runtime does)
            memory_url = self._resolve_memory_url()
            if not memory_url:
                return ""
            req = _json.dumps({
                "memory_type": "arc", "limit": 5, "include_superseded": False,
            }).encode()
            resp = urllib.request.urlopen(
                urllib.request.Request(
                    f"{memory_url}/compressed/search",
                    data=req, headers={"Content-Type": "application/json"},
                ), timeout=3,
            )
            data = _json.loads(resp.read())
            results = data.get("results", [])
            if not results:
                return ""
            lines = []
            for r in results[:5]:
                lines.append(
                    f"- [{r.get('memory_type', '?')}] {r.get('title', '')}: "
                    f"{r.get('summary', '')[:200]}"
                )
            return "\n".join(lines)
        except Exception:
            return ""

    @staticmethod
    def _resolve_memory_url() -> str | None:
        """Resolve memory service URL via gateway service discovery."""
        try:
            import urllib.request, json as _json
            resp = urllib.request.urlopen(
                "http://127.0.0.1:6000/admin/services", timeout=2,
            )
            services = _json.loads(resp.read()).get("services", {})
            for svc in services.values():
                if svc.get("service_type") == "memory":
                    return svc.get("address")
        except Exception:
            pass
        return None

    def _decision_for(
        self,
        family: str,
        decisions_by_family: Dict[str, Any],
        decisions_by_governance: Dict[str, Any],
    ) -> Dict[str, Any]:
        if family in decisions_by_family:
            return dict(decisions_by_family[family] or {})
        governance = "self_evolution"
        if family in {"memory_maintenance", "self_learning", "user"}:
            governance = family
        return dict(decisions_by_governance.get(governance) or {})


def _stable_key_for_topic(topic: str) -> str:
    """Generate a stable dedup key from a learning topic string.

    Uses a short hash so that genuinely different topics get different keys,
    allowing multiple creativity candidates to coexist in the queue.
    """
    import hashlib
    normalized = topic.strip().lower()
    if not normalized:
        return "creativity:idle_learning:fallback"
    h = hashlib.md5(normalized.encode()).hexdigest()[:8]
    return f"creativity:idle_learning:{h}"
