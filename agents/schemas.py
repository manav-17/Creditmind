"""
CreditMind - Structured outputs the LLM agents must return.

These schemas are deliberately loose (no hard limits) so the LLM call itself rarely fails;
strict checks happen afterwards in the output guardrail, which gives the Decision Agent
precise feedback to fix.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class FraudAssessment(BaseModel):
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    income_plausible: bool = Field(
        description="Is the stated income plausible for the job title and loan profile?")
    indicators: list[str] = Field(
        default_factory=list, description="Specific fraud indicators found; empty if none")
    refer_for_fraud_review: bool
    policy_clause: Optional[str] = Field(
        None, description="If referring for fraud review: POL-6.1, POL-6.2 or POL-6.3")
    reasoning: str = Field(description="2-3 sentences")


class ClauseFinding(BaseModel):
    clause_id: str
    applies: bool
    explanation: str = Field(description="One sentence using the applicant's actual figures")


class PolicyAssessment(BaseModel):
    findings: list[ClauseFinding]
    summary: str = Field(description="2-3 sentences on the overall policy position")


class ExplanationOutput(BaseModel):
    key_factors: list[str] = Field(description="3-5 plain-language risk drivers with values")
    candidate_reasons: list[str] = Field(
        description="Up to 4 specific adverse-action reasons, most important first")
    plain_summary: str = Field(description="2-3 sentences")


class DecisionDraft(BaseModel):
    decision: Literal["APPROVE", "DECLINE", "REFER"]
    principal_reasons: list[str] = Field(
        default_factory=list, description="1-4 specific reasons for DECLINE/REFER")
    cited_clauses: list[str] = Field(default_factory=list, description="Policy clause IDs")
    compensating_factors: list[str] = Field(default_factory=list)
    summary: str = Field(description="2-4 sentences for the credit officer")


class ReportOutput(BaseModel):
    internal_memo: str = Field(description="Markdown credit memo for the audit file")
    applicant_notice: str = Field(description="Plain-language letter to the applicant")