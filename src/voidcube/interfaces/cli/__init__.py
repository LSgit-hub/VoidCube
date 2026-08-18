"""CLI interface and launcher composition."""


def main(*args, **kwargs):
    """Load the canonical root launcher lazily.

    The root launcher owns fast paths such as ``--help`` and ``--version``
    before any daemon or repository-only service setup can run.
    """
    from .root_launcher import main as launcher_main

    return launcher_main(*args, **kwargs)


__all__ = ["main"]
