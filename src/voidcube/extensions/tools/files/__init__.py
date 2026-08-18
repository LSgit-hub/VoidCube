"""Canonical file manipulation tools and their path/security helpers."""

from .binary_extensions import BINARY_EXTENSIONS, has_binary_extension
from .file_operations import PatchResult, ReadResult, SearchResult, ShellFileOperations, WriteResult
from .file_tools import (
    clear_file_ops_cache,
    notify_other_tool_call,
    reset_file_dedup,
)
from .fuzzy_match import fuzzy_find_and_replace
from .patch_parser import apply_v4a_operations, parse_v4a_patch
from .path_security import (
    get_safe_write_root,
    has_traversal_component,
    is_blocked_device,
    is_write_denied,
    validate_file_write_path,
    validate_within_dir,
)

__all__ = [
    "BINARY_EXTENSIONS",
    "PatchResult",
    "ReadResult",
    "SearchResult",
    "ShellFileOperations",
    "WriteResult",
    "apply_v4a_operations",
    "clear_file_ops_cache",
    "fuzzy_find_and_replace",
    "get_safe_write_root",
    "has_binary_extension",
    "has_traversal_component",
    "is_blocked_device",
    "is_write_denied",
    "notify_other_tool_call",
    "parse_v4a_patch",
    "reset_file_dedup",
    "validate_file_write_path",
    "validate_within_dir",
]
