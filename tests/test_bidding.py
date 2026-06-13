"""Unit tests for the core bidding decision in ``Bid.getBidPrice``.

The bidding *logic* (the formula, budget hard-stop, floor enforcement, price
cap, and dynamic bidRatio) is independent of the specific model weights. To
test it deterministically — and without requiring the scikit-learn 1.2.2 pickle
ABI to be installed — we build a ``Bid`` instance via ``__new__`` and inject
trivial stand-in models whose ``predict_proba`` returns fixed probabilities.

A separate, optional integration test exercises the *real* ``.pkl`` artifacts
and is skipped automatically when the ML stack / model files are unavailable.
"""
import os
import random

import numpy as np
import pytest

import Bid as bid_module
from Bid import Bid
from BidRequest import BidRequest


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class _FixedModel:
    """Stand-in classifier: predict_proba returns a constant P(positive)."""

    def __init__(self, p):
        self._p = p

    def predict_proba(self, X):
        # mirrors sklearn: a (n_samples, n_classes) array, indexed as [:, 1][0]
        return np.array([[1.0 - self._p, self._p]])


class _PassThroughScaler:
    def transform(self, df):
        return df


def make_bidder(ctr=0.5, cvr=0.2, base=50, n_values=None):
    """Construct a Bid with injected models, bypassing __init__'s pkl loading."""
    b = Bid.__new__(Bid)
    b.bidRatio = 100  # always pass the random gate
    b.baseBidPrice = base
    b.ctr_model = _FixedModel(ctr)
    b.cvr_model = _FixedModel(cvr)
    b.scaler_ctr = _PassThroughScaler()
    b.scaler_cvr = _PassThroughScaler()
    b.advertiser_n_values = n_values or {1458: 0, 3358: 2, 3386: 0, 3427: 0, 3476: 10}
    b.totalBudget = 625000
    b.spentBudget = 0
    return b


def make_request(advertiser_id=3476, floor=0):
    br = BidRequest()
    br.setBidId("test-bid")
    br.setTimestamp("20130606000104407")  # yyyyMMddHHmmssSSS
    br.setUserAgent("Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537.36")
    br.setAdvertiserId(advertiser_id)
    br.setAdExchange(2)
    br.setRegion(1)
    br.setAdSlotWidth(300)
    br.setAdSlotHeight(250)
    br.setAdSlotVisibility("FirstView")
    br.setAdSlotFormat("Fixed")
    br.setAdSlotFloorPrice(floor)
    return br


@pytest.fixture(autouse=True)
def _deterministic_random():
    random.seed(1234)
    yield


# --------------------------------------------------------------------------- #
# The formula:  bid = int(base * CTR * (1 + N * CVR))
# --------------------------------------------------------------------------- #
class TestBiddingFormula:
    def test_conversion_weighted_advertiser(self):
        # base=50, CTR=0.5, CVR=0.2, N=10 -> 50 * 0.5 * (1 + 10*0.2) = 50*0.5*3 = 75
        bidder = make_bidder(ctr=0.5, cvr=0.2, base=50)
        assert bidder.getBidPrice(make_request(advertiser_id=3476)) == 75

    def test_clicks_only_advertiser_ignores_cvr(self):
        # N=0 -> bid = int(50 * 0.5 * (1 + 0)) = 25, CVR irrelevant
        bidder = make_bidder(ctr=0.5, cvr=0.99, base=50)
        assert bidder.getBidPrice(make_request(advertiser_id=1458)) == 25

    def test_unknown_advertiser_defaults_to_n_one(self):
        # advertiser not in the map -> N defaults to 1
        # bid = int(50 * 0.5 * (1 + 1*0.2)) = int(50*0.5*1.2) = 30
        bidder = make_bidder(ctr=0.5, cvr=0.2, base=50)
        assert bidder.getBidPrice(make_request(advertiser_id=9999)) == 30

    def test_zero_value_becomes_no_bid(self):
        # CTR=0 -> raw bid 0, no floor -> returns -1 (no bid)
        bidder = make_bidder(ctr=0.0, cvr=0.0, base=50)
        assert bidder.getBidPrice(make_request(advertiser_id=1458, floor=0)) == -1


# --------------------------------------------------------------------------- #
# Floor price, price cap, budget
# --------------------------------------------------------------------------- #
class TestConstraints:
    def test_floor_price_is_enforced(self):
        # tiny computed bid, but floor=40 lifts it to 40
        bidder = make_bidder(ctr=0.01, cvr=0.0, base=50)
        assert bidder.getBidPrice(make_request(advertiser_id=1458, floor=40)) == 40

    def test_bid_is_capped_at_300(self):
        # floor=500 would win, but the hard cap clamps to 300
        bidder = make_bidder(ctr=0.5, cvr=0.2, base=50)
        assert bidder.getBidPrice(make_request(advertiser_id=3476, floor=500)) == 300

    def test_budget_hard_stop_returns_no_bid(self):
        bidder = make_bidder()
        bidder.spentBudget = bidder.totalBudget
        assert bidder.getBidPrice(make_request()) == -1

    def test_zero_bid_ratio_never_bids(self):
        bidder = make_bidder()
        bidder.bidRatio = 0  # random.randint(0,99) < 0 is never true
        assert bidder.getBidPrice(make_request()) == -1


# --------------------------------------------------------------------------- #
# Budget accounting + dynamic bidRatio throttle
# --------------------------------------------------------------------------- #
class TestBudgetTracking:
    def test_spend_accumulates(self):
        bidder = make_bidder(ctr=0.5, cvr=0.2, base=50)  # each bid = 75
        bidder.getBidPrice(make_request(advertiser_id=3476))
        assert bidder.spentBudget == 75
        bidder.getBidPrice(make_request(advertiser_id=3476))
        assert bidder.spentBudget == 150

    def test_bid_ratio_throttles_as_budget_depletes(self):
        bidder = make_bidder(ctr=0.5, cvr=0.2, base=50)

        # >20% remaining -> full aggression
        bidder.spentBudget = 0
        bidder.getBidPrice(make_request(advertiser_id=3476))
        assert bidder.bidRatio == 90

        # <10% remaining -> most conservative
        bidder.spentBudget = int(bidder.totalBudget * 0.95)
        bidder.getBidPrice(make_request(advertiser_id=3476))
        assert bidder.bidRatio == 20


# --------------------------------------------------------------------------- #
# Optional: exercise the REAL model artifacts end-to-end.
# --------------------------------------------------------------------------- #
_MODELS_DIR = os.path.dirname(os.path.abspath(bid_module.__file__))
_HAVE_MODELS = all(
    os.path.exists(os.path.join(_MODELS_DIR, f))
    for f in ("model_ctr.pkl", "model_cvr.pkl", "scaler_ctr.pkl", "scaler_cvr.pkl")
)


@pytest.mark.integration
@pytest.mark.skipif(not _HAVE_MODELS, reason="model .pkl artifacts not present")
def test_real_models_produce_valid_bid():
    """Smoke test: the shipped models load and yield a bid in the valid range."""
    try:
        bidder = Bid()
    except Exception as exc:  # pragma: no cover - environment/ABI dependent
        pytest.skip(f"could not load real models in this environment: {exc}")

    bidder.bidRatio = 100  # force the bid path
    price = bidder.getBidPrice(make_request(advertiser_id=3476, floor=0))
    assert price == -1 or 1 <= price <= 300
