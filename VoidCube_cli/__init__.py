"""
Voidcube CLI - Unified command-line interface for Voidcube Agent.

Provides subcommands for:
- VoidCube chat          - Interactive chat (same as ./VoidCube)
- VoidCube gateway       - Run gateway in foreground
- VoidCube gateway start - Start gateway service
- VoidCube gateway stop  - Stop gateway service  
- VoidCube api           - API configuration
- VoidCube status        - Show status of all components
"""

try:
    from voidcube.version import __version__
except (ModuleNotFoundError, ImportError):
    try:
        from src.voidcube.version import __version__
    except (ModuleNotFoundError, ImportError):
        # setuptools imports this compatibility package from an isolated
        # build environment where the source-layout namespace is not importable.
        from pathlib import Path
        from runpy import run_path

        _version_file = (
            Path(__file__).resolve().parents[1] / "src" / "voidcube" / "version.py"
        )
        _version_namespace = run_path(str(_version_file))
        __version__ = str(_version_namespace["__version__"])
