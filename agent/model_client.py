"""One reasoning request per workflow call, routed across configured model tiers; retries disabled."""

import os
from time import monotonic

from .settings import PROVIDER_KEYS, configured, model_tiers, positive_number
from .errors import ModelRequestError

# Provider failures after which the request never produced an answer, so the next model in the
# tier may be tried without touching the workflow's model-call budget. Content problems never
# qualify: malformed JSON stays with the workflow's single correction allowance, and an
# authentication failure is a configuration error to surface, not to route around.
TRANSIENT_ERRORS = frozenset({"RateLimitError", "ServiceUnavailableError", "InternalServerError",
                              "APIConnectionError", "Timeout", "APITimeoutError", "NotFoundError",
                              "EmptyResponse"})
MAX_ATTEMPTS_PER_CALL = 3
MODEL_HINT = "Set LLM_MODEL, LLM_FAST_MODEL and LLM_FALLBACK_MODELS to openrouter/<author>/<model>, anthropic/... or openai/..."


class EmptyResponse(Exception):
    """The provider answered without any text; treated like a transport failure."""


class LiveModel:
    def __init__(self, completion=None):
        self.tiers = model_tiers()
        self.model = self.tiers["heavy"][0]
        self.keys = {}
        for name in dict.fromkeys(self.tiers["fast"] + self.tiers["heavy"]):
            if name == "openrouter/free":
                raise ValueError("Use openrouter/openrouter/free in the repo-root .env; LiteLLM needs its provider prefix before OpenRouter's openrouter/free model ID")
            key_name = PROVIDER_KEYS.get(name.split("/", 1)[0])
            if not key_name:
                raise ValueError(MODEL_HINT)
            if not configured(key_name):
                raise ValueError(f"Configure {key_name} for {name}")
            self.keys[name] = (key_name, os.environ[key_name])
        self.key_name = self.keys[self.model][0]
        self.timeout = positive_number("LLM_TIMEOUT_SECONDS", 45)
        self.max_tokens = positive_number("LLM_MAX_OUTPUT_TOKENS", 1200, integer=True)
        if completion is None:
            # Prevent LiteLLM's optional cost-map download at import in offline environments.
            os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
            from litellm import completion
        self.completion = completion
        self.calls = []

    def tier(self, hints):
        """Fast tier only for a diagnose call that already has a validated fast path and is not a
        correction retry; critique and novel incidents always use the heavy tier."""
        routine = hints.get("stage") == "diagnose" and bool(hints.get("fast_path")) and not hints.get("correction")
        return "fast" if routine and self.tiers["fast"] != self.tiers["heavy"] else "heavy"

    def __call__(self, *, system, prompt, **hints):
        tier = self.tier(hints)
        chain = self.tiers[tier][:MAX_ATTEMPTS_PER_CALL]
        for position, name in enumerate(chain):
            started = monotonic()
            key_name, api_key = self.keys[name]
            entry = {"model": name, "tier": tier, "stage": hints.get("stage")}
            try:
                response = self.completion(
                    model=name, api_key=api_key,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                    max_tokens=self.max_tokens, timeout=self.timeout, num_retries=0,
                )
                usage = getattr(response, "usage", None)
                usage = usage.model_dump() if hasattr(usage, "model_dump") else (usage or {})
                message = response.choices[0].message.content
                if not isinstance(message, str) or not message.strip():
                    raise EmptyResponse()
                self.calls.append({**entry, "seconds": round(monotonic() - started, 3), "status": "returned", "usage": usage})
                return message
            except Exception as exc:
                # Vendor exception bodies can contain request contents; never expose them.
                error_type = type(exc).__name__
                last = position == len(chain) - 1
                transient = error_type in TRANSIENT_ERRORS
                self.calls.append({**entry, "seconds": round(monotonic() - started, 3),
                                   "status": "error" if last or not transient else "fallback", "error_type": error_type})
                if transient and not last:
                    continue
                if isinstance(exc, ModelRequestError):
                    raise
                if isinstance(exc, EmptyResponse):
                    raise ModelRequestError("Model returned no text. Increase LLM_MAX_OUTPUT_TOKENS or select a specific chat model; a reasoning model may exhaust its output budget before answering") from None
                if error_type == "AuthenticationError":
                    raise ModelRequestError(
                        f"Model authentication failed (AuthenticationError). Check {key_name}; "
                        "an existing shell variable overrides the key in .env"
                    ) from None
                if error_type == "NotFoundError":
                    raise ModelRequestError(
                        "Model endpoint not found (NotFoundError). Check LLM_MODEL in the repo-root .env; "
                        "OpenRouter models require openrouter/<full-model-id>, including "
                        "openrouter/openrouter/free for the free router"
                    ) from None
                if error_type == "RateLimitError":
                    raise ModelRequestError(
                        "Model request was rate limited (RateLimitError) by every configured model; free OpenRouter "
                        "models allow 20 requests per minute and 50 per day. Wait, or add LLM_FALLBACK_MODELS"
                    ) from None
                raise ModelRequestError("Live model request failed; check credentials, model access, and connectivity") from None
