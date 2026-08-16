from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

from ..schema import BaseMemoryUnit


class JSONFileMemoryStore:
    """Persist structured memory units as JSON or JSONL files."""

    def save_json(self, path: str | Path, objects: Sequence[BaseMemoryUnit]) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.to_dict() for item in objects]
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save_jsonl(self, path: str | Path, objects: Sequence[BaseMemoryUnit]) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(item.to_dict(), ensure_ascii=False) for item in objects]
        target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
