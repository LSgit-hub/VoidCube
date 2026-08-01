"""Pure mapping from learning evidence to safe shell-body structure nodes."""

from __future__ import annotations

from datetime import datetime, timezone
import fnmatch
import re
from typing import Any, Dict, List, Optional

from systems.evolution_boundary import (
    AGENT_EVOLUTION_ALLOWED_FILES,
    AGENT_EVOLUTION_ALLOWED_PATHS,
    classify_agent_evolution_changes,
    normalize_repo_path,
)
from systems.supervisor.endogenous_candidate_pipeline import clamp01
from systems.supervisor.endogenous_learning import stable_learning_topic_key


_BODY_STRUCTURE_PATH_RE = re.compile(
    r"(?<![\w.-])((?:(?:agent|tools|skills|presets)/"
    r"[A-Za-z0-9_.\-/]+|run_agent\.py))"
)
_BODY_STRUCTURE_DOMAIN_TARGETS: tuple[
    tuple[str, tuple[str, ...], tuple[str, ...]],
    ...,
] = (
    (
        "prompt_context",
        ("prompt", "context", "reasoning", "提示词", "上下文", "推理"),
        ("agent/prompt_builder.py", "agent/context_engine.py", "agent/context_compressor.py"),
    ),
    (
        "stream_display",
        ("stream", "display", "render", "输出", "展示", "流式"),
        ("agent/stream_handler.py", "agent/display.py", "agent/subagent_display.py"),
    ),
    (
        "memory_access",
        ("memory", "recall", "记忆", "召回"),
        ("agent/memory_manager.py", "agent/memory_provider.py", "systems/memory/recall.py"),
    ),
    (
        "model_routing",
        ("model", "provider", "routing", "模型", "路由", "供应商"),
        ("agent/smart_model_routing.py", "agent/model_metadata.py", "agent/auxiliary_client.py"),
    ),
    (
        "tool_execution",
        ("tool", "terminal", "scheduler", "工具", "终端", "调度"),
        ("agent/tool_scheduler.py", "tools/registry.py", "tools/terminal_tool.py"),
    ),
    (
        "delegation",
        ("delegate", "subagent", "multi-agent", "委派", "子代理", "多代理"),
        ("tools/delegate_tool.py", "tools/mixture_of_agents_tool.py", "agent/subagent_display.py"),
    ),
    (
        "skills",
        ("skill", "skills", "技能"),
        ("agent/skill_utils.py", "agent/skill_commands.py", "tools/skills_tool.py"),
    ),
    (
        "error_resilience",
        ("error", "retry", "rate limit", "错误", "重试", "限流"),
        ("agent/error_classifier.py", "agent/retry_utils.py", "agent/rate_limit_tracker.py"),
    ),
    (
        "security",
        ("security", "redact", "credential", "安全", "脱敏", "凭证"),
        ("agent/redact.py", "agent/message_sanitizer.py", "tools/approval.py"),
    ),
    (
        "browser_web",
        ("browser", "web", "crawl", "浏览器", "网页", "抓取"),
        ("tools/browser_tool.py", "tools/web_tools.py", "tools/web_tools_local.py"),
    ),
    (
        "file_operations",
        ("file", "path", "filesystem", "文件", "路径"),
        ("tools/file_tools.py", "tools/file_operations.py", "tools/path_security.py"),
    ),
)


