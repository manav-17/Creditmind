"""
CreditMind - FastAPI backend.

Run (from the project root, with Postgres running):
    uvicorn api.main:app --reload --port 8000
Interactive docs: http://localhost:8000/docs
"""

import json
import os
from contextlib import asynccontextmanager
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

load_dotenv()

from api import db, service  # noqa: E402  (after load_dotenv so settings are available)

API_KEY = os.getenv("API_KEY")  # optional: when set, every call needs the X-API-Key header
DEMO_PATH = "data/processed/applications_demo.csv"


@asynccontextmanager
async def lifespan(app: FastAPI):
    service.startup()
    yield
    service.shutdown()


app = FastAPI(
    title="CreditMind API",
    version="1.0.0",
    description="Multi-agent credit underwriting: ML risk model, RAG policy checks, "
                "guardrails, critic loop and human-in-the-loop review.",
    lifespan=lifespan,
)


def require_api_key(x_api_key: Optional[str] = Header(None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")


class ReviewDecision(BaseModel):
    decision: Literal["APPROVE", "DECLINE"]
    officer_id: str = Field(min_length=2, max_length=50)
    note: str = Field("", max_length=1000)


# ---------------------------------------------------------------------------- endpoints
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/applications", status_code=202, dependencies=[Depends(require_api_key)])
def submit_application(application: dict, background: BackgroundTasks):
    """Validate the application, then run the agents in the background."""
    try:
        app_id, inputs, tags = service.accept(application)
    except service.InvalidApplication as exc:
        raise HTTPException(status_code=422, detail={"errors": exc.errors})
    except service.DuplicateApplication:
        raise HTTPException(status_code=409, detail="Application ID already exists")
    background.add_task(service.run_new, app_id, inputs, tags)
    return {"application_id": app_id, "status": "PROCESSING",
            "links": {"status": f"/applications/{app_id}"}}


@app.get("/applications", dependencies=[Depends(require_api_key)])
def list_applications(status: Optional[str] = Query(None), limit: int = Query(200, le=1000)):
    return db.list_applications(status=status, limit=limit)


@app.get("/applications/{application_id}", dependencies=[Depends(require_api_key)])
def get_application(application_id: str):
    row = db.get_application(application_id)
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return row


@app.get("/review-queue", dependencies=[Depends(require_api_key)])
def review_queue():
    return db.review_queue()


@app.post("/applications/{application_id}/review", status_code=202,
          dependencies=[Depends(require_api_key)])
def review_application(application_id: str, review: ReviewDecision,
                       background: BackgroundTasks):
    """Credit officer's decision: resumes the paused workflow (LangGraph interrupt)."""
    row = db.get_application(application_id)
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    if row["status"] != "PENDING_REVIEW":
        raise HTTPException(status_code=409,
                            detail=f"Application is {row['status']}, not PENDING_REVIEW")
    db.update_application(application_id, status="PROCESSING")
    background.add_task(service.resume, application_id, review.model_dump())
    return {"application_id": application_id, "status": "PROCESSING"}


@app.get("/demo/applications", dependencies=[Depends(require_api_key)])
def demo_applications(start: int = Query(0, ge=0), n: int = Query(20, ge=1, le=200)):
    """Synthetic demo applications (fake PII) for testing the dashboard."""
    import pandas as pd
    if not os.path.exists(DEMO_PATH):
        raise HTTPException(status_code=404, detail="Demo data not available")
    df = pd.read_csv(DEMO_PATH).iloc[start:start + n]
    return json.loads(df.to_json(orient="records"))