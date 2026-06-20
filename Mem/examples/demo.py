from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from memai import ChroniclePipeline, TranscriptTurn


def main() -> None:
    turns = [
        TranscriptTurn(
            turn_id="turn_001",
            speaker="user",
            text="今天我们决定把这个项目做成时间优先的记忆系统。",
            timestamp=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_002",
            speaker="assistant",
            text="This week we implemented the schema and built the first event extractor.",
            timestamp=datetime(2026, 3, 22, 11, 30, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_003",
            speaker="user",
            text="但是今天检索排序还有问题，需要继续修订。",
            timestamp=datetime(2026, 3, 22, 14, 45, tzinfo=timezone.utc),
        ),
    ]

    result = ChroniclePipeline().ingest(turns)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
