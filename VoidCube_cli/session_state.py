"""Session State Management.

This module provides persistent state management for sessions,
including working directory tracking and resume capability.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
from VoidCube_core.constants import get_VoidCube_home


class SessionState:
    """Manages persistent session state.

    Tracks:
        - Current working directory
        - Session start time
        - Last active time
        - Custom state data
    """

    def __init__(self, session_id: str, VoidCube_home: Optional[Path] = None):
        """Initialize session state manager.

        Args:
            session_id: Unique session identifier
            VoidCube_home: Path to VoidCube home directory (default: get_VoidCube_home())
        """
        self.session_id = session_id
        self.VoidCube_home = VoidCube_home or get_VoidCube_home()
        self.state_dir = self.VoidCube_home / "sessions"
        self.state_file = self.state_dir / f"{session_id}_state.json"
        self.state: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load state from disk."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    self.state = json.load(f)
            except Exception:
                self.state = {}

        # Ensure default structure
        if 'created_at' not in self.state:
            self.state['created_at'] = datetime.now().isoformat()
        if 'last_active' not in self.state:
            self.state['last_active'] = datetime.now().isoformat()
        if 'cwd' not in self.state:
            self.state['cwd'] = str(Path.cwd())
        if 'custom' not in self.state:
            self.state['custom'] = {}

    def _save(self) -> None:
        """Save state to disk."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state['last_active'] = datetime.now().isoformat()
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @property
    def cwd(self) -> str:
        """Get current working directory."""
        return self.state.get('cwd', str(Path.cwd()))

    @cwd.setter
    def cwd(self, path: str) -> None:
        """Set current working directory."""
        self.state['cwd'] = str(path)
        self._save()

    @property
    def created_at(self) -> str:
        """Get session creation time."""
        return self.state.get('created_at', datetime.now().isoformat())

    @property
    def last_active(self) -> str:
        """Get last active time."""
        return self.state.get('last_active', datetime.now().isoformat())

    def get_custom(self, key: str, default: Any = None) -> Any:
        """Get custom state value."""
        return self.state.get('custom', {}).get(key, default)

    def set_custom(self, key: str, value: Any) -> None:
        """Set custom state value."""
        if 'custom' not in self.state:
            self.state['custom'] = {}
        self.state['custom'][key] = value
        self._save()

    def delete_custom(self, key: str) -> None:
        """Delete custom state value."""
        if 'custom' in self.state and key in self.state['custom']:
            del self.state['custom'][key]
            self._save()

    def get_state(self) -> Dict[str, Any]:
        """Get full state dictionary."""
        return dict(self.state)

    def update_state(self, data: Dict[str, Any]) -> None:
        """Update multiple state values at once."""
        self.state.update(data)
        self._save()


def list_sessions(VoidCube_home: Optional[Path] = None) -> List[Dict[str, Any]]:
    """List all available sessions.

    Args:
        VoidCube_home: Path to VoidCube home directory

    Returns:
        List of session info dictionaries
    """
    home = VoidCube_home or get_VoidCube_home()
    state_dir = home / "sessions"
    sessions = []

    if not state_dir.exists():
        return sessions

    for state_file in state_dir.glob("*_state.json"):
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
            session_id = state_file.stem.replace("_state", "")
            sessions.append({
                "session_id": session_id,
                "created_at": state.get('created_at', ''),
                "last_active": state.get('last_active', ''),
                "cwd": state.get('cwd', ''),
            })
        except Exception:
            continue

    # Sort by last active time (newest first)
    sessions.sort(key=lambda x: x['last_active'], reverse=True)
    return sessions


def load_session(session_id: str, VoidCube_home: Optional[Path] = None) -> Optional[SessionState]:
    """Load an existing session by ID.

    Args:
        session_id: Session identifier
        VoidCube_home: Path to VoidCube home directory

    Returns:
        SessionState object or None if not found
    """
    state_file = (VoidCube_home or get_VoidCube_home()) / "sessions" / f"{session_id}_state.json"
    if not state_file.exists():
        return None
    return SessionState(session_id, VoidCube_home)


def delete_session(session_id: str, VoidCube_home: Optional[Path] = None) -> bool:
    """Delete a session by ID.

    Args:
        session_id: Session identifier
        VoidCube_home: Path to VoidCube home directory

    Returns:
        True if deleted, False otherwise
    """
    home = VoidCube_home or get_VoidCube_home()
    state_file = home / "sessions" / f"{session_id}_state.json"
    log_file = home / "sessions" / f"session_{session_id}.json"

    deleted = False
    if state_file.exists():
        state_file.unlink()
        deleted = True
    if log_file.exists():
        log_file.unlink()
        deleted = True
    return deleted


def cleanup_old_sessions(max_age_days: int = 30, VoidCube_home: Optional[Path] = None) -> int:
    """Clean up old sessions.

    Args:
        max_age_days: Maximum age of sessions to keep
        VoidCube_home: Path to VoidCube home directory

    Returns:
        Number of sessions deleted
    """
    from datetime import timedelta
    now = datetime.now()
    deleted_count = 0

    for session_info in list_sessions(VoidCube_home):
        try:
            last_active = datetime.fromisoformat(session_info['last_active'])
            if now - last_active > timedelta(days=max_age_days):
                if delete_session(session_info['session_id'], VoidCube_home):
                    deleted_count += 1
        except Exception:
            pass

    return deleted_count
