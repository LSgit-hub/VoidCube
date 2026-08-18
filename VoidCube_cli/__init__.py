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
    from src.voidcube.version import __version__
