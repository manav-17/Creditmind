"""
CreditMind - LangGraph workflow.

  intake ─┬─> risk_scoring ─┐
          ├─> fraud_check  ─┼─> consolidate ─> explain ─┬─> decide_vote x3 ─> tally ─> grounding_check ─> critic
          └─> policy_check ─┘                           │      (Send API: parallel self-consistency votes)    │
                                                        └──────────── retry with feedback (1 vote) <───────────┤
                                                                                                               ├─> human_review ─> report
                                                                                                               └─> report ─> END

  * risk_scoring, fraud_check and policy_check run in parallel (fan-out / fan-in)
  * self-consistency: N independent decision votes (Send API), median-strictness tally,
    agreement score recorded as an uncertainty signal
  * grounding_check: every figure in the decision must match a code-computed fact
  * critic = output guardrail; max 3 attempts, then a deterministic fail-safe
  * REFER decisions pause at human_review (LangGraph interrupt) until a credit officer answers
"""

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from agents.base import policy_retriever, risk_model
from agents.specialists import (NEUTRAL_VERIFICATION, decision_agent, explain_agent,
                                fraud_agent, policy_agent, reasons_for_applicant, report_agent)
from graph.state import CreditState
from guardrails import vault
from guardrails.grounding import _numbers_in, build_facts, find_unsupported
from guardrails.output_guard import ZONE_TO_OUTCOME, check_decision
from guardrails.pii import _mask_known_values, find_pii_leaks
from ml.risk_model import READABLE_NAMES, format_value
from policy.rules import STRICTNESS, evaluate_policy

MAX_ATTEMPTS = 3  # first try + 2 retries
DECISION_VOTES = max(1, int(os.getenv("DECISION_VOTES", "3")))     # self-consistency votes
VOTE_TEMPERATURE = float(os.getenv("VOTE_TEMPERATURE", "0.7"))       # diversity between votes
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


def _framework_text(clause_id, state, constraints):
    """Explanations code can write exactly: their meaning is fixed by the model and rules."""
    risk = state["risk"]
    pd_value, t = risk["probability_of_default"], risk["thresholds"]
    zone_text = {"APPROVE": f"below the {t['approve_below']:.1%} approval threshold",
                 "REVIEW": f"between {t['approve_below']:.1%} and {t['reject_above']:.1%} "
                           "(officer review zone)",
                 "REJECT": f"above the {t['reject_above']:.1%} decline threshold"}[risk["risk_zone"]]
    required = constraints["required_outcome"]
    return {
        "POL-1.2": f"Exactly one outcome applies; the minimum required outcome is "
                   f"{required.lower()}.",
        "POL-1.3": f"The model's probability of default is {pd_value:.1%}, {zone_text}, so the "
                   f"model alone supports {constraints['model_required'].lower()}.",
        "POL-1.4": f"The stricter outcome applies: model {constraints['model_required'].lower()}"
                   f", policy rules {constraints['policy_required'].lower()}, so the minimum "
                   f"is {required.lower()}.",
        "POL-7.1": "Prohibited factors are excluded from the model's inputs and from every "
                   "explanation.",
        "POL-7.2": "Principal reasons are taken from the binding policy rules and the model's "
                   "risk-increasing factors.",
    }.get(clause_id)


def _governance_finding(clause_id, state, constraints):
    """POL-8.x (review and governance): (applies, explanation) written by code from the
    actual review state, or None for other clauses."""
    if not clause_id.startswith("POL-8."):
        return None
    review = state.get("human_review")
    decided = (state.get("final_decision") or {}).get("decision") or constraints["required_outcome"]
    referred = decided == "REFER"
    if clause_id == "POL-8.1":
        if review:
            reason = "with a written reason" if review.get("note") else "without a written reason"
            return True, (f"Credit officer {review['officer_id']} decided "
                          f"{review['decision'].lower()}, {reason}.")
        if referred:
            return True, "The application is referred, so only a credit officer may approve it."
        return False, f"Not a referral: the outcome ({decided.lower()}) needs no officer decision."
    if clause_id == "POL-8.2":
        if review and review.get("override"):
            return True, (f"Override: credit officer {review['officer_id']} approved a referred "
                          "application; the written justification is kept in the audit trail.")
        if review:
            return False, ("Not an override: the officer declined, which is no more lenient "
                           "than the system's referral.")
        if referred:
            return True, ("Approving this referral would override the system and requires a "
                          "written justification.")
        return False, "No override: the outcome was decided without officer review."
    if clause_id == "POL-8.3":
        version = state.get("risk", {}).get("model_version", "unknown")
        items = [f"the model version ({version})", "the probability of default",
                 "the policy clauses", "the explanation"]
        if review:
            items.append("the officer review")
        return True, f"The decision record keeps {', '.join(items[:-1])} and {items[-1]}."
    if clause_id == "POL-8.4":
        return False, "Model monitoring applies to the model as a whole, not to one application."
    return None


