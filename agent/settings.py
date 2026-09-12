"""Environment setup shared by CLI and HTTP adapters. Values are never printed."""

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
# OpenRouter's free auto-router: the one free ID that never rotates away. Paid models are not used.
DEFAULT_MODEL = "openrouter/openrouter/free"
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


def model_tiers():
    """Ordered model IDs per tier. LLM_MODEL is the primary ("heavy") model; LLM_FAST_MODEL, when
    set, answers diagnose calls that already have a validated fast path; LLM_FALLBACK_MODELS is a
    comma-separated list tried in order when a request never produces an answer."""
    heavy = os.getenv("LLM_MODEL", DEFAULT_MODEL).strip()
    fast = os.getenv("LLM_FAST_MODEL", "").strip() or heavy
    fallbacks = [name.strip() for name in os.getenv("LLM_FALLBACK_MODELS", "").split(",") if name.strip()]
    tiers = {"heavy": [heavy, *fallbacks], "fast": [fast, heavy, *fallbacks]}
    return {tier: list(dict.fromkeys(chain)) for tier, chain in tiers.items()}


def integration_status():
    from .slack import slack_status
    model = os.getenv("LLM_MODEL", DEFAULT_MODEL)
    credential = PROVIDER_KEYS.get(model.split("/", 1)[0])
    tiers = model_tiers()
    return {
        "model": model,
        "model_key": ("configured, not verified" if configured(credential) else f"missing {credential}")
        if credential else "unsupported provider",
        "model_tiers": {"fast": tiers["fast"] if tiers["fast"] != tiers["heavy"] else "same as heavy",
                        "heavy": tiers["heavy"]},
        "ambiguous": "configured, not verified" if configured("AMBIGUOUS_API_KEY") else "missing AMBIGUOUS_API_KEY",
        "auth0": "configured, not verified" if configured("AUTH0_DOMAIN") and configured("AUTH0_AUDIENCE") else "missing AUTH0_DOMAIN / AUTH0_AUDIENCE",
        "slack": slack_status(),
        "copilotkit": "AG-UI endpoint available with serve; frontend/runtime connects separately",
    }
