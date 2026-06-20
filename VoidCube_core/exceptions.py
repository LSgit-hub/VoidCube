class VoidCubeError(Exception):
    pass

class ToolExecutionError(VoidCubeError):
    pass

class ConfigError(VoidCubeError):
    pass

class ApprovalRequiredError(VoidCubeError):
    def __init__(self, command: str, reason: str = ""):
        self.command = command
        self.reason = reason
        super().__init__(f"Approval required for command: {command} ({reason})")
