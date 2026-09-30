"""
CreditMind - Deterministic policy rule engine.

The LLM agents EXPLAIN the policy; this code DECIDES which numeric rules are triggered.
Arithmetic like "is DTI above 40%?" must never depend on an LLM.

    from policy.rules import evaluate_policy
    result = evaluate_policy(application_dict, injection_detected=False)
    result["required_outcome"]   # APPROVE / REFER / DECLINE (minimum strictness)
    result["hits"]               # every triggered clause with its outcome and reason
"""

import math
from dataclasses import asdict, dataclass

STRICTNESS = {"APPROVE": 0, "NOTE": 0, "REFER": 1, "DECLINE": 2}


@dataclass
class RuleHit:
    clause_id: str
    outcome: str  # DECLINE, REFER or NOTE (informational)
    reason: str


def _num(app, key):
    value = app.get(key)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def evaluate_policy(app, injection_detected=False):
    hits = []

    def hit(clause_id, outcome, reason):
        hits.append(RuleHit(clause_id, outcome, reason))

    loan, income, dti = _num(app, "loan_amnt"), _num(app, "annual_inc"), _num(app, "dti")
    lti = _num(app, "loan_to_income")
    if lti is None and loan is not None and income:
        lti = loan / income
    term, emp = _num(app, "term_months"), _num(app, "emp_length_years")
    history, util = _num(app, "credit_history_years"), _num(app, "revol_util")
    inq, opened = _num(app, "inq_last_6mths"), _num(app, "acc_open_past_24mths")
    since_delinq, delinq_2y = _num(app, "mths_since_last_delinq"), _num(app, "delinq_2yrs")
    bankrupt, pub_rec = _num(app, "pub_rec_bankruptcies"), _num(app, "pub_rec")
    since_record = _num(app, "mths_since_last_record")
    purpose = str(app.get("purpose", "")).lower()
    verification = str(app.get("verification_status", ""))

    # ---- 2. Eligibility
    if income is not None and income < 15000:
        hit("POL-2.1", "DECLINE", f"Annual income ${income:,.0f} is below the $15,000 minimum")
    if loan is not None and loan > 25000 and verification == "Not Verified":
        hit("POL-2.2", "REFER", f"Loan of ${loan:,.0f} requires verified income")
    if emp is None:
        hit("POL-2.3", "NOTE", "Employment length not reported")
    elif emp < 1 and (loan or 0) > 20000:
        hit("POL-2.3", "REFER", f"Less than 1 year in current job for a ${loan:,.0f} loan")
    if history is not None and history < 1:
        hit("POL-2.4", "DECLINE", f"Credit history of {history:g} years is under 1 year")
    elif history is not None and history < 3:
        hit("POL-2.4", "REFER", f"Credit history of {history:g} years is under 3 years")

    # ---- 3. Affordability
    if dti is not None and dti > 40:
        hit("POL-3.1", "DECLINE", f"Debt-to-income ratio of {dti:.1f}% exceeds the 40% limit")
    elif dti is not None and dti >= 30:
        factors = []
        if history is not None and history > 10:
            factors.append("credit history over 10 years")
        if delinq_2y == 0:
            factors.append("no late payments in 2 years")
        if util is not None and util < 50:
            factors.append("revolving utilization below 50%")
        if factors:
            hit("POL-3.2", "NOTE", f"DTI of {dti:.1f}% offset by: {', '.join(factors)}")
        else:
            hit("POL-3.2", "REFER",
                f"Debt-to-income ratio of {dti:.1f}% without compensating factors")
    if lti is not None and lti > 0.5:
        hit("POL-3.3", "DECLINE", f"Loan is {lti:.0%} of annual income (limit 50%)")
    elif lti is not None and lti >= 0.35:
        hit("POL-3.3", "REFER", f"Loan is {lti:.0%} of annual income (review above 35%)")
    if term == 60 and loan is not None and loan < 10000:
        hit("POL-3.4", "DECLINE", f"60-month term not available for ${loan:,.0f} (minimum $10,000)")
    elif term == 60 and dti is not None and dti > 30:
        hit("POL-3.4", "REFER", f"60-month term with DTI of {dti:.1f}%")

    # ---- 4. Credit conduct
    if (_num(app, "acc_now_delinq") or 0) > 0 or (_num(app, "delinq_amnt") or 0) > 0:
        hit("POL-4.1", "DECLINE", "Account currently delinquent or past-due amount owed")
    recent = []
    if since_delinq is not None and since_delinq <= 12:
        recent.append(f"late payment {since_delinq:.0f} months ago")
    if delinq_2y is not None and delinq_2y >= 2:
        recent.append(f"{delinq_2y:.0f} late payments in the last 2 years")
    if recent:
        hit("POL-4.2", "REFER", "Recent delinquency: " + "; ".join(recent))
    if (_num(app, "num_tl_90g_dpd_24m") or 0) > 0:
        hit("POL-4.3", "DECLINE", "Account 90+ days past due in the last 24 months")
    if bankrupt is not None and bankrupt >= 2:
        hit("POL-4.4", "DECLINE", f"{bankrupt:.0f} bankruptcies on record")
    elif (bankrupt or 0) >= 1 or (pub_rec or 0) >= 1:
        if since_record is None or since_record < 36:
            age = f"{since_record:.0f} months ago" if since_record is not None else "date unknown"
            hit("POL-4.4", "REFER", f"Public record or bankruptcy on file ({age})")
    if (inq is not None and inq >= 4) or (opened is not None and opened >= 10):
        hit("POL-4.5", "REFER", f"{inq or 0:.0f} inquiries in 6 months and "
                                f"{opened or 0:.0f} accounts opened in 24 months")
    if util is not None and util > 90:
        hit("POL-4.6", "REFER", f"Revolving utilization of {util:.0f}% exceeds 90%")
    elif util is not None and util >= 75:
        hit("POL-4.6", "NOTE", f"Revolving utilization of {util:.0f}% is elevated")
    if (_num(app, "chargeoff_within_12_mths") or 0) > 0:
        hit("POL-4.7", "DECLINE", "Charge-off within the last 12 months")
    elif (_num(app, "collections_12_mths_ex_med") or 0) > 0:
        hit("POL-4.7", "REFER", "Non-medical collection within the last 12 months")

    # ---- 5. Purpose
    if purpose == "small_business":
        if dti is not None and dti > 30:
            hit("POL-5.2", "DECLINE", f"Small business loan with DTI of {dti:.1f}%")
        elif loan is not None and loan > 25000:
            hit("POL-5.2", "REFER", f"Small business loan of ${loan:,.0f} exceeds $25,000")

    # ---- 6. Fraud indicators
    if income is not None and income > 250000 and verification == "Not Verified":
        hit("POL-6.1", "REFER", f"Unverified income of ${income:,.0f} (fraud review)")
    if (inq or 0) >= 3 and (opened or 0) >= 8 and (lti or 0) > 0.4:
        hit("POL-6.2", "REFER", "Bust-out pattern: heavy recent credit-seeking and large loan")
    if injection_detected:
        hit("POL-6.3", "REFER", "Application text attempted to manipulate the system (fraud review)")

    binding = [h for h in hits if h.outcome in ("REFER", "DECLINE")]
    required = max((h.outcome for h in binding), key=STRICTNESS.get, default="APPROVE")
    return {
        "required_outcome": required,
        "hits": [asdict(h) for h in hits],
        "binding_clauses": sorted({h.clause_id for h in binding if h.outcome == required}),
        "fraud_review": any(h.clause_id.startswith("POL-6") for h in binding),
    }