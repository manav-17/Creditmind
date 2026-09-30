"""
CreditMind - Service layer between the API and the LangGraph workflow.

  startup()  - Postgres pool, Postgres checkpointer, tables, vault, graph, warm-up
  accept()   - input guardrails + create the application record (fast, synchronous)
  run_new()  - run the graph in the background until it finishes or pauses for review
  resume()   - continue a paused graph with the credit officer's decision
"""

import os
import traceback
from datetime import datetime, timezone

from langgraph.types import Command

from api import db
from graph.workflow import build_graph, build_record
from guardrails import vault
from guardrails.input_guard import run_input_guardrails
from observability.langfuse_setup import add_score, build_config, flush, get_langfuse_handler

_state = {"graph": None, "pool": None, "handler": None}


class InvalidApplication(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


class DuplicateApplication(Exception):
    pass


# ---------------------------------------------------------------------------- lifecycle
def startup():
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg_pool import ConnectionPool

    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is missing - add it to .env "
                           "(and start Postgres with: docker compose up -d db)")
    pool = ConnectionPool(conninfo=url, max_size=10, open=True,
                          kwargs={"autocommit": True, "prepare_threshold": 0})
    checkpointer = PostgresSaver(pool)
    checkpointer.setup()          # creates LangGraph's checkpoint tables
    db.init(pool)                 # application records
    vault.configure(pool)         # PII vault in Postgres
    _state.update(pool=pool, graph=build_graph(checkpointer),
                  handler=get_langfuse_handler())

    # Load the ML model and policy index now, so the first request is fast
    from agents.base import policy_retriever, risk_model
    risk_model()
    policy_retriever()


def shutdown():
    flush()
    if _state["pool"]:
        _state["pool"].close()


# ---------------------------------------------------------------------------- pipeline
def accept(raw):
    """Validate and register an application. Returns (application_id, graph inputs, tags)."""
    guard = run_input_guardrails(raw)
    if not guard.valid:
        raise InvalidApplication(guard.errors)
    summary = {"injection": guard.injection, "pii_found": guard.pii_found}
    if not db.create_application(guard.application_id, summary):
        raise DuplicateApplication(guard.application_id)
    vault.store(guard.application_id, guard.restricted_pii)  # PII stays outside the graph

    inputs = {
        "application_id": guard.application_id,
        "llm_view": guard.llm_view,
        "model_features": guard.model_features,
        "input_flags": summary,
        "audit": [{"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "node": "input_guardrails",
                   "event": f"valid; injection={guard.injection['detected']}; "
                            f"pii fields={list(guard.pii_found)}"}],
    }
    tags = ["api"] + (["injection"] if guard.injection["detected"] else [])
    return guard.application_id, inputs, tags


def _config(application_id, tags):
    # session = application, so the first run and the resumed run are grouped in Langfuse
    return build_config(application_id, _state["handler"], session_id=application_id,
                        tags=tags)


def _save_result(application_id, result):
    risk = result.get("risk", {})
    common = {"probability_of_default": risk.get("probability_of_default"),
              "risk_zone": risk.get("risk_zone")}
    if result.get("__interrupt__"):
        db.update_application(application_id, status="PENDING_REVIEW",
                              review_request=result["__interrupt__"][0].value,
                              record=build_record(result), **common)
        return
    db.update_application(application_id, status="COMPLETED",
                          final_outcome=result.get("final_outcome"), review_request=None,
                          record=build_record(result), **common)
    handler = _state["handler"]
    final = result.get("final_decision") or {}
    add_score(handler, "critic_passed_first_try",
              1 if result.get("attempts") == 1 and not final.get("fail_safe") else 0)
    add_score(handler, "decision_attempts", result.get("attempts") or 0)


def _run(application_id, graph_input, tags):
    try:
        result = _state["graph"].invoke(graph_input, _config(application_id, tags))
        _save_result(application_id, result)
    except Exception as exc:
        traceback.print_exc()
        db.update_application(application_id, status="ERROR",
                              error=f"{type(exc).__name__}: {exc}"[:1000])


def run_new(application_id, inputs, tags):
    _run(application_id, inputs, tags)


def resume(application_id, answer):
    _run(application_id, Command(resume=answer), ["api", "review"])