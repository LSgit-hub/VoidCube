from voidcube.domain.events import AutonomousTaskStarted
from voidcube.infrastructure.events import SupervisorDomainEventSink


def test_supervisor_event_sink_serializes_event_and_keeps_task_identity_in_metadata():
    governance = []
    activity = []
    sink = SupervisorDomainEventSink(
        record_governance=governance.append,
        record_activity=lambda *args, **kwargs: activity.append((args, kwargs)),
    )

    outcome = sink(AutonomousTaskStarted(task_id="task-1", cycle_id="cycle-2", attempt=3))

    assert outcome.status == "succeeded"
    assert governance[0]["task_id"] == "task-1"
    assert governance[0]["metadata"]["task_id"] == "task-1"
    assert governance[0]["metadata"]["cycle_id"] == "cycle-2"
    assert activity[0][0] == ("AutonomousTaskStarted",)


def test_supervisor_event_sink_reports_partial_projection_failure():
    activity = []

    def fail(_event):
        raise RuntimeError("governance down")

    outcome = SupervisorDomainEventSink(
        record_governance=fail,
        record_activity=lambda *args, **kwargs: activity.append(kwargs),
    )(AutonomousTaskStarted(task_id="task-1"))

    assert outcome.status == "degraded"
    assert "governance down" in (outcome.error or "")
    assert activity and activity[0]["metadata"]["task_id"] == "task-1"


def test_supervisor_event_sink_skips_when_no_projection_exists():
    outcome = SupervisorDomainEventSink()(AutonomousTaskStarted(task_id="task-1"))
    assert outcome.status == "skipped"


def test_supervisor_event_sink_reports_projection_outcome_failures():
    from voidcube.domain.agent.effect_outcomes import EffectOutcome

    outcome = SupervisorDomainEventSink(
        record_governance=lambda _event: EffectOutcome(
            status="failed", error="disk full"
        ),
        record_activity=lambda *_args, **_kwargs: EffectOutcome(status="succeeded"),
    )(AutonomousTaskStarted(task_id="task-1"))

    assert outcome.status == "degraded"
    assert "disk full" in (outcome.error or "")
