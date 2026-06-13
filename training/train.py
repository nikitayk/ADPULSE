#!/usr/bin/env python3
"""
ADPULSE — model training pipeline (reproducible CTR + CVR models).

This script regenerates the four artifacts shipped in
``bidder.submission.code/python/`` from the raw IPinYou logs:

    model_ctr.pkl   scaler_ctr.pkl     # P(click  | bid request)
    model_cvr.pkl   scaler_cvr.pkl     # P(convert | bid request)

It is the missing "recipe" behind the pre-trained ``.pkl`` files: the feature
engineering here is kept BYTE-FOR-BYTE compatible with the inference path in
``Bid._preprocess_bid_request_ctr`` / ``_preprocess_bid_request_cvr`` so the
produced models are drop-in replacements.

Data
----
Point ``--dataset-dir`` at a folder of IPinYou logs (see ``dataset/README.md``):
    imp.<day>.txt   24-col impression logs   (the training universe)
    clk.<day>.txt   click logs               (→ positive CTR labels, by BidID)
    conv.<day>.txt  conversion logs          (→ positive CVR labels, by BidID)

Labels are built by joining BidIDs: an impression is `clicked` if its BidID
appears in any clk file, and `converted` if it appears in any conv file.

Usage
-----
    python training/train.py --dataset-dir ../dataset --days 06,07,08
    python training/train.py --dataset-dir ../dataset --days 06 --sample-frac 0.3

Reproducibility: a fixed RANDOM_SEED makes the split and the LightGBM training
deterministic. Library versions are pinned in ``training/requirements.txt``
(they must match ``bidder.submission.code/python/requirements.txt`` so the
pickles unpickle in production).
"""
import argparse
import json
import os
import sys
import warnings

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
RANDOM_SEED = 42

# 24-column imp/clk/conv schema (see the dataset README). UserProfileIDs is the
# 24th column (index 23), not listed in the README's 23-row table.
COLS_24 = [
    "BidID", "Timestamp", "Logtype", "VisitorID", "UserAgent", "IP", "Region",
    "City", "Adexchange", "Domain", "URL", "AnonymousURLID", "AdslotID",
    "Adslotwidth", "Adslotheight", "Adslotvisibility", "Adslotformat",
    "Adslotfloorprice", "CreativeID", "Biddingprice", "Payingprice",
    "KeypageURL", "AdvertiserID", "UserProfileIDs",
]

# Feature order MUST match Bid._preprocess_bid_request_ctr / _cvr exactly.
CTR_FEATURES = [
    "ua_browser", "ua_device", "ua_os", "weekday", "AdvertiserID",
    "Payingprice", "Adslotfloorprice", "Adslotformat", "Adslotheight",
    "Adslotvisibility", "Adslotwidth", "Timestamp",
]
CVR_FEATURES = [
    "clicked", "ua_os", "ua_device", "weekday", "Timestamp", "AdvertiserID",
    "Payingprice", "Adexchange", "Biddingprice", "Adslotformat",
    "Adslotheight", "Region",
]

# LightGBM hyperparameters (documented in training/README.md).
LGBM_PARAMS = dict(
    n_estimators=300,
    learning_rate=0.05,
    num_leaves=63,
    max_depth=-1,
    subsample=0.8,
    colsample_bytree=0.8,
    reg_lambda=1.0,
    random_state=RANDOM_SEED,
    n_jobs=-1,
)


# --------------------------------------------------------------------------- #
# User-agent parsing — identical logic to Bid.py (kept in sync intentionally)
# --------------------------------------------------------------------------- #
def _device(ua):
    if not isinstance(ua, str):
        return "Unknown"
    s = ua.lower()
    if "ipad" in s:
        return "Tablet"
    if any(k in s for k in ("mobile", "android", "iphone")):
        return "Mobile"
    if "tablet" in s:
        return "Tablet"
    if any(k in s for k in ("windows", "macintosh", "linux")):
        return "Desktop"
    return "Other"


