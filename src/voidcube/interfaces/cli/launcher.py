"""Canonical CLI startup orchestration.

The interactive host lives in this package's ``application`` module. This module owns
argument-level startup routing, worktree setup, daemon policy, and the
single-query versus interactive choice.
"""

from __future__ import annotations

import atexit
import os
import sys
from typing import Optional

from . import application as _app
from ...infrastructure.gateway import daemon_runtime as _daemon_runtime
from .attachments import _collect_query_images
from .runtime_handlers import (
    _cleanup_worktree,
    _git_repo_root,
    _prune_stale_worktrees,
    _setup_worktree,
)
from .application import (
    VoidcubeCLI,
    _auto_start_daemons,
    _get_cli_config,
    _get_preloaded_skills_prompt,
    _handle_serve_command,
    _init_cli_runtime,
    _maybe_stop_daemons_on_exit,
    _parse_skills_argument,
    _run_cleanup,
    render_tools_for_host,
    render_toolsets_for_host,
    t,
)

CLI_CONFIG = None


def _get_language_preference_prompt() -> str:
    """Return a language preference injection for the active locale."""
    try:
        from .i18n import get_i18n

        locale = get_i18n().get_current_locale()
    except Exception:
        return ""

    return {
        "zh_CN": (
            "## 语言偏好 (Language Preference)\n"
            "请始终使用**简体中文**回复用户。代码注释、技术解释和对话都应使用中文。\n"
            "代码本身（变量名、函数名等）保留英文。技术术语若没有通用中文译名可保留英文原文。"
        ),
    }.get(locale, "")

