"""
ADPULSE — Flask + Socket.IO backend wrapping the RTB bidding engine.

What this server does
---------------------
1. Loads the real LightGBM CTR/CVR models once at startup (via Bid()).
2. Exposes a REST API:
     GET  /api/health   -> liveness + models_loaded
     POST /api/bid      -> Bid Tester: run the real models on supplied params
     GET  /api/stats    -> current aggregated campaign KPIs
3. Streams a live auction feed over Socket.IO:
     - A background task synthesises realistic bid requests (using the real
       region/city lookup tables + the 5 real advertiser IDs) and runs every
       one of them through the REAL getBidPrice() algorithm.
     - Auction outcomes (WON/LOST) are simulated with a second-price auction.
     - Click/conversion outcomes are simulated using the model's OWN predicted
       probabilities (Monte-Carlo of the campaign) to drive the Score KPI.

The synthetic generator is the only thing standing in for the multi-GB IPinYou
`bid.07.txt`. The architecture is a producer/consumer pipeline: swap the
generator for a file/Kafka reader and nothing else changes.

Also serves the three frontend pages for convenient local testing.
"""

import os
import time
import uuid
import random
import threading
import datetime as dt

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO

from BidRequest import BidRequest
from Bid import Bid
from data_source import build_data_source

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
HERE = os.path.dirname(os.path.abspath(__file__))
# repo root is .../ADPULSE  (app.py lives in ADPULSE/bidder.submission.code/python)
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
FRONTEND_DIR = os.path.join(REPO_ROOT, "frontend")
REGION_FILE = os.path.join(REPO_ROOT, "region.txt")
CITY_FILE = os.path.join(REPO_ROOT, "city.txt")

# Real IPinYou dataset (multi-GB, git-ignored). Place files in ADPULSE/dataset/.
DATASET_DIR = os.environ.get("DATASET_DIR", os.path.join(REPO_ROOT, "dataset"))
# Which day(s) to replay (06–12). Comma-separated; defaults to a single day looped.
DATASET_DAYS = [d.strip() for d in os.environ.get("DATASET_DAYS", "06").split(",") if d.strip()]

# Stream speed (bids/sec). Override with FEED_BPS env var.
FEED_BPS = float(os.environ.get("FEED_BPS", "7"))
# Total campaign budget in CPM units (mirrors Bid.totalBudget default)
TOTAL_BUDGET = int(os.environ.get("TOTAL_BUDGET", "625000"))

# ---- Demo-visualisation knobs (kept SEPARATE from the real bidding algorithm) ----
# The LightGBM models output calibrated probabilities (~0.01% CTR), so with the
# submission's base_bid=50 the ML term truncates to 0 and bids would be purely
# floor-driven. These two knobs make the ML signal visible in the dashboard.
# Set BOTH to 1 to run exactly the faithful hackathon submission.
DEMO_BID_SCALE = float(os.environ.get("DEMO_BID_SCALE", "8000"))      # scales base_bid (tuned to real ~55 median market price)
DEMO_OUTCOME_SCALE = float(os.environ.get("DEMO_OUTCOME_SCALE", "1000"))  # scales simulated click/conv rates
# Click/conversion outcome source: "model" (default — model-driven sim, lively, because
# real display CTR ~0.06% is too sparse to render live) or "real" (ground-truth labels
# from the clk/conv joins — authentic but visually quiet; good for offline validation).
OUTCOME_MODE = os.environ.get("OUTCOME_MODE", "model").lower()

# --------------------------------------------------------------------------- #
# App / Socket.IO setup
# --------------------------------------------------------------------------- #
app = Flask(__name__, static_folder=None)
CORS(app)  # allow the Vercel-hosted frontend to call this API cross-origin
# threading async mode keeps deps minimal (no eventlet/gevent monkey-patching)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# --------------------------------------------------------------------------- #
# Models — loaded ONCE at startup, reused for every request
# --------------------------------------------------------------------------- #
print("[ADPULSE] Loading models...")
# TESTER instance: used by POST /api/bid. We never mutate its budget (we call the
# side-effect-free breakdown helper), so results are deterministic & explainable.
TESTER = Bid()
# STREAM instance: used by the live feed. Its budget genuinely depletes over the
# session via the real getBidPrice(), so the budget gauge reflects the real algo.
STREAM_BIDDER = Bid()
STREAM_BIDDER.totalBudget = TOTAL_BUDGET
STREAM_BIDDER.spentBudget = 0

