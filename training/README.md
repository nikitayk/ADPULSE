# 🧪 ADPULSE — model training pipeline

This folder is the **reproducible recipe** behind the four model artifacts that
ship in [`bidder.submission.code/python/`](../bidder.submission.code/python/):

| Artifact | What it is |
|----------|------------|
| `model_ctr.pkl` | LightGBM classifier — **P(click \| bid request)** |
| `scaler_ctr.pkl` | `StandardScaler` fitted on the CTR feature matrix |
| `model_cvr.pkl` | LightGBM classifier — **P(convert \| bid request)** |
| `scaler_cvr.pkl` | `StandardScaler` fitted on the CVR feature matrix |

Previously only the trained `.pkl` files were committed — there was no way to
verify, retrain, or audit them. [`train.py`](train.py) closes that gap.

---

## Quick start

```bash
# 1. install the pinned training stack (same versions as the runtime)
python3 -m venv .venv && ./.venv/bin/pip install -r training/requirements.txt

# 2. place IPinYou logs in ./dataset (see dataset/README.md for the download)

# 3. train — writes the .pkl files straight into the runtime folder
./.venv/bin/python training/train.py --dataset-dir ./dataset --days 06,07,08

# fast iteration on a subsample:
./.venv/bin/python training/train.py --dataset-dir ./dataset --days 06 --sample-frac 0.2
```

Evaluation metrics (ROC-AUC, PR-AUC, log-loss per model) are printed and also
written to `training/metrics.json`.

### Arguments

| Flag | Default | Meaning |
|------|:-------:|---------|
| `--dataset-dir` | *(required)* | folder of `imp/clk/conv.<day>.txt` logs |
| `--days` | `06` | comma-separated days to train on, e.g. `06,07,08` |
| `--sample-frac` | `1.0` | row subsample fraction for quick runs |
| `--out` | `bidder.submission.code/python` | where the `.pkl` files are written |
| `--metrics-out` | `training/metrics.json` | evaluation metrics output |

---

## How it works

1. **Load impressions** — the 24-column `imp.<day>.txt` logs are the training
   universe (one row per impression).
2. **Build labels by BidID join** — an impression is a **positive CTR** example
   if its `BidID` appears in any `clk.<day>.txt`, and a **positive CVR** example
   if it appears in any `conv.<day>.txt`. Clicks/conversions are sparse, so these
   ID sets are small and cheap to hold in memory.
3. **Feature engineering** — replicated **byte-for-byte** from the inference path
   (`Bid._preprocess_bid_request_ctr` / `_preprocess_bid_request_cvr`) so the
   resulting models are drop-in compatible with the live server. Categorical
   fields (UA device/OS/browser, ad-slot visibility/format) are encoded with
   pandas `category` codes; timestamps yield a numeric value plus a `weekday`.
4. **Train** — two `LGBMClassifier`s on `StandardScaler`-scaled features, with a
   stratified 80/20 split and a fixed seed.
5. **Persist** — models and scalers are dumped with `joblib`.

### Feature sets (order matters — matches inference exactly)

**CTR:** `ua_browser, ua_device, ua_os, weekday, AdvertiserID, Payingprice,
Adslotfloorprice, Adslotformat, Adslotheight, Adslotvisibility, Adslotwidth,
Timestamp`

**CVR:** `clicked, ua_os, ua_device, weekday, Timestamp, AdvertiserID,
Payingprice, Adexchange, Biddingprice, Adslotformat, Adslotheight, Region`

### Hyperparameters

```python
n_estimators=300, learning_rate=0.05, num_leaves=63, max_depth=-1,
subsample=0.8, colsample_bytree=0.8, reg_lambda=1.0, random_state=42
```

Class imbalance (display CTR is ~0.06%) is handled with LightGBM's
`scale_pos_weight = negatives / positives` rather than resampling, which keeps
the predicted probabilities calibrated.

---

## Reproducibility notes

- **Fixed seed** (`RANDOM_SEED = 42`) drives the train/test split and LightGBM,
  so repeated runs on the same data produce the same models and metrics.
- **Pinned versions** in [`requirements.txt`](requirements.txt) match the runtime
  `requirements.txt`. This matters because the models are pickled here and
  unpickled in production — a scikit-learn major/minor mismatch can break
  unpickling or silently change behaviour.

### Known train/serve detail

`Payingprice` and `Biddingprice` exist in the training logs but are **not known
at bid time**, so the live `Bid.py` feeds them as `0` during inference. The
models therefore learn to lean on the other features at serving time; this is an
intentional simplification of the original hackathon submission, documented here
so it isn't mistaken for a bug.
