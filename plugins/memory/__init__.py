"""Memory provider plugin loader.

Loads external memory providers by name. Each provider must implement the
MemoryProvider interface from agent.memory_provider.
"""

from typing import Optional

from agent.memory_provider import MemoryProvider


def load_memory_provider(name: str) -> Optional[MemoryProvider]:
    """Load a memory provider plugin by name.
    
    Args:
        name: The name of the memory provider ("mem", "hindsight", etc.)
    
    Returns:
        An instance of the MemoryProvider, or None if not found or unavailable.
    """
    try:
        if name == "mem":
            from plugins.memory.mem import MemMemoryProvider
            return MemMemoryProvider()
        elif name == "hindsight":
            from plugins.memory.hindsight import HindsightMemoryProvider
            return HindsightMemoryProvider()
    except ImportError as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning("Failed to load memory provider '%s': %s", name, e)
    
    return None
