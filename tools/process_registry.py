"""Tracked background command sessions for the terminal and process tools."""

from __future__ import annotations

import codecs
import json
import os
import queue
import shlex
import signal
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any


_IS_WINDOWS = os.name == "nt"
_FINAL_STATUSES = {"completed", "failed", "killed"}
_MAX_OUTPUT_CHARS = 1_000_000
_MAX_RETAINED_SESSIONS = 128


@dataclass
class ProcessSession:
    id: str
    command: str
    cwd: str
    task_id: str
    kind: str
    pid: int | None = None
    notify_on_complete: bool = False
    watch_patterns: tuple[str, ...] = ()
    status: str = "running"
    exit_code: int | None = None
    error: str | None = None
    _process: subprocess.Popen | None = field(default=None, repr=False)
    _env: Any = field(default=None, repr=False)
    _output: str = field(default="", repr=False)
    _output_truncated: bool = field(default=False, repr=False)
    _poll_cursor: int = field(default=0, repr=False)
    _watch_buffer: str = field(default="", repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    def snapshot(self, *, incremental: bool = False) -> dict[str, Any]:
        with self._lock:
            if incremental:
                output = self._output[self._poll_cursor:]
                self._poll_cursor = len(self._output)
            else:
                output = self._output
            return {
                "session_id": self.id,
                "status": self.status,
                "pid": self.pid,
                "command": self.command,
                "output": output,
                "output_truncated": self._output_truncated,
                "exit_code": self.exit_code,
                "error": self.error,
            }


class ProcessRegistry:
    """Thread-safe lifecycle manager for local and remote background commands."""

    def __init__(self) -> None:
        self._sessions: dict[str, ProcessSession] = {}
        self._lock = threading.RLock()
        self._completion_consumed: set[str] = set()
        self.completion_queue: queue.Queue[dict[str, Any]] = queue.Queue()

    @staticmethod
    def _new_id() -> str:
        return f"proc_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _patterns(patterns: list[str] | None) -> tuple[str, ...]:
        return tuple(dict.fromkeys(p for p in (patterns or []) if p))

    def _store(self, session: ProcessSession) -> None:
        with self._lock:
            if len(self._sessions) >= _MAX_RETAINED_SESSIONS:
                for session_id, old in list(self._sessions.items()):
                    if old.status in _FINAL_STATUSES:
                        self._sessions.pop(session_id)
                        self._completion_consumed.discard(session_id)
                        if len(self._sessions) < _MAX_RETAINED_SESSIONS:
                            break
            self._sessions[session.id] = session

    def get(self, session_id: str) -> ProcessSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def spawn_local(
        self,
        *,
        command: str,
        cwd: str,
        task_id: str,
        env_vars: dict | None = None,
        notify_on_complete: bool = False,
        watch_patterns: list[str] | None = None,
    ) -> ProcessSession:
        from tools.environments.local import _find_persistent_bash, _make_run_env

        effective_cwd = cwd or os.getcwd()
        script = (
            f"if builtin cd -- {shlex.quote(effective_cwd)}; then\n"
            f"{command}\n"
            "else exit 126; fi"
        )
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "env": _make_run_env(env_vars or {}),
            "bufsize": 0,
        }
        if _IS_WINDOWS:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["preexec_fn"] = os.setsid

        process = subprocess.Popen(
            [_find_persistent_bash(), "-l", "-c", script],
            **popen_kwargs,
        )
        session = ProcessSession(
            id=self._new_id(),
            command=command,
            cwd=effective_cwd,
            task_id=task_id,
            kind="local",
            pid=process.pid,
            notify_on_complete=notify_on_complete,
            watch_patterns=self._patterns(watch_patterns),
            _process=process,
        )
        self._store(session)

        reader = threading.Thread(
            target=self._read_local_output,
            args=(session,),
            name=f"process-output-{session.id}",
            daemon=True,
        )
        reader.start()
        threading.Thread(
            target=self._wait_local,
            args=(session, reader),
            name=f"process-wait-{session.id}",
            daemon=True,
        ).start()
        return session

    def spawn_via_env(
        self,
        *,
        env: Any,
        command: str,
        cwd: str,
        task_id: str,
        notify_on_complete: bool = False,
        watch_patterns: list[str] | None = None,
    ) -> ProcessSession:
        session = ProcessSession(
            id=self._new_id(),
            command=command,
            cwd=cwd,
            task_id=task_id,
            kind="remote",
            notify_on_complete=notify_on_complete,
            watch_patterns=self._patterns(watch_patterns),
            _env=env,
        )
        self._store(session)
        threading.Thread(
            target=self._run_remote,
            args=(session,),
            name=f"process-remote-{session.id}",
            daemon=True,
        ).start()
        return session

    def _read_local_output(self, session: ProcessSession) -> None:
        process = session._process
        if process is None or process.stdout is None:
            return
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                self._append_output(session, decoder.decode(chunk))
            tail = decoder.decode(b"", final=True)
            if tail:
                self._append_output(session, tail)
        except (OSError, ValueError) as exc:
            with session._lock:
                if session.status == "running":
                    session.error = str(exc)

    def _wait_local(self, session: ProcessSession, reader: threading.Thread) -> None:
        process = session._process
        if process is None:
            self._finish(session, None, "failed", "Process was not started")
            return
        try:
            exit_code = process.wait()
            reader.join(timeout=2)
            with session._lock:
                killed = session.status == "killed"
            self._finish(
                session,
                exit_code,
                "killed" if killed else "completed",
                None,
            )
        except Exception as exc:
            self._finish(session, None, "failed", str(exc))
        finally:
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except (AttributeError, OSError, ValueError):
                    pass

    def _run_remote(self, session: ProcessSession) -> None:
        try:
            result = session._env.execute(session.command, cwd=session.cwd)
            output = str(result.get("output", ""))
            if output:
                self._append_output(session, output)
            with session._lock:
                killed = session.status == "killed"
            self._finish(
                session,
                result.get("returncode"),
                "killed" if killed else "completed",
                None,
            )
        except Exception as exc:
            with session._lock:
                killed = session.status == "killed"
            self._finish(
                session,
                None,
                "killed" if killed else "failed",
                None if killed else str(exc),
            )

    def _append_output(self, session: ProcessSession, text: str) -> None:
        events: list[dict[str, Any]] = []
        with session._lock:
            session._output += text
            overflow = len(session._output) - _MAX_OUTPUT_CHARS
            if overflow > 0:
                session._output = session._output[overflow:]
                session._poll_cursor = max(0, session._poll_cursor - overflow)
                session._output_truncated = True
            combined = session._watch_buffer + text
            lines = combined.splitlines(keepends=True)
            if lines and not lines[-1].endswith(("\n", "\r")):
                session._watch_buffer = lines.pop()
            else:
                session._watch_buffer = ""
            for line in lines:
                events.extend(self._watch_events(session, line.rstrip("\r\n")))
        for event in events:
            self.completion_queue.put(event)

    @staticmethod
    def _watch_events(session: ProcessSession, line: str) -> list[dict[str, Any]]:
        return [
            {
                "type": "watch_match",
                "session_id": session.id,
                "command": session.command,
                "pattern": pattern,
                "output": line,
                "suppressed": 0,
            }
            for pattern in session.watch_patterns
            if pattern in line
        ]

    def _finish(
        self,
        session: ProcessSession,
        exit_code: int | None,
        status: str,
        error: str | None,
    ) -> None:
        events: list[dict[str, Any]] = []
        with session._lock:
            if session._done.is_set():
                return
            if session._watch_buffer:
                events = self._watch_events(session, session._watch_buffer)
                session._watch_buffer = ""
            session.exit_code = exit_code
            session.status = status
            session.error = error or session.error
            session._done.set()
            if session.notify_on_complete:
                events.append({
                    "type": "completion",
                    "session_id": session.id,
                    "command": session.command,
                    "exit_code": exit_code,
                    "output": session._output,
                    "output_truncated": session._output_truncated,
                })
        for event in events:
            self.completion_queue.put(event)

    def list_sessions(self, task_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._sessions.values())
        if task_id is not None:
            sessions = [session for session in sessions if session.task_id == task_id]
        results = []
        for session in sessions:
            result = session.snapshot()
            result.pop("output")
            results.append(result)
        return results

    def has_active_processes(self, task_id: str) -> bool:
        with self._lock:
            sessions = list(self._sessions.values())
        return any(
            session.task_id == task_id and session.status == "running"
            for session in sessions
        )

    def poll(self, session_id: str) -> dict[str, Any]:
        session = self._require(session_id)
        result = session.snapshot(incremental=True)
        if result["status"] in _FINAL_STATUSES:
            self._mark_completion_consumed(session_id)
        return result

    def wait(self, session_id: str, timeout: float | None = None) -> dict[str, Any]:
        session = self._require(session_id)
        finished = session._done.wait(timeout)
        result = session.snapshot(incremental=True)
        result["timed_out"] = not finished
        if finished:
            self._mark_completion_consumed(session_id)
        return result

    def write(self, session_id: str, data: str) -> dict[str, Any]:
        session = self._require(session_id)
        if session.kind != "local":
            raise ValueError("Remote background sessions do not support stdin")
        process = session._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise ValueError("Process stdin is not available")
        process.stdin.write(data.encode("utf-8"))
        process.stdin.flush()
        return session.snapshot(incremental=True)

    def close(self, session_id: str) -> dict[str, Any]:
        session = self._require(session_id)
        if session.kind != "local":
            raise ValueError("Remote background sessions do not support stdin")
        process = session._process
        if process is None or process.stdin is None or process.stdin.closed:
            raise ValueError("Process stdin is not available")
        process.stdin.close()
        return session.snapshot(incremental=True)

    def kill(self, session_id: str) -> dict[str, Any]:
        session = self._require(session_id)
        self._kill_session(session)
        session._done.wait(2)
        self._mark_completion_consumed(session_id)
        return session.snapshot(incremental=True)

    def _kill_session(self, session: ProcessSession) -> bool:
        with session._lock:
            if session.status != "running":
                return False
            session.status = "killed"
            process = session._process

        if session.kind == "remote":
            try:
                from tools.terminal_tool import cleanup_vm
                cleanup_vm(session.task_id)
            finally:
                self._finish(session, None, "killed", None)
            return True

        if process is not None:
            try:
                if _IS_WINDOWS:
                    result = subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=5,
                        check=False,
                    )
                    if result.returncode != 0 and process.poll() is None:
                        process.kill()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
                else:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (OSError, subprocess.SubprocessError):
                try:
                    process.kill()
                except OSError:
                    pass
        return True

    def kill_all(self, task_id: str | None = None) -> int:
        with self._lock:
            sessions = list(self._sessions.values())
        targets = [
            session for session in sessions
            if session.status == "running"
            and (task_id is None or session.task_id == task_id)
        ]
        for session in targets:
            self._kill_session(session)
        return len(targets)

    def is_completion_consumed(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._completion_consumed

    def _mark_completion_consumed(self, session_id: str) -> None:
        with self._lock:
            self._completion_consumed.add(session_id)

    def _require(self, session_id: str) -> ProcessSession:
        session = self.get(session_id)
        if session is None:
            raise KeyError(f"Unknown process session: {session_id}")
        return session


process_registry = ProcessRegistry()


PROCESS_SCHEMA = {
    "description": "Inspect and control background terminal sessions.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "poll", "wait", "write", "close", "kill"],
                "description": "Operation to perform.",
            },
            "session_id": {"type": "string", "description": "Background session ID."},
            "data": {"type": "string", "description": "Text to write to process stdin."},
            "timeout": {
                "type": "number",
                "minimum": 0,
                "maximum": 3600,
                "description": "Maximum seconds for wait; omitted means wait until completion.",
            },
        },
        "required": ["action"],
    },
}


def process_tool(args: dict | None = None, **_: Any) -> str:
    args = args or {}
    action = args.get("action")
    try:
        if action == "list":
            result: dict[str, Any] = {"sessions": process_registry.list_sessions()}
        else:
            session_id = args.get("session_id")
            if not session_id:
                raise ValueError(f"session_id is required for action {action!r}")
            if action == "poll":
                result = process_registry.poll(session_id)
            elif action == "wait":
                result = process_registry.wait(session_id, args.get("timeout"))
            elif action == "write":
                data = args.get("data")
                if not isinstance(data, str):
                    raise ValueError("data must be a string")
                result = process_registry.write(session_id, data)
            elif action == "close":
                result = process_registry.close(session_id)
            elif action == "kill":
                result = process_registry.kill(session_id)
            else:
                raise ValueError(f"Unknown process action: {action!r}")
        return json.dumps({"success": True, **result}, ensure_ascii=False)
    except (KeyError, ValueError, OSError) as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


from tools.registry import registry

registry.register(
    name="process",
    toolset="terminal",
    schema=PROCESS_SCHEMA,
    handler=process_tool,
    max_result_size_chars=100_000,
)