def build_body_structure_mapping(
    *,
    completed_learning_tasks: List[Dict[str, Any]],
    shell_slot_id: str,
    shell_worktree: str,
    policy: Dict[str, Any],
    learning_quality_score: float,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    if not shell_slot_id or not shell_worktree:
        return {"available": False, "reason": "shell_slot_unavailable"}
    if not completed_learning_tasks:
        return {"available": False, "reason": "learning_evidence_unavailable"}

    editable_roots = canonical_body_editable_roots(policy)
    if not editable_roots:
        return {"available": False, "reason": "no_canonical_editable_roots"}
    max_files = max(1, min(5, int(policy.get("body_improvement_max_files") or 5)))
    forbidden_patterns = [
        str(pattern).strip()
        for pattern in list(policy.get("body_improvement_forbidden_patterns") or [])
        if str(pattern).strip()
    ]
    current_time = now or datetime.now(timezone.utc)
    ranked_learning: List[tuple[float, Dict[str, Any]]] = []
    for task in completed_learning_tasks:
        freshness = learning_evidence_freshness(
            task.get("completed_at"),
            now=current_time,
        )
        if freshness <= 0.0:
            continue
        try:
            quality = float(task.get("quality_score"))
        except (TypeError, ValueError):
            quality = 0.5
        if quality > 1.0:
            quality /= 100.0
        relevance = clamp01(clamp01(quality) * 0.65 + freshness * 0.35)
        ranked_learning.append((relevance, task))
    ranked_learning.sort(key=lambda item: item[0], reverse=True)

    target_paths: List[str] = []
    structure_domains: List[str] = []
    learning_refs: List[Dict[str, Any]] = []
    evidence_summary: List[str] = []
    for relevance, task in ranked_learning[:5]:
        learning_task_id = str(task.get("task_id") or "").strip()
        if not learning_task_id:
            continue
        learning_text = "\n".join(
            part
            for part in [
                str(task.get("title") or ""),
                str(task.get("summary") or ""),
                str(task.get("conclusion") or ""),
                *[str(item) for item in list(task.get("evidence_summary") or [])],
            ]
            if part.strip()
        )
        task_targets, task_domains = _map_learning_text(
            learning_text,
            editable_roots=editable_roots,
            forbidden_patterns=forbidden_patterns,
        )
        for domain in task_domains:
            if domain not in structure_domains:
                structure_domains.append(domain)
        if not task_targets:
            continue
        for path in task_targets:
            if path not in target_paths and len(target_paths) < max_files:
                target_paths.append(path)
        learning_refs.append(
            {
                "mem_id": learning_task_id,
                "timestamp": str(task.get("completed_at") or ""),
                "relevance": round(relevance, 4),
                "title": str(task.get("title") or "")[:200],
                "target_paths": task_targets[:max_files],
            }
        )
        conclusion = str(task.get("conclusion") or task.get("summary") or "").strip()
        if conclusion:
            evidence_summary.append(conclusion[:400])
        if len(target_paths) >= max_files:
            break

    if not target_paths or not learning_refs:
        return {
            "available": False,
            "reason": "learning_evidence_has_no_safe_structure_mapping",
            "learning_quality_score": round(learning_quality_score, 4),
            "editable_roots": editable_roots,
        }

    mapping_key = stable_learning_topic_key(
        "|".join(
            [shell_slot_id, *target_paths, *[ref["mem_id"] for ref in learning_refs]]
        )
    ).rsplit(":", 1)[-1]
    return {
        "available": True,
        "mapping_key": mapping_key,
        "mapping_source": "learning_evidence_structure_projection_v1",
        "target_slot_id": shell_slot_id,
        "worktree_path": shell_worktree,
        "target_paths": target_paths,
        "structure_domains": structure_domains[:6],
        "editable_dirs": editable_roots,
        "forbidden_patterns": forbidden_patterns,
        "max_files_changed": max_files,
        "learning_quality_score": round(learning_quality_score, 4),
        "learning_refs": learning_refs,
        "evidence_summary": evidence_summary[:5],
    }


def canonical_body_editable_roots(policy: Dict[str, Any]) -> List[str]:
    configured = policy.get("body_improvement_editable_dirs")
    raw_roots = list(configured or AGENT_EVOLUTION_ALLOWED_PATHS)
    canonical_roots = [normalize_repo_path(path) for path in AGENT_EVOLUTION_ALLOWED_PATHS]
    canonical_files = {normalize_repo_path(path) for path in AGENT_EVOLUTION_ALLOWED_FILES}
    roots: List[str] = []
    for raw_root in raw_roots:
        root = normalize_repo_path(str(raw_root))
        if not root:
            continue
        if root in canonical_files:
            normalized = root
        else:
            normalized = root.rstrip("/") + "/"
            if not any(
                normalized == canonical or normalized.startswith(canonical)
                for canonical in canonical_roots
            ):
                continue
        if normalized not in roots:
            roots.append(normalized)
    return roots


def learning_evidence_freshness(
    completed_at: Any,
    *,
    now: Optional[datetime] = None,
) -> float:
    text = str(completed_at or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_days = max(
            0.0,
            ((now or datetime.now(timezone.utc)) - parsed).total_seconds() / 86400.0,
        )
        return max(0.0, 1.0 - age_days / 90.0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _map_learning_text(
    learning_text: str,
    *,
    editable_roots: List[str],
    forbidden_patterns: List[str],
) -> tuple[List[str], List[str]]:
    normalized_text = learning_text.replace("\\", "/")
    task_targets: List[str] = []
    domains: List[str] = []
    for match in _BODY_STRUCTURE_PATH_RE.findall(normalized_text):
        path = normalize_repo_path(match).rstrip(".,:;)]}")
        if path_within_body_editable_roots(path, editable_roots, forbidden_patterns):
            task_targets.append(path)
            if "explicit_code_reference" not in domains:
                domains.append("explicit_code_reference")

    if not task_targets:
        lowered = learning_text.lower()
        for domain, keywords, domain_targets in _BODY_STRUCTURE_DOMAIN_TARGETS:
            if not any(body_structure_keyword_matches(lowered, keyword) for keyword in keywords):
                continue
            added_for_domain = False
            for path in domain_targets:
                if path_within_body_editable_roots(path, editable_roots, forbidden_patterns):
                    task_targets.append(path)
                    added_for_domain = True
            if added_for_domain and domain not in domains:
                domains.append(domain)
    return list(dict.fromkeys(task_targets)), domains


def path_within_body_editable_roots(
    path: str,
    editable_roots: List[str],
    forbidden_patterns: List[str],
) -> bool:
    normalized = normalize_repo_path(path)
    if not normalized or not classify_agent_evolution_changes([normalized]).ok:
        return False
    if any(
        fnmatch.fnmatch(normalized, str(pattern).replace("\\", "/"))
        for pattern in forbidden_patterns
        if str(pattern).strip()
    ):
        return False
    return any(
        normalized == root.rstrip("/")
        if not root.endswith("/")
        else normalized.startswith(root)
        for root in editable_roots
    )


def body_structure_keyword_matches(text: str, keyword: str) -> bool:
    if keyword.isascii():
        return bool(
            re.search(rf"(?<![a-z0-9_]){re.escape(keyword)}(?![a-z0-9_])", text)
        )
    return keyword in text
