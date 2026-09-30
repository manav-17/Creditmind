"""
CreditMind - Langfuse tracing (optional: the pipeline runs without it).

.env:
    LANGFUSE_PUBLIC_KEY=pk-lf-...
    LANGFUSE_SECRET_KEY=sk-lf-...
    LANGFUSE_HOST=https://cloud.langfuse.com
"""

import os

from dotenv import load_dotenv

load_dotenv()


def get_langfuse_handler():
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        print("Langfuse keys not found in .env - running without tracing")
        return None
    try:
        from langfuse.langchain import CallbackHandler
        return CallbackHandler()
    except Exception as exc:
        print(f"Langfuse not available ({exc}) - running without tracing")
        return None


def build_config(application_id, handler, session_id, tags=None):
    """LangGraph run config: thread for the checkpointer, callbacks + metadata for Langfuse."""
    config = {"configurable": {"thread_id": application_id},
              "run_name": f"credit_underwriting {application_id}",
              "metadata": {"langfuse_session_id": session_id,
                           "langfuse_tags": ["creditmind"] + (tags or []),
                           "application_id": application_id}}
    if handler:
        config["callbacks"] = [handler]
    return config


def add_score(handler, name, value, comment=None):
    """Attach an evaluation score to the most recent trace (e.g. critic passed first try)."""
    if not handler:
        return
    try:
        from langfuse import get_client
        trace_id = getattr(handler, "last_trace_id", None)
        if trace_id:
            get_client().create_score(trace_id=trace_id, name=name, value=value,
                                      comment=comment)
    except Exception:
        pass  # scoring must never break the pipeline


def flush():
    try:
        from langfuse import get_client
        get_client().flush()
    except Exception:
        pass