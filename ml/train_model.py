"""
CreditMind - Step 3: Train the credit risk model.

  1. Train XGBoost on 2012-2014 loans (early stopping on Oct-Dec 2014)
  2. Calibrate probabilities on the 2015 H1 validation set (fixes the 17% -> 20% drift)
  3. Choose a cost-optimal threshold (approving a defaulter costs 5x rejecting a good borrower)
  4. Define the grey zone (applications routed to human review)
  5. Evaluate on the 2015 H2 test set: AUC, Gini, KS, Brier, cost, calibration
  6. Global SHAP feature importance + a simple fairness audit
  7. Save everything the agents need to models/

Usage (from the project root):
    pip install xgboost scikit-learn matplotlib joblib
    python ml/train_model.py
"""

import argparse
import json
import os
from datetime import datetime, timezone

import joblib
import matplotlib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, roc_auc_score, roc_curve

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

DATA_DIR = "data/processed"
MODEL_DIR = "models"
REPORT_DIR = "reports"
FIG_DIR = "reports/figures"
SEED = 42

MODEL_VERSION = "credit-xgb-v1"
COST_FALSE_APPROVAL = 5.0   # approving a borrower who then defaults
COST_FALSE_REJECTION = 1.0  # rejecting a borrower who would have repaid
REVIEW_SHARE = 0.12         # share of applications around the threshold sent to humans
EARLY_STOP_START = pd.Timestamp("2014-10-01")

XGB_PARAMS = {
    "objective": "binary:logistic",
    "eval_metric": "auc",
    "tree_method": "hist",
    "learning_rate": 0.05,
    "max_depth": 5,
    "min_child_weight": 50,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "max_cat_to_onehot": 1,
    "seed": SEED,
}


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def load_data():
    with open(f"{DATA_DIR}/metadata.json") as f:
        meta = json.load(f)
    parts = {}
    for name in ["train", "val", "test"]:
        df = pd.read_parquet(f"{DATA_DIR}/{name}.parquet")
        for col in meta["categorical_features"]:
            df[col] = pd.Categorical(df[col].astype("string"),
                                     categories=meta["categories"][col])
        parts[name] = df
    return meta, parts


def to_dmatrix(df, features, with_label=True):
    return xgb.DMatrix(df[features], label=df["target"] if with_label else None,
                       enable_categorical=True)


def ks_statistic(y, p):
    fpr, tpr, _ = roc_curve(y, p)
    return float(np.max(tpr - fpr))


def expected_cost(y, p, threshold):
    approved = p < threshold
    false_approvals = np.sum(approved & (y == 1))
    false_rejections = np.sum(~approved & (y == 0))
    return (COST_FALSE_APPROVAL * false_approvals
            + COST_FALSE_REJECTION * false_rejections) / len(y)


