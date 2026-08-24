from voidcube.interfaces.cli.dynamic_command_runtime import (
    CliDynamicCommandPorts,
    CliDynamicCommandRuntime,
)
from voidcube.interfaces.cli.commands.router import parse_cli_command


def _runtime(*, custom=None, plugins=None, skills=None, output=None):
    output = output if output is not None else []
    pending = []

    return (
        CliDynamicCommandRuntime(
            CliDynamicCommandPorts(
                custom_commands=custom or {},
                plugin_names=set(plugins or ()),
                skill_commands=skills or {},
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
            )
        ),
        output,
        pending,
    )


def test_dynamic_runtime_executes_plugin_and_skill_routes():
    runtime, output, pending = _runtime(
        plugins={"plug"},
        skills={"/skill": {"name": "demo"}},
    )

    assert runtime.run(parse_cli_command("/plug args")) is True
    assert output[-1] == "plugin:plug:args"
    assert runtime.run(parse_cli_command("/skill run")) is True
    assert pending == ["skill:/skill:run:session-1"]

def test_dynamic_runtime_reports_custom_and_unknown_routes():
    runtime, output, _pending = _runtime(
        custom={"bad": {"type": "exec", "command": ""}},
    )
    runtime.run(parse_cli_command("/bad"))
    assert "no command defined" in output[-1]

    runtime.run(parse_cli_command("/missing"))
    assert "Unknown command" in output[-2]
    assert "/help" in output[-1]
