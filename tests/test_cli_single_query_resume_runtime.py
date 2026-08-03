from VoidCube_app.session_lifecycle import SessionHydration, SessionHydrationStatus
from VoidCube_cli.cli_single_query_resume_runtime import (
    CliSingleQueryResumePorts,
    CliSingleQueryResumeRuntime,
)


def _runtime(output):
    return CliSingleQueryResumeRuntime(
        CliSingleQueryResumePorts(
            session_id=lambda: "session-id",
            accent_color=lambda: "#FFBF00",
            escape=lambda value: f"escaped:{value}",
            translate=lambda key, **_: key,
            emit=output.append,
        )
    )


def test_single_query_resume_reports_missing_only_when_newly_loaded():
    output = []
    runtime = _runtime(output)
    missing = SessionHydration(
        session_id="session-id",
        status=SessionHydrationStatus.MISSING,
    )

    assert runtime.report(missing, loaded_now=False) is False
    assert output == []
    assert runtime.report(missing, loaded_now=True) is False
    assert "Session not found: session-id" in output[0]
    assert len(output) == 2


def test_single_query_resume_reports_ready_and_empty_states_once():
    ready_output = []
    ready = SessionHydration(
        session_id="session-id",
        status=SessionHydrationStatus.READY,
        metadata={"title": "Work"},
        conversation_history=({"role": "user"}, {"role": "assistant"}),
    )
    assert _runtime(ready_output).report(ready, loaded_now=True) is True
    assert "escaped:session-id" in ready_output[0]
    assert "1 prompts.user_messages" in ready_output[0]

    empty_output = []
    empty = SessionHydration(
        session_id="session-id",
        status=SessionHydrationStatus.EMPTY,
    )
    assert _runtime(empty_output).report(empty, loaded_now=True) is True
    assert "found but has no messages" in empty_output[0]
