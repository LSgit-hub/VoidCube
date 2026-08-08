"""The single built-in VoidCube CLI visual style.

Keep terminal presentation tokens here so prompt-toolkit, Rich and ANSI
fallbacks share one visual language. The palette is intentionally restrained:
cool cyan carries focus, warm amber marks attention, and status colors are
reserved for state rather than decoration.
"""

from __future__ import annotations


# Theme tokens
BACKGROUND = "#0B1016"
SURFACE = "#111923"
BORDER = "#273443"
TEXT = "#E7EDF4"
MUTED = "#8190A0"
PRIMARY = "#69D9E8"
ACCENT = "#F0B86E"
SECONDARY = "#A7B7FF"
INFO = "#7FB2FF"
GOOD = "#65D6A1"
WARN = "#F3C969"
DANGER = "#FF7D8A"

# Compatibility names used by display-only ports. They remain aliases to the
# canonical tokens, rather than a second palette.
BANNER_ACCENT = PRIMARY
BANNER_BORDER = BORDER
BANNER_DIM = MUTED
BANNER_TEXT = TEXT
BANNER_TITLE = PRIMARY
RESPONSE_LABEL = "> Voidcube"
PROMPT_SYMBOL = "❯ "
GOODBYE = "bye."
AGENT_NAME = "Voidcube Agent"
