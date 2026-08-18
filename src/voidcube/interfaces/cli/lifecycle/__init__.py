"""Canonical CLI lifecycle composition and run-state adapters."""

from .assembly import CliInteractiveLifecycleAssemblyPorts, CliInteractiveLifecycleAssemblyRuntime
from .registration import CliInteractiveRegistrationPorts, CliInteractiveRegistrationRuntime, CliInteractiveRegistrations
from .runtime import CliInteractiveLifecyclePorts, CliInteractiveLifecycleRuntime
from .run import CliRunRuntime, CliRunRuntimePorts
from .state import CliInteractiveRunState, CliInteractiveStateApplyPorts, CliInteractiveStatePorts, CliInteractiveStateRuntime
from .startup import CliStartupPorts, CliStartupRuntime

__all__ = [
    "CliInteractiveLifecycleAssemblyPorts",
    "CliInteractiveLifecycleAssemblyRuntime",
    "CliInteractiveRegistrationPorts",
    "CliInteractiveRegistrationRuntime",
    "CliInteractiveRegistrations",
    "CliInteractiveLifecyclePorts",
    "CliInteractiveLifecycleRuntime",
    "CliRunRuntime",
    "CliRunRuntimePorts",
    "CliInteractiveRunState",
    "CliInteractiveStateApplyPorts",
    "CliInteractiveStatePorts",
    "CliInteractiveStateRuntime",
    "CliStartupPorts",
    "CliStartupRuntime",
]
