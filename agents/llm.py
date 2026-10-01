"""
CreditMind - LLM access with provider failover.

Primary: Groq. Backup: Google Gemini. If a Groq call fails (rate limit or any other error),
the same call is retried on Gemini immediately, so the pipeline never stalls on one provider.

.env:
    GROQ_API_KEY=...
    GROQ_MODEL_FAST=openai/gpt-oss-20b        # fraud, policy, explain agents
    GROQ_MODEL_STRONG=openai/gpt-oss-120b     # decision, report agents

    GOOGLE_API_KEY=...                        # optional: enables the Gemini backup
    GEMINI_MODEL_FAST=gemini-3.5-flash-lite
    GEMINI_MODEL_STRONG=gemini-3.5-flash
"""

import logging
import os
import warnings
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

# Gemini SDK notices that are informational only (keep terminal and demo output clean)
warnings.filterwarnings("ignore", message=".*uses fixed sampling defaults.*")
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

GROQ_FAST = os.getenv("GROQ_MODEL_FAST", "openai/gpt-oss-20b")
GROQ_STRONG = os.getenv("GROQ_MODEL_STRONG", "openai/gpt-oss-120b")
GEMINI_FAST = os.getenv("GEMINI_MODEL_FAST", "gemini-3.5-flash-lite")
GEMINI_STRONG = os.getenv("GEMINI_MODEL_STRONG", "gemini-3.5-flash")


def gemini_enabled():
    return bool(os.getenv("GOOGLE_API_KEY"))


def groq_enabled():
    return bool(os.getenv("GROQ_API_KEY"))


@lru_cache(maxsize=8)
def _groq(tier, temperature=0.0):
    from langchain_groq import ChatGroq
    return ChatGroq(
        model=GROQ_FAST if tier == "fast" else GROQ_STRONG,
        temperature=temperature,
        # With a backup provider, fail over quickly instead of retrying Groq for long
        max_retries=1 if gemini_enabled() else 5,
        timeout=90,
    )


@lru_cache(maxsize=4)
def _gemini(tier):
    from langchain_google_genai import ChatGoogleGenerativeAI
    # No temperature: current Gemini models use fixed sampling defaults
    return ChatGoogleGenerativeAI(
        model=GEMINI_FAST if tier == "fast" else GEMINI_STRONG,
        max_retries=2,
        timeout=90,
    )


def providers(schema, tier="fast", temperature=0.0):
    """Ordered list of (provider name, structured LLM) to try.

    temperature > 0 is used for self-consistency voting, so votes reason independently.
    """
    chain = []
    if groq_enabled():
        chain.append(("groq", _groq(tier, temperature).with_structured_output(schema)))
    if gemini_enabled():
        chain.append(("gemini", _gemini(tier).with_structured_output(schema)))
    if not chain:
        raise RuntimeError("No LLM configured - add GROQ_API_KEY and/or GOOGLE_API_KEY to .env")
    return chain