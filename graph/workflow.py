"""
CreditMind - LangGraph workflow.

  intake ─┬─> risk_scoring ─┐
          ├─> fraud_check  ─┼─> consolidate ─> explain ─> decide ─> critic ─┬─> report ─> END
          └─> policy_check ─┘                              ^                │
                                                           └── retry (≤2) ──┤
                                                                            └─> human_review ─> report

  * risk_scoring, fraud_check and policy_check run in parallel
  * critic = output guardrail; failed decisions go back with feedback, max 3 attempts,
    then a deterministic fail-safe takes over
  * REFER decisions pause at human_review (LangGraph interrupt) until a credit officer answers
"""

import json
import os
from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agents.base import policy_retriever, risk_model
from agents.specialists import (decision_agent, explain_agent, fraud_agent, policy_agent,
                                report_agent)
from graph.state import CreditState
from guardrails import vault
from guardrails.output_guard import ZONE_TO_OUTCOME, check_decision
from guardrails.pii import _mask_known_values, find_pii_leaks
from ml.risk_model import READABLE_NAMES, format_value
from policy.rules import STRICTNESS, evaluate_policy

MAX_ATTEMPTS = 3  # first try + 2 retries
DECISIONS_DIR = "outputs/decisions"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def event(node, message):
    return [{"time": now(), "node": node, "event": message}]


# ============================================================================ nodes
def intake(state: CreditState):
    """Readable profile of the (already validated and masked) application."""
    features = state["model_features"]
    profile = {READABLE_NAMES[k]: format_value(k, v)
               for k, v in features.items() if k in READABLE_NAMES}
    profile["Job title (borrower text)"] = state["llm_view"]["borrower_text"].get("emp_title")
    profile["Loan title (borrower text)"] = state["llm_view"]["borrower_text"].get("title")
    return {"profile": profile,
            "audit": event("intake", f"{len(profile)} profile fields prepared")}


def risk_scoring(state: CreditState):
    assessment = risk_model().assess(state["model_features"], top_k=6)
    return {"risk": assessment,
            "audit": event("risk_scoring", f"PD {assessment['probability_of_default']:.3f}, "
                                           f"zone {assessment['risk_zone']}")}


def _rule_engine(state):
    return evaluate_policy(state["model_features"],
                           injection_detected=state["input_flags"]["injection"]["detected"])


def fraud_check(state: CreditState, config):
    result = fraud_agent(state, _rule_engine(state), config)
    return {"fraud": result,
            "audit": event("fraud_check", f"risk {result['risk_level']}, fraud review "
                                          f"{result['refer_for_fraud_review']} [{result['source']}]")}


def policy_check(state: CreditState, config):
    rules = _rule_engine(state)
    app_for_search = {**state["model_features"],
                      "desc": state["llm_view"]["borrower_text"].get("desc")}
    retrieved = policy_retriever().for_application(app_for_search)
    assessment = policy_agent(state, rules, retrieved, config)
    return {"policy": {"rule_engine": rules,
                       "retrieved": [c["clause_id"] for c in retrieved], **assessment},
            "audit": event("policy_check", f"rules require {rules['required_outcome']}; "
                                           f"{len(retrieved)} clauses retrieved "
                                           f"[{assessment['source']}]")}


