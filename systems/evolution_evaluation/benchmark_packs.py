import sys
try:
    from voidcube.systems.evolution_evaluation import benchmark_packs as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.evolution_evaluation import benchmark_packs as _implementation
sys.modules[__name__] = _implementation

