"""Reasoning effort normalization shared by CLI and provider adapters."""

VALID_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


def parse_reasoning_effort(effort: str) -> dict | None:
    if not effort or not effort.strip():
        return None
    normalized = effort.strip().lower()
    if normalized == "none":
        return {"enabled": False}
    if normalized in VALID_REASONING_EFFORTS:
        return {"enabled": True, "effort": normalized}
    return None


__all__ = ["VALID_REASONING_EFFORTS", "parse_reasoning_effort"]
