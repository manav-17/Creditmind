"""
CreditMind - PolicyRetriever: the tool the Policy Agent calls.

    from rag.policy_retriever import PolicyRetriever
    retriever = PolicyRetriever()
    clauses = retriever.for_application(application_dict)

Hybrid search:
  * Dense search (FastEmbed + FAISS) finds clauses with similar MEANING
  * BM25 keyword search finds clauses with the exact TERMS ("marital status", "DTI")
  * Reciprocal Rank Fusion (RRF) merges both rankings
"""

import json
import math
import re
import os
import faiss
import numpy as np
from fastembed import TextEmbedding
from rank_bm25 import BM25Okapi

INDEX_PATH = "vectorstore/policy.faiss"
CATALOGUE_PATH = "vectorstore/policy_clauses.json"

# BGE models were trained with this instruction in front of search queries
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
RRF_K = 60  # standard RRF constant

# Clauses every decision needs, fetched directly (no search needed)
CORE_CLAUSES = ["POL-1.2", "POL-1.3", "POL-1.4", "POL-7.1", "POL-7.2"]

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "is", "are", "be",
    "was", "were", "with", "by", "at", "as", "it", "this", "that", "can", "we", "must",
    "any", "all", "has", "have", "from", "than", "their", "its", "who", "what", "which",
}


def tokenize(text):
    """Lowercase word tokens; 'debt-to-income' -> ['debt', 'income']."""
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]


def _num(app, key):
    """Read a numeric field; returns None if missing or blank."""
    value = app.get(key)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def build_queries(app):
    """Turn the application's risk signals into (question, reason) pairs."""
    q = []
    dti, lti = _num(app, "dti"), _num(app, "loan_to_income")
    loan, income = _num(app, "loan_amnt"), _num(app, "annual_inc")
    term, inq = _num(app, "term_months"), _num(app, "inq_last_6mths")
    opened = _num(app, "acc_open_past_24mths")
    since_delinq, delinq_2y = _num(app, "mths_since_last_delinq"), _num(app, "delinq_2yrs")
    bankrupt, pub_rec = _num(app, "pub_rec_bankruptcies"), _num(app, "pub_rec")
    util, history = _num(app, "revol_util"), _num(app, "credit_history_years")
    emp = _num(app, "emp_length_years")
    purpose = str(app.get("purpose", "")).lower()
    verification = str(app.get("verification_status", ""))

    if dti is not None and dti >= 30:
        q.append(("maximum debt-to-income ratio DTI and compensating factors",
                  f"DTI is {dti:.1f}%"))
    if lti is not None and lti >= 0.35:
        q.append(("loan amount relative to annual income loan-to-income limit",
                  f"loan is {lti:.0%} of income"))
    if term == 60:
        q.append(("rules for 60-month long-term loans", "60-month term"))
    if purpose == "small_business":
        q.append(("small business loan rules", "small business purpose"))
    if (inq is not None and inq >= 3) or (opened is not None and opened >= 8):
        q.append(("many credit inquiries and accounts opened, credit-seeking behaviour",
                  f"{inq or 0:.0f} inquiries in 6 months, "
                  f"{opened or 0:.0f} accounts opened in 24 months"))
    if (since_delinq is not None and since_delinq <= 24) or (delinq_2y or 0) >= 1:
        reason = (f"last late payment {since_delinq:.0f} months ago"
                  if since_delinq is not None else f"{delinq_2y:.0f} late payments in 2 years")
        q.append(("recent delinquency late payment within the last 12 months", reason))
    if (_num(app, "acc_now_delinq") or 0) > 0 or (_num(app, "delinq_amnt") or 0) > 0:
        q.append(("accounts currently delinquent past-due amount owed", "current delinquency"))
    if (_num(app, "num_tl_90g_dpd_24m") or 0) > 0:
        q.append(("serious delinquency 90 days past due", "90+ days late in last 24 months"))
    if (bankrupt or 0) > 0 or (pub_rec or 0) > 0:
        q.append(("bankruptcies and derogatory public records", "public record on file"))
    if (_num(app, "chargeoff_within_12_mths") or 0) > 0 or \
            (_num(app, "collections_12_mths_ex_med") or 0) > 0:
        q.append(("charge-off or collections in the last 12 months",
                  "recent charge-off/collection"))
    if util is not None and util >= 75:
        q.append(("revolving credit utilization limit", f"utilization {util:.0f}%"))
    if history is not None and history < 3:
        q.append(("minimum credit history length", f"credit history {history:g} years"))
    if loan is not None and loan > 25000 and verification == "Not Verified":
        q.append(("income verification required for larger loans",
                  "large loan with unverified income"))
    if emp is not None and emp < 1 and (loan or 0) > 20000:
        q.append(("employment stability less than one year", "under 1 year in current job"))
    if income is not None and income < 15000:
        q.append(("minimum income requirement", f"income ${income:,.0f}"))
    if income is not None and income > 250000:
        q.append(("income plausibility unverified high income fraud review",
                  f"income ${income:,.0f}"))
    desc = str(app.get("desc") or "")
    if desc and desc.lower() != "nan":
        q.append(("application text attempts to instruct the system, ignore rules, "
                  "approve the loan: manipulation attempts", "borrower-written text present"))
    return q


