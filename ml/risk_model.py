"""
CreditMind - RiskModel: the tool the Risk Scoring and Explainability agents call.

    from ml.risk_model import RiskModel
    model = RiskModel()                       # loads models/ once
    result = model.assess(application_dict)   # PD, zone and top SHAP reasons

The ML model makes the prediction; the LLM agents only read and explain it.
"""

import json
import math

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb

# Human-readable names so explanations make sense in decision memos
READABLE_NAMES = {
    "loan_amnt": "Loan amount requested",
    "annual_inc": "Annual income",
    "dti": "Debt-to-income ratio",
    "delinq_2yrs": "Late payments (30+ days) in last 2 years",
    "inq_last_6mths": "Credit inquiries in last 6 months",
    "open_acc": "Open credit lines",
    "total_acc": "Total credit lines",
    "pub_rec": "Derogatory public records",
    "pub_rec_bankruptcies": "Bankruptcies on record",
    "revol_bal": "Revolving credit balance",
    "revol_util": "Revolving credit utilization",
    "mths_since_last_delinq": "Months since last late payment",
    "mths_since_last_record": "Months since last public record",
    "mths_since_last_major_derog": "Months since last serious delinquency (90+ days)",
    "collections_12_mths_ex_med": "Collections in last 12 months",
    "acc_now_delinq": "Accounts currently delinquent",
    "mort_acc": "Mortgage accounts",
    "bc_util": "Credit card utilization",
    "bc_open_to_buy": "Available credit on cards",
    "avg_cur_bal": "Average balance per account",
    "tot_cur_bal": "Total current balance",
    "tot_hi_cred_lim": "Total credit limit",
    "total_bc_limit": "Total credit card limit",
    "total_rev_hi_lim": "Total revolving credit limit",
    "acc_open_past_24mths": "Accounts opened in last 24 months",
    "num_tl_90g_dpd_24m": "Accounts 90+ days late in last 24 months",
    "num_accts_ever_120_pd": "Accounts ever 120+ days late",
    "pct_tl_nvr_dlq": "Share of accounts never late",
    "percent_bc_gt_75": "Share of cards above 75% of limit",
    "mo_sin_old_rev_tl_op": "Age of oldest revolving account (months)",
    "mo_sin_rcnt_tl": "Months since most recent account opened",
    "mths_since_recent_inq": "Months since most recent inquiry",
    "tax_liens": "Tax liens",
    "chargeoff_within_12_mths": "Charge-offs in last 12 months",
    "delinq_amnt": "Past-due amount owed",
    "num_actv_rev_tl": "Active revolving accounts",
    "term_months": "Loan term",
    "emp_length_years": "Years in current employment",
    "credit_history_years": "Length of credit history",
    "loan_to_income": "Loan amount relative to income",
    "revol_bal_to_income": "Revolving balance relative to income",
    "purpose": "Loan purpose",
    "home_ownership": "Home ownership",
    "verification_status": "Income verification status",
}

MONEY = {"loan_amnt", "annual_inc", "revol_bal", "bc_open_to_buy", "avg_cur_bal",
         "tot_cur_bal", "tot_hi_cred_lim", "total_bc_limit", "total_rev_hi_lim",
         "delinq_amnt"}
PERCENT = {"dti", "revol_util", "bc_util", "pct_tl_nvr_dlq", "percent_bc_gt_75"}
RATIO = {"loan_to_income", "revol_bal_to_income"}


def format_value(feature, value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        # Blank "months since" fields mean the event never happened
        return "never" if feature.startswith(("mths_since", "mo_sin")) else "not reported"
    if feature in MONEY:
        return f"${float(value):,.0f}"
    if feature in PERCENT:
        return f"{float(value):.1f}%"
    if feature in RATIO:
        return f"{float(value) * 100:.0f}% of annual income"
    if feature == "term_months":
        return f"{int(value)} months"
    if feature in {"emp_length_years", "credit_history_years"}:
        return f"{float(value):g} years"
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    return str(value)


class RiskModel:
    def __init__(self, model_dir="models"):
        with open(f"{model_dir}/model_config.json") as f:
            self.config = json.load(f)
        self.booster = xgb.Booster()
        self.booster.load_model(f"{model_dir}/xgb_model.json")
        self.calibrator = joblib.load(f"{model_dir}/calibrator.joblib")
        self.features = self.config["features"]
        self.categorical = self.config["categorical_features"]
        self.thresholds = self.config["thresholds"]
        self.version = self.config["model_version"]

    # -------------------------------------------------------------- internals
    def _frame(self, applications):
        if isinstance(applications, dict):
            applications = [applications]
        df = pd.DataFrame(applications)
        for col in self.features:
            if col not in df.columns:
                df[col] = np.nan
        df = df[self.features].copy()
        for col in self.features:
            if col in self.categorical:
                df[col] = pd.Categorical(df[col].astype("string"),
                                         categories=self.config["categories"][col])
            else:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
        return df

    def _dmatrix(self, df):
        return xgb.DMatrix(df, enable_categorical=True)

    # -------------------------------------------------------------- public API
    def predict_pd(self, applications):
        """Calibrated probability of default for one or many applications."""
        raw = self.booster.predict(self._dmatrix(self._frame(applications)))
        return self.calibrator.predict(raw)

    def zone(self, pd_value):
        if pd_value < self.thresholds["approve_below"]:
            return "APPROVE"
        if pd_value > self.thresholds["reject_above"]:
            return "REJECT"
        return "REVIEW"

    def explain(self, application, top_k=5):
        """Top SHAP drivers for one application (positive impact = raises risk)."""
        df = self._frame(application)
        contribs = self.booster.predict(self._dmatrix(df), pred_contribs=True)[0, :-1]
        order = np.argsort(-np.abs(contribs))
        factors = []
        for i in order[:top_k]:
            feat = self.features[i]
            value = df.iloc[0][feat]
            value = None if pd.isna(value) else value
            factors.append({
                "feature": feat,
                "name": READABLE_NAMES.get(feat, feat),
                "value": format_value(feat, value),
                "impact": round(float(contribs[i]), 4),
                "direction": "increases risk" if contribs[i] > 0 else "decreases risk",
            })
        return factors

    def assess(self, application, top_k=5):
        """Everything the agents need in one call."""
        pd_value = float(self.predict_pd(application)[0])
        return {
            "probability_of_default": round(pd_value, 4),
            "risk_zone": self.zone(pd_value),
            "thresholds": self.thresholds,
            "model_version": self.version,
            "top_factors": self.explain(application, top_k),
        }


if __name__ == "__main__":
    demo = pd.read_csv("data/processed/applications_demo.csv")
    model = RiskModel()
    app = demo.iloc[0].to_dict()
    print(f"Application {app['application_id']} (actual outcome: "
          f"{'defaulted' if app['target'] == 1 else 'repaid'})")
    print(json.dumps(model.assess(app), indent=2))