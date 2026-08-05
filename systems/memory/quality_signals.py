"""Reusable evidence-fidelity signals for memory compression."""

from __future__ import annotations

import re


_LATIN_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:/-]{2,}", re.IGNORECASE)
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_IDENTIFIER_RE = re.compile(
    r"(?<![\w])(?:https?://[^\s]+|[a-z][a-z0-9._:/-]*\d[a-z0-9._:/-]*|"
    r"\d+(?:\.\d+)+(?:[a-z0-9._-]*)?|\d{2,})(?![\w])",
    re.IGNORECASE,
)
_STOP_WORDS = {
    "about", "after", "also", "and", "are", "been", "before", "being",
    "from", "into", "that", "the", "then", "this", "was", "were", "with",
}
_NEGATION_MARKERS = (
    "must not", "do not", "does not", "did not", "should not", "cannot",
    "can't", "never", "forbid", "forbidden", "prohibit", "prohibited", "avoid",
    "不得", "不要", "不能", "不允许", "禁止", "严禁", "从未", "没有", "未能",
)


def quality_tokens(value: object) -> set[str]:
    text = str(value or "").lower()
    tokens = {token for token in _LATIN_TOKEN_RE.findall(text) if token not in _STOP_WORDS}
    for run in _CJK_RUN_RE.findall(text):
        if len(run) == 1:
            tokens.add(run)
        else:
            tokens.update(run[index:index + 2] for index in range(len(run) - 1))
    return tokens


def source_support(summary: str, source_text: str) -> float:
    summary_tokens = quality_tokens(summary)
    if not summary_tokens:
        return 0.0
    return len(summary_tokens & quality_tokens(source_text)) / len(summary_tokens)


def identifiers(value: object) -> set[str]:
    return {
        match.rstrip(".,;:!?)]}").lower()
        for match in _IDENTIFIER_RE.findall(str(value or ""))
    }


def has_explicit_negation(value: object) -> bool:
    normalized = " ".join(str(value or "").lower().split())
    return any(marker in normalized for marker in _NEGATION_MARKERS)
