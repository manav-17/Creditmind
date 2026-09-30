"""
CreditMind - The LLM agents.

Each agent: builds a JSON payload from the (masked) state, calls the LLM with a strict
output schema, then applies deterministic safeguards. If the LLM call fails, the agent
falls back to a deterministic result so the pipeline never crashes mid-application.
"""

from agents.base import ask
from agents.prompts import (DECISION_SYSTEM, EXPLAIN_SYSTEM, FRAUD_SYSTEM, POLICY_SYSTEM,
                            REPORT_SYSTEM)
from agents.schemas import (DecisionDraft, ExplanationOutput, FraudAssessment,
                            PolicyAssessment, ReportOutput)


def model_verdict(risk):
    """PD vs thresholds, compared by code and written in one format (never by the LLM)."""
    pd_v, t = risk["probability_of_default"], risk["thresholds"]
    lo, hi = t["approve_below"], t["reject_above"]
    if risk["risk_zone"] == "APPROVE":
        text = (f"PD {pd_v:.1%} is BELOW the approval threshold of {lo:.1%}: "
                "the model supports approval. Do not cite PD as a reason for concern.")
    elif risk["risk_zone"] == "REJECT":
        text = f"PD {pd_v:.1%} is ABOVE the rejection threshold of {hi:.1%}: model recommends decline."
    else:
        text = (f"PD {pd_v:.1%} is BETWEEN {lo:.1%} and {hi:.1%} (grey zone): "
                "model requires manual review.")
    return {"probability_of_default": f"{pd_v:.1%}", "approve_below": f"{lo:.1%}",
            "reject_above": f"{hi:.1%}", "risk_zone": risk["risk_zone"], "verdict": text}


# ----------------------------------------------------------------------------- Fraud
def fraud_agent(state, rule_engine, config=None):
    f = state["model_features"]
    deterministic = [h for h in rule_engine["hits"] if h["clause_id"].startswith("POL-6")]
    payload = {
        "stated_annual_income": f.get("annual_inc"),
        "income_verification": f.get("verification_status"),
        "loan_amount": f.get("loan_amnt"),
        "loan_to_income": f.get("loan_to_income"),
        "years_in_job": f.get("emp_length_years"),
        "job_title": state["llm_view"]["borrower_text"].get("emp_title"),
        "loan_title": state["llm_view"]["borrower_text"].get("title"),
        "loan_description": state["llm_view"]["borrower_text"].get("desc"),
        "credit_inquiries_6m": f.get("inq_last_6mths"),
        "accounts_opened_24m": f.get("acc_open_past_24mths"),
        "security_flags": state["llm_view"]["security_flags"],
        "rule_engine_fraud_hits": deterministic,
    }
    try:
        result = ask(FraudAssessment, FRAUD_SYSTEM, payload, config, name="fraud_agent")
        result["source"] = "llm"
    except Exception as exc:  # deterministic fallback
        result = {"risk_level": "HIGH" if deterministic else "LOW", "income_plausible": True,
                  "indicators": [h["reason"] for h in deterministic],
                  "refer_for_fraud_review": bool(deterministic), "policy_clause": None,
                  "reasoning": "LLM unavailable; result based on rule-engine checks only.",
                  "source": f"fallback ({type(exc).__name__}: {str(exc)[:150]})"}
    clause = str(result.get("policy_clause") or "").upper().strip()
    result["policy_clause"] = clause if clause in ("POL-6.1", "POL-6.2", "POL-6.3") else None

    # Grounding: code decides POL-6.2 (bust-out) and POL-6.3 (manipulation). The LLM may
    # only escalate on its own under POL-6.1, when it judges the income implausible.
    result["advisory_notes"] = []
    if deterministic:
        # The LLM can never dismiss a deterministic fraud rule
        result["refer_for_fraud_review"] = True
        result["policy_clause"] = deterministic[0]["clause_id"]
        if result["risk_level"] == "LOW":
            result["risk_level"] = "MEDIUM"
    elif result.get("refer_for_fraud_review"):
        if result.get("income_plausible") is False:
            result["policy_clause"] = "POL-6.1"
        else:
            # Not grounded in a fraud clause: keep as advice for the officer, not binding
            result["advisory_notes"] = result.get("indicators", [])
            result["indicators"] = []
            result["refer_for_fraud_review"] = False
            result["policy_clause"] = None
            result["risk_level"] = "LOW"
            result["reasoning"] += (" [Downgraded: concerns are not fraud indicators under "
                                    "POL-6.1 to POL-6.3; recorded as advisory notes.]")
    return result


