import os
from typing import Optional
from pydantic import BaseModel, Field

from systems.supervisor.config_models import SupervisorConfig


class GatewayConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 6000
    auth_token: Optional[str] = None
    log_level: str = "INFO"


class MemoryServiceConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 6001
    db_path: str = "./memory.db"
    gateway_address: str = "http://127.0.0.1:6000"
    llm_api_key: Optional[str] = None
    llm_base_url: str = "https://api.deepseek.com"
    decay_interval_hours: int = 24

class AgentConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 6080
    gateway_address: str = "http://127.0.0.1:6000"


class SystemConfig(BaseModel):
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    memory: MemoryServiceConfig = Field(default_factory=MemoryServiceConfig)
    supervisor: SupervisorConfig = Field(default_factory=SupervisorConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)


def load_config_from_env() -> SystemConfig:
    config = SystemConfig()
    
    config.gateway.host = os.getenv("GATEWAY_HOST", config.gateway.host)
    config.gateway.port = int(os.getenv("GATEWAY_PORT", config.gateway.port))
    config.gateway.auth_token = os.getenv("GATEWAY_AUTH_TOKEN", config.gateway.auth_token)
    config.gateway.log_level = os.getenv("GATEWAY_LOG_LEVEL", config.gateway.log_level)
    
    config.memory.host = os.getenv("MEMORY_HOST", config.memory.host)
    config.memory.port = int(os.getenv("MEMORY_PORT", config.memory.port))
    config.memory.db_path = os.getenv("MEMORY_DB_PATH", config.memory.db_path)
    config.memory.gateway_address = os.getenv("MEMORY_GATEWAY_ADDRESS", config.memory.gateway_address)
    config.memory.llm_api_key = os.getenv("DEEPSEEK_API_KEY", config.memory.llm_api_key)
    config.memory.llm_base_url = os.getenv("MEMORY_LLM_BASE_URL", config.memory.llm_base_url)
    config.memory.decay_interval_hours = int(os.getenv("MEMORY_DECAY_INTERVAL", config.memory.decay_interval_hours))
    
    config.supervisor.host = os.getenv("SUPERVISOR_HOST", config.supervisor.host)
    config.supervisor.port = int(os.getenv("SUPERVISOR_PORT", config.supervisor.port))
    config.supervisor.ui_enabled = (
        os.getenv("SUPERVISOR_UI_ENABLED", str(config.supervisor.ui_enabled)).strip().lower()
        not in {"0", "false", "no", "off"}
    )
    config.supervisor.ui_auto_open = (
        os.getenv("SUPERVISOR_UI_AUTO_OPEN", str(config.supervisor.ui_auto_open)).strip().lower()
        not in {"0", "false", "no", "off"}
    )
    config.supervisor.ui_auto_open_delay_seconds = float(
        os.getenv(
            "SUPERVISOR_UI_AUTO_OPEN_DELAY_SECONDS",
            config.supervisor.ui_auto_open_delay_seconds,
        )
    )
    config.supervisor.ui_event_interval_seconds = float(
        os.getenv(
            "SUPERVISOR_UI_EVENT_INTERVAL_SECONDS",
            config.supervisor.ui_event_interval_seconds,
        )
    )
    config.supervisor.ui_activity_buffer_size = int(
        os.getenv(
            "SUPERVISOR_UI_ACTIVITY_BUFFER_SIZE",
            config.supervisor.ui_activity_buffer_size,
        )
    )
    config.supervisor.ui_path = os.getenv("SUPERVISOR_UI_PATH", config.supervisor.ui_path)
    config.supervisor.execution.gateway_address = os.getenv(
        "SUPERVISOR_GATEWAY_ADDRESS",
        config.supervisor.execution.gateway_address,
    )
    config.supervisor.service_runtime.health_check_interval = int(
        os.getenv(
            "SUPERVISOR_HEALTH_INTERVAL",
            config.supervisor.service_runtime.health_check_interval,
        )
    )
    config.supervisor.service_runtime.memory_compression_interval = int(
        os.getenv(
            "SUPERVISOR_COMPRESSION_INTERVAL",
            config.supervisor.service_runtime.memory_compression_interval,
        )
    )
    config.supervisor.service_runtime.self_evolution_review_interval = int(
        os.getenv(
            "SUPERVISOR_SELF_EVOLUTION_REVIEW_INTERVAL",
            config.supervisor.service_runtime.self_evolution_review_interval,
        )
    )
    config.supervisor.service_runtime.endogenous_drive_enabled = (
        os.getenv(
            "SUPERVISOR_ENDOGENOUS_DRIVE_ENABLED",
            str(config.supervisor.service_runtime.endogenous_drive_enabled),
        ).strip().lower()
        not in {"0", "false", "no", "off"}
    )
    config.supervisor.service_runtime.endogenous_drive_interval = int(
        os.getenv(
            "SUPERVISOR_ENDOGENOUS_DRIVE_INTERVAL",
            config.supervisor.service_runtime.endogenous_drive_interval,
        )
    )
    config.supervisor.service_runtime.endogenous_drive_max_candidates = int(
        os.getenv(
            "SUPERVISOR_ENDOGENOUS_DRIVE_MAX_CANDIDATES",
            config.supervisor.service_runtime.endogenous_drive_max_candidates,
        )
    )
    config.supervisor.execution.git_repo_path = os.getenv(
        "SUPERVISOR_GIT_REPO",
        config.supervisor.execution.git_repo_path,
    )
    config.supervisor.body_runtime.stable_window_days = int(
        os.getenv("BODY_STABLE_WINDOW_DAYS", config.supervisor.body_runtime.stable_window_days)
    )
    config.supervisor.body_runtime.stable_health_checks = int(
        os.getenv("BODY_STABLE_HEALTH_CHECKS", config.supervisor.body_runtime.stable_health_checks)
    )
    
    config.agent.host = os.getenv("AGENT_HOST", config.agent.host)
    config.agent.port = int(os.getenv("AGENT_PORT", config.agent.port))
    config.agent.gateway_address = os.getenv("AGENT_GATEWAY_ADDRESS", config.agent.gateway_address)

    config.supervisor.body_runtime.slots_dir_name = os.getenv(
        "BODY_SLOTS_DIR_NAME",
        config.supervisor.body_runtime.slots_dir_name,
    )
    config.supervisor.body_runtime.registry_file_name = os.getenv(
        "BODY_REGISTRY_FILE_NAME",
        config.supervisor.body_runtime.registry_file_name,
    )
    config.supervisor.body_runtime.slot_a_name = os.getenv(
        "BODY_SLOT_A_NAME",
        config.supervisor.body_runtime.slot_a_name,
    )
    config.supervisor.body_runtime.slot_b_name = os.getenv(
        "BODY_SLOT_B_NAME",
        config.supervisor.body_runtime.slot_b_name,
    )
    config.supervisor.execution.probe_watch_window_seconds = int(
        os.getenv(
            "BODY_PROBE_WATCH_WINDOW_SECONDS",
            config.supervisor.execution.probe_watch_window_seconds,
        )
    )
    
    return config


def get_config() -> SystemConfig:
    return load_config_from_env()
