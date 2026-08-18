"""Provider endpoint constants."""

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS_URL = f"{OPENROUTER_BASE_URL}/models"
AI_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"
NOUS_API_BASE_URL = "https://inference-api.nousresearch.com/v1"

__all__ = [
    "AI_GATEWAY_BASE_URL",
    "NOUS_API_BASE_URL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_MODELS_URL",
]