# Apply the demo bid scaling to both instances (set DEMO_BID_SCALE=1 for faithful mode).
# This only changes base_bid — the formula base_bid x CTR x (1 + N x CVR) is untouched.
TESTER.baseBidPrice = int(TESTER.baseBidPrice * DEMO_BID_SCALE)
STREAM_BIDDER.baseBidPrice = int(STREAM_BIDDER.baseBidPrice * DEMO_BID_SCALE)
print(f"[ADPULSE] Models loaded. base_bid={STREAM_BIDDER.baseBidPrice} "
      f"(scale x{DEMO_BID_SCALE}), outcome_scale=x{DEMO_OUTCOME_SCALE}")

ADVERTISERS = TESTER.advertiser_n_values  # {1458:0, 3358:2, 3386:0, 3427:0, 3476:10}

# Real dataset stream (None → synthetic fallback). Built once at startup.
DATA_SOURCE = build_data_source(DATASET_DIR, DATASET_DAYS)
if DATA_SOURCE:
    print(f"[ADPULSE] Real dataset stream: {DATA_SOURCE.info()}")
else:
    print(f"[ADPULSE] No dataset in {DATASET_DIR} — using synthetic generator.")

# --------------------------------------------------------------------------- #
# Reference data for the synthetic generator
# --------------------------------------------------------------------------- #
def _load_codes(path):
    """Read a '<code>\\t<name>' lookup file into a list of int codes."""
    codes = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                parts = line.strip().split("\t")
                if parts and parts[0].isdigit():
                    codes.append(int(parts[0]))
    except FileNotFoundError:
        pass
    return codes or [0]

REGION_CODES = _load_codes(REGION_FILE)
CITY_CODES = _load_codes(CITY_FILE)

# A small pool of realistic User-Agent strings spanning device/OS/browser combos.
USER_AGENTS = [
    # Desktop Chrome / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    # Desktop Firefox / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    # Desktop Safari / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    # Mobile Chrome / Android
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36",
    # Mobile Safari / iPhone (iOS)
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    # Tablet / iPad
    "Mozilla/5.0 (iPad; CPU OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    # Edge / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36 Edg/120.0",
]

AD_SLOT_SIZES = [(300, 250), (728, 90), (160, 600), (300, 600), (970, 250), (320, 50)]
AD_VISIBILITY = ["FirstView", "SecondView", "Na"]
AD_FORMAT = ["Fixed", "Pop", "Background", "Float", "Na"]

# --------------------------------------------------------------------------- #
# Shared live stats (mutated by the background stream thread)
# --------------------------------------------------------------------------- #
STATS_LOCK = threading.Lock()
STATS = {
    "total_auctions": 0,   # actual bids placed (bid != -1)
    "requests_seen": 0,    # every synthetic request, incl. skipped
    "wins": 0,
    "losses": 0,
    "skipped": 0,
    "budget_spent": 0,     # mirrors STREAM_BIDDER.spentBudget
    "budget_total": TOTAL_BUDGET,
    "clicks": 0,
    "conversions": 0,
    "score": 0,            # clicks + sum(N per conversion)
}

STREAM_STARTED = False
STREAM_PAUSED = False


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def make_timestamp(now=None):
    """Return a 'yyyyMMddHHmmssSSS' timestamp string (matches dataset format)."""
    now = now or dt.datetime.now()
    return now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}"


