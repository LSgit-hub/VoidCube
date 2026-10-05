"""Compatibility exports for external CLI integrations.

Production callers use ``voidcube.application.session_goal`` directly; this
module remains a stable import path for third-party extensions and older
embedders.
"""

from ...application.session_goal import *  # noqa: F401,F403
from ...application.session_goal import __all__
