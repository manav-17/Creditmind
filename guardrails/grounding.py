"""
CreditMind - Grounding check for LLM-written decisions.

Every figure the Decision Agent writes (percentages, amounts, counts, months) must match a
fact computed by code: application data, the model's PD and thresholds, rule-engine
reasons, or policy limits. A figure that matches nothing is treated as hallucinated and
sent back to the critic loop.

    facts = build_facts(state, policy_texts)
    unsupported = find_unsupported(text, facts)
"""

import math
import re

CLAUSE_ID = re.compile(r"POL-\d+\.\d+")
NUMBER = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?%?")
ALWAYS_ALLOWED = {0.0, 1.0, 2.0, 3.0}  # small counts such as "two late payments"
RATIO_FEATURES = {"loan_to_income", "revol_bal_to_income"}


def _variants(value):
    """A value plus the rounded forms a writer might use."""
    return {value, round(value), round(value, 1), round(value, 2)}


def _numbers_in(text):
    """Extract numbers from text, ignoring clause IDs like POL-4.2."""
    text = CLAUSE_ID.sub(" ", text or "")
    found = []
    for raw in NUMBER.findall(text):
        cleaned = raw.replace("$", "").replace(",", "").rstrip("%")
        try:
            found.append((raw, float(cleaned)))
        except ValueError:
            continue
    return found


def build_facts(state, policy_texts=()):
    """All figures the decision text may legitimately use, computed by code."""
    facts = set(ALWAYS_ALLOWED)
    for key, value in (state.get("model_features") or {}).items():
        if isinstance(value, (int, float)) and not isinstance(value, bool) \
                and value is not None and not math.isnan(value):
            facts |= _variants(float(value))
            if key in RATIO_FEATURES:            # 0.06 is written as 6%
                facts |= _variants(float(value) * 100)

    risk = state.get("risk") or {}
    for value in [risk.get("probability_of_default"),
                  *(risk.get("thresholds") or {}).values()]:
        if isinstance(value, (int, float)):
            facts |= _variants(float(value) * 100) | _variants(float(value))

    texts = list(policy_texts)
    texts += (state.get("constraints") or {}).get("binding_reasons", [])
    texts += [h.get("reason", "") for h in
              ((state.get("policy") or {}).get("rule_engine") or {}).get("hits", [])]
    texts += [f.get("value", "") for f in risk.get("top_factors", [])]
    for text in texts:
        for _, number in _numbers_in(text):
            facts |= _variants(number)
    return facts


def find_unsupported(text, facts):
    """Figures in text that match no fact (tolerance covers rounding)."""
    unsupported = []
    for raw, number in _numbers_in(text):
        tolerance = max(0.051, abs(number) * 0.005)
        if not any(abs(number - fact) <= tolerance for fact in facts):
            unsupported.append(raw)
    return sorted(set(unsupported))