def _os(ua):
    if not isinstance(ua, str):
        return "Unknown"
    s = ua.lower()
    if "windows" in s:
        return "Windows"
    if "macintosh" in s or "macos" in s:
        return "MacOS"
    if "android" in s:
        return "Android"
    if any(k in s for k in ("ios", "iphone", "ipad")):
        return "iOS"
    if "linux" in s:
        return "Linux"
    return "Other"


def _browser(ua):
    if not isinstance(ua, str):
        return "Unknown"
    s = ua.lower()
    if "chrome" in s:
        return "Chrome"
    if "firefox" in s:
        return "Firefox"
    if "safari" in s and "chrome" not in s:
        return "Safari"
    if "edge" in s:
        return "Edge"
    if "msie" in s or "trident" in s:
        return "IE"
    return "Other"


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def _path(dataset_dir, prefix, day):
    return os.path.join(dataset_dir, f"{prefix}.{day}.txt")


def load_bidid_set(dataset_dir, prefix, days):
    """Union of BidIDs (col 0) across the given log files."""
    ids = set()
    for day in days:
        p = _path(dataset_dir, prefix, day)
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                bid = line.split("\t", 1)[0].strip()
                if bid:
                    ids.add(bid)
    return ids


def load_impressions(dataset_dir, days, sample_frac):
    """Load impression logs for the given days into one DataFrame."""
    frames = []
    for day in days:
        p = _path(dataset_dir, "imp", day)
        if not os.path.exists(p):
            print(f"  [skip] {p} not found")
            continue
        print(f"  reading {p} ...")
        df = pd.read_csv(
            p, sep="\t", header=None, names=COLS_24,
            na_values=["null", "Null", "NULL", "na", "NA"],
            dtype=str, quoting=3, engine="c",
        )
        if sample_frac < 1.0:
            df = df.sample(frac=sample_frac, random_state=RANDOM_SEED)
        frames.append(df)
    if not frames:
        sys.exit("No impression logs found — check --dataset-dir / --days.")
    return pd.concat(frames, ignore_index=True)


# --------------------------------------------------------------------------- #
# Feature engineering — mirrors the inference preprocessing in Bid.py
# --------------------------------------------------------------------------- #
def engineer_features(df, clicked_ids, converted_ids):
    out = pd.DataFrame(index=df.index)

    out["ua_browser"] = df["UserAgent"].map(_browser).astype("category").cat.codes
    out["ua_device"] = df["UserAgent"].map(_device).astype("category").cat.codes
    out["ua_os"] = df["UserAgent"].map(_os).astype("category").cat.codes

    ts = pd.to_datetime(df["Timestamp"], format="%Y%m%d%H%M%S%f", errors="coerce")
    out["weekday"] = ts.dt.weekday.fillna(0).astype(int)
    out["Timestamp"] = pd.to_numeric(df["Timestamp"], errors="coerce").fillna(0.0)

    out["AdvertiserID"] = pd.to_numeric(df["AdvertiserID"], errors="coerce").fillna(0).astype(int)
    out["Payingprice"] = pd.to_numeric(df["Payingprice"], errors="coerce").fillna(0).astype(int)
    out["Biddingprice"] = pd.to_numeric(df["Biddingprice"], errors="coerce").fillna(0).astype(int)
    out["Adslotfloorprice"] = pd.to_numeric(df["Adslotfloorprice"], errors="coerce").fillna(0).astype(int)
    out["Adslotheight"] = pd.to_numeric(df["Adslotheight"], errors="coerce").fillna(0).astype(int)
    out["Adslotwidth"] = pd.to_numeric(df["Adslotwidth"], errors="coerce").fillna(0).astype(int)
    out["Adexchange"] = pd.to_numeric(df["Adexchange"], errors="coerce").fillna(0).astype(int)
    out["Region"] = pd.to_numeric(df["Region"], errors="coerce").fillna(0).astype(int)

    out["Adslotvisibility"] = df["Adslotvisibility"].astype("category").cat.codes
    out["Adslotformat"] = df["Adslotformat"].astype("category").cat.codes

    # Labels via BidID join (clicks/conversions are sparse → small sets).
    bidids = df["BidID"].astype(str)
    out["clicked"] = bidids.isin(clicked_ids).astype(int)
    out["converted"] = bidids.isin(converted_ids).astype(int)
    return out


