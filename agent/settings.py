"""Environment setup shared by CLI and HTTP adapters. Values are never printed."""

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
DEFAULT_MODEL = "openrouter/anthropic/claude-sonnet-4.6"
PROVIDER_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def load_settings():
    load_dotenv(ENV_FILE, override=False)


def configured(name):
    value = os.getenv(name, "").strip()
    return bool(value) and not any(part in value.lower() for part in
                                   ("your_key", "your-key", "your_api", "your-api", "placeholder", "changeme"))


def positive_number(name, default, integer=False):
    import math
    value = (int if integer else float)(os.getenv(name, str(default)))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return value


def integration_status():
    model = os.getenv("LLM_MODEL", DEFAULT_MODEL)
    credential = PROVIDER_KEYS.get(model.split("/", 1)[0])
    return {
        "model": model,
        "model_key": ("configured, not verified" if configured(credential) else f"missing {credential}")
        if credential else "unsupported provider",
        "exa": "configured, not verified" if configured("EXA_API_KEY") else "missing EXA_API_KEY",
        "ambiguous": "configured, not verified" if configured("AMBIGUOUS_API_KEY") else "missing AMBIGUOUS_API_KEY",
        "auth0": "configured, not verified" if configured("AUTH0_DOMAIN") and configured("AUTH0_AUDIENCE") else "missing AUTH0_DOMAIN / AUTH0_AUDIENCE",
        "copilotkit": "AG-UI endpoint available with serve; frontend/runtime connects separately",
    }
