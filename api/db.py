"""
CreditMind - Application status and decision records in Postgres.

Status lifecycle:
    PROCESSING -> PENDING_REVIEW -> PROCESSING -> COMPLETED
    PROCESSING -> COMPLETED
    PROCESSING -> ERROR
"""

from psycopg.types.json import Jsonb

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    application_id          TEXT PRIMARY KEY,
    status                  TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    final_outcome           TEXT,
    probability_of_default  DOUBLE PRECISION,
    risk_zone               TEXT,
    input_summary           JSONB,
    review_request          JSONB,
    record                  JSONB,
    error                   TEXT
);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications (status);
"""

JSON_FIELDS = {"input_summary", "review_request", "record"}
UPDATABLE = {"status", "final_outcome", "probability_of_default", "risk_zone",
             "review_request", "record", "error"}
LIST_COLUMNS = ("application_id, status, created_at, updated_at, final_outcome, "
                "probability_of_default, risk_zone")

_pool = None


def init(pool):
    global _pool
    _pool = pool
    # One statement per execute: prepared statements allow only a single command
    with _pool.connection() as conn:
        for statement in SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)


def _row(cursor):
    row = cursor.fetchone()
    if row is None:
        return None
    return {desc.name: value for desc, value in zip(cursor.description, row)}


def create_application(application_id, input_summary):
    """Returns False if the application ID already exists."""
    with _pool.connection() as conn:
        cur = conn.execute(
            "INSERT INTO applications (application_id, status, input_summary) "
            "VALUES (%s, 'PROCESSING', %s) ON CONFLICT (application_id) DO NOTHING "
            "RETURNING application_id",
            (application_id, Jsonb(input_summary)))
        return cur.fetchone() is not None


def update_application(application_id, **fields):
    unknown = set(fields) - UPDATABLE
    if unknown:
        raise ValueError(f"Cannot update fields: {unknown}")
    sets, values = [], []
    for key, value in fields.items():
        sets.append(f"{key} = %s")
        values.append(Jsonb(value) if key in JSON_FIELDS and value is not None else value)
    sets.append("updated_at = now()")
    with _pool.connection() as conn:
        conn.execute(f"UPDATE applications SET {', '.join(sets)} WHERE application_id = %s",
                     (*values, application_id))


def get_application(application_id):
    with _pool.connection() as conn:
        return _row(conn.execute("SELECT * FROM applications WHERE application_id = %s",
                                 (application_id,)))


def list_applications(status=None, limit=200):
    query = f"SELECT {LIST_COLUMNS} FROM applications"
    params = []
    if status:
        query += " WHERE status = %s"
        params.append(status)
    query += " ORDER BY updated_at DESC LIMIT %s"
    params.append(limit)
    with _pool.connection() as conn:
        cur = conn.execute(query, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def review_queue():
    with _pool.connection() as conn:
        cur = conn.execute(
            "SELECT application_id, updated_at, probability_of_default, risk_zone, "
            "review_request FROM applications WHERE status = 'PENDING_REVIEW' "
            "ORDER BY updated_at ASC")
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]