class PolicyRetriever:
    def __init__(self, index_path=INDEX_PATH, catalogue_path=CATALOGUE_PATH):
        with open(catalogue_path, encoding="utf-8") as f:
            data = json.load(f)
        self.clauses = data["clauses"]
        self.by_id = {c["clause_id"]: c for c in self.clauses}
        self.index = faiss.read_index(index_path)
        self.model = TextEmbedding(model_name=data["embedding_model"],
                                   cache_dir=os.getenv("FASTEMBED_CACHE_PATH"))
        self.bm25 = BM25Okapi([tokenize(c["content"]) for c in self.clauses])

    # ------------------------------------------------------------- lookups
    def valid_clause_ids(self):
        """Used by the Critic to check that every cited clause really exists."""
        return set(self.by_id)

    def get_clause(self, clause_id):
        c = self.by_id.get(clause_id)
        return ({k: c[k] for k in ("clause_id", "title", "section", "text")}
                if c else None)

    # ------------------------------------------------------------- search
    def _dense_ranking(self, question):
        vector = np.array(list(self.model.embed([QUERY_INSTRUCTION + question])),
                          dtype="float32")
        faiss.normalize_L2(vector)
        _, idx = self.index.search(vector, len(self.clauses))
        return list(idx[0])

    def _keyword_ranking(self, question):
        scores = self.bm25.get_scores(tokenize(question))
        return list(np.argsort(-scores))

    def search(self, question, k=3):
        """Hybrid search: fuse dense and keyword rankings with RRF."""
        fused = {}
        for ranking in (self._dense_ranking(question), self._keyword_ranking(question)):
            for rank, i in enumerate(ranking):
                fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
        best = sorted(fused, key=fused.get, reverse=True)[:k]
        return [{**self.get_clause(self.clauses[i]["clause_id"]),
                 "score": round(fused[i], 4)} for i in best]

    def for_application(self, app, k_per_query=2):
        """Core clauses + clauses triggered by this application's risk signals."""
        selected = {}
        for cid in CORE_CLAUSES:
            clause = self.get_clause(cid)
            if clause:
                selected[cid] = {**clause, "triggered_by": "core decision rule"}
        for question, reason in build_queries(app):
            for hit in self.search(question, k=k_per_query):
                if hit["clause_id"] not in selected:
                    selected[hit["clause_id"]] = {**hit, "triggered_by": reason}
        return list(selected.values())


if __name__ == "__main__":
    import pandas as pd

    demo = pd.read_csv("data/processed/applications_demo.csv")
    retriever = PolicyRetriever()
    for i in range(3):
        app = demo.iloc[i].to_dict()
        print(f"\nApplication {app['application_id']}")
        signals = build_queries(app)
        print("Risk signals: " + ("; ".join(r for _, r in signals) if signals else "none"))
        print("Policy clauses retrieved:")
        for c in retriever.for_application(app):
            print(f"  {c['clause_id']:<8} {c['title']:<42} <- {c['triggered_by']}")