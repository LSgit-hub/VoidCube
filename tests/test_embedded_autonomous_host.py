from VoidCube_app.autonomous_component_runtime import (
    AutonomousComponentHostPorts,
    ensure_autonomous_component_host,
)


def test_component_host_assembly_initializes_each_lifecycle_port_once() -> None:
    child = object()
    calls: list[object] = []
    stored: list[object] = []
    ports = AutonomousComponentHostPorts(
        get_component_host=lambda: None,
        create_component_host=lambda: calls.append("create") or child,
        set_component_active=lambda host, active: calls.append(("active", host, active)),
        bind_component_parent=lambda host: calls.append(("parent", host)),
        ensure_task_session=lambda host: calls.append(("session", host)),
        store_component_host=stored.append,
    )

    result = ensure_autonomous_component_host(ports)

    assert result is child
    assert calls == [
        "create",
        ("active", child, True),
        ("parent", child),
        ("session", child),
    ]
    assert stored == [child]


def test_component_host_assembly_reuses_existing_host_without_reinitializing() -> None:
    existing = object()
    ports = AutonomousComponentHostPorts(
        get_component_host=lambda: existing,
        create_component_host=lambda: (_ for _ in ()).throw(AssertionError("must not create")),
        set_component_active=lambda host, active: (_ for _ in ()).throw(AssertionError("must not reset")),
        bind_component_parent=lambda host: (_ for _ in ()).throw(AssertionError("must not bind")),
        ensure_task_session=lambda host: (_ for _ in ()).throw(AssertionError("must not hydrate")),
        store_component_host=lambda host: (_ for _ in ()).throw(AssertionError("must not store")),
    )

    assert ensure_autonomous_component_host(ports) is existing