def build_bid_request(params: dict) -> BidRequest:
    """Construct a BidRequest from a dict of raw values (used by API + stream)."""
    br = BidRequest()
    br.setBidId(params.get("bidId") or uuid.uuid4().hex)
    br.setTimestamp(params.get("timestamp") or make_timestamp())
    br.setVisitorId(params.get("visitorId"))
    br.setUserAgent(params.get("userAgent") or USER_AGENTS[0])
    br.setIpAddress(params.get("ipAddress") or "118.81.189.0")
    br.setRegion(params.get("region"))
    br.setCity(params.get("city"))
    br.setAdExchange(params.get("adExchange"))
    br.setDomain(params.get("domain"))
    br.setUrl(params.get("url"))
    br.setAnonymousURLID(params.get("anonymousURLID"))
    br.setAdSlotID(params.get("adSlotID"))
    br.setAdSlotWidth(params.get("adSlotWidth") or 300)
    br.setAdSlotHeight(params.get("adSlotHeight") or 250)
    br.setAdSlotVisibility(params.get("adSlotVisibility") or "FirstView")
    br.setAdSlotFormat(params.get("adSlotFormat") or "Fixed")
    br.setAdSlotFloorPrice(params.get("adSlotFloorPrice") or 0)
    br.setCreativeID(params.get("creativeID"))
    br.setAdvertiserId(params.get("advertiserId") or 3476)
    br.setUserTags(params.get("userTags"))
    return br


def compute_breakdown(bidder: Bid, br: BidRequest) -> dict:
    """
    Side-effect-free computation of the full bid breakdown.

    Mirrors getBidPrice() math exactly (base_bid x CTR x (1 + N x CVR)) but
    WITHOUT the random bidRatio gate and WITHOUT mutating budget — so the Bid
    Tester is deterministic and every intermediate value is explainable.
    """
    t0 = time.time()

    ctr_df = bidder._preprocess_bid_request_ctr(br)
    ctr_scaled = bidder.scaler_ctr.transform(ctr_df)
    ctr = float(bidder.ctr_model.predict_proba(ctr_scaled)[:, 1][0])

    cvr_df = bidder._preprocess_bid_request_cvr(br)
    cvr_scaled = bidder.scaler_cvr.transform(cvr_df)
    cvr = float(bidder.cvr_model.predict_proba(cvr_scaled)[:, 1][0])

    advertiser_id = int(br.getAdvertiserId())
    n_value = bidder.advertiser_n_values.get(advertiser_id, 1)

    estimated_value = ctr * (1 + n_value * cvr)
    raw_bid = int(bidder.baseBidPrice * estimated_value)

    floor_price = int(br.getAdSlotFloorPrice() or 0)
    final_bid = max(raw_bid, floor_price)
    capped = False
    if final_bid <= 0:
        final_bid = -1
    elif final_bid > 300:
        final_bid = 300
        capped = True

    exec_ms = (time.time() - t0) * 1000.0

    return {
        "bid_id": br.getBidId(),
        "timestamp": br.getTimestamp(),
        "advertiser_id": advertiser_id,
        "ctr": ctr,
        "cvr": cvr,
        "n_value": n_value,
        "base_bid": bidder.baseBidPrice,
        "estimated_value": estimated_value,
        "raw_bid": raw_bid,
        "floor_price": floor_price,
        "capped": capped,
        "bid_price": final_bid,
        "execution_time_ms": round(exec_ms, 3),
    }


def random_bid_request() -> BidRequest:
    """Generate one realistic synthetic bid request."""
    w, h = random.choice(AD_SLOT_SIZES)
    return build_bid_request({
        "userAgent": random.choice(USER_AGENTS),
        "region": random.choice(REGION_CODES),
        "city": random.choice(CITY_CODES),
        "adExchange": random.randint(1, 3),
        "adSlotWidth": w,
        "adSlotHeight": h,
        "adSlotVisibility": random.choice(AD_VISIBILITY),
        "adSlotFormat": random.choice(AD_FORMAT),
        "adSlotFloorPrice": random.choice([0, 0, 0, 5, 10, 20, 50]),
        "advertiserId": random.choice(list(ADVERTISERS.keys())),
    })


def request_iter():
    """
    Yields (BidRequest, meta) forever.
    Source of truth: the real IPinYou stream if a dataset is present, otherwise
    the synthetic generator. `meta` carries real outcome hints when available:
      paying_price (real market price), clicked / converted (real labels).
    """
    if DATA_SOURCE:
        for params, meta in DATA_SOURCE.stream():
            yield build_bid_request(params), meta
    else:
        while True:
            yield random_bid_request(), {"paying_price": None, "clicked": None, "converted": None}


