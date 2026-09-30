"""
CreditMind - LLM access (Groq). Model names come from .env so they can change without code edits.

    GROQ_API_KEY=...
    GROQ_MODEL_FAST=openai/gpt-oss-20b      # screening agents (fraud, policy, explain)
    GROQ_MODEL_STRONG=openai/gpt-oss-120b   # decision and report agents
"""

import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv()

FAST_MODEL = os.getenv("GROQ_MODEL_FAST", "openai/gpt-oss-20b")
STRONG_MODEL = os.getenv("GROQ_MODEL_STRONG", "openai/gpt-oss-120b")


@lru_cache(maxsize=2)
def get_llm(tier="fast"):
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError("GROQ_API_KEY is missing - add it to your .env file")
    return ChatGroq(
        model=FAST_MODEL if tier == "fast" else STRONG_MODEL,
        temperature=0,
        max_retries=5,   # retries with backoff on rate limits (HTTP 429)
        timeout=90,
    )


def structured_llm(schema, tier="fast"):
    """LLM that must return an instance of the given Pydantic schema."""
    return get_llm(tier).with_structured_output(schema)