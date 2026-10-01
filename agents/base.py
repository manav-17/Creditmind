"""CreditMind - Shared helpers for agents: tools loaded once, and one way to call the LLM."""

import json
import re
import time
from functools import lru_cache

from agents.llm import providers

MAX_RATE_LIMIT_WAITS = 4


@lru_cache(maxsize=1)
def risk_model():
    from ml.risk_model import RiskModel
    return RiskModel()


@lru_cache(maxsize=1)
def policy_retriever():
    from rag.policy_retriever import PolicyRetriever
    return PolicyRetriever()


def to_json(data):
    return json.dumps(data, indent=1, default=str, ensure_ascii=False)


def _is_rate_limit(exc):
    return ("RateLimit" in type(exc).__name__ or "429" in str(exc)
            or "RESOURCE_EXHAUSTED" in str(exc))


def _retry_after(exc):
    """Providers say e.g. 'Please try again in 7.52s' or '850ms'."""
    match = re.search(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", str(exc))
    if not match:
        return 5.0
    minutes = float(match.group(1) or 0)
    value = float(match.group(2))
    return minutes * 60 + (value / 1000 if match.group(3) == "ms" else value)


def _invoke(llm, messages, config, name, patient):
    """One provider. patient=True: wait out rate limits (used for the last provider)."""
    other_errors, waits = 0, 0
    while True:
        run_config = {**(config or {}),
                      "run_name": name if other_errors + waits == 0 else f"{name}_retry"}
        try:
            result = llm.invoke(messages, config=run_config)
            return result.model_dump() if hasattr(result, "model_dump") else dict(result)
        except Exception as exc:
            if _is_rate_limit(exc):
                if patient and waits < MAX_RATE_LIMIT_WAITS:
                    waits += 1
                    time.sleep(min(_retry_after(exc) + 0.5, 60))
                    continue
                raise
            other_errors += 1
            if other_errors > 1:
                raise  # e.g. malformed structured output twice


def ask(schema, system, payload, config=None, tier="fast", name="agent", temperature=0.0):
    """Call the LLM with failover: Groq first, then Gemini. Returns a dict.

    The dict includes '_provider' (e.g. 'llm:groq' or 'llm:gemini') for the audit trail.
    """
    messages = [("system", system), ("human", "INPUT (JSON):\n" + to_json(payload))]
    chain = providers(schema, tier, temperature)
    last_error = None
    for i, (provider, llm) in enumerate(chain):
        is_last = i == len(chain) - 1
        try:
            result = _invoke(llm, messages, config, f"{name}[{provider}]", patient=is_last)
            result["_provider"] = f"llm:{provider}"
            return result
        except Exception as exc:
            last_error = exc  # fail over to the next provider
    raise last_error