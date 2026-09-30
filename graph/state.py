"""CreditMind - The shared state passed between LangGraph nodes (the 'case file')."""

import operator
from typing import Annotated, TypedDict


class CreditState(TypedDict, total=False):
    # Set before the graph starts (masked data only - no PII)
    application_id: str
    llm_view: dict            # masked features + safe borrower text + security flags
    model_features: dict      # numeric/categorical features for the ML model and rules
    input_flags: dict         # injection result + PII entity types found (no values)

    # Filled by nodes
    profile: dict             # intake: readable applicant profile
    risk: dict                # risk scoring: PD, zone, SHAP factors
    fraud: dict               # fraud agent
    policy: dict              # policy agent: rule engine + retrieved clauses + findings
    constraints: dict         # consolidate: required minimum outcome and binding clauses
    explanation: dict         # explain agent
    decision: dict            # latest decision draft
    attempts: int             # decision attempts so far
    critic: dict              # latest output-guardrail result
    final_decision: dict      # validated decision (or fail-safe)
    human_review: dict        # credit officer's decision, if referred
    final_outcome: str        # APPROVE / DECLINE / REFER (pending)
    report: dict              # memo + applicant notice

    # Append-only audit log; parallel nodes can write to it at the same time
    audit: Annotated[list, operator.add]