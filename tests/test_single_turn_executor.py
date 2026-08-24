from __future__ import annotations

import pytest

from voidcube.application.single_turn_executor import SingleTurnExecutor, SingleTurnExecutorPorts


def test_single_turn_executor_runs_lifecycle_in_order() -> None:
    calls: list[str] = []

    runtime = SingleTurnExecutor(
        SingleTurnExecutorPorts(
            execute=lambda: calls.append("execute") or "raw",
            apply_result=lambda value: calls.append(f"apply:{value}") or "applied",
            postprocess=lambda value: calls.append(f"post:{value}") or "postprocessed",
            finish=lambda applied, post: calls.append(f"finish:{applied}:{post}"),
            finalize=lambda applied, post: calls.append(f"final:{applied}:{post}") or "response",
        )
    )

    assert runtime.execute() == "response"
    assert calls == [
        "execute",
        "apply:raw",
        "post:applied",
        "finish:applied:postprocessed",
        "final:applied:postprocessed",
    ]


def test_single_turn_executor_does_not_finish_after_failure() -> None:
    calls: list[str] = []
    runtime = SingleTurnExecutor(
        SingleTurnExecutorPorts(
            execute=lambda: calls.append("execute") or "raw",
            apply_result=lambda _value: (_ for _ in ()).throw(RuntimeError("bad result")),
            postprocess=lambda _value: calls.append("post") or "post",
            finish=lambda _applied, _post: calls.append("finish"),
            finalize=lambda _applied, _post: calls.append("final") or "final",
        )
    )

    with pytest.raises(RuntimeError, match="bad result"):
        runtime.execute()
    assert calls == ["execute"]
