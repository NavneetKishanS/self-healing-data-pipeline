"""One real LiteLLM call per workflow invocation, with retries disabled."""

import os
from time import monotonic

from .settings import DEFAULT_MODEL, PROVIDER_KEYS, configured, positive_number
from .errors import ModelRequestError


class LiveModel:
    def __init__(self, completion=None):
        self.model = os.getenv("LLM_MODEL", DEFAULT_MODEL)
        if self.model == "openrouter/free":
            raise ValueError("Set LLM_MODEL=openrouter/openrouter/free in agent/.env; LiteLLM needs its provider prefix before OpenRouter's openrouter/free model ID")
        provider = self.model.split("/", 1)[0]
        key_name = PROVIDER_KEYS.get(provider)
        if not key_name:
            raise ValueError("Set LLM_MODEL to openrouter/<author>/<model>, anthropic/... or openai/...")
        if not configured(key_name):
            raise ValueError(f"Configure {key_name} for the selected LLM_MODEL")
        self.api_key = os.environ[key_name]
        self.key_name = key_name
        self.timeout = positive_number("LLM_TIMEOUT_SECONDS", 45)
        self.max_tokens = positive_number("LLM_MAX_OUTPUT_TOKENS", 1200, integer=True)
        if completion is None:
            # Prevent LiteLLM's optional cost-map download at import in offline environments.
            os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
            from litellm import completion
        self.completion = completion
        self.calls = []

    def __call__(self, *, system, prompt):
        started = monotonic()
        try:
            response = self.completion(
                model=self.model, api_key=self.api_key,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                max_tokens=self.max_tokens, timeout=self.timeout, num_retries=0,
            )
            usage = getattr(response, "usage", None)
            usage = usage.model_dump() if hasattr(usage, "model_dump") else (usage or {})
            message = response.choices[0].message.content
            if not isinstance(message, str) or not message.strip():
                raise ModelRequestError("Model returned no text. Increase LLM_MAX_OUTPUT_TOKENS or select a specific chat model; a reasoning model may exhaust its output budget before answering")
            self.calls.append({"model": self.model, "seconds": round(monotonic() - started, 3),
                               "status": "returned", "usage": usage})
            return message
        except Exception as exc:
            # Vendor exception bodies can contain request contents; never expose them.
            self.calls.append({"model": self.model, "seconds": round(monotonic() - started, 3),
                               "status": "error", "error_type": type(exc).__name__})
            if isinstance(exc, ModelRequestError):
                raise
            if type(exc).__name__ == "AuthenticationError":
                raise ModelRequestError(
                    f"Model authentication failed (AuthenticationError). Check {self.key_name}; "
                    "an existing shell variable overrides the key in .env"
                ) from None
            if type(exc).__name__ == "NotFoundError":
                raise ModelRequestError(
                    "Model endpoint not found (NotFoundError). Check LLM_MODEL in agent/.env; "
                    "OpenRouter models require openrouter/<full-model-id>, including "
                    "openrouter/openrouter/free for the free router"
                ) from None
            raise ModelRequestError("Live model request failed; check credentials, model access, and connectivity") from None
