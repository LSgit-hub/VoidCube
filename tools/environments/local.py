"""Local execution environment with optional persistent Bash sessions."""

import os
import platform
import queue
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid

from tools.environments.base import BaseEnvironment, _get_activity_callback, _pipe_stdin
from tools.interrupt import is_interrupted

_IS_WINDOWS = platform.system() == "Windows"


# Voidcube-internal env vars that should NOT leak into terminal subprocesses.
_VOIDCUBE_PROVIDER_ENV_FORCE_PREFIX = "_VOIDCUBE_FORCE_"


def _build_provider_env_blocklist() -> frozenset:
    """Derive the blocklist from provider, tool, and gateway config."""
    blocked: set[str] = set()

    try:
        from VoidCube_app.infrastructure.providers.registry import PROVIDER_REGISTRY
        for pconfig in PROVIDER_REGISTRY.values():
            api_key_env_vars = pconfig.get("api_key_env_vars", [])
            blocked.update(api_key_env_vars)
            base_url_env_var = pconfig.get("base_url_env_var")
            if base_url_env_var:
                blocked.add(base_url_env_var)
    except ImportError:
        pass

    try:
        from VoidCube_app.config import OPTIONAL_ENV_VARS
        for name, metadata in OPTIONAL_ENV_VARS.items():
            category = metadata.get("category")
            if category in {"tool", "messaging"}:
                blocked.add(name)
            elif category == "setting" and metadata.get("password"):
                blocked.add(name)
    except ImportError:
        pass

    blocked.update({
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
        "OPENAI_ORG_ID",
        "OPENAI_ORGANIZATION",
        "OPENROUTER_API_KEY",
        "LLM_MODEL",
        "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY",
        "MISTRAL_API_KEY",
        "GROQ_API_KEY",
        "TOGETHER_API_KEY",
        "PERPLEXITY_API_KEY",
        "COHERE_API_KEY",
        "FIREWORKS_API_KEY",
        "XAI_API_KEY",
        "HELICONE_API_KEY",
        "PARALLEL_API_KEY",
        "FIRECRAWL_API_KEY",
        "FIRECRAWL_API_URL",
        "SIGNAL_HTTP_URL",
        "SIGNAL_ACCOUNT",
        "SIGNAL_ALLOWED_USERS",
        "SIGNAL_GROUP_ALLOWED_USERS",
        "SIGNAL_HOME_CHANNEL",
        "SIGNAL_HOME_CHANNEL_NAME",
        "SIGNAL_IGNORE_STORIES",
        "HASS_TOKEN",
        "HASS_URL",
        "EMAIL_ADDRESS",
        "EMAIL_PASSWORD",
        "EMAIL_IMAP_HOST",
        "EMAIL_SMTP_HOST",
        "EMAIL_HOME_ADDRESS",
        "EMAIL_HOME_ADDRESS_NAME",
        "GATEWAY_ALLOWED_USERS",
        "GH_TOKEN",
        "GITHUB_APP_ID",
        "GITHUB_APP_PRIVATE_KEY_PATH",
        "GITHUB_APP_INSTALLATION_ID",
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
        "DAYTONA_API_KEY",
    })
    return frozenset(blocked)


_VOIDCUBE_PROVIDER_ENV_BLOCKLIST = _build_provider_env_blocklist()


def _find_bash() -> str:
    """Find bash for command execution."""
    if not _IS_WINDOWS:
        return (
            shutil.which("bash")
            or ("/usr/bin/bash" if os.path.isfile("/usr/bin/bash") else None)
            or ("/bin/bash" if os.path.isfile("/bin/bash") else None)
            or os.environ.get("SHELL")
            or "/bin/sh"
        )

    def _is_windows_bash_launcher(path: str | None) -> bool:
        """Return True for WSL launcher shims that are not real shell runtimes."""
        if not path:
            return False
        normalized = os.path.normcase(os.path.abspath(path))
        blocked_suffixes = (
            os.path.normcase(r"C:\Windows\System32\bash.exe"),
            os.path.normcase(
                os.path.join(
                    os.environ.get("LOCALAPPDATA", ""),
                    "Microsoft",
                    "WindowsApps",
                    "bash.exe",
                )
            ),
        )
        return normalized in blocked_suffixes

    custom = os.environ.get("VOIDCUBE_GIT_BASH_PATH")
    if custom and os.path.isfile(custom) and not _is_windows_bash_launcher(custom):
        return custom

    for candidate in (
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "bash.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Git", "bin", "bash.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Git", "bin", "bash.exe"),
    ):
        if candidate and os.path.isfile(candidate):
            return candidate

    found = shutil.which("bash")
    if found and not _is_windows_bash_launcher(found):
        return found

    raise RuntimeError(
        "Git Bash not found. Voidcube Agent requires Git for Windows on Windows.\n"
        "Install it from: https://git-scm.com/download/win\n"
        "Or set VOIDCUBE_GIT_BASH_PATH to your bash.exe location."
    )


