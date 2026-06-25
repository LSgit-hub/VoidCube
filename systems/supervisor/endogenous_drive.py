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
        # Gateway exposes counts as both top-level fields (e.g. error_count,
        # uncertainty_high_count) and (in some snapshots) under a "counts"
        # sub-key.  Merge both so we don't double-count or miss signals.
        nested_counts = dict(activity.get("counts") or {})
        counts: Dict[str, Any] = dict(nested_counts)
        for _key in (
            "error_count",
            "recent_errors",
            "uncertainty_high_count",
            "high_uncertainty",
        ):
            value = activity.get(_key)
            if value is not None and _key not in counts:
                counts[_key] = value
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
        # Prefer the pre-decayed signal from evaluate_idle_window when it
        # is available, since that path applies a 4-hour half-life to keep
        # old errors from permanently producing truthfulness candidates.
        pre_decayed = idle_window.get("correction_signals")
        if pre_decayed is not None:
            try:
                correction_signals = max(0, int(pre_decayed))
            except (TypeError, ValueError):
                correction_signals = recent_errors + uncertainty_count
        else:
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
                        "correction_signals": correction_signals,
                        "signal_source": "evaluate_idle_window" if pre_decayed is not None else "raw_counts",
                    },
                )
            )

        active_sessions = int(activity.get("active_sessions") or 0)
        if active_sessions == 0 and self_learning_plan.get("eligible_for_planning"):
            # Three-tier fallback chain for learning topics, matching the
            # architectural baseline §3.4 "LLM 优先 + 启发式降级" pattern:
            #   Tier 1: LLM-generated topics from compressed memory context
            #   Tier 2: Recent compressed memories from Mem (local, no LLM)
            #   Tier 3: Mechanical extraction from activity metadata
            topics: list[dict] = []
            topic_source = "none"

            llm_topics = self._llm_generate_learning_topics(activity, max_topics=3)
            if llm_topics:
                topics = llm_topics
                topic_source = "llm"

            if not topics:
                mem_topics = self._mem_extract_learning_topics(activity, max_topics=3)
                if mem_topics:
                    topics = mem_topics
                    topic_source = "mem_compressed"

            if not topics:
                mechanical_topic = self._extract_learning_topic(activity)
                if mechanical_topic:
                    topics = [{"title": mechanical_topic, "summary": (
                        f"Use idle capacity to research '{mechanical_topic}' — the most recent "
                        f"user-discussed topic that may benefit from deeper investigation."
                    )}]
                    topic_source = "activity_metadata"

            generated_count = 0
            for topic in topics:
                topic_key = _stable_key_for_topic(topic["title"])
                if topic_key in existing_keys:
                    continue  # Skip duplicate topic
                title = f"Research: {topic['title']}"
                summary = topic.get("summary") or topic["title"]
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
                        utility=0.72 if topic_source == "llm" else 0.65,
                        evidence={
                            "active_sessions": active_sessions,
                            "trigger": "idle_capacity",
                            "learning_topic": topic["title"],
                            "topic_source": topic_source,
                            "llm_generated": topic_source == "llm",
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

            # Final fallback: completely static topic when even Tier 3 found
            # nothing.  This is the only path that yields a generic task and
            # exists so the creativity candidate is never silently dropped.
            if generated_count == 0:
                topic_key = "creativity:idle_learning:fallback"
                if topic_key not in existing_keys:
                    candidates.append(
                        EndogenousTaskCandidate(
                            stable_key=topic_key,
                            title="Explore one unresolved learning thread",
                            summary=(
                                "Use idle capacity to ask the agent for one evidence-producing "
                                "learning pass over unresolved recent topics."
                            ),
                            priority="normal",
                            governance_task_type="self_learning",
                            task_family="self_learning",
                            execution_kind=None,
                            value_tags=["creativity"],
                            utility=0.58,
                            evidence={
                                "active_sessions": active_sessions,
                                "trigger": "idle_capacity",
                                "learning_topic": "",
                                "topic_source": "static_fallback",
                                "llm_generated": False,
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

        if self_evolution_plan.get("eligible_for_planning"):
            learning_quality = self._calculate_learning_quality_score(idle_window)
            shell_slot_meta = self._get_shell_slot_meta(idle_window)
            if (learning_quality >= 60
                and shell_slot_meta
                and "body_improvement" not in existing_keys):

                improvement = self._generate_body_improvement_direction(
                    idle_window,
                    learning_quality,
                    shell_slot_meta,
                )
                if improvement:
                    task_key = f"body_improvement:{_stable_key_for_topic(improvement['title'])}"
                    if task_key not in existing_keys:
                        candidates.append(
                            EndogenousTaskCandidate(
                                stable_key=task_key,
                                title=f"Improve shell body: {improvement['title']}",
                                summary=improvement.get("summary", improvement["title"]),
                                priority="high" if learning_quality >= 80 else "normal",
                                governance_task_type="self_evolution",
                                task_family="body_upgrade",
                                execution_kind="body_improvement",
                                value_tags=["creativity", "continuity"],
                                utility=0.80 if learning_quality >= 80 else 0.70,
                                constraints={
                                    "execution_policy": "improve_shell_body",
                                    "target_slot": "shell",
                                    "target_slot_id": shell_slot_meta.slot_id,
                                    "worktree_path": shell_slot_meta.worktree_path,
                                    "must_commit": True,
                                    "evolution_boundary_check": True,
                                    "max_files_changed": 5,
                                    "editable_dirs": ["skills/", "tools/", "agent/", "prompts/"],
                                    "forbidden_patterns": [
                                        "**/credential*", "**/.env*", "systems/**",
                                    ],
                                },
                                evidence={
                                    "learning_quality_score": learning_quality,
                                    "shell_slot_id": shell_slot_meta.slot_id,
                                    "worktree_path": shell_slot_meta.worktree_path,
                                    "git_diff_summary": improvement.get("diff_summary", ""),
                                    "source": improvement.get("source", "fallback"),
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

        LLM credentials are resolved by ``memai.model_config.resolve_mem_llm_client`` —
        the canonical source of truth shared with ``MemoryService`` and the
        Tier1→Tier2 bridge.  Whatever the user configured via the CLI
        ``/api`` command (which writes to ``memory.llm.*``) is what runs
        here; there is no separate supervisor-side model config.

        Returns list of {"title": ..., "summary": ...} dicts.
        Falls back to empty list if LLM is unavailable.
        """
        try:
            from memai.model_config import resolve_mem_llm_client

            client, _ = resolve_mem_llm_client(role="default")
            if client is None:
                return []

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

    def _mem_extract_learning_topics(
        self, activity: Dict[str, Any], max_topics: int = 3
    ) -> List[Dict[str, str]]:
        """Tier-2 fallback: pull learning topics from Mem compressed memories.

        This is the local path that does NOT require an LLM API key — it
        reads `compressed_memories` rows (Arc / Scene / Epoch summaries) and
        turns each row's title + summary into a self-learning topic candidate.
        The architectural baseline §3.4 "LLM 优先 + 启发式降级" pattern
        applies here: when the LLM path is unavailable, structured compressed
        memory is still meaningful enough to drive a learning task.

        The HTTP call goes through the same gateway-resolved memory URL that
        `_fetch_memory_context` uses, keeping with baseline §4.2
        (gateway as the internal entry point).
        """
        try:
            import urllib.request, json as _json
            memory_url = self._resolve_memory_url()
            if not memory_url:
                return []
            req = _json.dumps({
                "memory_type": "arc",
                "limit": max_topics,
                "include_superseded": False,
            }).encode()
            resp = urllib.request.urlopen(
                urllib.request.Request(
                    f"{memory_url}/compressed/search",
                    data=req,
                    headers={"Content-Type": "application/json"},
                ),
                timeout=3,
            )
            data = _json.loads(resp.read())
            results = data.get("results", [])
        except Exception:
            return []

        topics: List[Dict[str, str]] = []
        for r in results:
            title = str(r.get("title", "")).strip()
            summary = str(r.get("summary", "")).strip()
            if not title:
                continue
            # Trim long titles but keep them human-readable
            if len(title) > 80:
                title = title[:77] + "..."
            topics.append({
                "title": title,
                "summary": (
                    f"Use idle capacity to revisit memory arc '{title}' — "
                    f"{summary[:240]}" if summary else
                    f"Use idle capacity to revisit memory arc '{title}' and "
                    f"check whether new evidence requires follow-up."
                ),
            })
            if len(topics) >= max_topics:
                break
        return topics

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

    def _calculate_learning_quality_score(self, idle_window: Dict[str, Any]) -> float:
        try:
            learning_tasks = idle_window.get("completed_learning_tasks", [])
            completed_count = len(learning_tasks)
            if completed_count == 0:
                return 0.0

            quality_sum = 0.0
            freshness_sum = 0.0
            now = None
            try:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
            except Exception:
                pass

            for task in learning_tasks:
                quality_sum += float(task.get("quality_score") or 0.5)
                if now and task.get("completed_at"):
                    try:
                        t = datetime.fromisoformat(str(task["completed_at"]))
                        if t.tzinfo is None:
                            t = t.replace(tzinfo=timezone.utc)
                        age_days = (now - t).days
                        freshness = max(0.0, 1.0 - age_days / 90.0)
                        freshness_sum += freshness
                    except Exception:
                        freshness_sum += 0.5
                else:
                    freshness_sum += 0.5

            avg_quality = quality_sum / completed_count
            avg_freshness = freshness_sum / completed_count
            score = avg_quality * 60 + avg_freshness * 40
            return max(0.0, min(100.0, score))
        except Exception:
            return 0.0

    def _get_shell_slot_meta(self, idle_window: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            shell_slot = idle_window.get("shell_slot")
            if shell_slot and isinstance(shell_slot, dict):
                return shell_slot
        except Exception:
            pass

        try:
            import urllib.request, json as _json
            memory_url = self._resolve_memory_url()
            if not memory_url:
                return None
            resp = urllib.request.urlopen(
                f"{memory_url}/body/shell/slot",
                timeout=3,
            )
            data = _json.loads(resp.read())
            if data.get("slot_id"):
                return data
        except Exception:
            pass

        return None

    def _generate_body_improvement_direction(
        self,
        idle_window: Dict[str, Any],
        learning_quality: float,
        shell_slot_meta: Dict[str, Any],
    ) -> Optional[Dict[str, str]]:
        activity = dict(idle_window.get("activity") or {})

        llm_direction = self._llm_generate_improvement_direction(
            activity,
            learning_quality,
            shell_slot_meta,
        )
        if llm_direction:
            llm_direction["source"] = "llm"
            return llm_direction

        history_direction = self._generate_improvement_from_history(
            idle_window,
            shell_slot_meta,
        )
        if history_direction:
            history_direction["source"] = "history"
            return history_direction

        git_direction = self._generate_improvement_from_git_diff(
            shell_slot_meta,
        )
        if git_direction:
            git_direction["source"] = "git_diff"
            return git_direction

        fallback_direction = {
            "title": "General code quality improvement",
            "summary": (
                "Apply recent learning findings to improve the shell body's code quality. "
                "Focus on fixing identified issues, improving documentation, and enhancing "
                "code maintainability within the allowed evolution boundaries."
            ),
            "diff_summary": "",
            "source": "fallback",
        }
        return fallback_direction

    def _llm_generate_improvement_direction(
        self,
        activity: Dict[str, Any],
        learning_quality: float,
        shell_slot_meta: Dict[str, Any],
    ) -> Optional[Dict[str, str]]:
        try:
            from memai.model_config import resolve_mem_llm_client
            client, _ = resolve_mem_llm_client(role="default")
            if client is None:
                return None

            memory_context = self._fetch_memory_context()
            recent = dict(activity.get("recent_metadata") or {})
            user_req = str(recent.get("user_request", {}).get("text", ""))[:500]
            agent_resp = str(recent.get("agent_work", {}).get("summary", ""))[:500]

            prompt = (
                f"基于以下信息，为替身 Agent 的代码改进生成一个具体方向。\n\n"
                f"【学习质量评分】{learning_quality:.1f}/100\n"
                f"【替身槽位】{shell_slot_meta.get('slot_id', '?')}\n"
                f"【替身工作树路径】{shell_slot_meta.get('worktree_path', '?')}\n"
                f"【最近用户请求】{user_req if user_req else '无'}\n"
                f"【最近 Agent 响应】{agent_resp if agent_resp else '无'}\n\n"
                f"【压缩记忆上下文】\n{memory_context if memory_context else '(暂无)'}\n\n"
                f"分析学习成果和记忆中的问题，提出一个具体的代码改进方向。"
                f"改进方向应该是：\n"
                f"- 基于实际学习成果\n"
                f"- 在允许的演化边界内（agent/, skills/, tools/, presets/）\n"
                f"- 可操作且有明确目标\n"
                f"输出JSON: {{\"title\": \"...\", \"summary\": \"...\", \"diff_summary\": \"...\"}}"
            )

            result = client.complete_json(
                system_prompt=(
                    "你是代码改进专家。基于学习成果和系统状态，"
                    "为替身 Agent 提出具体、可操作的代码改进方向。"
                    "只关注 agent/、skills/、tools/、presets/ 目录内的改进。"
                ),
                user_payload={"task": prompt},
                task="scholar.revision",
            )

            if isinstance(result, dict):
                title = str(result.get("title", "")).strip()
                summary = str(result.get("summary", "")).strip()
                if title:
                    return {
                        "title": title,
                        "summary": summary or title,
                        "diff_summary": str(result.get("diff_summary", "")),
                    }
        except Exception:
            pass
        return None

    def _generate_improvement_from_history(
        self,
        idle_window: Dict[str, Any],
        shell_slot_meta: Dict[str, Any],
    ) -> Optional[Dict[str, str]]:
        try:
            learning_tasks = idle_window.get("completed_learning_tasks", [])
            if not learning_tasks:
                return None

            recent_tasks = sorted(
                learning_tasks,
                key=lambda t: t.get("completed_at", ""),
                reverse=True,
            )[:3]

            topics = []
            for task in recent_tasks:
                title = str(task.get("title", "") or task.get("topic", ""))
                if title:
                    topics.append(title)

            if topics:
                return {
                    "title": "Apply recent learning: " + ", ".join(topics[:2]),
                    "summary": (
                        f"Apply recent learning findings to improve the shell body. "
                        f"Recent learning topics: {', '.join(topics)}. "
                        f"Focus on implementing improvements based on these research results."
                    ),
                    "diff_summary": "",
                }
        except Exception:
            pass
        return None

    def _generate_improvement_from_git_diff(
        self,
        shell_slot_meta: Dict[str, Any],
    ) -> Optional[Dict[str, str]]:
        try:
            worktree_path = shell_slot_meta.get("worktree_path")
            if not worktree_path:
                return None

            import subprocess
            result = subprocess.run(
                ["git", "status", "--short"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                changed_count = len(result.stdout.strip().splitlines())
                return {
                    "title": f"Review {changed_count} pending changes",
                    "summary": (
                        f"The shell body worktree has {changed_count} files with pending changes. "
                        f"Review these changes and apply appropriate improvements based on learning findings."
                    ),
                    "diff_summary": result.stdout[:500],
                }
        except Exception:
            pass
        return None


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