# --------------------------------------------------------------------------- #
# Background live-auction stream
# --------------------------------------------------------------------------- #
def stream_loop():
    """Producer/consumer loop: pull request -> bid -> resolve auction -> emit."""
    print("[ADPULSE] Live auction stream started.")
    n = 0
    requests = request_iter()
    while True:
        if STREAM_PAUSED:          # complete freeze: don't even pull the next row
            socketio.sleep(0.2)
            continue
        try:
            br, meta = next(requests)
        except StopIteration:
            break

        # 1) Real model breakdown (for display: CTR/CVR/bid math)
        bd = compute_breakdown(STREAM_BIDDER, br)

        # 2) The REAL bidding decision (random gate + budget depletion)
        actual_bid = STREAM_BIDDER.getBidPrice(br)

        # 3) Resolve the second-price auction outcome.
        #    Real data: compare our bid to the historical Payingprice (true market
        #    price). Synthetic: draw a market price around our bid (~50% win rate).
        if actual_bid == -1:
            result = "SKIPPED"
            pay_price = 0
        else:
            real_pp = meta.get("paying_price")
            market_price = real_pp if real_pp is not None else int(actual_bid * random.uniform(0.5, 1.5))
            if actual_bid >= market_price:
                result = "WON"
                pay_price = market_price  # second-price: you pay the runner-up
            else:
                result = "LOST"
                pay_price = 0

        # 4) Click/conversion outcome.
        #    Real data (clk/conv joins present): use the TRUE label.
        #    Otherwise: Monte-Carlo from the model's own probabilities
        #    (DEMO_OUTCOME_SCALE amplifies the tiny real rates for visibility).
        clicked = converted = False
        if result == "WON":
            if OUTCOME_MODE == "real" and meta.get("clicked") is not None:
                # ground-truth labels from clk/conv joins (authentic, sparse)
                clicked = bool(meta["clicked"])
                converted = bool(meta.get("converted"))
            else:
                # model-driven Monte-Carlo (amplified) — live visibility
                click_p = min(bd["ctr"] * DEMO_OUTCOME_SCALE, 0.6)
                clicked = random.random() < click_p
                if clicked:
                    conv_p = min(bd["cvr"] * DEMO_OUTCOME_SCALE, 0.6)
                    converted = random.random() < conv_p

        # 5) Update shared stats
        with STATS_LOCK:
            STATS["requests_seen"] += 1
            if actual_bid != -1:
                STATS["total_auctions"] += 1
            STATS["budget_spent"] = STREAM_BIDDER.spentBudget
            if result == "WON":
                STATS["wins"] += 1
            elif result == "LOST":
                STATS["losses"] += 1
            else:
                STATS["skipped"] += 1
            if clicked:
                STATS["clicks"] += 1
                STATS["score"] += 1
            if converted:
                STATS["conversions"] += 1
                STATS["score"] += bd["n_value"]
            stats_snapshot = dict(STATS)

        # 6) Emit the per-bid event
        row = dict(bd)
        row.update({
            "bid_price": actual_bid,   # authoritative decision from getBidPrice
            "result": result,
            "pay_price": pay_price,
            "clicked": clicked,
            "converted": converted,
            "ad_width": int(br.getAdSlotWidth()),
            "ad_height": int(br.getAdSlotHeight()),
        })
        socketio.emit("bid_result", row)

        # 7) Emit aggregate stats every 5 bids
        n += 1
        if n % 5 == 0:
            socketio.emit("stats_update", build_stats_payload(stats_snapshot))

        socketio.sleep(1.0 / FEED_BPS)


