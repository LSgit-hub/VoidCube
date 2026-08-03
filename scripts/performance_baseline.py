"""Measure repeatable local startup and application-contract performance baselines."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "voidcube.performance-baseline.v1"
DEFAULT_REPEAT = 3


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    description: str
    code: str | None
    captures_operation: bool = False


def _operation_code(setup: str, expression: str) -> str:
    return (
        "import json,time; "
        f"{setup}; "
        "started=time.perf_counter(); "
        f"{expression}; "
        "print(json.dumps({'operation_ms': (time.perf_counter()-started)*1000}))"
    )


def scenarios() -> tuple[Scenario, ...]:
    supervisor_code = (
        "import tempfile; from pathlib import Path; "
        "from systems.supervisor.config_models import SupervisorConfig; "
        "from systems.supervisor.supervisor import Supervisor; "
        "root=Path(tempfile.mkdtemp(prefix='voidcube-perf-')); "
        "config=SupervisorConfig(" 
        "execution={'git_repo_path': str(root)}, "
        "body_runtime={'state_root': str(root/'body')}, "
        "soul_store_path=str(root/'soul.json'), "
        "autonomous_chain_store_path=str(root/'chain.json'), "
        "scheduled_task_store_path=str(root/'scheduled.json'), "
        "ui_auto_open=False); Supervisor(config)"
    )
    turn_code = _operation_code(
        "from VoidCube_app.turn_contract import begin_turn,normalize_turn_outcome",
        "turn=begin_turn([], 'baseline'); "
        "normalize_turn_outcome({'messages': turn.conversation_history, 'final_response': 'ok'}, "
        "fallback_history=turn.conversation_history)",
    )
    ui_code = _operation_code(
        "from systems.supervisor.ui_state_projection import project_supervisor_scene,project_ui_metrics; "
        "observation={'counts': {}, 'board': {}, 'groups': {}, 'loop': {}}",
        "project_supervisor_scene(autonomous_observation=observation, observation_input_available=False); "
        "project_ui_metrics([], autonomous_observation=observation, body_status={}, error_count=0)"
    )
    return (
        Scenario(
            name="import_graph",
            description="Cold process import of the shared, CLI and Supervisor entry packages.",
            code=(
                "import VoidCube_app,VoidCube_cli.app,systems.supervisor.supervisor"
            ),
        ),
        Scenario(
            name="cli_help",
            description="Cold process CLI help entry without starting an interactive session.",
            code=None,
        ),
        Scenario(
            name="turn_contract",
            description="Cold process first turn input/result contract path.",
            code=turn_code,
            captures_operation=True,
        ),
        Scenario(
            name="supervisor_init",
            description="Cold process Supervisor construction with isolated temporary stores and UI auto-open disabled.",
            code=supervisor_code,
            captures_operation=False,
        ),
        Scenario(
            name="ui_projection",
            description="Cold process empty-room Supervisor state projection path.",
            code=ui_code,
            captures_operation=True,
        ),
    )


def _subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _run_scenario(scenario: Scenario) -> tuple[float, float | None]:
    command = [sys.executable]
    if scenario.name == "cli_help":
        command.extend(("-m", "VoidCube_cli.main", "--help"))
    else:
        command.extend(("-c", scenario.code))

    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=_subprocess_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    cold_ms = (time.perf_counter() - started) * 1000
    if completed.returncode:
        raise RuntimeError(
            f"performance scenario {scenario.name!r} failed with "
            f"exit code {completed.returncode}: {completed.stderr.strip()}"
        )

    operation_ms = None
    if scenario.captures_operation:
        try:
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
            operation_ms = float(payload["operation_ms"])
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"performance scenario {scenario.name!r} returned invalid timing output"
            ) from exc
    return cold_ms, operation_ms


def _summary(samples: list[float]) -> dict[str, float]:
    ordered = sorted(float(value) for value in samples)
    return {
        "min_ms": round(ordered[0], 3),
        "median_ms": round(statistics.median(ordered), 3),
        "max_ms": round(ordered[-1], 3),
    }


def collect_baseline(*, repeat: int = DEFAULT_REPEAT) -> dict[str, Any]:
    if repeat < 1:
        raise ValueError("repeat must be at least 1")

    metric_payload: dict[str, Any] = {}
    for scenario in scenarios():
        cold_samples: list[float] = []
        operation_samples: list[float] = []
        for _ in range(repeat):
            cold_ms, operation_ms = _run_scenario(scenario)
            cold_samples.append(cold_ms)
            if operation_ms is not None:
                operation_samples.append(operation_ms)
        metric: dict[str, Any] = {
            "description": scenario.description,
            "cold_process": {
                "samples_ms": [round(value, 3) for value in cold_samples],
                **_summary(cold_samples),
            },
        }
        if operation_samples:
            metric["operation"] = {
                "samples_ms": [round(value, 3) for value in operation_samples],
                **_summary(operation_samples),
            }
        metric_payload[scenario.name] = metric

    return {
        "schema": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "repeat": repeat,
        "metrics": metric_payload,
    }


def write_or_print(payload: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output is None:
        print(text, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(f"Wrote performance baseline: {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repeat",
        type=int,
        default=DEFAULT_REPEAT,
        help=f"Samples per scenario (default: {DEFAULT_REPEAT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write JSON to this path; print JSON when omitted",
    )
    args = parser.parse_args(argv)
    write_or_print(collect_baseline(repeat=args.repeat), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
