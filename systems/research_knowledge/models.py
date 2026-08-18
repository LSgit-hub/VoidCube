import sys
try:
    from voidcube.systems.research_knowledge import models as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.research_knowledge import models as _implementation
sys.modules[__name__] = _implementation

