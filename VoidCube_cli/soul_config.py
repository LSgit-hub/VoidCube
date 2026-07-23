"""SOUL.md Configuration Parser.

This module provides structured configuration support from SOUL.md,
including YAML configuration extraction and validation.
"""

import re
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List
from VoidCube_core.constants import get_VoidCube_home


# Default SOUL configuration values
DEFAULT_SOUL_CONFIG = {
    "security": {
        "dangerous_commands": ["rm -rf", "mkfs", "dd", ":(){ :|:& };:", "chmod 777", "chown root", "iptables -F"],
        "approval_mode": "ask"
    },
    "logging": {
        "level": "info",
        "path": "~/.VoidCube/logs",
        "max_log_size": 10
    },
    "agent": {
        "max_tool_workers": 5,
        "default_detail_level": "standard",
        "thinking_mode": "auto"
    },
    "tools": {
        "allowed": ["*"],
        "blocked": [],
        "default_timeout": 300
    },
    "voice": {
        "enabled": False,
        "input_device": "",
        "output_device": "",
        "wake_word": "Voidcube"
    }
}


def extract_yaml_from_soul(content: str) -> Optional[str]:
    """Extract YAML configuration block from SOUL.md content.

    Looks for configuration sections with YAML-like structure between
    configuration markers or directly after section headers.

    Args:
        content: Full SOUL.md content

    Returns:
        Extracted YAML string or None if no configuration found
    """
    # Look for YAML blocks that start after ## Configuration and before ## End of Configuration
    config_start = content.find("## Configuration")
    if config_start == -1:
        return None

    # Look for end marker or just take everything after config section
    end_marker = "## End of Configuration"
    config_end = content.find(end_marker)
    if config_end == -1:
        config_section = content[config_start:]
    else:
        config_section = content[config_start:config_end]

    # Extract lines that look like YAML (with colons and indentation)
    yaml_lines = []
    in_yaml_section = False

    for line in config_section.split("\n"):
        line_stripped = line.strip()

        # Skip empty lines and section headers
        if not line_stripped or line_stripped.startswith("#"):
            continue

        # Look for lines with key: value structure
        if ":" in line_stripped and not line_stripped.startswith("http"):
            in_yaml_section = True
            yaml_lines.append(line)
        elif in_yaml_section and (line.startswith("  ") or line.startswith("\t")):
            yaml_lines.append(line)
        elif in_yaml_section:
            # End of YAML block
            break

    if yaml_lines:
        return "\n".join(yaml_lines)

    return None


def parse_soul_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Parse SOUL.md configuration file.

    Args:
        path: Path to SOUL.md (defaults to VOIDCUBE_HOME/SOUL.md)

    Returns:
        Parsed configuration dictionary with defaults applied
    """
    if path is None:
        path = get_VoidCube_home() / "SOUL.md"

    config = dict(DEFAULT_SOUL_CONFIG)

    if not path.exists():
        # Create default SOUL.md if it doesn't exist
        try:
            from VoidCube_cli.default_soul import DEFAULT_SOUL_MD
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_SOUL_MD, encoding="utf-8")
        except Exception:
            pass
        return config

    try:
        content = path.read_text(encoding="utf-8")

        # Check if configuration section exists
        if "## Configuration" not in content:
            # Append configuration section to existing content
            from VoidCube_cli.default_soul import DEFAULT_SOUL_MD
            # Extract just the configuration part from default
            config_start = DEFAULT_SOUL_MD.find("## Configuration")
            if config_start != -1:
                config_section = DEFAULT_SOUL_MD[config_start:]
                content += "\n\n" + config_section
                # Write back with configuration appended
                path.write_text(content, encoding="utf-8")

        # Extract YAML configuration
        yaml_content = extract_yaml_from_soul(content)

        if yaml_content:
            parsed_yaml = yaml.safe_load(yaml_content)
            if parsed_yaml and isinstance(parsed_yaml, dict):
                # Deep merge with defaults
                _deep_merge_inplace(config, parsed_yaml)

    except Exception as e:
        # Silently fail, return defaults
        pass

    return config


def _deep_merge_inplace(target: Dict, source: Dict) -> None:
    """Deep merge source dictionary into target (mutates in-place).

    Args:
        target: Target dictionary to merge into
        source: Source dictionary to merge from
    """
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _deep_merge_inplace(target[key], value)
        else:
            target[key] = value


def get_soul_personality(path: Optional[Path] = None) -> str:
    """Get the core personality text from SOUL.md, without configuration.

    Args:
        path: Path to SOUL.md (defaults to VOIDCUBE_HOME/SOUL.md)

    Returns:
        Personality text string
    """
    if path is None:
        path = get_VoidCube_home() / "SOUL.md"

    if not path.exists():
        from VoidCube_cli.default_soul import DEFAULT_SOUL_MD
        # Extract just the personality part from default
        default_content = DEFAULT_SOUL_MD
        config_start = default_content.find("## Configuration")
        if config_start != -1:
            return default_content[:config_start].strip()
        return default_content

    try:
        content = path.read_text(encoding="utf-8")
        config_start = content.find("## Configuration")
        if config_start != -1:
            return content[:config_start].strip()
        return content.strip()
    except Exception:
        from VoidCube_cli.default_soul import DEFAULT_SOUL_MD
        config_start = DEFAULT_SOUL_MD.find("## Configuration")
        if config_start != -1:
            return DEFAULT_SOUL_MD[:config_start].strip()
        return DEFAULT_SOUL_MD