def _find_persistent_bash() -> str:
    """Resolve the real Git Bash binary so it can be terminated reliably."""
    bash = _find_bash()
    if not _IS_WINDOWS:
        return bash

    path = os.path.normpath(bash)
    if os.path.basename(os.path.dirname(path)).lower() == "bin":
        candidate = os.path.join(os.path.dirname(os.path.dirname(path)), "usr", "bin", "bash.exe")
        if os.path.isfile(candidate):
            return candidate
    return bash


# Standard PATH entries for environments with minimal PATH.
_SANE_PATH = (
    "/opt/homebrew/bin:/opt/homebrew/sbin:"
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)


def _make_run_env(env: dict) -> dict:
    """Build a run environment with a sane PATH and provider-var stripping."""
    try:
        from tools.env_passthrough import is_env_passthrough as _is_passthrough
    except Exception:
        _is_passthrough = lambda _: False  # noqa: E731

    merged = dict(os.environ | env)
    run_env = {}
    for k, v in merged.items():
        if k.startswith(_VOIDCUBE_PROVIDER_ENV_FORCE_PREFIX):
            real_key = k[len(_VOIDCUBE_PROVIDER_ENV_FORCE_PREFIX):]
            run_env[real_key] = v
        elif k not in _VOIDCUBE_PROVIDER_ENV_BLOCKLIST or _is_passthrough(k):
            run_env[k] = v
    existing_path = run_env.get("PATH", "")
    if "/usr/bin" not in existing_path.split(":"):
        run_env["PATH"] = f"{existing_path}:{_SANE_PATH}" if existing_path else _SANE_PATH

    # Per-profile HOME isolation: redirect system tool configs (git, ssh, gh,
    # npm …) into {VOIDCUBE_HOME}/home/ when that directory exists.  Only the
    # subprocess sees the override — the Python process keeps the real HOME.
    from VoidCube_app.infrastructure.config.runtime_paths import get_subprocess_home
    _profile_home = get_subprocess_home()
    if _profile_home:
        run_env["HOME"] = _profile_home

    return run_env


