"""
CreditMind - PII vault.

The applicant's real identity is stored here, OUTSIDE the LangGraph state, so it never
appears in agent prompts or Langfuse traces. Only the output guardrail reads it, to check
that no personal data leaked into an LLM's output.

This in-memory version is for local runs. In production it would be an encrypted table
(e.g. Cloud SQL with column encryption / Secret Manager keys) with strict access control.
"""

_VAULT = {}


def store(application_id, pii):
    _VAULT[application_id] = dict(pii)


def get(application_id):
    return dict(_VAULT.get(application_id, {}))


def delete(application_id):
    _VAULT.pop(application_id, None)