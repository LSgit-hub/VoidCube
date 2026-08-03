from VoidCube_cli.cli_dynamic_command_runtime import (
    CliDynamicCommandPorts,
    CliDynamicCommandRuntime,
)
from VoidCube_cli.command_router import parse_cli_command


def _runtime(*, quick=None, plugins=None, skills=None, known=None, output=None):
    output = output if output is not None else []
    pending = []
    redirects = []

    def run_redirect(value):
        redirects.append(value)
        return True

    return (
        CliDynamicCommandRuntime(
            CliDynamicCommandPorts(
                quick_commands=quick or {},
                plugin_names=set(plugins or ()),
                skill_commands=skills or {},
                known_commands=set(known or ()),
                get_plugin_handler=lambda name: (
                    (lambda args: f"plugin:{name}:{args}")
                    if name in (plugins or ())
                    else None
                ),
                build_skill_message=lambda name, args, task_id: f"skill:{name}:{args}:{task_id}",
                session_id=lambda: "session-1",
                enqueue_pending_input=pending.append,
                emit=output.append,
                emit_markup=output.append,
                run_redirect=run_redirect,
            )
        ),
        output,
        pending,
        redirects,
    )


def test_dynamic_runtime_executes_plugin_skill_and_redirect_routes():
    runtime, output, pending, redirects = _runtime(
        plugins={"plug"},
        skills={"/skill": {"name": "demo"}},
        known={"/tasks"},
    )

    assert runtime.run(parse_cli_command("/plug args")) is True
    assert output[-1] == "plugin:plug:args"
    assert runtime.run(parse_cli_command("/skill run")) is True
    assert pending == ["skill:/skill:run:session-1"]

    assert runtime.run(parse_cli_command("/tas x")) is True
    assert redirects == ["/tasks x"]


def test_dynamic_runtime_reports_quick_and_unknown_routes():
    runtime, output, _pending, _redirects = _runtime(
        quick={"bad": {"type": "exec", "command": ""}},
    )
    runtime.run(parse_cli_command("/bad"))
    assert "no command defined" in output[-1]

    runtime.run(parse_cli_command("/missing"))
    assert "Unknown command" in output[-2]
    assert "/help" in output[-1]
