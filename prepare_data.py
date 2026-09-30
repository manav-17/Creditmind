"""
CreditMind - Step 2: Data preparation.

Turns the raw Lending Club CSV into clean, leakage-free datasets for the ML model
and a small set of realistic loan applications for the agent pipeline.

Decisions (from the EDA report):
  * Only finished loans: Fully Paid (0) vs Charged Off (1)
  * Only loans issued 2012-2015 (later years are right-censored)
  * Only Individual applications
  * Only features known at application time (no leakage, no LC grade/int_rate/installment)
  * Proxies for protected attributes removed (zip_code, addr_state)
  * Out-of-time split: train 2012-2014, validation Jan-Jun 2015, test Jul-Dec 2015
  * Synthetic PII (Indian locale) added ONLY to the demo applications, never to model data

Usage (from the project root):
    pip install pandas numpy pyarrow faker
    python prepare_data.py                    # uses all 2012-2015 loans
    python prepare_data.py --sample 200000    # smaller, faster dataset

Outputs:
    data/processed/train.parquet, val.parquet, test.parquet
    data/processed/applications_demo.csv       (for the agent pipeline, with synthetic PII)
    data/processed/metadata.json               (feature lists, split info)
    reports/data_prep_report.txt
"""

import argparse
import json
import os
import random
import re
import string

import numpy as np
import pandas as pd

RAW_PATH = "data/raw/loan.csv"
OUT_DIR = "data/processed"
REPORT_PATH = "reports/data_prep_report.txt"
SEED = 42

YEAR_MIN, YEAR_MAX = 2012, 2015
VAL_START = pd.Timestamp("2015-01-01")
TEST_START = pd.Timestamp("2015-07-01")

# ----------------------------------------------------------------------------
# Feature definitions
# ----------------------------------------------------------------------------
# Numeric features known at application time
NUMERIC_RAW = [
    "loan_amnt", "annual_inc", "dti", "delinq_2yrs", "inq_last_6mths", "open_acc",
    "total_acc", "pub_rec", "pub_rec_bankruptcies", "revol_bal", "revol_util",
    "mths_since_last_delinq", "mths_since_last_record", "mths_since_last_major_derog",
    "collections_12_mths_ex_med", "acc_now_delinq",
    # credit bureau features (~3% missing)
    "mort_acc", "bc_util", "bc_open_to_buy", "avg_cur_bal", "tot_cur_bal",
    "tot_hi_cred_lim", "total_bc_limit", "total_rev_hi_lim", "acc_open_past_24mths",
    "num_tl_90g_dpd_24m", "num_accts_ever_120_pd", "pct_tl_nvr_dlq",
    "percent_bc_gt_75", "mo_sin_old_rev_tl_op", "mo_sin_rcnt_tl",
    "mths_since_recent_inq", "tax_liens", "chargeoff_within_12_mths",
    "delinq_amnt", "num_actv_rev_tl",
]
# Columns that need conversion before becoming numeric features
CONVERTED = ["term", "emp_length", "earliest_cr_line"]
CATEGORICAL = ["purpose", "home_ownership", "verification_status"]
TEXT_FIELDS = ["desc", "emp_title", "title"]
META = ["loan_status", "issue_d", "application_type"]

# Engineered features (created below)
ENGINEERED = ["term_months", "emp_length_years", "credit_history_years",
              "loan_to_income", "revol_bal_to_income"]

MODEL_NUMERIC = NUMERIC_RAW + ENGINEERED
MODEL_FEATURES = MODEL_NUMERIC + CATEGORICAL

USECOLS = NUMERIC_RAW + CONVERTED + CATEGORICAL + TEXT_FIELDS + META


# ----------------------------------------------------------------------------
# Loading (chunked, so a 1.2 GB file doesn't need 6 GB of RAM)
# ----------------------------------------------------------------------------
def load_filtered(path, chunksize=200_000):
    keep_status = {"Fully Paid", "Charged Off"}
    parts, total = [], 0
    for chunk in pd.read_csv(path, usecols=lambda c: c in USECOLS,
                             chunksize=chunksize, low_memory=False):
        total += len(chunk)
        chunk = chunk[chunk["loan_status"].isin(keep_status)]
        chunk = chunk[chunk["application_type"] == "Individual"]
        issue = pd.to_datetime(chunk["issue_d"], format="%b-%Y", errors="coerce")
        chunk = chunk[issue.dt.year.between(YEAR_MIN, YEAR_MAX)]
        parts.append(chunk)
        print(f"  read {total:,} rows...", end="\r")
    df = pd.concat(parts, ignore_index=True)
    print(f"  read {total:,} rows; kept {len(df):,} after filtering")
    return df, total