class _PersistentBashSession:
    """Serialize commands through one login shell using private output frames."""

    _POLL_INTERVAL = 0.1
    _ACTIVITY_INTERVAL = 10.0
    _STARTUP_TIMEOUT = 30.0

    def __init__(self, env: dict, kill_process):
        self._env = env
        self._kill_process = kill_process
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._chunks: queue.Queue[bytes] = queue.Queue()
        self._buffer = bytearray()
        self._start_locked()

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None and process.poll() is None else None

    def _start_locked(self) -> None:
        process = subprocess.Popen(
            [_find_persistent_bash(), "-l"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=self._env,
            bufsize=0,
            preexec_fn=None if _IS_WINDOWS else os.setsid,
        )
        chunks: queue.Queue[bytes] = queue.Queue()

        def _read_stdout() -> None:
            try:
                while True:
                    chunk = process.stdout.read(65536)
                    chunks.put(chunk)
                    if not chunk:
                        return
            except (OSError, ValueError):
                chunks.put(b"")

        self._process = process
        self._chunks = chunks
        self._buffer = bytearray()
        self._reader_thread = threading.Thread(target=_read_stdout, daemon=True)
        self._reader_thread.start()

    def _ensure_started_locked(self) -> subprocess.Popen:
        if self._process is None or self._process.poll() is not None:
            self._stop_locked(kill=False)
            self._start_locked()
        return self._process

    def _stop_locked(self, *, kill: bool) -> None:
        process = self._process
        reader_thread = self._reader_thread
        self._process = None
        self._reader_thread = None
        self._buffer = bytearray()

        if process is not None:
            if kill and process.poll() is None:
                self._kill_process(process)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except (AttributeError, OSError, ValueError):
                    pass

        if reader_thread is not None and reader_thread is not threading.current_thread():
            reader_thread.join(timeout=1)

    @staticmethod
    def _decode(data: bytes | bytearray) -> str:
        return bytes(data).decode("utf-8", errors="replace")

    def _abort_locked(self, output: bytearray, message: str, returncode: int) -> dict:
        self._stop_locked(kill=True)
        partial = self._decode(output)
        return {
            "output": partial + message if partial else message.lstrip(),
            "returncode": returncode,
        }

    def execute(
        self,
        command: str,
        *,
        cwd: str,
        cwd_file: str,
        cwd_marker: str,
        timeout: int,
        stdin_data: str | None = None,
    ) -> dict:
        with self._lock:
            process = self._ensure_started_locked()
            token = uuid.uuid4().hex
            start_marker = f"\x1eVOIDCUBE_START_{token}\x1f".encode()
            end_prefix = f"\x1eVOIDCUBE_END_{token}:".encode()
            quoted_cwd = cwd if cwd == "~" or cwd.startswith("~/") else shlex.quote(cwd)
            if stdin_data is None:
                stdin_redirect = " </dev/null"
            else:
                encoded_stdin = "".join(
                    f"\\{byte:03o}" for byte in stdin_data.encode("utf-8")
                )
                stdin_redirect = (
                    f" < <(builtin printf '%b' {shlex.quote(encoded_stdin)})"
                )
            script = (
                "set +e; set +u; shopt -s expand_aliases\n"
                f"builtin printf '\\036VOIDCUBE_START_{token}\\037'\n"
                f"if builtin cd {quoted_cwd}; then\n"
                f"  eval {shlex.quote(command)}{stdin_redirect}\n"
                "  __voidcube_ec=$?\n"
                "else __voidcube_ec=126; fi\n"
                f"builtin pwd -P > {shlex.quote(cwd_file)} 2>/dev/null || true\n"
                f"builtin printf '\\n{cwd_marker}'; builtin pwd -P; "
                f"builtin printf '{cwd_marker}\\n'\n"
                f"builtin printf '\\036VOIDCUBE_END_{token}:%s\\037' \"$__voidcube_ec\"\n"
                "builtin unset __voidcube_ec\n"
            ).encode("utf-8")

            try:
                process.stdin.write(script)
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                self._stop_locked(kill=True)
                process = self._ensure_started_locked()
                try:
                    process.stdin.write(script)
                    process.stdin.flush()
                except (BrokenPipeError, OSError, ValueError):
                    self._stop_locked(kill=True)
                    return {
                        "output": "Persistent shell exited before command execution",
                        "returncode": 1,
                    }

            wait_started = time.monotonic()
            startup_deadline = wait_started + self._STARTUP_TIMEOUT
            command_deadline = None
            last_activity_touch = wait_started
            output = bytearray()
            frame_started = False

            while True:
                if is_interrupted():
                    if frame_started:
                        marker_at = self._buffer.find(end_prefix)
                        output.extend(
                            self._buffer[: marker_at if marker_at >= 0 else None]
                        )
                    return self._abort_locked(
                        output, "\n[Command interrupted]", 130
                    )

                now = time.monotonic()
                active_deadline = command_deadline or startup_deadline
                if now >= active_deadline:
                    if frame_started:
                        marker_at = self._buffer.find(end_prefix)
                        output.extend(
                            self._buffer[: marker_at if marker_at >= 0 else None]
                        )
                    message = (
                        f"\n[Command timed out after {timeout}s]"
                        if frame_started
                        else f"\n[Persistent shell startup timed out after {self._STARTUP_TIMEOUT:g}s]"
                    )
                    return self._abort_locked(
                        output, message, 124
                    )

                if now - last_activity_touch >= self._ACTIVITY_INTERVAL:
                    last_activity_touch = now
                    callback = _get_activity_callback()
                    if callback:
                        try:
                            callback(
                                f"terminal command running ({int(now - wait_started)}s elapsed)"
                            )
                        except Exception:
                            pass

                try:
                    chunk = self._chunks.get(
                        timeout=min(
                            self._POLL_INTERVAL,
                            max(0.0, active_deadline - now),
                        )
                    )
                except queue.Empty:
                    continue

                self._buffer.extend(chunk)
                if not frame_started:
                    marker_at = self._buffer.find(start_marker)
                    if marker_at >= 0:
                        del self._buffer[: marker_at + len(start_marker)]
                        frame_started = True
                        command_deadline = time.monotonic() + timeout

                if frame_started:
                    marker_at = self._buffer.find(end_prefix)
                    if marker_at >= 0:
                        marker_end = self._buffer.find(b"\x1f", marker_at + len(end_prefix))
                        if marker_end >= 0:
                            output.extend(self._buffer[:marker_at])
                            returncode = int(
                                self._buffer[marker_at + len(end_prefix) : marker_end]
                            )
                            del self._buffer[: marker_end + 1]
                            return {
                                "output": self._decode(output),
                                "returncode": returncode,
                            }

                if not chunk:
                    if frame_started:
                        output.extend(self._buffer)
                    try:
                        returncode = process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        returncode = process.poll()
                    self._stop_locked(kill=False)
                    return {
                        "output": self._decode(output),
                        "returncode": returncode if returncode is not None else 1,
                    }

    def close(self) -> None:
        with self._lock:
            self._stop_locked(kill=True)


class LocalEnvironment(BaseEnvironment):
    """Run commands directly on the host machine.

    Persistent mode serializes foreground commands through one login shell so
    cwd, environment variables, shell variables, aliases, and functions survive
    naturally. Non-persistent mode retains the snapshot-based process isolation.
    """

    def __init__(
        self,
        cwd: str = "",
        timeout: int = 60,
        env: dict = None,
        persistent: bool = False,
    ):
        super().__init__(cwd=cwd or os.getcwd(), timeout=timeout, env=env)
        self._persistent = persistent
        self._execute_lock = threading.Lock()
        self._persistent_shell = None
        if persistent:
            self._persistent_shell = _PersistentBashSession(
                _make_run_env(self.env), self._kill_process
            )
        else:
            self.init_session()

    def get_temp_dir(self) -> str:
        """Return a shell-safe writable temp dir for local execution.

        Termux does not provide /tmp by default, but exposes a POSIX TMPDIR.
        Prefer POSIX-style env vars when available, keep using /tmp on regular
        Unix systems, and only fall back to tempfile.gettempdir() when it also
        resolves to a POSIX path.

        Check the environment configured for this backend first so callers can
        override the temp root explicitly (for example via terminal.env or a
        custom TMPDIR), then fall back to the host process environment.
        """
        for env_var in ("TMPDIR", "TMP", "TEMP"):
            candidate = self.env.get(env_var) or os.environ.get(env_var)
            if candidate and candidate.startswith("/"):
                return candidate.rstrip("/") or "/"

        if os.path.isdir("/tmp") and os.access("/tmp", os.W_OK | os.X_OK):
            return "/tmp"

        candidate = tempfile.gettempdir()
        if candidate.startswith("/"):
            return candidate.rstrip("/") or "/"

        return "/tmp"

    def _run_bash(self, cmd_string: str, *, login: bool = False,
                  timeout: int = 120,
                  stdin_data: str | None = None) -> subprocess.Popen:
        bash = _find_bash()
        args = [bash, "-l", "-c", cmd_string] if login else [bash, "-c", cmd_string]
        run_env = _make_run_env(self.env)

        proc = subprocess.Popen(
            args,
            cwd=self.cwd,
            text=True,
            env=run_env,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            preexec_fn=None if _IS_WINDOWS else os.setsid,
        )

        if stdin_data is not None:
            _pipe_stdin(proc, stdin_data)

        return proc

    def _kill_process(self, proc):
        """Kill the entire process group (all children)."""
        try:
            if _IS_WINDOWS:
                proc.terminate()
            else:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except Exception:
                pass

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict:
        if not self._persistent_shell:
            return super().execute(command, cwd, timeout=timeout, stdin_data=stdin_data)

        with self._execute_lock:
            self._before_execute()
            exec_command, sudo_stdin = self._prepare_command(command)
            if sudo_stdin is not None and stdin_data is not None:
                effective_stdin = sudo_stdin + stdin_data
            elif sudo_stdin is not None:
                effective_stdin = sudo_stdin
            else:
                effective_stdin = stdin_data

            result = self._persistent_shell.execute(
                exec_command,
                cwd=cwd or self.cwd,
                cwd_file=self._cwd_file,
                cwd_marker=self._cwd_marker,
                timeout=timeout or self.timeout,
                stdin_data=effective_stdin,
            )
            if result["returncode"] not in (124, 130):
                self._update_cwd(result)
            return result

    def _update_cwd(self, result: dict):
        """Read CWD from temp file (local-only, no round-trip needed)."""
        previous_cwd = self.cwd
        try:
            with open(self._cwd_file, 'r') as f:
                cwd_path = f.read().strip()
                if cwd_path:
                    self.cwd = cwd_path
        except (OSError, FileNotFoundError):
            pass

        # Still strip the marker from output so it's not visible
        self._extract_cwd_from_output(result)
        from tools.path_runtime import normalize_host_path

        normalized_cwd = normalize_host_path(self.cwd)
        self.cwd = normalized_cwd if os.path.isdir(normalized_cwd) else previous_cwd

    def cleanup(self):
        """Stop the persistent shell and clean up temp files."""
        if self._persistent_shell:
            self._persistent_shell.close()
        self._cleanup_temp_files()
