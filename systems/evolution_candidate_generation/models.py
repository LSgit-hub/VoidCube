import sys
try:
    from voidcube.systems.evolution_candidate_generation import models as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.evolution_candidate_generation import models as _implementation
sys.modules[__name__] = _implementation