def _refresh_governance(state):
    """Rewrite POL-8.x findings with the review state as it is now (used at report time)."""
    findings = []
    for finding in state["policy"].get("findings", []):
        governance = _governance_finding(finding["clause_id"], state, state["constraints"])
        if governance:
            applies, text = governance
            finding = {**finding, "applies": applies, "explanation": text, "source": "system"}
        findings.append(finding)
    return findings


def _ground_findings(state, hits, constraints):
    """Code writes what code knows; the LLM's text is kept only where it adds understanding.

    * clauses triggered by the rule engine (or fraud escalation): the rule's exact reason
    * decision-framework clauses: text computed from the model zone and rules
    * other retrieved clauses: the policy agent's explanation, grounding-checked
    """
    facts = build_facts(state, [c["text"] for c in policy_retriever().clauses])
    triggered = {h["clause_id"]: h["reason"] for h in hits}
    noted = {h["clause_id"]: h["reason"] for h in state["policy"]["rule_engine"]["hits"]
             if h["outcome"] == "NOTE"}
    findings, corrected = [], []
    for finding in state["policy"].get("findings", []):
        cid = finding["clause_id"]
        rule_reason = triggered.get(cid) or noted.get(cid)
        governance = None if rule_reason else _governance_finding(cid, state, constraints)
        framework = None if rule_reason or governance else _framework_text(cid, state,
                                                                             constraints)
        if rule_reason:
            finding = {**finding, "applies": True, "explanation": rule_reason,
                       "source": "rule_engine"}
        elif governance:
            applies, text = governance
            finding = {**finding, "applies": applies, "explanation": text, "source": "system"}
        elif framework:
            finding = {**finding, "applies": True, "explanation": framework,
                       "source": "system"}
        else:
            unsupported = find_unsupported(finding.get("explanation", ""), facts)
            if unsupported:
                corrected.append(f"{cid} ({', '.join(unsupported)})")
                finding = {**finding, "unverified_figures": unsupported,
                           "explanation": "Explanation removed: it contained figures that "
                                          "could not be verified against the application data."}
            finding = {**finding, "source": "llm"}
        findings.append(finding)
    present = {f["clause_id"] for f in findings}
    for cid, reason in triggered.items():  # every binding clause appears in the findings
        if cid not in present:
            findings.append({"clause_id": cid, "applies": True, "explanation": reason,
                             "source": "rule_engine"})
    return findings, corrected


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
    findings, corrected = _ground_findings(state, hits, constraints)
    audit = event("consolidate", f"required minimum outcome {required} "
                                 f"(model {model_required}, policy {policy_required})")
    if corrected:
        audit += event("grounding", "policy findings corrected, unverified figures: "
                                    + "; ".join(corrected))
    return {"constraints": constraints,
            "policy": {**state["policy"], "findings": findings},
            "audit": audit}


def explain(state: CreditState, config):
    result = explain_agent(state, config)
    return {"explanation": result,
            "audit": event("explain", f"{len(result['key_factors'])} key factors "
                                      f"[{result['source']}]")}


def vote_sends(state: CreditState):
    """Fan out decision votes with the Send API (map step of map-reduce).

    First attempt: DECISION_VOTES independent votes in parallel.
    Retries: one vote that fixes the critic's feedback (the vote already happened).
    """
    attempt = state.get("attempts", 0) + 1
    count = DECISION_VOTES if attempt == 1 else 1
    critic_result = state.get("critic") or {}
    feedback = (critic_result.get("violations")
                if critic_result and not critic_result.get("passed") else None)
    return [Send("decide_vote", {**state, "vote_attempt": attempt, "vote_index": i,
                                 "vote_count": count, "vote_feedback": feedback})
            for i in range(count)]


def decide_vote(state: CreditState, config):
    attempt, index, count = state["vote_attempt"], state["vote_index"], state["vote_count"]
    temperature = VOTE_TEMPERATURE if count > 1 else 0.0
    draft = decision_agent(state, state.get("vote_feedback"), config,
                           temperature=temperature, name=f"decision_vote_{index + 1}")
    return {"votes": [{**draft, "attempt": attempt, "vote": index + 1}],
            "audit": event("decide", f"attempt {attempt} vote {index + 1}/{count}: "
                                     f"{draft['decision']} [{draft['source']}]")}