# --------------------------------------------------------------------------- #
# Training
# --------------------------------------------------------------------------- #
def train_one(name, X, y, out_dir):
    pos = int(y.sum())
    neg = int(len(y) - pos)
    if pos == 0:
        print(f"  [warn] {name}: no positive labels found — skipping. "
              f"(are the clk/conv files present for these days?)")
        return None
    print(f"  {name}: {len(y):,} rows | positives={pos:,} ({pos / len(y):.4%})")

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.2, random_state=RANDOM_SEED, stratify=y,
    )

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)

    # Handle heavy class imbalance (CTR ~0.06%) without resampling.
    scale_pos_weight = max(neg / pos, 1.0)
    model = LGBMClassifier(scale_pos_weight=scale_pos_weight, **LGBM_PARAMS)
    model.fit(X_tr_s, y_tr)

    proba = model.predict_proba(X_te_s)[:, 1]
    metrics = {
        "rows": int(len(y)),
        "positives": pos,
        "positive_rate": pos / len(y),
        "roc_auc": float(roc_auc_score(y_te, proba)),
        "pr_auc": float(average_precision_score(y_te, proba)),
        "log_loss": float(log_loss(y_te, proba, labels=[0, 1])),
    }
    print(f"    ROC-AUC={metrics['roc_auc']:.4f}  PR-AUC={metrics['pr_auc']:.4f}  "
          f"logloss={metrics['log_loss']:.4f}")

    suffix = "ctr" if name == "CTR" else "cvr"
    joblib.dump(model, os.path.join(out_dir, f"model_{suffix}.pkl"))
    joblib.dump(scaler, os.path.join(out_dir, f"scaler_{suffix}.pkl"))
    print(f"    saved model_{suffix}.pkl + scaler_{suffix}.pkl")
    return metrics


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.abspath(os.path.join(here, "..", "bidder.submission.code", "python"))

    ap = argparse.ArgumentParser(description="Train ADPULSE CTR/CVR models from IPinYou logs.")
    ap.add_argument("--dataset-dir", required=True, help="folder containing imp/clk/conv .txt logs")
    ap.add_argument("--days", default="06", help="comma-separated days, e.g. 06,07,08")
    ap.add_argument("--sample-frac", type=float, default=1.0, help="row subsample fraction (0-1]")
    ap.add_argument("--out", default=default_out, help="where to write the .pkl artifacts")
    ap.add_argument("--metrics-out", default=os.path.join(here, "metrics.json"),
                    help="where to write the evaluation metrics JSON")
    args = ap.parse_args()

    np.random.seed(RANDOM_SEED)
    days = [d.strip() for d in args.days.split(",") if d.strip()]
    os.makedirs(args.out, exist_ok=True)

    print(f"[1/4] Loading impressions (days={days}, sample_frac={args.sample_frac}) ...")
    imp = load_impressions(args.dataset_dir, days, args.sample_frac)
    print(f"      {len(imp):,} impressions loaded")

    print("[2/4] Loading click / conversion label sets ...")
    clicked_ids = load_bidid_set(args.dataset_dir, "clk", days)
    converted_ids = load_bidid_set(args.dataset_dir, "conv", days)
    print(f"      {len(clicked_ids):,} clicked BidIDs | {len(converted_ids):,} converted BidIDs")

    print("[3/4] Engineering features ...")
    feats = engineer_features(imp, clicked_ids, converted_ids)

    print("[4/4] Training models ...")
    metrics = {"random_seed": RANDOM_SEED, "days": days, "sample_frac": args.sample_frac}
    metrics["CTR"] = train_one("CTR", feats[CTR_FEATURES], feats["clicked"], args.out)
    metrics["CVR"] = train_one("CVR", feats[CVR_FEATURES], feats["converted"], args.out)

    with open(args.metrics_out, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)
    print(f"\nDone. Metrics → {args.metrics_out}")


if __name__ == "__main__":
    main()
