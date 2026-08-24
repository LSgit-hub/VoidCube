from voidcube.application.sessions import SessionHydration, SessionHydrationStatus
from voidcube.interfaces.cli.session_resume import (
    CliSessionResumePorts,
    CliSessionResumeRuntime,
)


def _runtime(status, output, *, history=(), metadata=None):
    hydration = SessionHydration(
        session_id="session",
        status=status,
        metadata=metadata,
        conversation_history=history,
    )
    return CliSessionResumeRuntime(
        CliSessionResumePorts(
            resumed=lambda: True,
            repository_available=lambda: True,
            session_id=lambda: "session",
            hydrate=lambda: (hydration, True),
            accent_color=lambda: "#FFBF00",
            translate=lambda key, **_: key,
            emit=output.append,
        )
    )


def test_session_resume_runtime_projects_ready_preload_summary():
    output = []
    runtime = _runtime(
        SessionHydrationStatus.READY,
        output,
        history=({"role": "user"}, {"role": "assistant"}),
        metadata={"title": "Work"},
    )

    assert runtime.preload() is True
    assert len(output) == 1
    assert "session" in output[0]
    assert '"Work"' in output[0]
    assert "1 prompts.user_messages" in output[0]


def test_session_resume_runtime_handles_missing_and_empty_without_success():
    missing_output = []
    assert _runtime(SessionHydrationStatus.MISSING, missing_output).preload() is False
    assert len(missing_output) == 2

    empty_output = []
    assert _runtime(SessionHydrationStatus.EMPTY, empty_output).preload() is False
    assert "no messages" in empty_output[0]
