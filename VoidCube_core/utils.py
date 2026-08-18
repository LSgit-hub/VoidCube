"""Legacy compatibility facade for shared helpers.

New code imports value helpers from ``infrastructure.shared`` and persistence
operations from ``infrastructure.persistence.file_store``.
"""

from VoidCube_app.infrastructure.persistence.file_store import (
    atomic_json_write,
    atomic_yaml_write,
    interprocess_file_lock,
)
from VoidCube_app.infrastructure.shared.value_helpers import (
    TRUTHY_STRINGS,
    append_jsonl,
    env_bool,
    env_int,
    env_lower,
    env_str,
    env_var_enabled,
    is_truthy_value,
    normalize_str,
    read_file_if_exists,
    read_json_file,
    read_jsonl,
    safe_dict_get,
    safe_json_loads,
)

__all__ = [
    "TRUTHY_STRINGS",
    "append_jsonl",
    "atomic_json_write",
    "atomic_yaml_write",
    "env_bool",
    "env_int",
    "env_lower",
    "env_str",
    "env_var_enabled",
    "interprocess_file_lock",
    "is_truthy_value",
    "normalize_str",
    "read_file_if_exists",
    "read_json_file",
    "read_jsonl",
    "safe_dict_get",
    "safe_json_loads",
]
