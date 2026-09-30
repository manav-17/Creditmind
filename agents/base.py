"""CreditMind - Shared helpers for agents: tools loaded once, and one way to call the LLM."""

import json
from functools import lru_cache

from agents.llm import structured_llm


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


def ask(schema, system, payload, config=None, tier="fast", name="agent"):
    """Call the LLM with a system prompt and a JSON payload; returns a dict."""
    llm = structured_llm(schema, tier)
    messages = [("system", system), ("human", "INPUT (JSON):\n" + to_json(payload))]
    last_error = None
    for attempt in (1, 2):  # one retry: models occasionally emit malformed structured output
        run_config = {**(config or {}), "run_name": name if attempt == 1 else f"{name}_retry"}
        try:
            result = llm.invoke(messages, config=run_config)
            return result.model_dump() if hasattr(result, "model_dump") else dict(result)
        except Exception as exc:
            last_error = exc
    raise last_error