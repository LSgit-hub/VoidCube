"""CLI interface and launcher composition."""


def main(*args, **kwargs):
    """Load the launcher lazily to keep application imports acyclic."""
    from .launcher import main as launcher_main

    return launcher_main(*args, **kwargs)


__all__ = ["main"]