def build_stats_payload(snap: dict) -> dict:
    total = snap["total_auctions"]
    wins = snap["wins"]
    win_rate = (wins / total * 100.0) if total > 0 else 0.0
    spent = snap["budget_spent"]
    budget_total = snap["budget_total"]
    remaining = max(budget_total - spent, 0)
    return {
        "total_auctions": total,
        "requests_seen": snap["requests_seen"],
        "wins": wins,
        "losses": snap["losses"],
        "skipped": snap["skipped"],
        "win_rate": round(win_rate, 1),
        "budget_spent": spent,
        "budget_total": budget_total,
        "budget_remaining": remaining,
        "budget_pct": round(spent / budget_total * 100.0, 1) if budget_total else 0.0,
        "clicks": snap["clicks"],
        "conversions": snap["conversions"],
        "score": snap["score"],
    }


def ensure_stream_started():
    global STREAM_STARTED
    if not STREAM_STARTED:
        STREAM_STARTED = True
        socketio.start_background_task(stream_loop)


# --------------------------------------------------------------------------- #
# REST API
# --------------------------------------------------------------------------- #
@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "models_loaded": TESTER is not None,
        "advertisers": ADVERTISERS,
        "feed_bps": FEED_BPS,
        "base_bid": STREAM_BIDDER.baseBidPrice,
        "demo_bid_scale": DEMO_BID_SCALE,
        "demo_outcome_scale": DEMO_OUTCOME_SCALE,
        "outcome_mode": OUTCOME_MODE,
        "data_source": DATA_SOURCE.info() if DATA_SOURCE else {"mode": "synthetic"},
    })


@app.route("/api/bid", methods=["POST"])
def api_bid():
    """Bid Tester endpoint — run the real models on supplied parameters."""
    try:
        params = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"error": "Invalid JSON body"}), 400

    try:
        br = build_bid_request(params)
        breakdown = compute_breakdown(TESTER, br)
        return jsonify(breakdown), 200
    except (ValueError, KeyError, TypeError) as e:
        # bad/unparseable input from the client
        return jsonify({"error": f"Invalid bid request: {e}"}), 400
    except Exception as e:  # model / preprocessing failure
        app.logger.exception("api_bid failed")
        return jsonify({"error": f"Server error: {e}"}), 500


@app.route("/api/stats")
def api_stats():
    with STATS_LOCK:
        snap = dict(STATS)
    return jsonify(build_stats_payload(snap))


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Reset budget + stats so the campaign demo can be re-run."""
    global STREAM_PAUSED
    with STATS_LOCK:
        STREAM_BIDDER.spentBudget = 0
        STREAM_BIDDER.bidRatio = 90
        for k in ("total_auctions", "requests_seen", "wins", "losses",
                  "skipped", "budget_spent", "clicks", "conversions", "score"):
            STATS[k] = 0
        snap = dict(STATS)
    socketio.emit("stats_update", build_stats_payload(snap))
    return jsonify({"status": "reset"})


# --------------------------------------------------------------------------- #
# Socket.IO events
# --------------------------------------------------------------------------- #
@socketio.on("connect")
def on_connect():
    print("[ADPULSE] Client connected.")
    ensure_stream_started()
    with STATS_LOCK:
        snap = dict(STATS)
    socketio.emit("stats_update", build_stats_payload(snap))


@socketio.on("set_paused")
def on_set_paused(data):
    """Complete freeze: pause/resume the producer thread entirely."""
    global STREAM_PAUSED
    STREAM_PAUSED = bool((data or {}).get("paused", False))
    print(f"[ADPULSE] Stream paused = {STREAM_PAUSED}")
    socketio.emit("paused_state", {"paused": STREAM_PAUSED})


# --------------------------------------------------------------------------- #
# Frontend (served for convenient local testing; deploy separately on Vercel)
# --------------------------------------------------------------------------- #
@app.route("/")
def landing():
    return send_from_directory(FRONTEND_DIR, "landing.html")


@app.route("/dashboard")
def dashboard():
    return send_from_directory(FRONTEND_DIR, "dashboard.html")


@app.route("/bidtester")
def bidtester():
    return send_from_directory(FRONTEND_DIR, "bidtester.html")


@app.route("/<path:filename>")
def frontend_assets(filename):
    return send_from_directory(FRONTEND_DIR, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"[ADPULSE] Serving on http://localhost:{port}")
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
