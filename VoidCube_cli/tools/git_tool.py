"""
Git Management Tool — let the agent manage local git repositories.

Designed for self-healing/self-evolution workflows:
  - Automatic commit checkpoints before risky modifications
  - Branch creation for experimental changes
  - Instant rollback via revert/reset
  - Change audit via log/diff

All operations are scoped to the workspace directory (terminal cwd).
"""

from VoidCube_cli.tools import register_tool


TOOL_SCHEMA = {
    "name": "git_manage",
    "description": "Manage a local git repository for code change tracking and "
                   "rollback.  Supports status, diff, log, commit, branch, "
                   "revert, reset, stash, and tag operations.  "
                   "Use this to checkpoint before risky changes, create "
                   "experimental branches, and audit what changed.  "
                   "All operations are scoped to the current workspace directory.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "status",       # show working tree status
                    "diff",         # show changes (unstaged by default)
                    "diff_staged",  # show staged changes only
                    "log",          # show commit history
                    "commit",       # stage all + commit
                    "add",          # stage specific files
                    "branch",       # list/create/switch branches
                    "checkout",     # switch branch or restore files
                    "revert",       # revert a specific commit
                    "reset",        # reset to a previous commit
                    "stash",        # stash changes
                    "stash_pop",    # pop stashed changes
                    "tag",          # create/list tags
                    "remote_status",# show remote tracking info
                    "pull",         # pull from remote
                    "push",         # push to remote
                ],
                "description": "Git operation to perform:\n"
                               "- status: show working tree status (modified, staged, untracked)\n"
                               "- diff: show unstaged changes\n"
                               "- diff_staged: show staged changes\n"
                               "- log: show recent commit history\n"
                               "- commit: stage all changes and commit with message\n"
                               "- add: stage specific files\n"
                               "- branch: list branches or create/switch\n"
                               "- checkout: switch branch or restore file(s)\n"
                               "- revert: revert a commit (safe, creates new commit)\n"
                               "- reset: reset to a commit (use --soft by default)\n"
                               "- stash: stash current changes\n"
                               "- stash_pop: restore stashed changes\n"
                               "- tag: create or list tags\n"
                               "- remote_status: show ahead/behind remote\n"
                               "- pull: pull from remote\n"
                               "- push: push to remote",
            },
            "message": {
                "type": "string",
                "description": "Commit message (required for 'commit' action). "
                               "Use conventional commits format: "
                               "'feat: ...', 'fix: ...', 'refactor: ...', "
                               "'chore: ...', 'revert: ...'",
            },
            "files": {
                "type": "array",
                "description": "List of file paths to stage (for 'add' action) "
                               "or restore (for 'checkout' action). "
                               "Use '.' to stage all.",
                "items": {"type": "string"},
            },
            "branch": {
                "type": "string",
                "description": "Branch name for 'branch' (create), 'checkout' "
                               "(switch to), or 'push' actions.",
            },
            "target": {
                "type": "string",
                "description": "Target commit hash, branch, or tag for "
                               "'revert', 'reset', 'diff', or 'log' actions. "
                               "Use 'HEAD~1' for one commit back.",
            },
            "limit": {
                "type": "integer",
                "description": "Max number of entries for 'log' action "
                               "(default: 10, max: 50)",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
            },
            "stash_message": {
                "type": "string",
                "description": "Optional description for 'stash' action.",
            },
            "tag_name": {
                "type": "string",
                "description": "Tag name for 'tag' action.",
            },
            "create_branch": {
                "type": "boolean",
                "description": "For 'checkout' action: create the branch "
                               "before switching (-b flag).",
                "default": False,
            },
        },
        "required": ["action"],
    },
}


def register() -> None:
    """Register the git management tool schema."""
    register_tool("git_manage", TOOL_SCHEMA)
