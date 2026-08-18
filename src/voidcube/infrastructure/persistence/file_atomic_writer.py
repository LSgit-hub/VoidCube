"""
原子文件写入 — 先写临时文件再 os.replace() 替换目标，防止中断导致文件损坏。

Cross-platform compatible: uses os.replace() for atomic rename on both Windows and POSIX.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class FileAtomicWriter:
    @staticmethod
    def write_text(target: Path, content: str, encoding: str = "utf-8") -> None:
        tmp_path = target.with_suffix(target.suffix + ".tmp")
        try:
            tmp_path.write_text(content, encoding=encoding)
            os.replace(str(tmp_path), str(target))
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    @staticmethod
    def write_bytes(target: Path, content: bytes) -> None:
        tmp_path = target.with_suffix(target.suffix + ".tmp")
        try:
            tmp_path.write_bytes(content)
            os.replace(str(tmp_path), str(target))
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    @staticmethod
    def write_yaml(target: Path, data: Dict[str, Any]) -> None:
        import yaml
        content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
        FileAtomicWriter.write_text(target, content)

    @staticmethod
    def write_json(target: Path, data: Dict[str, Any], indent: int = 2) -> None:
        import json
        content = json.dumps(data, indent=indent, ensure_ascii=False)
        FileAtomicWriter.write_text(target, content)

    @staticmethod
    def backup(target: Path, max_backups: int = 7) -> Optional[Path]:
        if not target.exists():
            return None
        import time
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_path = Path(f"{target}.bak.{timestamp}")
        try:
            import shutil
            shutil.copy2(str(target), str(backup_path))
            FileAtomicWriter._cleanup_backups(target, max_backups)
            return backup_path
        except Exception as e:
            logger.warning(f"Failed to create backup of {target}: {e}")
            return None

    @staticmethod
    def _cleanup_backups(target: Path, max_backups: int) -> None:
        import time
        backup_pattern = f"{target.name}.bak.*"
        parent = target.parent
        backups = sorted(parent.glob(backup_pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        for old_backup in backups[max_backups:]:
            try:
                old_backup.unlink()
            except Exception:
                pass

    @staticmethod
    def restore_from_backup(target: Path, backup_path: Path) -> bool:
        if not backup_path.exists():
            return False
        try:
            import shutil
            shutil.copy2(str(backup_path), str(target))
            return True
        except Exception as e:
            logger.error(f"Failed to restore {target} from {backup_path}: {e}")
            return False

    @staticmethod
    def list_backups(target: Path) -> list:
        backup_pattern = f"{target.name}.bak.*"
        return sorted(target.parent.glob(backup_pattern), key=lambda p: p.stat().st_mtime, reverse=True)
