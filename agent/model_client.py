"""One real LiteLLM call per workflow invocation, with retries disabled."""

import os
from time import monotonic

from .settings import configured, positive_number


class LiveModel:
    def __init__(self, completion=None):
        self.model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-6")
        provider = self.model.split("/", 1)[0]
        key_name = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider)
        if not key_name or not configured(key_name):
            raise ValueError("Set LLM_MODEL to anthropic/... or openai/... and configure its provider key")
        self.api_key = os.environ[key_name]
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
            if not isinstance(message, str):
                raise ValueError("Model returned no text")
            self.calls.append({"model": self.model, "seconds": round(monotonic() - started, 3),
                               "status": "returned", "usage": usage})
            return message
        except Exception as exc:
            # Vendor exception bodies can contain request contents; never expose them.
            self.calls.append({"model": self.model, "seconds": round(monotonic() - started, 3),
                               "status": "error", "error_type": type(exc).__name__})
            raise RuntimeError("Live model request failed; check credentials, model access, and connectivity") from None
