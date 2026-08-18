"""Deterministic quality assessment for completed autonomous learning tasks."""

from __future__ import annotations

from typing import Any, Dict
from urllib.parse import urlparse


def assess_autonomous_learning_quality(
    task: Any,
    turn_observation: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Score observable research evidence; never trust a model-provided score."""

    observation = dict(turn_observation or {})
    if any(bool(observation.get(field)) for field in ("failed", "partial", "interrupted")):
        return {"score": 0.0, "signals": ["turn_not_completed"]}

    response = str(observation.get("response") or "").strip()
    if not response:
        return {"score": 0.0, "signals": ["missing_conclusion"]}

    metadata = dict(getattr(task, "metadata", {}) or {})
    evidence = dict(getattr(task, "evidence", {}) or {})
    branch = str(
        metadata.get("learning_branch") or evidence.get("learning_branch") or ""
    ).strip().lower()
    tools_used = {
        str(name).strip()
        for name in list(observation.get("tools_used") or [])
        if str(name).strip()
    }
    source_urls = [
        str(value).strip()
        for value in list(observation.get("source_urls") or [])
        if _is_web_url(value)
    ]

    score = 0.15
    signals = ["completed_turn"]
    if len(response) >= 120:
        score += 0.15
        signals.append("conclusion_minimum_length")
    if len(response) >= 400:
        score += 0.10
        signals.append("conclusion_substantive_length")
    if len(response) >= 1000:
        score += 0.05
        signals.append("conclusion_detailed_length")
    if any(
        token in response.casefold()
        for token in ("evidence", "source", "uncertainty", "证据", "来源", "不确定")
    ):
        score += 0.05
        signals.append("evidence_or_uncertainty_disclosed")

    if branch == "exploratory":
        if "web_search" in tools_used:
            score += 0.20
            signals.append("web_search_recorded")
        if source_urls:
            score += 0.15 + min(len(source_urls) - 1, 2) * 0.05
            signals.append(f"web_sources:{min(len(source_urls), 3)}")
        if "web_extract" in tools_used:
            score += 0.10
            signals.append("web_extract_recorded")
    else:
        local_tools = {"read_file", "search_files"} & tools_used
        if local_tools:
            score += 0.25
            signals.append("local_inspection_recorded")
        if len(local_tools) == 2:
            score += 0.10
            signals.append("multiple_local_inspection_methods")
        if source_urls:
            score += 0.15
            signals.append("web_sources:1+")

    return {"score": round(max(0.0, min(1.0, score)), 4), "signals": signals}


def _is_web_url(value: Any) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


__all__ = ["assess_autonomous_learning_quality"]