def tally(state: CreditState):
    """Reduce step: median-strictness vote (majority for 3 votes; full split -> REFER)."""
    attempt = state.get("attempts", 0) + 1
    votes = [v for v in state.get("votes", []) if v.get("attempt") == attempt]
    usable = [v for v in votes if v.get("summary")] or votes  # skip failed LLM calls
    ordered = sorted(usable, key=lambda v: STRICTNESS[v["decision"]])
    chosen_decision = ordered[len(ordered) // 2]["decision"]
    chosen = next(v for v in usable if v["decision"] == chosen_decision)
    decision = {k: v for k, v in chosen.items() if k not in ("attempt", "vote")}

    distribution = Counter(v["decision"] for v in votes)
    agreement = round(distribution[chosen_decision] / len(votes), 2)
    update = {"decision": decision, "attempts": attempt}
    if attempt == 1:  # keep the original vote as the uncertainty signal
        update["consistency"] = {"votes": len(votes), "distribution": dict(distribution),
                                 "chosen": chosen_decision, "agreement": agreement}
    summary = ", ".join(f"{d} x{n}" for d, n in distribution.items())
    update["audit"] = event("tally", f"attempt {attempt}: {summary} -> {chosen_decision}"
                                     f" (agreement {agreement:.0%})")
    return update


def grounding_check(state: CreditState):
    """Every figure in the decision text must match a fact computed by code."""
    d = state["decision"]
    text = " ".join(d.get("principal_reasons", []) + [d.get("summary", "")])
    facts = build_facts(state, [c["text"] for c in policy_retriever().clauses])
    unsupported = find_unsupported(text, facts)
    checked = len(_numbers_in(text))
    return {"grounding": {"attempt": state["attempts"], "checked": checked,
                          "unsupported": unsupported},
            "audit": event("grounding", f"{checked} figures checked, "
                                        f"{len(unsupported)} unsupported"
                                        + (f": {', '.join(unsupported)}" if unsupported else ""))}


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
    violations = list(result.violations)
    grounding = state.get("grounding") or {}
    if grounding.get("attempt") == state["attempts"] and grounding.get("unsupported"):
        violations.append("Unsupported figures not found in the input data: "
                          f"{', '.join(grounding['unsupported'])}. Use only figures provided "
                          "in the input; never invent or estimate numbers")
    passed = not violations

    review = {"passed": passed, "violations": violations,
              "warnings": result.warnings, "attempt": state["attempts"]}
    update = {"critic": review}
    if passed:
        update["final_decision"] = {**result.decision, "fail_safe": False}
        update["audit"] = event("critic", f"attempt {state['attempts']} passed")
    elif state["attempts"] >= MAX_ATTEMPTS:
        update["final_decision"] = fail_safe(state)
        update["audit"] = event("critic", f"attempt {state['attempts']} failed; "
                                          "fail-safe decision applied")
    else:
        update["audit"] = event("critic", f"attempt {state['attempts']} blocked, retrying: "
                                          + " | ".join(v[:140] for v in violations))
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


def build_record(state):
    """The complete, PII-free decision record (stored in files, the database and the UI)."""
    return {
        "application_id": state["application_id"],
        "completed_at": now(),
        "final_outcome": state.get("final_outcome"),
        "final_decision": state.get("final_decision"),
        "risk": state.get("risk"),
        "constraints": state.get("constraints"),
        "policy": {k: state.get("policy", {}).get(k)
                   for k in ("rule_engine", "retrieved", "findings", "summary")},
        "fraud": state.get("fraud"),
        "explanation": state.get("explanation"),
        "decision_attempts": state.get("attempts"),
        "consistency": state.get("consistency"),
        "grounding": state.get("grounding"),
        "votes": [{"attempt": v.get("attempt"), "vote": v.get("vote"),
                   "decision": v.get("decision"), "source": v.get("source")}
                  for v in state.get("votes", [])],
        "critic": state.get("critic"),
        "human_review": state.get("human_review"),
        "input_flags": state.get("input_flags"),
        "report": state.get("report"),
        "audit": state.get("audit", []),
    }


SOURCE_LABELS = {"rule_engine": "rule engine", "system": "system", "llm": "policy agent"}


def policy_table(findings):
    """The official policy findings, written by code from the decision record."""
    rows = [f for f in findings if f.get("applies")]
    if not rows:
        return ""
    lines = ["## Policy findings (system record)", "",
             "| Clause | Finding | Source |", "|---|---|---|"]
    for f in rows:
        text = str(f.get("explanation", "")).replace("|", "/").replace("\n", " ")
        source = SOURCE_LABELS.get(f.get("source"), "policy agent")
        lines.append(f"| {f['clause_id']} | {text} | {source} |")
    return "\n".join(lines)


def report(state: CreditState, config):
    if not state.get("final_outcome"):
        state = {**state, "final_outcome": state["final_decision"]["decision"]}
    # Governance findings must describe the review as it actually happened
    state = {**state, "policy": {**state["policy"], "findings": _refresh_governance(state)}}
    result = report_agent(state, config)

    # Last line of defence: no personal data in anything we store or send
    pii = vault.get(state["application_id"])
    for key in ("internal_memo", "applicant_notice"):
        if find_pii_leaks(result[key], pii):
            result[key], _ = _mask_known_values(result[key], pii)

    # The neutral verification reason can never be dropped from a decline letter
    notice = result["applicant_notice"]
    if (state["final_outcome"] == "DECLINE"
            and NEUTRAL_VERIFICATION.rstrip(".") in reasons_for_applicant(state)
            and "verify" not in notice.lower()):
        marker = re.search(r"\n\s*(Sincerely|Regards|Kind regards|Yours)", notice)
        insert = f"\n\n{NEUTRAL_VERIFICATION}\n"
        notice = (notice[:marker.start()] + insert + notice[marker.start():]) if marker \
            else notice.rstrip() + insert
        result["applicant_notice"] = notice

    table = policy_table(state["policy"].get("findings", []))
    if table:
        result["internal_memo"] = result["internal_memo"].rstrip() + "\n\n" + table
    record = build_record({**state, "report": result})
    try:  # local copy for inspection; the API stores the record in Postgres
        os.makedirs(DECISIONS_DIR, exist_ok=True)
        with open(f"{DECISIONS_DIR}/{state['application_id']}.json", "w",
                  encoding="utf-8") as f:
            json.dump(record, f, indent=2, default=str)
        with open(f"{DECISIONS_DIR}/{state['application_id']}.md", "w",
                  encoding="utf-8") as f:
            f.write(result["internal_memo"] + "\n\n---\n\n## Applicant notice\n\n"
                    + result["applicant_notice"])
    except OSError:
        pass
    return {"report": result, "final_outcome": state["final_outcome"],
            "audit": event("report", f"memo saved, outcome {state['final_outcome']}")}


# ============================================================================ routing
def route_after_critic(state: CreditState):
    if not state["critic"]["passed"] and state["attempts"] < MAX_ATTEMPTS:
        return vote_sends(state)  # retry: one corrective vote with the critic's feedback
    if state["final_decision"]["decision"] == "REFER":
        return "human_review"
    return "report"


# ============================================================================ graph
def build_graph(checkpointer=None):
    g = StateGraph(CreditState)
    for name, fn in [("intake", intake), ("risk_scoring", risk_scoring),
                     ("fraud_check", fraud_check), ("policy_check", policy_check),
                     ("consolidate", consolidate), ("explain", explain),
                     ("decide_vote", decide_vote), ("tally", tally),
                     ("grounding_check", grounding_check), ("critic", critic),
                     ("human_review", human_review), ("report", report)]:
        g.add_node(name, fn)

    g.add_edge(START, "intake")
    for branch in ("risk_scoring", "fraud_check", "policy_check"):  # parallel fan-out
        g.add_edge("intake", branch)
    g.add_edge(["risk_scoring", "fraud_check", "policy_check"], "consolidate")  # fan-in
    g.add_edge("consolidate", "explain")
    g.add_conditional_edges("explain", vote_sends, ["decide_vote"])  # Send: parallel votes
    g.add_edge("decide_vote", "tally")                                 # reduce
    g.add_edge("tally", "grounding_check")
    g.add_edge("grounding_check", "critic")
    g.add_conditional_edges("critic", route_after_critic,
                            ["decide_vote", "human_review", "report"])
    g.add_edge("human_review", "report")
    g.add_edge("report", END)

    if checkpointer is None:
        try:
            from langgraph.checkpoint.memory import InMemorySaver as Saver
        except ImportError:
            from langgraph.checkpoint.memory import MemorySaver as Saver
        checkpointer = Saver()
    return g.compile(checkpointer=checkpointer)