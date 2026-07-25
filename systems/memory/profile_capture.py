"""Conservative, deterministic capture of explicit user profile facts."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from memai.schema import CertaintyState, MemoryKind, ProfileMemory


_EXPLICIT_MEMORY_MARKERS = (
    "请记住",
    "记住这一点",
    "以后记得",
    "please remember",
    "remember that",
    "remember this",
)
_TEMPORARY_MARKERS = (
    "这次",
    "本次",
    "暂时",
    "今天",
    "当前任务",
    "这一轮",
    "for this task",
    "this time",
    "temporarily",
    "today",
)
_UNCERTAINTY_MARKERS = (
    "也许",
    "可能",
    "大概",
    "不确定",
    "猜测",
    "maybe",
    "might",
    "probably",
    "not sure",
)
_SENSITIVE_MARKERS = (
    "api key",
    "api_key",
    "password",
    "passwd",
    "access token",
    "refresh token",
    "private key",
    "authorization:",
    "密码",
    "密钥",
    "令牌",
    "私钥",
    "[redacted",
)
_REVOKE_ALL_PATTERNS = (
    "忘掉关于我的所有信息",
    "删除我的所有个人信息",
    "清除我的所有画像",
    "forget everything about me",
    "delete all my profile information",
)
_PREDICATE_ALIASES = {
    "preferred_name": ("名字", "姓名", "称呼", "叫法", "name", "call me"),
    "preferred_language": (
        "语言偏好",
        "回复语言",
        "交流语言",
        "language preference",
        "preferred language",
    ),
    "container_runtime": ("容器运行时", "container runtime"),
    "editor": ("编辑器", "editor"),
    "package_manager": ("包管理器", "package manager"),
    "shell": ("shell", "终端"),
    "python_version": ("python版本", "python 版本", "python version"),
    "response_style": ("回答风格", "回复风格", "response style"),
    "timezone": ("时区", "timezone", "time zone"),
    "location": ("居住地", "住址", "location", "where i live"),
    "occupation": ("职业", "工作", "occupation", "profession", "job"),
    "allergy": ("过敏", "allergy", "allergies"),
}
ALL_PROFILE_PREDICATES = tuple(
    (*_PREDICATE_ALIASES.keys(), "long_term_preference")
)
_VALUE_TRIM = " \t\r\n,，。.!！?？;；:：'\"`"
_TRAILING_POLITENESS_RE = re.compile(
    r"(?:即可|就好|谢谢|please|from now on|in future)$", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class ProfileCapture:
    profiles: tuple[ProfileMemory, ...] = ()
    revoke_predicates: tuple[str, ...] = ()
    explicit_signal: bool = False

    @property
    def action(self) -> str:
        if self.revoke_predicates:
            return "revoke"
        if self.profiles:
            return "upsert"
        return "none"


def capture_explicit_user_profile(
    text: str,
    *,
    turn_id: str,
    timestamp: datetime,
) -> ProfileCapture:
    """Extract only explicit, stable first-person facts from one user turn."""
    normalized = unicodedata.normalize("NFKC", str(text or "")).strip()
    lowered = normalized.casefold()
    if not normalized or _contains_sensitive_content(lowered):
        return ProfileCapture()

    explicit_signal = any(marker in lowered for marker in _EXPLICIT_MEMORY_MARKERS)
    revoked = _extract_revocations(lowered)
    if revoked:
        return ProfileCapture(
            revoke_predicates=tuple(revoked),
            explicit_signal=True,
        )

    profiles: list[ProfileMemory] = []
    seen: set[str] = set()
    for clause in _split_clauses(normalized):
        clause_lower = clause.casefold()
        clause_explicit = explicit_signal or any(
            marker in clause_lower for marker in _EXPLICIT_MEMORY_MARKERS
        )
        if any(marker in clause_lower for marker in _TEMPORARY_MARKERS):
            continue
        if any(marker in clause_lower for marker in _UNCERTAINTY_MARKERS):
            continue
        extracted = _extract_clause(clause, explicit=clause_explicit)
        if extracted is None or extracted.predicate in seen:
            continue
        seen.add(extracted.predicate)
        profiles.append(
            ProfileMemory.create(
                memory_kind=extracted.memory_kind,
                subject="user",
                predicate=extracted.predicate,
                value=extracted.value,
                summary=extracted.summary,
                confidence=(0.99 if clause_explicit else 0.95),
                certainty_state=CertaintyState.CONFIRMED,
                valid_from=timestamp,
                evidence_refs=[f"turn:{turn_id}", "signal:user_explicit_profile"],
                source_turns=[turn_id],
            )
        )
    return ProfileCapture(
        profiles=tuple(profiles),
        explicit_signal=explicit_signal,
    )


@dataclass(frozen=True, slots=True)
class _ExtractedProfile:
    memory_kind: MemoryKind
    predicate: str
    value: str
    summary: str


def _extract_clause(clause: str, *, explicit: bool) -> _ExtractedProfile | None:
    value = _match_value(
        clause,
        (
            r"(?:请叫我|以后叫我|我的名字(?:是|叫)|我叫)\s*([^,，。.!！?？;；]{1,40})",
            r"(?:my name is|please call me)\s+([^,.;!?]{1,40})",
            r"^call me\s+([A-Za-z][A-Za-z0-9 _-]{0,39})$",
        ),
    )
    if value and _valid_name(value):
        return _fact("preferred_name", value, f"用户明确要求称呼为 {value}。")

    value = _match_value(
        clause,
        (
            r"(?:我(?:(?:确认)?仍然)?(?:偏好|喜欢|希望)(?:使用|用)?|请(?:一直)?用)\s*(中文|英文|英语|简体中文|繁体中文)(?:交流|回答|回复|沟通)?",
            r"(?:i prefer|please (?:always )?(?:reply|respond|communicate) in)\s+(chinese|english)",
        ),
    )
    if value:
        canonical = _canonical_language(value)
        return _preference(
            "preferred_language", canonical, f"用户偏好的交流语言是 {canonical}。"
        )

    known_preference_patterns = (
        (
            "container_runtime",
            (
                r"我(?:现在)?(?:偏好使用|偏好|喜欢用|改用|使用)\s*([A-Za-z0-9_.+-]{2,30})\s*作为容器运行时",
                r"i (?:now )?(?:prefer|use|switched to)\s+([A-Za-z0-9_.+-]{2,30})\s+as (?:the )?container runtime",
            ),
            "容器运行时",
        ),
        (
            "editor",
            (
                r"我(?:现在)?(?:偏好使用|偏好|喜欢用|改用|使用)\s*([^,，。.!！?？;；]{1,40})\s*作为编辑器",
                r"i (?:now )?(?:prefer|use|switched to)\s+([^,.;!?]{1,40})\s+as (?:my )?editor",
            ),
            "编辑器",
        ),
        (
            "package_manager",
            (
                r"我(?:现在)?(?:偏好使用|偏好|喜欢用|改用|使用)\s*([A-Za-z0-9_.+-]{1,30})\s*作为包管理器",
                r"i (?:now )?(?:prefer|use|switched to)\s+([A-Za-z0-9_.+-]{1,30})\s+as (?:the )?package manager",
            ),
            "包管理器",
        ),
        (
            "shell",
            (
                r"我(?:现在)?(?:偏好使用|偏好|喜欢用|改用|使用)\s*([A-Za-z0-9_.+-]{1,30})\s*作为(?:shell|终端)",
                r"i (?:now )?(?:prefer|use|switched to)\s+([A-Za-z0-9_.+-]{1,30})\s+as (?:my )?shell",
            ),
            "Shell",
        ),
        (
            "python_version",
            (
                r"我(?:的项目)?(?:固定|偏好使用|使用)\s*python\s*([0-9]+(?:\.[0-9]+){0,2})",
                r"i (?:prefer|use|pin)\s+python\s*([0-9]+(?:\.[0-9]+){0,2})",
            ),
            "Python 版本",
        ),
    )
    for predicate, patterns, label in known_preference_patterns:
        value = _match_value(clause, patterns)
        if value:
            if predicate == "python_version":
                value = f"Python {value}"
            return _preference(predicate, value, f"用户偏好的{label}是 {value}。")

    style_patterns = (
        ("简洁", ("简洁", "精简", "短一些", "concise", "brief")),
        ("详细", ("详细", "展开说明", "更完整", "detailed", "thorough")),
    )
    lowered = clause.casefold()
    if any(
        prefix in lowered
        for prefix in ("我喜欢", "我偏好", "请一直", "i prefer", "please always")
    ):
        for value, markers in style_patterns:
            if any(marker in lowered for marker in markers):
                return _preference(
                    "response_style", value, f"用户偏好的回答风格是{value}。"
                )

    stable_fact_patterns = (
        (
            "timezone",
            (
                r"我的时区(?:是|为)\s*([^,，。.!！?？;；]{1,60})",
                r"my time ?zone is\s+([^,.;!?]{1,60})",
            ),
            "用户时区是 {value}。",
        ),
        (
            "location",
            (
                r"我(?:目前)?住在\s*([^,，。.!！?？;；]{1,60})",
                r"i live in\s+([^,.;!?]{1,60})",
            ),
            "用户居住在 {value}。",
        ),
        (
            "occupation",
            (
                r"(?:我的职业(?:是|为)|我从事|我是一名)\s*([^,，。.!！?？;；]{1,60})",
                r"i (?:work as (?:an? )?|am an? )([^,.;!?]{1,60})",
            ),
            "用户职业是 {value}。",
        ),
        (
            "allergy",
            (
                r"我对\s*([^,，。.!！?？;；]{1,60})\s*过敏",
                r"i am allergic to\s+([^,.;!?]{1,60})",
            ),
            "用户对 {value} 过敏。",
        ),
    )
    for predicate, patterns, summary_template in stable_fact_patterns:
        value = _match_value(clause, patterns)
        if value:
            return _fact(predicate, value, summary_template.format(value=value))

    if explicit:
        value = _match_value(
            clause,
            (
                r"我的(?:固定|长期)偏好(?:是|为)\s*([^,，。.!！?？;；]{1,80})",
                r"my long[- ]term preference is\s+([^,.;!?]{1,80})",
            ),
        )
        if value:
            return _preference(
                "long_term_preference",
                value,
                f"用户明确声明的长期偏好是 {value}。",
            )
    return None


def _extract_revocations(lowered: str) -> list[str]:
    revoke_signal = any(
        marker in lowered
        for marker in (
            "忘掉",
            "删除",
            "清除",
            "不要再记",
            "别再记",
            "forget",
            "delete",
            "remove",
        )
    )
    if not revoke_signal:
        return []
    if any(pattern in lowered for pattern in _REVOKE_ALL_PATTERNS):
        return ["*"]
    if not any(
        marker in lowered for marker in ("我的", "关于我", "我个人", "my ", "about me")
    ):
        return []
    return [
        predicate
        for predicate, aliases in _PREDICATE_ALIASES.items()
        if any(alias in lowered for alias in aliases)
    ]


def _split_clauses(text: str) -> Iterable[str]:
    for clause in re.split(r"[\n。！？!?；;]+", text):
        cleaned = clause.strip(_VALUE_TRIM)
        if cleaned:
            yield cleaned


def _match_value(clause: str, patterns: Iterable[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, clause, re.IGNORECASE)
        if not match:
            continue
        value = _TRAILING_POLITENESS_RE.sub("", match.group(1).strip(_VALUE_TRIM))
        value = value.strip(_VALUE_TRIM)
        if _valid_value(value):
            return value
    return ""


def _valid_value(value: str) -> bool:
    lowered = value.casefold()
    return bool(
        1 <= len(value) <= 120
        and "\n" not in value
        and "@" not in value
        and "://" not in value
        and not lowered.startswith("www.")
        and not _contains_sensitive_content(lowered)
        and not re.search(r"[<>]{2,}|\{\{|\}\}", value)
    )


def _contains_sensitive_content(lowered: str) -> bool:
    return "***" in lowered or any(marker in lowered for marker in _SENSITIVE_MARKERS)


def _valid_name(value: str) -> bool:
    lowered = value.casefold()
    return (
        _valid_value(value)
        and not lowered.startswith(("when ", "if ", "later ", "back ", "not "))
        and not any(
            marker in lowered
            for marker in (
                "帮我",
                "帮忙",
                "做什么",
                "处理",
                " when ",
                " if ",
                " later",
                " back",
                "not important",
            )
        )
    )


def _canonical_language(value: str) -> str:
    return "English" if value.casefold() in {"英文", "英语", "english"} else value


def _preference(predicate: str, value: str, summary: str) -> _ExtractedProfile:
    return _ExtractedProfile(MemoryKind.PREFERENCE, predicate, value, summary)


def _fact(predicate: str, value: str, summary: str) -> _ExtractedProfile:
    return _ExtractedProfile(MemoryKind.FACT, predicate, value, summary)