def main(
    query: Optional[str] = None,
    q: Optional[str] = None,
    image: Optional[str] = None,
    toolsets: Optional[str] = None,
    skills: Optional[str | list[str] | tuple[str, ...]] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    max_turns: Optional[int] = None,
    verbose: bool = False,
    quiet: bool = False,
    compact: bool = False,
    list_tools: bool = False,
    list_toolsets: bool = False,
    resume: Optional[str] = None,
    worktree: bool = False,
    w: bool = False,
    checkpoints: bool = False,
    pass_session_id: bool = False,
    version: bool = False,
    serve: Optional[str] = None,
):
    """
    Voidcube Agent CLI - Interactive AI Assistant
    
    Args:
        query: Single query to execute (then exit). Alias: -q
        q: Shorthand for --query
        image: Optional local image path to attach to a single query
        toolsets: Comma-separated list of toolsets to enable (e.g., "web,terminal")
        skills: Comma-separated or repeated list of skills to preload for the session
        model: Model to use (default: from the active provider config)
        provider: Inference provider ("auto", "openrouter", "nous", "zai", "kimi-coding", "minimax", "minimax-cn")
        api_key: API key for authentication
        base_url: Base URL for the API
        max_turns: Maximum tool-calling iterations (default: 60)
        verbose: Enable verbose logging
        compact: Use compact display mode
        list_tools: List available tools and exit
        list_toolsets: List available toolsets and exit
        resume: Resume a previous session by its ID (e.g., 20260225_143052_a1b2c3)
        worktree: Run in an isolated git worktree (for parallel agents). Alias: -w
        w: Shorthand for --worktree
        version: Show version information and exit
    
    Examples:
        python cli.py                            # Start interactive mode
        python cli.py --toolsets web,terminal    # Use specific toolsets
        python cli.py --skills VoidCube-agent-dev,github-auth
        python cli.py -q "What is Python?"       # Single query mode
        python cli.py -q "Describe this" --image ~/storage/shared/Pictures/cat.png
        python cli.py --list-tools               # List tools and exit
        python cli.py --resume 20260225_143052_a1b2c3  # Resume session
        python cli.py -w                         # Start in isolated git worktree
        python cli.py -w -q "Fix issue #123"     # Single query in worktree
        python cli.py --version                  # Show version and exit
    """
    if version:
        from ...version import __version__

        print(f"VoidCube CLI v{__version__}")
        print("轻量安装·快速配置·友好交互 — 服务器运维与部署智能体")
        print("项目地址: https://gitee.com/LSgit-hub/voidcub-CLI")
        return

    # ── serve command ─────────────────────────────────────────────────
    if serve is not None:
        _handle_serve_command(serve)
        return

    # Deferred runtime initialization: logging, config, and tool preview.
    # Moved out of module-level to avoid ~300ms of import-chain cost at startup.
    _init_cli_runtime()

    # Ensure CLI_CONFIG is cached in module globals so bare-name references
    # in main(), VoidcubeCLI.__init__, and class methods resolve correctly.
    _app.CLI_CONFIG = _get_cli_config()
    globals()["CLI_CONFIG"] = _app.CLI_CONFIG

    # Signal to terminal_tool that we're in interactive mode
    # This enables interactive sudo password prompts with timeout
    os.environ["VOIDCUBE_INTERACTIVE"] = "1"

    # Skip worktree for list commands (they exit immediately)
    if not list_tools and not list_toolsets:
        # ── Git worktree isolation (#652) ──
        # Create an isolated worktree so this agent instance doesn't collide
        # with other agents working on the same repo.
        use_worktree = worktree or w or CLI_CONFIG.get("worktree", False)
        wt_info = None
        if use_worktree:
            # Prune stale worktrees from crashed/killed sessions
            _repo = _git_repo_root()
            if _repo:
                _prune_stale_worktrees(_repo)
            wt_info = _setup_worktree()
            if wt_info:
                os.environ["TERMINAL_CWD"] = wt_info["path"]
                atexit.register(_cleanup_worktree, wt_info)
            else:
                # Worktree was explicitly requested but setup failed —
                # don't silently run without isolation.
                return
    else:
        wt_info = None
    
    # Handle query shorthand
    query = query or q

    # ── Auto-start daemons (interactive mode only) ─────────────────────
    # Single-query (-q), list commands, and other short-lived operations
    # skip the daemon lifecycle to keep startup fast.
    #
    # When VOIDCUBE_DAEMONS_STARTED=1 (set by voidcube.py), daemons were
    # already started by the wrapper — skip the start but still register
    # cleanup so /quit and atexit can stop them. The desktop shell owns its
    # service processes independently, so its embedded CLI neither starts
    # nor stops them.
    is_interactive = query is None and not list_tools and not list_toolsets
    daemons_already_started = os.environ.get("VOIDCUBE_DAEMONS_STARTED") == "1"
    desktop_manages_services = (
        os.environ.get("VOIDCUBE_DESKTOP_MANAGED_SERVICES") == "1"
    )
    if os.environ.get("VOIDCUBE_DESKTOP") == "1":
        from .execution_context import (
            clear_execution_context,
            publish_execution_context,
        )

        publish_execution_context(wt_info)
        atexit.register(clear_execution_context, os.getpid())
    if is_interactive and not desktop_manages_services:
        if daemons_already_started:
            # Daemons were started by voidcube.py — we still own cleanup
            _daemon_runtime.mark_daemons_auto_started()
            atexit.register(_maybe_stop_daemons_on_exit)
        else:
            _auto_start_daemons()
            atexit.register(_maybe_stop_daemons_on_exit)

    # Parse toolsets - handle both string and tuple/list inputs
    # Parse the explicitly selected toolsets when provided.
    toolsets_list = None
    if toolsets:
        if isinstance(toolsets, str):
            toolsets_list = [t.strip() for t in toolsets.split(",")]
        elif isinstance(toolsets, (list, tuple)):
            # Fire may pass multiple --toolsets as a tuple
            toolsets_list = []
            for t in toolsets:
                if isinstance(t, str):
                    toolsets_list.extend([x.strip() for x in t.split(",")])
                else:
                    toolsets_list.append(str(t))
    else:
        # Use the shared resolver so MCP servers are included at runtime
        from ...extensions.tools.configuration import get_platform_tools
        toolsets_list = sorted(get_platform_tools(CLI_CONFIG, "cli"))
    
    parsed_skills = _parse_skills_argument(skills)

    # Create CLI instance
    cli = VoidcubeCLI(
        model=model,
        toolsets=toolsets_list,
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        max_turns=max_turns,
        verbose=verbose,
        compact=compact,
        resume=resume,
        checkpoints=checkpoints,
        pass_session_id=pass_session_id,
    )

    if parsed_skills:
        skills_prompt, loaded_skills, missing_skills = _get_preloaded_skills_prompt(
            parsed_skills,
            task_id=cli.session_id,
        )
        if missing_skills:
            missing_display = ", ".join(missing_skills)
            raise ValueError(f"Unknown skill(s): {missing_display}")
        if skills_prompt:
            cli.system_prompt = "\n\n".join(
                part for part in (cli.system_prompt, skills_prompt) if part
            ).strip()
            cli.preloaded_skills = loaded_skills

    # Inject language preference based on current locale
    lang_prompt = _get_language_preference_prompt()
    if lang_prompt:
        cli.system_prompt = "\n\n".join(
            part for part in (cli.system_prompt, lang_prompt) if part
        ).strip()

    # Inject worktree context into agent's system prompt
    if wt_info:
        wt_note = (
            f"\n\n[System note: You are working in an isolated git worktree at "
            f"{wt_info['path']}. Your branch is `{wt_info['branch']}`. "
            f"Changes here do not affect the main working tree or other agents. "
            f"Remember to commit and push your changes, and create a PR if appropriate. "
            f"The original repo is at {wt_info['repo_root']}.]"
        )
        cli.system_prompt = (cli.system_prompt or "") + wt_note
    
    # Handle list commands (don't init agent for these)
    if list_tools:
        cli.show_banner()
        render_tools_for_host(cli, emit=print, translate=t)
        sys.exit(0)
    
    if list_toolsets:
        cli.show_banner()
        render_toolsets_for_host(cli, emit=print, translate=t)
        sys.exit(0)
    
    # Register cleanup for single-query mode (interactive mode registers in run())
    atexit.register(_run_cleanup)
    
    # Handle single query mode
    if query or image:
        query, single_query_images = _collect_query_images(query, image)
        if quiet:
            # Quiet mode: suppress banner, spinner, tool previews.
            # Only print the final response and parseable session info.
            cli.tool_progress_mode = "off"
            if cli._ensure_runtime_credentials():
                effective_query = query
                if single_query_images:
                    effective_query = cli._preprocess_images_with_vision(
                        query,
                        single_query_images,
                        announce=False,
                    )
                turn_route = cli._resolve_turn_agent_config(effective_query)
                if turn_route["signature"] != cli._active_agent_route_signature:
                    cli.agent = None
                if cli._init_agent(
                    model_override=turn_route["model"],
                    runtime_override=turn_route["runtime"],
                    route_label=turn_route["label"],
                    request_overrides=turn_route.get("request_overrides"),
                ):
                    cli.agent.quiet_mode = True
                    cli.agent.suppress_status_output = True
                    result = cli.agent.run_conversation(
                        user_message=effective_query,
                        conversation_history=cli.conversation_history,
                    )
                    response = result.get("final_response", "") if isinstance(result, dict) else str(result)
                    if response:
                        print(response)
                    print(f"\nsession_id: {cli.session_id}")
                    
                    # Ensure proper exit code for automation wrappers
                    sys.exit(1 if isinstance(result, dict) and result.get("failed") else 0)
            
            # Exit with error code if credentials or agent init fails
            sys.exit(1)
        else:
            cli.show_banner()
            _query_label = query or ("[image attached]" if single_query_images else "")
            if _query_label:
                cli.console.print(f"[bold blue]Query:[/] {_query_label}")
            cli.chat(query, images=single_query_images or None)
            cli._print_exit_summary()
        return
    
    # Run interactive mode
    cli.run()


if __name__ == "__main__":
    import fire

    fire.Fire(main)
