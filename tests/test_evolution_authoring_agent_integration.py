from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from voidcube.systems.evolution_authoring import (
    AIAgentAuthoringAdapter,
    EvolutionAuthoringExecutor,
    EvolutionAuthoringSpec,
)
from voidcube.extensions.tools.files.file_tools import write_file_tool
from voidcube.infrastructure.execution.task_execution import get_task_execution_state


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        platform.system().lower() != "windows",
        reason="native authoring requires Windows",
    ),
]


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git("init", cwd=repository)
    _git("config", "user.name", "VoidCube Test", cwd=repository)
    _git("config", "user.email", "test@example.invalid", cwd=repository)
    target = repository / "src" / "voidcube" / "runtime" / "agent" / "demo.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 'baseline'\n", encoding="utf-8")
    (repository / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git("add", ".gitignore", "src/voidcube/runtime/agent/demo.py", cwd=repository)
    _git("commit", "-m", "baseline", cwd=repository)
    return repository, _git("rev-parse", "HEAD", cwd=repository)


class _ScopedConversationAgent:
    valid_tool_names = {
        "read_file",
        "write_file",
        "patch",
        "search_files",
        "terminal",
        "process",
    }

    def run_conversation(self, _user_message: str, **kwargs: object):
        task_id = str(kwargs["task_id"])
        write_result = json.loads(
            write_file_tool(
                "src/voidcube/runtime/agent/demo.py",
                "VALUE = 'candidate'\n",
                task_id=task_id,
            )
        )
        assert not write_result.get("error")

        from voidcube.runtime.agent.runner import AIAgent

        agent = AIAgent.__new__(AIAgent)
        agent.verbose_logging = False
        cleanup = agent._cleanup_task_resources(task_id)
        assert cleanup.details["terminal"] == {
            "status": "skipped",
            "details": {"reason": "executor_owned_environment"},
        }
        state = get_task_execution_state(task_id)
        assert state is not None and state.status == "ready"
        return {
            "completed": True,
            "interrupted": False,
            "final_response": "Updated src/voidcube/runtime/agent/demo.py; executor verification remains pending.",
        }


@pytest.mark.asyncio
async def test_adapter_and_executor_share_native_scope_until_candidate_commit(
    tmp_path: Path,
):
    repository, baseline = _repository(tmp_path)
    adapter = AIAgentAuthoringAdapter(
        runtime_provider_resolver=lambda **_kwargs: {},
        model_resolver=lambda: "test-model",
        ai_agent_factory=lambda **_kwargs: _ScopedConversationAgent(),
    )
    executor = EvolutionAuthoringExecutor(
        repository,
        worktree_root=tmp_path / "authoring-worktrees",
        python_executable=sys.executable,
    )
    spec = EvolutionAuthoringSpec(
        task_id="native-agent-adapter",
        objective="Update the demo value.",
        improvement_hypothesis="A focused value change produces the candidate.",
        baseline_commit=baseline,
        allowed_paths=("src/voidcube/runtime/agent/demo.py",),
        max_files_changed=1,
        test_commands=("python -m py_compile src/voidcube/runtime/agent/demo.py",),
        command_timeout_seconds=60,
        commit_message="Update demo value",
    )

    result = await executor.execute(spec, agent=adapter)

    assert result.status == "candidate_created", (
        result.error_code,
        result.error_reason,
        result.changed_files,
        result.agent_summary,
    )
    assert result.candidate_commit != baseline
    assert result.changed_files == ("src/voidcube/runtime/agent/demo.py",)
    assert result.environment_manifest_id
    assert result.environment_identity_id
    assert len(result.command_evidence) == 3
    assert get_task_execution_state(spec.task_id).status == "released"
    assert (repository / "src/voidcube/runtime/agent/demo.py").read_text(encoding="utf-8") == (
        "VALUE = 'baseline'\n"
    )
    assert (
        _git(
            "show",
            f"{result.candidate_ref}:src/voidcube/runtime/agent/demo.py",
            cwd=repository,
        )
        == "VALUE = 'candidate'"
    )