# ----------------------------------------------------------------------------
# Cleaning and feature engineering
# ----------------------------------------------------------------------------
def clean_desc(text):
    if not isinstance(text, str):
        return np.nan
    text = re.sub(r"Borrower added on \d{2}/\d{2}/\d{2} >", " ", text)
    text = re.sub(r"<br\s*/?>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if text else np.nan


def parse_emp_length(value):
    if not isinstance(value, str):
        return np.nan
    if "<" in value:
        return 0.0
    digits = re.findall(r"\d+", value)
    return float(digits[0]) if digits else np.nan


def clean(df):
    df = df.copy()
    notes = []

    df["issue_d"] = pd.to_datetime(df["issue_d"], format="%b-%Y")
    df["target"] = (df["loan_status"] == "Charged Off").astype(int)

    # Text-formatted columns -> numbers
    df["term_months"] = df["term"].astype(str).str.extract(r"(\d+)")[0].astype(float)
    df["emp_length_years"] = df["emp_length"].apply(parse_emp_length)
    ecl = pd.to_datetime(df["earliest_cr_line"], format="%b-%Y", errors="coerce")
    df["credit_history_years"] = ((df["issue_d"] - ecl).dt.days / 365.25).round(1)
    df.loc[df["credit_history_years"] < 0, "credit_history_years"] = np.nan
    df["revol_util"] = pd.to_numeric(df["revol_util"], errors="coerce")

    # Invalid values (fixed rules, not learned from data -> no leakage)
    n = len(df)
    df = df[df["annual_inc"] > 0]
    notes.append(f"Dropped {n - len(df):,} rows with annual_inc <= 0")
    df.loc[(df["dti"] < 0) | (df["dti"] > 100), "dti"] = np.nan
    df["revol_util"] = df["revol_util"].clip(upper=150)
    df["bc_util"] = df["bc_util"].clip(upper=150)
    df["home_ownership"] = df["home_ownership"].replace(
        {"ANY": "OTHER", "NONE": "OTHER"})

    # Engineered affordability features
    df["loan_to_income"] = (df["loan_amnt"] / df["annual_inc"]).round(4)
    df["revol_bal_to_income"] = (df["revol_bal"] / df["annual_inc"]).round(4)

    # Categorical dtypes (XGBoost handles these natively)
    for col in CATEGORICAL:
        df[col] = df[col].astype(str).str.strip().astype("category")

    # Text fields for the agents
    df["desc"] = df["desc"].apply(clean_desc)
    for col in ["emp_title", "title"]:
        df[col] = df[col].astype("string").str.strip()

    df = df.sort_values("issue_d").reset_index(drop=True)
    df.insert(0, "application_id",
              [f"APP-{d:%Y%m}-{i:06d}" for i, d in enumerate(df["issue_d"])])
    return df, notes


# ----------------------------------------------------------------------------
# Synthetic PII (Indian locale) for demo applications only
# ----------------------------------------------------------------------------
def make_pan(rng):
    letters = string.ascii_uppercase
    return ("".join(rng.choices(letters, k=3)) + "P" + rng.choice(letters)
            + "".join(rng.choices(string.digits, k=4)) + rng.choice(letters))


def add_synthetic_pii(df):
    from faker import Faker
    fake = Faker("en_IN")
    Faker.seed(SEED)
    rng = random.Random(SEED)
    df = df.copy()
    names = [fake.name() for _ in range(len(df))]
    df.insert(1, "applicant_name", names)
    df.insert(2, "phone", [f"+91 {rng.choice('6789')}{rng.randint(0, 999999999):09d}"
                           for _ in range(len(df))])
    df.insert(3, "email", [n.lower().replace(" ", ".").replace("..", ".")
                           + f"{rng.randint(1, 99)}@example.com" for n in names])
    df.insert(4, "pan", [make_pan(rng) for _ in range(len(df))])
    return df


def build_demo_set(test_df, n_demo):
    """Pick demo applications from the TEST set: mix of outcomes, prefer ones with desc."""
    rng_state = SEED
    with_desc = test_df[test_df["desc"].notna()]
    n_desc = min(len(with_desc), n_demo // 2)
    part1 = with_desc.sample(n_desc, random_state=rng_state) if n_desc else with_desc.head(0)
    rest = test_df.drop(part1.index)
    n_good = (n_demo - n_desc) // 2
    n_bad = n_demo - n_desc - n_good
    part2 = [rest[rest["target"] == t].sample(
                 min(n, int((rest["target"] == t).sum())), random_state=rng_state)
             for t, n in ((0, n_good), (1, n_bad))]
    demo = pd.concat([part1, *part2]).sample(frac=1, random_state=rng_state)
    cols = (["application_id", "issue_d"] + MODEL_FEATURES
            + ["term", "emp_length"] + TEXT_FIELDS + ["target"])
    demo = demo[[c for c in cols if c in demo.columns]]
    return add_synthetic_pii(demo.reset_index(drop=True))


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=RAW_PATH)
    parser.add_argument("--sample", type=int, default=None,
                        help="Randomly keep N loans after filtering (e.g. 200000)")
    parser.add_argument("--n-demo", type=int, default=200,
                        help="Number of demo applications for the agent pipeline")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    log = []

    def out(text=""):
        print(text)
        log.append(str(text))

    out(f"Loading {args.file} ...")
    raw, total_rows = load_filtered(args.file)
    out(f"Raw rows: {total_rows:,}")
    out(f"After filters (finished, Individual, {YEAR_MIN}-{YEAR_MAX}): {len(raw):,}")

    if args.sample and args.sample < len(raw):
        raw = raw.sample(args.sample, random_state=SEED)
        out(f"Random sample: {len(raw):,}")

    df, notes = clean(raw)
    for note in notes:
        out(note)
    out(f"Final dataset: {len(df):,} loans, default rate {df['target'].mean():.1%}")

    # Out-of-time split
    train = df[df["issue_d"] < VAL_START]
    val = df[(df["issue_d"] >= VAL_START) & (df["issue_d"] < TEST_START)]
    test = df[df["issue_d"] >= TEST_START]
    out("\nOut-of-time split:")
    for name, part in [("train", train), ("val", val), ("test", test)]:
        out(f"  {name:<5} {len(part):>9,} loans ({len(part) / len(df):.0%}), "
            f"default {part['target'].mean():.1%}, "
            f"{part['issue_d'].min():%b %Y} - {part['issue_d'].max():%b %Y}")

    keep = ["application_id", "issue_d"] + MODEL_FEATURES + TEXT_FIELDS + ["target"]
    for name, part in [("train", train), ("val", val), ("test", test)]:
        part[keep].to_parquet(f"{OUT_DIR}/{name}.parquet", index=False)

    demo = build_demo_set(test, args.n_demo)
    demo.to_csv(f"{OUT_DIR}/applications_demo.csv", index=False)
    out(f"\nDemo applications: {len(demo)} (from test set, with synthetic PII), "
        f"{demo['desc'].notna().sum()} with borrower description, "
        f"default rate {demo['target'].mean():.1%}")

    # Missing values in model features (train)
    out("\nMissing % in model features (train):")
    miss = train[MODEL_FEATURES].isna().mean().sort_values(ascending=False)
    for col, pct in miss[miss > 0].items():
        out(f"  {col:<32}{pct:>7.1%}")

    metadata = {
        "numeric_features": MODEL_NUMERIC,
        "categorical_features": CATEGORICAL,
        "model_features": MODEL_FEATURES,
        "text_fields": TEXT_FIELDS,
        "target": "target",
        "target_meaning": {"0": "Fully Paid", "1": "Charged Off (default)"},
        "categories": {c: df[c].cat.categories.tolist() for c in CATEGORICAL},
        "split": {"train": f"{YEAR_MIN}-01 to 2014-12",
                  "val": "2015-01 to 2015-06", "test": "2015-07 to 2015-12"},
        "rows": {"train": len(train), "val": len(val), "test": len(test)},
        "excluded": {
            "leakage": "payment, recovery, hardship, settlement, funded amounts, last credit pull",
            "lc_risk_outputs": ["grade", "sub_grade", "int_rate", "installment"],
            "fairness_proxies": ["zip_code", "addr_state"],
        },
    }
    with open(f"{OUT_DIR}/metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print(f"\nSaved files to {OUT_DIR}/ and report to {REPORT_PATH}")


if __name__ == "__main__":
    main()