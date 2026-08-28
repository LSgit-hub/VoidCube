from __future__ import annotations

def cmd_login(args):
    """Authenticate Voidcube CLI with a provider."""
    from ..auth import login_command
    login_command(args)


def cmd_logout(args):
    """Clear provider authentication."""
    from ..auth import logout_command
    logout_command(args)




def cmd_status(args):
    """Show status of all components."""
    from ..status import show_status
    show_status(args)
