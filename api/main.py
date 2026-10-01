"""
CreditMind - FastAPI backend.

Run locally (with Postgres running):
    python -m uvicorn api.main:app --reload --port 8000
Interactive docs: http://localhost:8000/docs
"""

import json
import os
from contextlib import asynccontextmanager
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

from api import auth, db, service  # noqa: E402  (after load_dotenv so settings are available)

DEMO_PATH = "data/processed/applications_demo.csv"
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
                if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    service.startup()
    yield
    service.shutdown()


app = FastAPI(
    title="CreditMind API",
    version="2.0.0",
    description="Multi-agent credit underwriting: ML risk model, RAG policy checks, "
                "guardrails, critic loop, self-consistency and human-in-the-loop review.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False,
                   allow_methods=["GET", "POST", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type", "X-API-Key"])

protected = [Depends(auth.require_auth)]


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class ReviewDecision(BaseModel):
    decision: Literal["APPROVE", "DECLINE"]
    officer_id: str = Field(min_length=2, max_length=50)
    note: str = Field("", max_length=1000)


# ---------------------------------------------------------------------------- public
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/auth/login")
def login(body: LoginRequest):
    if not auth.auth_enabled():
        return {"token": "local-dev", "expires_at": None, "auth": "disabled"}
    if not auth.check_password(body.password):
        raise HTTPException(status_code=401, detail="Incorrect password")
    return auth.create_token()


# ---------------------------------------------------------------------------- protected
@app.post("/applications", status_code=202, dependencies=protected)
def submit_application(application: dict, background: BackgroundTasks):
    """Validate the application, then run the agents in the background."""
    try:
        app_id, inputs, tags = service.accept(application)
    except service.InvalidApplication as exc:
        raise HTTPException(status_code=422, detail={"errors": exc.errors})
    except service.DuplicateApplication:
        raise HTTPException(status_code=409, detail="Application ID already exists")
    background.add_task(service.run_new, app_id, inputs, tags)
    return {"application_id": app_id, "status": "PROCESSING"}


@app.get("/applications", dependencies=protected)
def list_applications(status: Optional[str] = Query(None), limit: int = Query(200, le=1000)):
    return db.list_applications(status=status, limit=limit)


@app.get("/applications/{application_id}", dependencies=protected)
def get_application(application_id: str):
    row = db.get_application(application_id)
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return row


@app.get("/review-queue", dependencies=protected)
def review_queue():
    return db.review_queue()


@app.post("/applications/{application_id}/review", status_code=202, dependencies=protected)
def review_application(application_id: str, review: ReviewDecision,
                       background: BackgroundTasks):
    """Credit officer's decision: resumes the paused workflow (LangGraph interrupt)."""
    row = db.get_application(application_id)
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    if row["status"] != "PENDING_REVIEW":
        raise HTTPException(status_code=409,
                            detail=f"Application is {row['status']}, not PENDING_REVIEW")
    if review.decision == "APPROVE" and len(review.note.strip()) < 20:
        raise HTTPException(status_code=422,
                            detail="Approving a referral is an override (POL-8.2): "
                                   "write a justification of at least 20 characters")
    db.update_application(application_id, status="PROCESSING")
    background.add_task(service.resume, application_id, review.model_dump())
    return {"application_id": application_id, "status": "PROCESSING"}


@app.get("/stats", dependencies=protected)
def stats():
    """Portfolio overview for the dashboard."""
    return db.stats()


@app.get("/demo/applications", dependencies=protected)
def demo_applications(start: int = Query(0, ge=0), n: int = Query(20, ge=1, le=200)):
    """Synthetic demo applications (real Lending Club credit data, fake personal details)."""
    import pandas as pd
    if not os.path.exists(DEMO_PATH):
        raise HTTPException(status_code=404, detail="Demo data not available")
    df = pd.read_csv(DEMO_PATH).iloc[start:start + n]
    return json.loads(df.to_json(orient="records"))