def consolidate(state: CreditState):
    """Fan-in: combine model zone, policy rules and fraud escalation into hard constraints."""
    rules = state["policy"]["rule_engine"]
    hits = [h for h in rules["hits"] if h["outcome"] in ("REFER", "DECLINE")]
    fraud = state["fraud"]
    if fraud["refer_for_fraud_review"] and not any(h["clause_id"].startswith("POL-6")
                                                   for h in hits):
        hits.append({"clause_id": fraud.get("policy_clause") or "POL-6.1", "outcome": "REFER",
                     "reason": "Fraud screening: " + ("; ".join(fraud["indicators"])
                                                      or "referred for fraud review")})
    policy_required = max((h["outcome"] for h in hits), key=STRICTNESS.get, default="APPROVE")
    model_required = ZONE_TO_OUTCOME[state["risk"]["risk_zone"]]
    required = max(model_required, policy_required, key=STRICTNESS.get)
    binding = [h for h in hits if h["outcome"] == policy_required]
    constraints = {
        "required_outcome": required,
        "model_required": model_required,
        "policy_required": policy_required,
        "binding_clauses": sorted({h["clause_id"] for h in binding}),
        "binding_reasons": [h["reason"] for h in binding],
    }
    return {"constraints": constraints,
            "audit": event("consolidate", f"required minimum outcome {required} "
                                          f"(model {model_required}, policy {policy_required})")}


def explain(state: CreditState, config):
    result = explain_agent(state, config)
    return {"explanation": result,
            "audit": event("explain", f"{len(result['key_factors'])} key factors "
                                      f"[{result['source']}]")}


def decide(state: CreditState, config):
    attempts = state.get("attempts", 0) + 1
    critic = state.get("critic") or {}
    feedback = critic.get("violations") if critic and not critic.get("passed") else None
    draft = decision_agent(state, feedback, config)
    return {"decision": draft, "attempts": attempts,
            "audit": event("decide", f"attempt {attempts}: {draft['decision']} "
                                     f"[{draft['source']}]")}


def critic(state: CreditState):
    constraints = state["constraints"]
    context = {
        "risk_zone": state["risk"]["risk_zone"],
        "policy": {"required_outcome": constraints["policy_required"],
                   "binding_clauses": constraints["binding_clauses"],
                   # clauses the rule engine noted (apply as risk factors, not triggers)
                   "noted_clauses": [h["clause_id"] for h in state["policy"]["rule_engine"]["hits"]
                                     if h["outcome"] == "NOTE"]},
        "valid_clause_ids": policy_retriever().valid_clause_ids(),
        "restricted_pii": vault.get(state["application_id"]),
    }
    draft = {k: v for k, v in state["decision"].items() if k != "source"}
    result = check_decision(draft, context)
    review = {"passed": result.passed, "violations": result.violations,
              "warnings": result.warnings, "attempt": state["attempts"]}
    update = {"critic": review}

    if result.passed:
        update["final_decision"] = {**result.decision, "fail_safe": False}
        update["audit"] = event("critic", f"attempt {state['attempts']} passed")
    elif state["attempts"] >= MAX_ATTEMPTS:
        update["final_decision"] = fail_safe(state)
        update["audit"] = event("critic", f"attempt {state['attempts']} failed; "
                                          "fail-safe decision applied")
    else:
        update["audit"] = event("critic", f"attempt {state['attempts']} blocked, retrying: "
                                          + " | ".join(v[:140] for v in result.violations))
    return update


def fail_safe(state):
    """Deterministic, policy-compliant decision used when the LLM cannot pass validation."""
    c = state["constraints"]
    outcome = "DECLINE" if c["required_outcome"] == "DECLINE" else "REFER"
    adverse = [f"{f['name']}: {f['value']}" for f in state["risk"]["top_factors"]
               if f["direction"] == "increases risk"]
    return {"decision": outcome,
            "principal_reasons": (c["binding_reasons"] + adverse)[:4],
            "cited_clauses": sorted(set(c["binding_clauses"]) | {"POL-1.4"}),
            "compensating_factors": [],
            "summary": ("The automated decision could not be validated, so a conservative "
                        f"{outcome} was applied under POL-1.4. A credit officer should review."),
            "fail_safe": True}


