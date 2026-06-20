from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_PROMPT_FILES = {
    "extractor.events": "events.txt",
    "scholar.scene": "scene.txt",
    "scholar.arc": "arc.txt",
    "scholar.revision": "revision.txt",
}

BUILTIN_PROMPT_PACKS = {
    "default": "default",
    "conservative": "conservative",
    "high-recall": "high-recall",
    "scholar-heavy": "scholar-heavy",
}


@dataclass(slots=True)
class PromptRegistry:
    prompt_pack_dir: Path | None = None
    overrides: dict[str, str] = field(default_factory=dict)

    def get(self, key: str, fallback: str | None = None) -> str:
        if key in self.overrides:
            return self.overrides[key]
        if self.prompt_pack_dir is not None:
            relative = DEFAULT_PROMPT_FILES.get(key)
            if relative is not None:
                candidate = self.prompt_pack_dir / relative
                if candidate.exists():
                    return candidate.read_text(encoding="utf-8")
        if fallback is not None:
            return fallback
        raise KeyError(f"Unknown prompt key without fallback: {key}")

    def with_override(self, key: str, prompt: str) -> "PromptRegistry":
        merged = dict(self.overrides)
        merged[key] = prompt
        return PromptRegistry(prompt_pack_dir=self.prompt_pack_dir, overrides=merged)

    @classmethod
    def default(cls) -> "PromptRegistry":
        return cls.builtin("default")

    @classmethod
    def builtin(cls, name: str) -> "PromptRegistry":
        if name not in BUILTIN_PROMPT_PACKS:
            available = ", ".join(sorted(BUILTIN_PROMPT_PACKS))
            raise KeyError(
                f"Unknown builtin prompt pack: {name}. Available: {available}"
            )
        return cls(
            prompt_pack_dir=Path(__file__).resolve().parent
            / "prompts"
            / BUILTIN_PROMPT_PACKS[name]
        )

    @classmethod
    def from_path(
        cls,
        prompt_pack_dir: str | Path | None,
        *,
        builtin_name: str | None = None,
    ) -> "PromptRegistry":
        if prompt_pack_dir is not None:
            return cls(prompt_pack_dir=Path(prompt_pack_dir))
        if builtin_name is not None:
            return cls.builtin(builtin_name)
        if prompt_pack_dir is None:
            return cls.default()
        return cls(prompt_pack_dir=Path(prompt_pack_dir))
