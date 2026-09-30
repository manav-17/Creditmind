"""
CreditMind - Step 1: Explore the Lending Club dataset.

Reads the large CSV on your machine and writes a SMALL text report
(reports/eda_report.txt) that you can upload to the chat instead of the dataset.

Usage (from the project root):
    python explore_data.py
    python explore_data.py --file data/raw/loan.csv
    python explore_data.py --sample 200000      # if your laptop runs out of memory
"""

import argparse
import glob
import os
import sys

import pandas as pd

# ----------------------------------------------------------------------------
# Columns we planned to use / remove (from the data dictionary discussion)
# ----------------------------------------------------------------------------
MODEL_FEATURES = [
    "loan_amnt", "term", "purpose", "annual_inc", "emp_length", "home_ownership",
    "verification_status", "dti", "fico_range_low", "fico_range_high",
    "earliest_cr_line", "delinq_2yrs", "inq_last_6mths", "open_acc", "total_acc",
    "pub_rec", "pub_rec_bankruptcies", "revol_bal", "revol_util",
    "mths_since_last_delinq", "mths_since_last_record",
    "collections_12_mths_ex_med", "acc_now_delinq", "application_type",
]
TEXT_FIELDS = ["desc", "emp_title", "title"]
SPECIAL = ["loan_status", "issue_d"]
LEAKAGE = [
    "total_pymnt", "total_pymnt_inv", "total_rec_prncp", "total_rec_int",
    "total_rec_late_fee", "last_pymnt_d", "last_pymnt_amnt", "next_pymnt_d",
    "out_prncp", "out_prncp_inv", "recoveries", "collection_recovery_fee",
    "last_credit_pull_d", "last_fico_range_high", "last_fico_range_low",
    "funded_amnt", "funded_amnt_inv", "pymnt_plan", "debt_settlement_flag",
]
LC_RISK_OUTPUTS = ["grade", "sub_grade", "int_rate", "installment"]
PROXIES_AND_IDS = ["zip_code", "addr_state", "id", "member_id", "url", "policy_code"]


