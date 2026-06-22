"""Agent process lifecycle manager (S-01).

Extracted from ``ProcessGatewayRuntimeMixin`` per architecture baseline
§3.6 ("supervisor 不负责直接启停 Agent 进程") and conflicts audit C-01.
The supervisor delegates process management to this module instead of
owning the implementations directly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("executor.process_manager")


class AgentProcessManager:
    """Deterministic agent process lifecycle: spawn, terminate, monitor, restart.

    This is the canonical owner of agent process operations.  The supervisor
    (and tests) inject this manager rather than calling subprocess directly.
    """

    def __init__(self, *, agent_model: type, subprocess_module=subprocess) -> None:
        self._agent_model = agent_model
        self._subprocess = subprocess_module

    # ── spawn ──────────────────────────────────────────────────────────

    def spawn(
        self,
        *,
        agent: Any,
        body_target: Any,
        gateway_address: str,
    ) -> None:
        """Launch an agent subprocess and update the agent model in-place."""
        env = os.environ.copy()
        env["AGENT_PORT"] = str(agent.port)
        env["GATEWAY_ADDRESS"] = gateway_address
        env["VOIDCUBE_ACTIVE_SLOT"] = body_target.slot_id
        env["VOIDCUBE_BODY_WORKTREE"] = body_target.worktree_path
        env["VOIDCUBE_BODY_RUNTIME"] = body_target.runtime_path
        env["VOIDCUBE_BODY_LOGS"] = body_target.logs_path
        env["VOIDCUBE_BODY_VERSION"] = body_target.body_version

        script_path = Path(body_target.launch_script_path)
        cwd = body_target.launch_cwd

        if sys.platform == "win32":
            process = self._subprocess.Popen(
                [sys.executable, str(script_path), "--port", str(agent.port)],
                env=env,
                cwd=cwd,
                creationflags=self._subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            process = self._subprocess.Popen(
                [sys.executable, str(script_path), "--port", str(agent.port)],
                env=env,
                cwd=cwd,
                start_new_session=True,
            )

        agent.pid = process.pid
        agent.status = "running"
        agent.started_at = datetime.now()
        agent.healthy = False
        agent.version = body_target.body_version
        agent.slot_id = body_target.slot_id
        agent.name = f"agent-{body_target.slot_id}"
        agent.body_worktree = body_target.worktree_path
        agent.body_runtime = body_target.runtime_path
        agent.body_logs = body_target.logs_path

        # Monitor in background
        asyncio.create_task(self._monitor(process, agent))

    # ── terminate ──────────────────────────────────────────────────────

    def terminate(self, agent: Any) -> None:
        """Stop an agent process tree (best-effort)."""
        if not agent.pid:
            return

        # Prefer psutil for process-tree termination
        try:
            import psutil
            proc = psutil.Process(agent.pid)
            children = proc.children(recursive=True)
            for child in children:
                child.terminate()
            proc.terminate()
            _, alive = psutil.wait_procs([proc, *children], timeout=3)
            for remaining in alive:
                remaining.kill()
            return
        except Exception:
            pass

        # Fallback: platform-specific kill
        if sys.platform == "win32":
            self._subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(agent.pid)], check=False
            )
        else:
            self._subprocess.run(["kill", "-TERM", str(agent.pid)], check=True)
            time.sleep(1)
            self._subprocess.run(["kill", "-KILL", str(agent.pid)], check=False)

    # ── monitor / restart ──────────────────────────────────────────────

    async def _monitor(self, process: Any, agent: Any) -> None:
        """Background coroutine: watch process and auto-restart on crash."""
        while True:
            retcode = process.poll()
            if retcode is not None:
                agent.status = "exited"
                agent.healthy = False
                logger.warning("Agent %s exited with code %s", agent.name, retcode)
                if retcode != 0:
                    await asyncio.sleep(5)
                    await self._restart(agent)
                break
            await asyncio.sleep(1)

    async def _restart(self, agent: Any) -> None:
        """Restart a failed agent by re-spawning it."""
        logger.info("Restarting failed agent: %s", agent.name)
        agent.status = "restarting"
        try:
            body_target = type(
                "BodyTarget",
                (),
                {
                    "slot_id": agent.slot_id,
                    "body_version": agent.version,
                    "worktree_path": agent.body_worktree,
                    "runtime_path": agent.body_runtime,
                    "logs_path": agent.body_logs,
                    "launch_script_path": "systems/agent/run_agent_instance.py",
                    "launch_cwd": str(Path(agent.body_worktree or ".")),
                },
            )()
            self.spawn(
                agent=agent,
                body_target=body_target,
                gateway_address="",
            )
            logger.info("Agent %s restarted successfully", agent.name)
        except Exception as exc:
            logger.error("Failed to restart agent %s: %s", agent.name, exc)
