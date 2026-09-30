"""
CreditMind - Input guardrails. Runs BEFORE any agent sees an application.

    from guardrails.input_guard import run_input_guardrails
    result = run_input_guardrails(raw_application_dict)
    if not result.valid: ...            # reject: result.errors
    result.model_features               # for the ML model (no PII, no free text)
    result.llm_view                     # the ONLY thing LLM agents may see
    result.restricted_pii               # kept out of every LLM call

Steps:
  1. Schema validation (Pydantic): types, ranges, allowed categories
  2. Derived features computed server-side (never trusted from the client)
  3. Prompt-injection detection on borrower-written text
  4. PII masking on borrower-written text; structured PII removed entirely
"""

from dataclasses import dataclass, field

from pydantic import ValidationError

from guardrails.injection import detect_injection
from guardrails.pii import ENTITIES, mask_pii
from guardrails.schemas import LoanApplication

PII_FIELDS = ["applicant_name", "phone", "email", "pan"]
TEXT_FIELDS = ["desc", "emp_title", "title"]
REMOVED_TEXT = "[REMOVED: text flagged as a possible prompt injection]"
# Short fields like job titles ("Senior Accountant") are often mistaken for names
# by the NER model, so only pattern-based detection runs on them
SHORT_FIELD_ENTITIES = [e for e in ENTITIES if e != "PERSON"]


@dataclass
class InputGuardResult:
    valid: bool
    application_id: str = ""
    errors: list = field(default_factory=list)
    model_features: dict = field(default_factory=dict)
    llm_view: dict = field(default_factory=dict)
    restricted_pii: dict = field(default_factory=dict)
    pii_found: dict = field(default_factory=dict)       # field -> entity types
    injection: dict = field(default_factory=dict)       # detected, fields, patterns

    def summary(self):
        return {"valid": self.valid, "application_id": self.application_id,
                "errors": self.errors, "pii_found": self.pii_found,
                "injection": self.injection}


def _format_errors(exc):
    return [f"{'.'.join(str(p) for p in e['loc']) or 'application'}: {e['msg']}"
            for e in exc.errors()]


def run_input_guardrails(raw):
    # 1. Schema validation
    try:
        app = LoanApplication.model_validate(raw)
    except ValidationError as exc:
        return InputGuardResult(valid=False, application_id=str(raw.get("application_id", "")),
                                errors=_format_errors(exc))

    data = app.model_dump()
    restricted = {k: data.pop(k) for k in PII_FIELDS if data.get(k)}
    texts = {k: data.pop(k) for k in TEXT_FIELDS}

    # 2. Derived features computed here, not accepted from the input
    data["loan_to_income"] = app.loan_to_income
    data["revol_bal_to_income"] = app.revol_bal_to_income
    model_features = {k: v for k, v in data.items() if k != "application_id"}

    # 3 + 4. Injection detection (on raw text) and PII masking
    safe_text, pii_found, injected_fields, patterns, top_score = {}, {}, [], set(), 0.0
    for name, text in texts.items():
        if not text:
            safe_text[name] = None
            continue
        inj = detect_injection(text)
        top_score = max(top_score, inj.score)
        patterns.update(inj.matches)
        masked, entities = mask_pii(
            text, known=restricted,
            entities=None if name == "desc" else SHORT_FIELD_ENTITIES)
        if entities:
            pii_found[name] = entities
        if inj.detected:
            injected_fields.append(name)
            safe_text[name] = REMOVED_TEXT
        else:
            safe_text[name] = masked

    injection = {"detected": bool(injected_fields), "fields": injected_fields,
                 "patterns": sorted(patterns), "score": top_score}

    llm_view = {
        "application_id": app.application_id,
        "applicant": "<APPLICANT>",  # identity is never shared with the LLM
        "features": model_features,
        "borrower_text": safe_text,
        "security_flags": (["Borrower text removed: possible prompt injection "
                            f"in {', '.join(injected_fields)}"] if injected_fields else []),
    }
    return InputGuardResult(valid=True, application_id=app.application_id,
                            model_features=model_features, llm_view=llm_view,
                            restricted_pii=restricted, pii_found=pii_found,
                            injection=injection)