def human_review(state: CreditState):
    """Pause here until a credit officer decides (LangGraph interrupt)."""
    d = state["final_decision"]
    answer = interrupt({
        "application_id": state["application_id"],
        "recommendation": d["decision"],
        "probability_of_default": state["risk"]["probability_of_default"],
        "risk_zone": state["risk"]["risk_zone"],
        "reasons": d["principal_reasons"],
        "cited_clauses": d["cited_clauses"],
        "fraud_indicators": state["fraud"].get("indicators", []),
        "summary": d["summary"],
        "question": "Credit officer: APPROVE or DECLINE this application?",
    })
    outcome = str(answer.get("decision", "")).upper()
    if outcome not in ("APPROVE", "DECLINE"):
        outcome = "REFER"  # no valid answer: stays pending
    review = {"officer_id": answer.get("officer_id", "officer"), "decision": outcome,
              "note": answer.get("note", ""), "time": now(),
              "override": outcome == "APPROVE"}  # overriding the system's referral (POL-8.2)
    return {"human_review": review, "final_outcome": outcome,
            "audit": event("human_review", f"officer decided {outcome}")}


def report(state: CreditState, config):
    if not state.get("final_outcome"):
        state = {**state, "final_outcome": state["final_decision"]["decision"]}
    result = report_agent(state, config)

    # Last line of defence: no personal data in anything we store or send
    pii = vault.get(state["application_id"])
    for key in ("internal_memo", "applicant_notice"):
        if find_pii_leaks(result[key], pii):
            result[key], _ = _mask_known_values(result[key], pii)

    record = {
        "application_id": state["application_id"],
        "completed_at": now(),
        "final_outcome": state["final_outcome"],
        "final_decision": state["final_decision"],
        "risk": state["risk"],
        "constraints": state["constraints"],
        "policy": {k: state["policy"][k] for k in ("rule_engine", "retrieved", "findings",
                                                    "summary")},
        "fraud": state["fraud"],
        "explanation": state["explanation"],
        "decision_attempts": state["attempts"],
        "human_review": state.get("human_review"),
        "input_flags": state["input_flags"],
        "report": result,
    }
    os.makedirs(DECISIONS_DIR, exist_ok=True)
    with open(f"{DECISIONS_DIR}/{state['application_id']}.json", "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, default=str)
    with open(f"{DECISIONS_DIR}/{state['application_id']}.md", "w", encoding="utf-8") as f:
        f.write(result["internal_memo"] + "\n\n---\n\n## Applicant notice\n\n"
                + result["applicant_notice"])
    return {"report": result, "final_outcome": state["final_outcome"],
            "audit": event("report", f"memo saved, outcome {state['final_outcome']}")}


# ============================================================================ routing
def route_after_critic(state: CreditState):
    if not state["critic"]["passed"] and state["attempts"] < MAX_ATTEMPTS:
        return "decide"
    if state["final_decision"]["decision"] == "REFER":
        return "human_review"
    return "report"


# ============================================================================ graph
def build_graph(checkpointer=None):
    g = StateGraph(CreditState)
    for name, fn in [("intake", intake), ("risk_scoring", risk_scoring),
                     ("fraud_check", fraud_check), ("policy_check", policy_check),
                     ("consolidate", consolidate), ("explain", explain), ("decide", decide),
                     ("critic", critic), ("human_review", human_review), ("report", report)]:
        g.add_node(name, fn)

    g.add_edge(START, "intake")
    for branch in ("risk_scoring", "fraud_check", "policy_check"):  # parallel fan-out
        g.add_edge("intake", branch)
    g.add_edge(["risk_scoring", "fraud_check", "policy_check"], "consolidate")  # fan-in
    g.add_edge("consolidate", "explain")
    g.add_edge("explain", "decide")
    g.add_edge("decide", "critic")
    g.add_conditional_edges("critic", route_after_critic,
                            ["decide", "human_review", "report"])
    g.add_edge("human_review", "report")
    g.add_edge("report", END)

    if checkpointer is None:
        try:
            from langgraph.checkpoint.memory import InMemorySaver as Saver
        except ImportError:
            from langgraph.checkpoint.memory import MemorySaver as Saver
        checkpointer = Saver()
    return g.compile(checkpointer=checkpointer)