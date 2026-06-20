from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memai import BenchmarkRunner


def main() -> None:
    root = Path(__file__).resolve().parent / "fixtures"
    report = BenchmarkRunner().run_directory(root).to_dict()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
