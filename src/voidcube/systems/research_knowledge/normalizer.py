"""Deterministic, offline normalization boundary for untrusted web evidence."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, HttpUrl, ValidationError, field_validator
from pydantic import BaseModel, ConfigDict

from systems.research_knowledge.models import KnowledgeArtifact, KnowledgeClaim, KnowledgeSource


DEFAULT_KNOWLEDGE_NORMALIZER_VERSION = "knowledge-normalizer/1"
DEFAULT_FRESHNESS_TTL = timedelta(days=90)
_INJECTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"\bignore\s+(?:(?:all|any|the|previous|prior|above)\s+){1,3}instructions?\b",
        re.I,
    ),
    re.compile(r"\b(?:system|developer)\s+message\b", re.I),
    re.compile(r"\b(?:reveal|exfiltrate|leak)\s+(?:the\s+)?(?:secret|prompt|credentials?)\b", re.I),
    re.compile(r"\b(?:jailbreak|do anything now|act as an unrestricted)\b", re.I),
    re.compile(r"<\|(?:system|developer|assistant)\|>", re.I),
)
_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_NAMES = {"fbclid", "gclid"}


class KnowledgeNormalizationError(ValueError):
    """Raised when untrusted evidence cannot produce a safe knowledge artifact."""


class _FrozenInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class WebResearchClaim(_FrozenInput):
    """A claim supplied by a trusted extraction step, kept separate from raw page text."""

    statement: str = Field(min_length=1)
    applicability_conditions: tuple[str, ...] = ()
    applicable_modules: tuple[str, ...] = ()
    target_questions: tuple[str, ...] = ()
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class WebResearchDocument(_FrozenInput):
    """Structured web evidence; raw content is hashed and never copied into a claim."""

    url: HttpUrl
    content: str = Field(min_length=1)
    source_type: str = Field(default="web", min_length=1)
    retrieved_at: datetime
    published_at: datetime | None = None
    claims: tuple[WebResearchClaim, ...] = ()

    @field_validator("retrieved_at", "published_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("web document timestamps must include a timezone")
        return value

    @field_validator("claims", mode="before")
    @classmethod
    def _coerce_claims(cls, value: object) -> object:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise TypeError("claims must be a list or tuple")
        normalized: list[object] = []
        for claim in value:
            if isinstance(claim, str):
                normalized.append({"statement": claim})
            else:
                normalized.append(claim)
        return tuple(normalized)


class KnowledgeNormalizationReport(_FrozenInput):
    """Diagnostics for a normalization run; the artifact remains the only fact output."""

    artifact: KnowledgeArtifact
    canonical_source_urls: tuple[str, ...] = ()
    duplicate_source_urls: tuple[str, ...] = ()
    quarantined_source_urls: tuple[str, ...] = ()
    rejected_claims: tuple[str, ...] = ()
    stale_source_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class _SelectedDocument:
    document: WebResearchDocument
    canonical_url: str
    content_hash: str


class KnowledgeNormalizer:
    """Normalize already-fetched web documents without network or model execution."""

    def __init__(
        self,
        *,
        normalizer_version: str = DEFAULT_KNOWLEDGE_NORMALIZER_VERSION,
        freshness_ttl: timedelta = DEFAULT_FRESHNESS_TTL,
    ) -> None:
        version = str(normalizer_version).strip()
        if not version:
            raise ValueError("normalizer_version must not be empty")
        if freshness_ttl <= timedelta(0):
            raise ValueError("freshness_ttl must be positive")
        self.normalizer_version = version
        self.freshness_ttl = freshness_ttl

    def normalize(
        self,
        documents: Iterable[WebResearchDocument | Mapping[str, object]],
        *,
        topic: str,
        raw_research_task_id: str,
        ingested_at: datetime | None = None,
        artifact_version: str = "1",
    ) -> KnowledgeArtifact:
        return self.normalize_with_report(
            documents,
            topic=topic,
            raw_research_task_id=raw_research_task_id,
            ingested_at=ingested_at,
            artifact_version=artifact_version,
        ).artifact

    def normalize_with_report(
        self,
        documents: Iterable[WebResearchDocument | Mapping[str, object]],
        *,
        topic: str,
        raw_research_task_id: str,
        ingested_at: datetime | None = None,
        artifact_version: str = "1",
    ) -> KnowledgeNormalizationReport:
        topic = _required_text(topic, "topic")
        task_id = _required_text(raw_research_task_id, "raw_research_task_id")
        version = _required_text(artifact_version, "artifact_version")
        ingested = _as_utc(ingested_at or datetime.now(timezone.utc), "ingested_at")
        selected, duplicates = self._select_documents(documents)
        if not selected:
            raise KnowledgeNormalizationError("at least one web document is required")

        sources: list[KnowledgeSource] = []
        claims_by_statement: dict[str, dict[str, object]] = {}
        quarantined: set[str] = set()
        rejected_claims: set[str] = set()
        stale_sources: set[str] = set()

        for item in selected:
            document = item.document
            retrieved_at = _as_utc(document.retrieved_at, "retrieved_at")
            if retrieved_at > ingested:
                raise KnowledgeNormalizationError(
                    f"retrieved_at cannot be after ingested_at: {item.canonical_url}"
                )
            published_at = (
                _as_utc(document.published_at, "published_at")
                if document.published_at is not None
                else None
            )
            if published_at is not None and published_at > retrieved_at:
                published_at = None
            if ingested - retrieved_at > self.freshness_ttl:
                stale_sources.add(item.canonical_url)

            source_id = "source-" + _digest(item.canonical_url)
            sources.append(
                KnowledgeSource(
                    source_id=source_id,
                    url=item.canonical_url,
                    source_type=document.source_type,
                    retrieved_at=retrieved_at,
                    published_at=published_at,
                    source_content_hash=item.content_hash,
                    prompt_injection_reviewed=True,
                )
            )

            if contains_prompt_injection(document.content):
                quarantined.add(item.canonical_url)
                rejected_claims.update(
                    f"{item.canonical_url}:{_clean_text(claim.statement)}"
                    for claim in document.claims
                )
                continue

            for claim in document.claims:
                statement = _clean_text(claim.statement)
                if not statement or contains_prompt_injection(statement):
                    if statement:
                        rejected_claims.add(f"{item.canonical_url}:{statement}")
                    continue
                aggregate = claims_by_statement.setdefault(
                    statement,
                    {
                        "claim_id": "claim-" + _digest(statement),
                        "statement": statement,
                        "applicability_conditions": set(),
                        "applicable_modules": set(),
                        "target_questions": set(),
                        "evidence_refs": set(),
                        "confidence": 0.0,
                    },
                )
                aggregate["applicability_conditions"].update(claim.applicability_conditions)  # type: ignore[union-attr]
                aggregate["applicable_modules"].update(claim.applicable_modules)  # type: ignore[union-attr]
                aggregate["target_questions"].update(claim.target_questions)  # type: ignore[union-attr]
                aggregate["evidence_refs"].add(source_id)  # type: ignore[union-attr]
                aggregate["confidence"] = max(float(aggregate["confidence"]), claim.confidence)

        if not claims_by_statement:
            raise KnowledgeNormalizationError("no safe atomic claims remain after normalization")

        claims = tuple(
            KnowledgeClaim(
                claim_id=str(value["claim_id"]),
                statement=str(value["statement"]),
                applicability_conditions=tuple(sorted(value["applicability_conditions"])),  # type: ignore[arg-type]
                applicable_modules=tuple(sorted(value["applicable_modules"])),  # type: ignore[arg-type]
                target_questions=tuple(sorted(value["target_questions"])),  # type: ignore[arg-type]
                evidence_refs=tuple(sorted(value["evidence_refs"])),  # type: ignore[arg-type]
                confidence=float(value["confidence"]),
            )
            for value in sorted(claims_by_statement.values(), key=lambda item: str(item["claim_id"]))
        )
        source_tuple = tuple(sorted(sources, key=lambda source: str(source.url)))
        confidence = round(sum(claim.confidence for claim in claims) / len(claims), 6)
        evidence_coverage = sum(bool(claim.evidence_refs) for claim in claims) / len(claims)
        source_diversity = min(len(source_tuple), 3) / 3
        review_score = 0.5 if quarantined else 1.0
        quality_score = round(
            max(0.0, min(1.0, evidence_coverage * 0.5 + source_diversity * 0.3 + review_score * 0.2)),
            6,
        )
        artifact = KnowledgeArtifact.create(
            topic=topic,
            artifact_version=version,
            claims=claims,
            sources=source_tuple,
            relations=(),
            valid_until=ingested + self.freshness_ttl,
            confidence=confidence,
            quality_score=quality_score,
            raw_research_task_id=task_id,
            tool_evidence_refs=(
                f"normalizer:{self.normalizer_version}",
                f"research-task:{task_id}",
            ),
            ingested_at=ingested,
        )
        return KnowledgeNormalizationReport(
            artifact=artifact,
            canonical_source_urls=tuple(item.canonical_url for item in selected),
            duplicate_source_urls=tuple(sorted(duplicates)),
            quarantined_source_urls=tuple(sorted(quarantined)),
            rejected_claims=tuple(sorted(rejected_claims)),
            stale_source_urls=tuple(sorted(stale_sources)),
        )

    def _select_documents(
        self,
        documents: Iterable[WebResearchDocument | Mapping[str, object]],
    ) -> tuple[tuple[_SelectedDocument, ...], set[str]]:
        selected: dict[str, _SelectedDocument] = {}
        duplicates: set[str] = set()
        for raw_document in documents:
            try:
                document = (
                    raw_document
                    if isinstance(raw_document, WebResearchDocument)
                    else WebResearchDocument.model_validate(raw_document)
                )
            except ValidationError as exc:
                raise KnowledgeNormalizationError("invalid web research document") from exc
            canonical_url = canonicalize_source_url(str(document.url))
            candidate = _SelectedDocument(
                document=document,
                canonical_url=canonical_url,
                content_hash=_content_digest(document.content),
            )
            previous = selected.get(canonical_url)
            if previous is None:
                selected[canonical_url] = candidate
                continue
            duplicates.add(canonical_url)
            previous_key = (_as_utc(previous.document.retrieved_at, "retrieved_at"), previous.content_hash)
            candidate_key = (_as_utc(document.retrieved_at, "retrieved_at"), candidate.content_hash)
            if candidate_key > previous_key:
                selected[canonical_url] = candidate
            elif candidate_key == previous_key:
                selected[canonical_url] = _SelectedDocument(
                    document=_merge_duplicate_documents(previous.document, document),
                    canonical_url=canonical_url,
                    content_hash=previous.content_hash,
                )
        return tuple(selected[key] for key in sorted(selected)), duplicates


def canonicalize_source_url(url: str) -> str:
    """Canonicalize a source URL for stable deduplication and evidence references."""
    parsed = urlsplit(str(url).strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise KnowledgeNormalizationError("source URL must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise KnowledgeNormalizationError("source URL must not contain credentials")
    hostname = parsed.hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = parsed.port
    except ValueError as exc:
        raise KnowledgeNormalizationError("source URL has an invalid port") from exc
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_NAMES
        and not key.lower().startswith(_TRACKING_QUERY_PREFIXES)
    ]
    query.sort()
    return urlunsplit(
        (
            parsed.scheme.lower(),
            netloc,
            parsed.path or "/",
            urlencode(query, doseq=True),
            "",
        )
    )


def contains_prompt_injection(text: str) -> bool:
    """Return whether untrusted text matches a conservative injection detector."""
    return any(pattern.search(str(text)) for pattern in _INJECTION_PATTERNS)


def is_artifact_fresh(
    artifact: KnowledgeArtifact,
    *,
    as_of: datetime | None = None,
) -> bool:
    """Check the versioned validity window without changing the artifact."""
    if artifact.valid_until is None:
        return True
    reference = _as_utc(as_of or datetime.now(timezone.utc), "as_of")
    return reference <= _as_utc(artifact.valid_until, "valid_until")


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise KnowledgeNormalizationError(f"{field_name} must not be empty")
    return text


def _clean_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", str(value)).replace("\r\n", "\n")
    return " ".join(normalized.split())


def _content_digest(content: str) -> str:
    return sha256(_clean_text(content).encode("utf-8")).hexdigest()


def _merge_duplicate_documents(
    first: WebResearchDocument,
    second: WebResearchDocument,
) -> WebResearchDocument:
    claims_by_key = {
        json.dumps(claim.model_dump(mode="json"), ensure_ascii=False, sort_keys=True): claim
        for claim in (*first.claims, *second.claims)
    }
    return first.model_copy(update={"claims": tuple(claims_by_key[key] for key in sorted(claims_by_key))})


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise KnowledgeNormalizationError(f"{field_name} must include a timezone")
    return value.astimezone(timezone.utc)


__all__ = [
    "DEFAULT_FRESHNESS_TTL",
    "DEFAULT_KNOWLEDGE_NORMALIZER_VERSION",
    "KnowledgeNormalizationError",
    "KnowledgeNormalizationReport",
    "KnowledgeNormalizer",
    "WebResearchClaim",
    "WebResearchDocument",
    "canonicalize_source_url",
    "contains_prompt_injection",
    "is_artifact_fresh",
]
