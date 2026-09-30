"""
CreditMind - PII vault.

The applicant's real identity is stored here, OUTSIDE the LangGraph state, so it never
appears in agent prompts or Langfuse traces. Only the output guardrail reads it, to check
that no personal data leaked into an LLM's output.

  * In-memory by default (terminal runs with graph.run)
  * Postgres when the API calls configure(pool), so it survives restarts
In production this table would use column-level encryption with keys in a secrets
manager (e.g. Cloud KMS) and strict access control.
"""

_MEMORY = {}
_POOL = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS pii_vault (
    application_id  TEXT PRIMARY KEY,
    data            JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def configure(pool):
    """Switch the vault to Postgres (called once by the API at startup)."""
    global _POOL
    _POOL = pool
    with _POOL.connection() as conn:
        conn.execute(SCHEMA)


def store(application_id, pii):
    if _POOL is None:
        _MEMORY[application_id] = dict(pii)
        return
    from psycopg.types.json import Jsonb
    with _POOL.connection() as conn:
        conn.execute("INSERT INTO pii_vault (application_id, data) VALUES (%s, %s) "
                     "ON CONFLICT (application_id) DO UPDATE SET data = EXCLUDED.data",
                     (application_id, Jsonb(dict(pii))))


def get(application_id):
    if _POOL is None:
        return dict(_MEMORY.get(application_id, {}))
    with _POOL.connection() as conn:
        row = conn.execute("SELECT data FROM pii_vault WHERE application_id = %s",
                           (application_id,)).fetchone()
    return dict(row[0]) if row else {}


def delete(application_id):
    if _POOL is None:
        _MEMORY.pop(application_id, None)
        return
    with _POOL.connection() as conn:
        conn.execute("DELETE FROM pii_vault WHERE application_id = %s", (application_id,))