# ----------------------------------------------------------------------------- Policy
def policy_agent(state, rule_engine, retrieved, config=None):
    triggered = {h["clause_id"] for h in rule_engine["hits"] if h["outcome"] != "NOTE"}
    payload = {
        "applicant_profile": state["profile"],
        "rule_engine": {"required_outcome": rule_engine["required_outcome"],
                        "triggered_rules": rule_engine["hits"]},
        "retrieved_clauses": [{"clause_id": c["clause_id"], "title": c["title"],
                               "text": c["text"], "retrieved_because": c["triggered_by"]}
                              for c in retrieved],
    }
    try:
        result = ask(PolicyAssessment, POLICY_SYSTEM, payload, config, name="policy_agent")
        result["source"] = "llm"
    except Exception as exc:
        result = {"findings": [{"clause_id": c["clause_id"],
                                "applies": c["clause_id"] in triggered,
                                "explanation": c["triggered_by"]} for c in retrieved],
                  "summary": f"Rule engine requires {rule_engine['required_outcome']}.",
                  "source": f"fallback ({type(exc).__name__}: {str(exc)[:150]})"}
    # Rule-engine results are authoritative: triggered clauses always apply
    for finding in result["findings"]:
        if finding["clause_id"] in triggered:
            finding["applies"] = True
    return result


# ----------------------------------------------------------------------------- Explain
def explain_agent(state, config=None):
    risk, constraints = state["risk"], state["constraints"]
    payload = {
        "risk_model": model_verdict(risk),
        "model_factors_shap": risk["top_factors"],
        "binding_policy_rules": constraints["binding_reasons"],
        "fraud_indicators": state["fraud"].get("indicators", []),
    }
    try:
        result = ask(ExplanationOutput, EXPLAIN_SYSTEM, payload, config, name="explain_agent")
        result["source"] = "llm"
    except Exception as exc:
        factors = [f"{f['name']} of {f['value']} {f['direction']}" for f in risk["top_factors"]]
        adverse = [f for f in risk["top_factors"] if f["direction"] == "increases risk"]
        result = {"key_factors": factors[:5],
                  "candidate_reasons": (constraints["binding_reasons"]
                                        + [f"{f['name']}: {f['value']}" for f in adverse])[:4],
                  "plain_summary": f"Estimated probability of default is "
                                   f"{risk['probability_of_default']:.1%}.",
                  "source": f"fallback ({type(exc).__name__}: {str(exc)[:150]})"}
    return result


# ----------------------------------------------------------------------------- Decision
def decision_agent(state, feedback=None, config=None):
    risk, constraints = state["risk"], state["constraints"]
    payload = {
        "REQUIRED_MINIMUM_OUTCOME": constraints["required_outcome"],
        "binding_policy_clauses": constraints["binding_clauses"],
        "binding_rule_reasons": constraints["binding_reasons"],
        "risk_model": model_verdict(risk),
        "policy_findings": [f for f in state["policy"]["findings"] if f["applies"]],
        "policy_summary": state["policy"]["summary"],
        "fraud_screening": {k: state["fraud"].get(k) for k in
                            ("risk_level", "indicators", "refer_for_fraud_review")},
        "officer_advisory_notes": state["fraud"].get("advisory_notes", []),
        "explanation": state["explanation"],
        "available_clause_ids": sorted({f["clause_id"] for f in state["policy"]["findings"]}
                                       | set(constraints["binding_clauses"])),
    }
    if feedback:
        payload["REVIEWER_FEEDBACK_FROM_PREVIOUS_ATTEMPT"] = feedback
    try:
        result = ask(DecisionDraft, DECISION_SYSTEM, payload, config, tier="strong",
                     name="decision_agent")
        result["source"] = "llm"
    except Exception as exc:
        # An empty draft fails the critic, which triggers a retry or the fail-safe
        result = {"decision": "REFER", "principal_reasons": [], "cited_clauses": [],
                  "compensating_factors": [], "summary": "",
                  "source": f"error ({type(exc).__name__}: {str(exc)[:120]})"}
    return result


# ----------------------------------------------------------------------------- Report
def report_agent(state, config=None):
    record = {
        "application_id": state["application_id"],
        "final_outcome": state["final_outcome"],
        "system_decision": state["final_decision"],
        "risk": {k: state["risk"][k] for k in
                 ("probability_of_default", "risk_zone", "model_version")},
        "key_factors": state["explanation"]["key_factors"],
        "policy_findings": [f for f in state["policy"]["findings"] if f["applies"]],
        "fraud_screening": {k: state["fraud"].get(k) for k in ("risk_level", "indicators")},
        "human_review": state.get("human_review"),
    }
    try:
        result = ask(ReportOutput, REPORT_SYSTEM, record, config, tier="strong",
                     name="report_agent")
        result["source"] = "llm"
    except Exception as exc:
        d = state["final_decision"]
        reasons = "\n".join(f"- {r}" for r in d.get("principal_reasons", []))
        result = {
            "internal_memo": (f"# Credit memo {state['application_id']}\n\n"
                              f"**Outcome:** {state['final_outcome']}\n\n{d.get('summary', '')}"
                              f"\n\n**Reasons:**\n{reasons}\n"),
            "applicant_notice": (f"Dear Applicant,\n\nThe outcome of your application is: "
                                 f"{state['final_outcome']}.\n{reasons}\n"),
            "source": f"fallback ({type(exc).__name__}: {str(exc)[:150]})"}
    return result