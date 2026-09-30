"""
CreditMind - Pydantic schemas for guardrails.

LoanApplication: validates every incoming application before anything else runs.
DecisionOutput:  the structure the Decision Agent must return.
"""

import math
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Purpose = Literal[
    "debt_consolidation", "credit_card", "home_improvement", "other", "major_purchase",
    "small_business", "medical", "car", "moving", "vacation", "house", "wedding",
    "renewable_energy", "educational",
]
NonNeg = Optional[float]


class LoanApplication(BaseModel):
    model_config = ConfigDict(extra="ignore")  # unknown fields (e.g. target) are dropped

    application_id: str = Field(min_length=3, max_length=50)

    # Restricted PII: never sent to the LLM
    applicant_name: Optional[str] = Field(None, max_length=120)
    phone: Optional[str] = Field(None, max_length=30)
    email: Optional[str] = Field(None, max_length=120)
    pan: Optional[str] = Field(None, max_length=20)

    # Loan request
    loan_amnt: float = Field(ge=1000, le=40000)
    term_months: int
    purpose: Purpose

    # Applicant profile
    annual_inc: float = Field(gt=0, le=10_000_000)
    emp_length_years: Optional[float] = Field(None, ge=0, le=10)
    home_ownership: Literal["RENT", "OWN", "MORTGAGE", "OTHER"]
    verification_status: Literal["Verified", "Source Verified", "Not Verified"]

    # Affordability and utilization (percentages)
    dti: Optional[float] = Field(None, ge=0, le=100)
    revol_util: Optional[float] = Field(None, ge=0, le=150)
    bc_util: Optional[float] = Field(None, ge=0, le=150)
    pct_tl_nvr_dlq: Optional[float] = Field(None, ge=0, le=100)
    percent_bc_gt_75: Optional[float] = Field(None, ge=0, le=100)

    # Credit history (counts, balances, months)
    credit_history_years: Optional[float] = Field(None, ge=0, le=80)
    delinq_2yrs: NonNeg = Field(None, ge=0)
    inq_last_6mths: NonNeg = Field(None, ge=0)
    open_acc: NonNeg = Field(None, ge=0)
    total_acc: NonNeg = Field(None, ge=0)
    pub_rec: NonNeg = Field(None, ge=0)
    pub_rec_bankruptcies: NonNeg = Field(None, ge=0)
    revol_bal: NonNeg = Field(None, ge=0)
    mths_since_last_delinq: NonNeg = Field(None, ge=0)
    mths_since_last_record: NonNeg = Field(None, ge=0)
    mths_since_last_major_derog: NonNeg = Field(None, ge=0)
    collections_12_mths_ex_med: NonNeg = Field(None, ge=0)
    acc_now_delinq: NonNeg = Field(None, ge=0)
    mort_acc: NonNeg = Field(None, ge=0)
    bc_open_to_buy: NonNeg = Field(None, ge=0)
    avg_cur_bal: NonNeg = Field(None, ge=0)
    tot_cur_bal: NonNeg = Field(None, ge=0)
    tot_hi_cred_lim: NonNeg = Field(None, ge=0)
    total_bc_limit: NonNeg = Field(None, ge=0)
    total_rev_hi_lim: NonNeg = Field(None, ge=0)
    acc_open_past_24mths: NonNeg = Field(None, ge=0)
    num_tl_90g_dpd_24m: NonNeg = Field(None, ge=0)
    num_accts_ever_120_pd: NonNeg = Field(None, ge=0)
    mo_sin_old_rev_tl_op: NonNeg = Field(None, ge=0)
    mo_sin_rcnt_tl: NonNeg = Field(None, ge=0)
    mths_since_recent_inq: NonNeg = Field(None, ge=0)
    tax_liens: NonNeg = Field(None, ge=0)
    chargeoff_within_12_mths: NonNeg = Field(None, ge=0)
    delinq_amnt: NonNeg = Field(None, ge=0)
    num_actv_rev_tl: NonNeg = Field(None, ge=0)

    # Free text written by the borrower (untrusted)
    desc: Optional[str] = Field(None, max_length=5000)
    emp_title: Optional[str] = Field(None, max_length=200)
    title: Optional[str] = Field(None, max_length=200)

    @model_validator(mode="before")
    @classmethod
    def clean_missing(cls, data):
        """Turn NaN / numpy scalars (from pandas) into plain Python values."""
        if not isinstance(data, dict):
            return data
        cleaned = {}
        for key, value in data.items():
            if hasattr(value, "item") and not isinstance(value, (str, bytes)):
                value = value.item()
            if isinstance(value, float) and math.isnan(value):
                value = None
            if isinstance(value, str) and value.strip() == "":
                value = None
            cleaned[key] = value
        return cleaned

    @field_validator("term_months", mode="before")
    @classmethod
    def valid_term(cls, value):
        value = int(float(value))
        if value not in (36, 60):
            raise ValueError("term must be 36 or 60 months")
        return value

    # Derived features are computed here, never trusted from the client
    @property
    def loan_to_income(self):
        return round(self.loan_amnt / self.annual_inc, 4)

    @property
    def revol_bal_to_income(self):
        return None if self.revol_bal is None else round(self.revol_bal / self.annual_inc, 4)


class DecisionOutput(BaseModel):
    """What the Decision Agent must return (validated by the output guardrail)."""
    decision: Literal["APPROVE", "DECLINE", "REFER"]
    principal_reasons: list[str] = Field(default_factory=list, max_length=4)
    cited_clauses: list[str] = Field(default_factory=list)
    compensating_factors: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=20, max_length=2000)