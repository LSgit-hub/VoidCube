import sys
try:
    from voidcube.systems.evolution_evaluation import repository as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.evolution_evaluation import repository as _implementation
sys.modules[__name__] = _implementation