def find_csv(path_arg):
    if path_arg:
        return path_arg
    candidates = glob.glob("data/raw/*.csv")
    if not candidates:
        sys.exit("No CSV found in data/raw/. Pass the path with --file.")
    # pick the largest CSV (the main loan file)
    return max(candidates, key=os.path.getsize)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="Path to the Lending Club CSV")
    parser.add_argument("--sample", type=int, default=None,
                        help="Read only the first N rows (use if memory is low)")
    args = parser.parse_args()

    csv_path = find_csv(args.file)
    os.makedirs("reports", exist_ok=True)
    report_path = "reports/eda_report.txt"
    lines = []

    def out(text=""):
        print(text)
        lines.append(str(text))

    out(f"FILE: {csv_path}  ({os.path.getsize(csv_path) / 1e6:.0f} MB)")
    df = pd.read_csv(csv_path, low_memory=False, nrows=args.sample)
    out(f"SHAPE: {df.shape[0]:,} rows x {df.shape[1]} columns")
    out(f"MEMORY: {df.memory_usage(deep=True).sum() / 1e6:.0f} MB in pandas")
    if args.sample:
        out(f"NOTE: only first {args.sample:,} rows were read")

    # ------------------------------------------------------------------
    # 1. Every column: type, missing %, unique count, example value
    # ------------------------------------------------------------------
    out("\n=== 1. ALL COLUMNS ===")
    out(f"{'column':<34}{'dtype':<10}{'missing%':>9}{'unique':>10}  example")
    for col in df.columns:
        s = df[col]
        non_null = s.dropna()
        example = str(non_null.iloc[0])[:40].replace("\n", " ") if len(non_null) else "-"
        out(f"{col:<34}{str(s.dtype):<10}{s.isna().mean() * 100:>8.1f}%"
            f"{s.nunique(dropna=True):>10}  {example}")

    # ------------------------------------------------------------------
    # 2. Target
    # ------------------------------------------------------------------
    out("\n=== 2. LOAN STATUS (target) ===")
    if "loan_status" in df.columns:
        out(df["loan_status"].value_counts(dropna=False).to_string())
    else:
        out("loan_status column NOT FOUND")

    # ------------------------------------------------------------------
    # 3. Which planned columns exist?
    # ------------------------------------------------------------------
    out("\n=== 3. PLANNED COLUMNS: FOUND / MISSING ===")
    groups = {
        "Model features": MODEL_FEATURES,
        "Text fields (agents)": TEXT_FIELDS,
        "Target / date": SPECIAL,
        "Leakage (to drop)": LEAKAGE,
        "LC risk outputs (to drop)": LC_RISK_OUTPUTS,
        "Proxies / IDs (to drop)": PROXIES_AND_IDS,
    }
    for name, cols in groups.items():
        found = [c for c in cols if c in df.columns]
        missing = [c for c in cols if c not in df.columns]
        out(f"{name}: {len(found)}/{len(cols)} found")
        if missing:
            out(f"   missing: {missing}")

    planned = set(sum(groups.values(), []))
    unplanned = [c for c in df.columns if c not in planned]
    out(f"\nColumns in file not in any plan ({len(unplanned)}): {unplanned}")

    # ------------------------------------------------------------------
    # 4. Finished loans only: size and default rate
    # ------------------------------------------------------------------
    out("\n=== 4. FINISHED LOANS (Fully Paid vs Charged Off) ===")
    if "loan_status" in df.columns:
        status = df["loan_status"].astype(str)
        paid = status.str.contains("Fully Paid", case=False)
        charged = status.str.contains("Charged Off", case=False)
        finished = df[paid | charged].copy()
        finished["target"] = charged[paid | charged].astype(int)
        out(f"Finished loans: {len(finished):,}")
        out(f"Default (Charged Off) rate: {finished['target'].mean() * 100:.1f}%")
        out("(statuses containing 'Does not meet the credit policy' are included "
            "above; check section 2)")

        for col in ["term", "home_ownership", "verification_status",
                    "application_type", "purpose"]:
            if col in finished.columns:
                out(f"\nDefault rate by {col}:")
                grp = finished.groupby(col)["target"].agg(["count", "mean"])
                grp["mean"] = (grp["mean"] * 100).round(1)
                grp.columns = ["loans", "default_%"]
                out(grp.sort_values("loans", ascending=False).head(15).to_string())

        if "issue_d" in finished.columns:
            dates = pd.to_datetime(finished["issue_d"], format="%b-%Y", errors="coerce")
            out(f"\nissue_d parsed: {dates.notna().mean() * 100:.1f}% "
                f"(raw example: {finished['issue_d'].dropna().iloc[0]})")
            if dates.notna().any():
                out(f"Date range: {dates.min():%b %Y} to {dates.max():%b %Y}")
                by_year = finished.groupby(dates.dt.year)["target"].agg(["count", "mean"])
                by_year["mean"] = (by_year["mean"] * 100).round(1)
                by_year.columns = ["loans", "default_%"]
                out("Loans and default rate by year:")
                out(by_year.to_string())

    # ------------------------------------------------------------------
    # 5. Numeric summary of key features
    # ------------------------------------------------------------------
    out("\n=== 5. NUMERIC SUMMARY (key features) ===")
    num_cols = [c for c in MODEL_FEATURES
                if c in df.columns and pd.api.types.is_numeric_dtype(df[c])]
    if num_cols:
        desc = df[num_cols].describe(percentiles=[0.01, 0.5, 0.99]).T
        out(desc[["count", "mean", "min", "1%", "50%", "99%", "max"]]
            .round(2).to_string())

    # Non-numeric columns that should be numbers (e.g. "36 months", "13.5%")
    out("\nText-formatted columns to convert:")
    for col in ["term", "emp_length", "revol_util", "int_rate", "earliest_cr_line"]:
        if col in df.columns:
            out(f"  {col}: {df[col].dropna().astype(str).unique()[:6].tolist()}")

    # ------------------------------------------------------------------
    # 6. Text fields
    # ------------------------------------------------------------------
    out("\n=== 6. TEXT FIELDS ===")
    for col in TEXT_FIELDS:
        if col in df.columns:
            s = df[col].dropna().astype(str).str.strip()
            s = s[s != ""]
            out(f"{col}: {len(s):,} non-empty ({len(s) / len(df) * 100:.1f}%), "
                f"avg length {s.str.len().mean():.0f} chars")
            for v in s.sample(min(2, len(s)), random_state=1):
                out(f"   e.g. {v[:120]!r}")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nReport saved to {report_path} - upload this file to the chat.")


if __name__ == "__main__":
    main()