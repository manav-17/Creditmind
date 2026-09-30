"""
CreditMind - Output guardrails. Checks the Decision Agent's output before it is accepted.

    from guardrails.output_guard import check_decision
    result = check_decision(decision_dict, context)
    if not result.passed:
        feedback = result.violations   # sent back to the Decision Agent (critic loop)

context = {
    "risk_zone": "APPROVE" | "REVIEW" | "REJECT",     # from the ML model
    "policy": evaluate_policy(...) result,             # deterministic rule engine
    "valid_clause_ids": set of clause IDs,             # from the policy catalogue
    "restricted_pii": {...},                           # applicant's own PII values
}
"""

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from guardrails.pii import find_pii_leaks
from guardrails.schemas import DecisionOutput
from policy.rules import STRICTNESS

ZONE_TO_OUTCOME = {"APPROVE": "APPROVE", "REVIEW": "REFER", "REJECT": "DECLINE"}

# POL-7.1: explanations must never mention prohibited factors or their proxies
PROHIBITED = {
    "age": r"\bage\b|\byears? old\b|\b(young|elderly|senior citizen)\b",
    "sex/gender": r"\b(gender|sex|male|female|woman|women)\b",
    "marital status": r"\b(marital|married|unmarried|divorced|widowed|spouse|husband|wife)\b",
    "race/ethnicity": r"\b(race|racial|ethnic|ethnicity|colou?r of skin)\b",
    "religion": r"\b(religion|religious|caste)\b",
    "national origin": r"\b(national origin|nationality|immigrant|immigration|foreigner)\b",
    "disability": r"\b(disabled|disability|handicap)\b",
    "pregnancy": r"\b(pregnant|pregnancy)\b",
    "location proxy": r"\b(zip ?code|postal code|pin ?code|state of residence|neighbou?rhood)\b",
    "public assistance": r"\b(public assistance|welfare|food stamps)\b",
}
GENERIC_REASONS = r"\b(insufficient creditworthiness|does not meet (our )?criteria|internal policy)\b"
CLAUSE_RE = re.compile(r"POL-\d+\.\d+")


@dataclass
class OutputGuardResult:
    passed: bool
    violations: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    decision: dict = field(default_factory=dict)


def check_decision(raw_decision, context):
    violations, warnings = [], []

    # 1. Schema
    try:
        decision = DecisionOutput.model_validate(raw_decision)
    except ValidationError as exc:
        errs = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        return OutputGuardResult(False, [f"Invalid output format - {'; '.join(errs)}"])

    policy = context["policy"]
    model_required = ZONE_TO_OUTCOME[context["risk_zone"]]
    policy_required = policy["required_outcome"]
    required = max(model_required, policy_required, key=STRICTNESS.get)

    # 2. Strictness: never less strict than the model zone or the policy rules require
    if STRICTNESS[decision.decision] < STRICTNESS[required]:
        why = []
        if STRICTNESS[model_required] >= STRICTNESS[required]:
            why.append(f"model risk zone is {context['risk_zone']}")
        if STRICTNESS[policy_required] >= STRICTNESS[required]:
            why.append(f"policy clauses {', '.join(policy['binding_clauses'])} require it")
        violations.append(f"Decision {decision.decision} is not allowed: minimum outcome is "
                          f"{required} ({'; '.join(why)}) - see POL-1.2 and POL-1.4")
    elif STRICTNESS[decision.decision] > STRICTNESS[required] and not decision.principal_reasons:
        warnings.append(f"Decision {decision.decision} is stricter than required ({required}) "
                        "but gives no reasons")

    # 3. Citations: must exist, and binding clauses must be cited
    cited = set(decision.cited_clauses) | set(CLAUSE_RE.findall(
        " ".join(decision.principal_reasons) + " " + decision.summary))
    unknown = sorted(cited - context["valid_clause_ids"])
    if unknown:
        violations.append(f"Cited clauses do not exist in the policy: {', '.join(unknown)}")
    missing = sorted(set(policy["binding_clauses"]) - cited)
    if missing and decision.decision != "APPROVE":
        violations.append(f"Binding policy clauses not cited: {', '.join(missing)}")

    # 4. Adverse action reasons (POL-7.2)
    if decision.decision in ("DECLINE", "REFER"):
        if not decision.principal_reasons:
            violations.append("DECLINE/REFER must include 1-4 principal reasons (POL-7.2)")
        for reason in decision.principal_reasons:
            if len(reason) < 15 or re.search(GENERIC_REASONS, reason, re.IGNORECASE):
                violations.append(f"Reason is too generic, state the specific credit factor "
                                  f"(POL-7.2): '{reason}'")

    # 5. Prohibited factors (POL-7.1)
    text = " ".join(decision.principal_reasons + decision.compensating_factors
                    + [decision.summary])
    for factor, regex in PROHIBITED.items():
        if re.search(regex, text, re.IGNORECASE):
            violations.append(f"Explanation mentions a prohibited factor ({factor}) - "
                              "remove it entirely (POL-7.1)")

    # 6. PII leakage (POL-7.3)
    leaks = find_pii_leaks(text, context.get("restricted_pii"))
    if leaks:
        violations.append(f"Output contains personal data ({', '.join(leaks)}) - "
                          "use placeholders only (POL-7.3)")

    return OutputGuardResult(passed=not violations, violations=violations,
                             warnings=warnings, decision=decision.model_dump())