def zone_of(p, t_low, t_high):
    return np.where(p < t_low, "APPROVE", np.where(p > t_high, "REJECT", "REVIEW"))


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3000)
    parser.add_argument("--shap-sample", type=int, default=20000)
    args = parser.parse_args()

    for d in [MODEL_DIR, REPORT_DIR, FIG_DIR]:
        os.makedirs(d, exist_ok=True)
    log = []

    def out(text=""):
        print(text)
        log.append(str(text))

    meta, parts = load_data()
    features = meta["model_features"]
    train, val, test = parts["train"], parts["val"], parts["test"]
    y_val, y_test = val["target"].to_numpy(), test["target"].to_numpy()

    # ------------------------------------------------------------------ 1. Train
    fit = train[train["issue_d"] < EARLY_STOP_START]
    es = train[train["issue_d"] >= EARLY_STOP_START]
    out(f"Features: {len(features)} ({len(meta['categorical_features'])} categorical)")
    out(f"Fit set: {len(fit):,} | early-stopping set (Oct-Dec 2014): {len(es):,}")

    d_fit, d_es = to_dmatrix(fit, features), to_dmatrix(es, features)
    booster = xgb.train(XGB_PARAMS, d_fit, num_boost_round=args.rounds,
                        evals=[(d_fit, "fit"), (d_es, "early_stop")],
                        early_stopping_rounds=100, verbose_eval=200)
    best_iter = booster.best_iteration
    booster = booster[: best_iter + 1]  # keep only the trees up to the best round
    out(f"Best iteration: {best_iter + 1} trees")

    raw_val = booster.predict(to_dmatrix(val, features))
    raw_test = booster.predict(to_dmatrix(test, features))

    # ------------------------------------------------------------------ 2. Calibrate
    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    calibrator.fit(raw_val, y_val)
    p_val = calibrator.predict(raw_val)
    p_test = calibrator.predict(raw_test)

    out("\n=== CALIBRATION (fixing the base-rate drift) ===")
    out(f"Actual default rate      val {y_val.mean():.1%}   test {y_test.mean():.1%}")
    out(f"Raw model mean PD        val {raw_val.mean():.1%}   test {raw_test.mean():.1%}")
    out(f"Calibrated mean PD       val {p_val.mean():.1%}   test {p_test.mean():.1%}")
    out(f"Brier score (test)       raw {brier_score_loss(y_test, raw_test):.4f}   "
        f"calibrated {brier_score_loss(y_test, p_test):.4f}  (lower is better)")

    # ------------------------------------------------------------------ 3. Threshold
    grid = np.round(np.arange(0.03, 0.60, 0.005), 3)
    costs = [expected_cost(y_val, p_val, t) for t in grid]
    t_opt = float(grid[int(np.argmin(costs))])
    t_theory = COST_FALSE_REJECTION / (COST_FALSE_REJECTION + COST_FALSE_APPROVAL)
    out("\n=== DECISION THRESHOLD (cost matrix: false approval = "
        f"{COST_FALSE_APPROVAL:g}, false rejection = {COST_FALSE_REJECTION:g}) ===")
    out(f"Cost-optimal threshold on validation: PD < {t_opt:.3f} -> approve")
    out(f"Theoretical optimum for calibrated PD: {t_theory:.3f}")

    # ------------------------------------------------------------------ 4. Grey zone
    q = float(np.mean(p_val < t_opt))
    t_low = float(np.quantile(p_val, max(q - REVIEW_SHARE / 2, 0.01)))
    t_high = float(np.quantile(p_val, min(q + REVIEW_SHARE / 2, 0.99)))
    t_low, t_high = min(t_low, t_opt - 0.005), max(t_high, t_opt + 0.005)
    out(f"Grey zone (human review): {t_low:.3f} <= PD <= {t_high:.3f}")

    # ------------------------------------------------------------------ 5. Evaluate
    auc = roc_auc_score(y_test, p_test)
    out("\n=== TEST SET PERFORMANCE (Jul-Dec 2015, never seen in training) ===")
    out(f"AUC  {auc:.4f}   Gini {2 * auc - 1:.4f}   KS {ks_statistic(y_test, p_test):.4f}")
    out(f"Validation AUC {roc_auc_score(y_val, p_val):.4f} (for comparison)")

    zones = zone_of(p_test, t_low, t_high)
    out("\nThree-way decision on test:")
    out(f"{'zone':<9}{'share':>8}{'actual default rate':>22}")
    for z in ["APPROVE", "REVIEW", "REJECT"]:
        mask = zones == z
        out(f"{z:<9}{mask.mean():>8.1%}{(y_test[mask].mean() if mask.any() else 0):>22.1%}")

    cost_model = expected_cost(y_test, p_test, t_opt)
    cost_all = expected_cost(y_test, np.zeros_like(p_test), 1.0)
    cost_naive = expected_cost(y_test, p_test, 0.5)
    out("\nExpected cost per application (test, binary decision at the optimal threshold):")
    out(f"  Approve everyone         {cost_all:.4f}")
    out(f"  Model, naive 0.5 cutoff  {cost_naive:.4f}")
    out(f"  Model, cost-optimal      {cost_model:.4f}  "
        f"({(1 - cost_model / cost_all):.0%} lower than approving everyone)")

    out("\nCalibration by decile (test):")
    dec = pd.DataFrame({"p": p_test, "y": y_test})
    dec["decile"] = pd.qcut(dec["p"].rank(method="first"), 10, labels=False) + 1
    table = dec.groupby("decile").agg(predicted=("p", "mean"), actual=("y", "mean"),
                                      loans=("y", "size")).astype({"predicted": float})
    out((table.assign(predicted=lambda t: (t.predicted * 100).round(1),
                      actual=lambda t: (t.actual * 100).round(1))).to_string())

    # ------------------------------------------------------------------ 6. SHAP + fairness
    sample = test.sample(min(args.shap_sample, len(test)), random_state=SEED)
    contribs = booster.predict(to_dmatrix(sample, features, with_label=False),
                               pred_contribs=True)[:, :-1]  # last column = bias
    importance = (pd.Series(np.abs(contribs).mean(axis=0), index=features)
                  .sort_values(ascending=False))
    out("\n=== GLOBAL SHAP IMPORTANCE (mean |SHAP|, log-odds) - top 15 ===")
    for f_name, v in importance.head(15).items():
        out(f"  {f_name:<32}{v:.4f}")

    out("\n=== FAIRNESS AUDIT (approval = PD below optimal threshold, test) ===")
    audit = test[["home_ownership", "annual_inc"]].copy()
    audit["approved"] = p_test < t_opt
    audit["defaulted"] = y_test
    audit["income_band"] = pd.cut(audit["annual_inc"], [0, 40_000, 60_000, 90_000, np.inf],
                                  labels=["<40k", "40-60k", "60-90k", ">90k"])
    for group in ["home_ownership", "income_band"]:
        g = audit.groupby(group, observed=True).agg(
            loans=("approved", "size"), approval_rate=("approved", "mean"),
            actual_default=("defaulted", "mean"))
        g = g[g["loans"] >= 100]
        ratio = g["approval_rate"].min() / g["approval_rate"].max()
        out(f"\nBy {group}:")
        out((g.assign(approval_rate=lambda t: (t.approval_rate * 100).round(1),
                      actual_default=lambda t: (t.actual_default * 100).round(1)))
            .to_string())
        out(f"Approval-rate ratio (lowest/highest): {ratio:.2f} "
            "(below 0.80 flags a group for review)")

    # ------------------------------------------------------------------ Figures
    fpr, tpr, _ = roc_curve(y_test, p_test)
    plt.figure(figsize=(5, 5))
    plt.plot(fpr, tpr, label=f"XGBoost (AUC {auc:.3f})")
    plt.plot([0, 1], [0, 1], "--", color="grey")
    plt.xlabel("False positive rate"), plt.ylabel("True positive rate")
    plt.title("ROC curve - test set"), plt.legend(), plt.tight_layout()
    plt.savefig(f"{FIG_DIR}/roc_curve.png", dpi=150), plt.close()

    plt.figure(figsize=(5, 5))
    for label, p in [("Raw model", raw_test), ("Calibrated", p_test)]:
        frame = pd.DataFrame({"p": p, "y": y_test})
        frame["bin"] = pd.qcut(frame["p"].rank(method="first"), 10, labels=False)
        agg = frame.groupby("bin").mean()
        plt.plot(agg["p"], agg["y"], "o-", label=label)
    lim = max(p_test.max(), raw_test.max()) * 1.05
    plt.plot([0, lim], [0, lim], "--", color="grey")
    plt.xlabel("Predicted default probability"), plt.ylabel("Actual default rate")
    plt.title("Calibration - test set"), plt.legend(), plt.tight_layout()
    plt.savefig(f"{FIG_DIR}/calibration.png", dpi=150), plt.close()

    top = importance.head(20)[::-1]
    plt.figure(figsize=(7, 7))
    plt.barh(top.index, top.values)
    plt.xlabel("Mean |SHAP value|"), plt.title("Top 20 risk drivers"), plt.tight_layout()
    plt.savefig(f"{FIG_DIR}/shap_importance.png", dpi=150), plt.close()

    # ------------------------------------------------------------------ Save
    booster.save_model(f"{MODEL_DIR}/xgb_model.json")
    joblib.dump(calibrator, f"{MODEL_DIR}/calibrator.joblib")
    config = {
        "model_version": MODEL_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "features": features,
        "numeric_features": meta["numeric_features"],
        "categorical_features": meta["categorical_features"],
        "categories": meta["categories"],
        "thresholds": {"approve_below": round(t_low, 4), "reject_above": round(t_high, 4),
                       "cost_optimal": t_opt},
        "cost_matrix": {"false_approval": COST_FALSE_APPROVAL,
                        "false_rejection": COST_FALSE_REJECTION},
        "metrics_test": {"auc": round(auc, 4), "gini": round(2 * auc - 1, 4),
                         "ks": round(ks_statistic(y_test, p_test), 4),
                         "brier": round(brier_score_loss(y_test, p_test), 4),
                         "default_rate": round(float(y_test.mean()), 4)},
        "zone_shares_test": {z: round(float(np.mean(zones == z)), 4)
                             for z in ["APPROVE", "REVIEW", "REJECT"]},
        "top_features": importance.head(15).round(4).to_dict(),
        "n_trees": best_iter + 1,
        "training_data": "Lending Club 2012-2014 (fit), Oct-Dec 2014 (early stop), "
                         "Jan-Jun 2015 (calibration/threshold), Jul-Dec 2015 (test)",
    }
    with open(f"{MODEL_DIR}/model_config.json", "w") as f:
        json.dump(config, f, indent=2)
    with open(f"{REPORT_DIR}/model_report.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print(f"\nSaved model to {MODEL_DIR}/, report to {REPORT_DIR}/model_report.txt, "
          f"figures to {FIG_DIR}/")


if __name__ == "__main__":
    main()