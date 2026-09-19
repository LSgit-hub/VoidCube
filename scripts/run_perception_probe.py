"""Explicit, finite screen -> local Ollama -> summary -> SQLite verification.

Frames stay in memory. Prints counts only unless --show-summary is requested.
No remote model, desktop actions, Supervisor or auto-start service is used.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

from voidcube.systems.perception import (
    LocalPerceptionLoop, LocalTimelineSummarizer, LocalVisionAnalyzer,
    LocalVisionConfig, MssScreenCapture, PerceptionRuntime, TimelineStore,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true", help="Explicitly capture the selected screen")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--monitor", type=int, default=1)
    parser.add_argument("--roi", nargs=4, type=int, metavar=("LEFT", "TOP", "WIDTH", "HEIGHT"))
    parser.add_argument("--store", type=Path, required=True, help="Text-summary SQLite output")
    parser.add_argument("--show-summary", action="store_true")
    args = parser.parse_args()
    if not args.capture:
        parser.error("--capture is required; no screen is read implicitly")
    if not 1 <= args.samples <= 1000 or args.interval < 0:
        parser.error("samples must be 1..1000 and interval must be non-negative")
    roi = dict(zip(("left", "top", "width", "height"), args.roi)) if args.roi else None
    config = LocalVisionConfig.from_runtime()
    started = datetime.now(timezone.utc)
    with MssScreenCapture(monitor=args.monitor, roi=roi) as source, TimelineStore(args.store) as store:
        runtime = PerceptionRuntime(
            LocalPerceptionLoop(source, LocalVisionAnalyzer(config)),
            store=store, summarizer=LocalTimelineSummarizer(model=config.model),
        )
        runtime.authorize()
        runtime.start()
        try:
            for index in range(args.samples):
                tick = time.monotonic()
                result = runtime.step()
                print(json.dumps({"sample": index + 1, "model": config.model,
                                  "analyzed": result.record is not None,
                                  "seconds": round(time.monotonic() - tick, 2)}), flush=True)
                if index + 1 < args.samples:
                    time.sleep(args.interval)
        finally:
            runtime.stop()
        rows = store.query(start_at=started, end_at=datetime.now(timezone.utc) + timedelta(seconds=1))
        report = {"runtime": asdict(runtime.status()), "readback_segments": len(rows),
                  "summary_nonempty": all(bool(row.summary.strip()) for row in rows)}
        if args.show_summary:
            report["summaries"] = [row.summary for row in rows]
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 0 if rows and report["summary_nonempty"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
