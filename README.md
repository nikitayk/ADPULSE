<div align="center">

# 📈 ADPULSE

### Real-Time Bidding intelligence — bid smarter, in under 5 milliseconds.

A full-stack **Demand-Side Platform (DSP)** that pairs two **LightGBM** models with a budget-aware
bidding strategy and streams every auction decision to a **live operator dashboard** with an
interactive **3D RTB globe**. Every number is computed by real models — nothing is mocked.

<br/>

[![Live Demo](https://img.shields.io/badge/▶_Live_Demo-Open-2fd39a?style=for-the-badge)](https://adpulse-p8oo.onrender.com)
&nbsp;
[![Dashboard](https://img.shields.io/badge/Live_Dashboard-2f6bff?style=for-the-badge)](https://adpulse-p8oo.onrender.com/dashboard)
&nbsp;
[![Bid Tester](https://img.shields.io/badge/Bid_Tester-38bdf8?style=for-the-badge)](https://adpulse-p8oo.onrender.com/bidtester)

<br/>

![Python](https://img.shields.io/badge/Python-3.9-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?logo=flask&logoColor=white)
![Socket.IO](https://img.shields.io/badge/Socket.IO-live_feed-010101?logo=socketdotio&logoColor=white)
![LightGBM](https://img.shields.io/badge/LightGBM-CTR_·_CVR-9cf)
![scikit-learn](https://img.shields.io/badge/scikit--learn-1.2.2-F7931E?logo=scikitlearn&logoColor=white)
![Three.js](https://img.shields.io/badge/three.js-3D_globe-000000?logo=threedotjs&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-containerized-2496ED?logo=docker&logoColor=white)
![Render](https://img.shields.io/badge/Render-deployed-46E3B7?logo=render&logoColor=white)

</div>

---

## 🎯 What is this?

Every time a webpage loads, a **millisecond auction** happens behind the scenes for the ad slot.
ADPULSE is the **bidder** in that auction: for each incoming request it must decide — in real time,
under a fixed budget — **whether to bid and how much**. It does this by predicting the probability
of a click and a conversion with machine learning, then pricing the bid to maximise advertiser value.

> **The hard part isn't the ML — it's doing it in single-digit milliseconds, under a budget, at the scale of a live auction stream, without ever running out of memory.**

---

## ✨ Highlights

- 🧠 **Two real LightGBM models** predict click-through rate (CTR) and conversion rate (CVR) per request.
- ⚡ **~5 ms bid decisions** — models loaded once at startup, zero DB calls in the hot path.
- 🌍 **Interactive 3D RTB globe** (Three.js / globe.gl) that visualises live bid flow in real time over WebSocket.
- 📊 **Live operator dashboard** — win rate, budget burn-down, CTR/CVR trends, and a streaming auction feed.
- 🧪 **Bid Tester** — fire a synthetic request and watch the full decision break down term-by-term.
- 🌊 **O(1)-memory streaming** of the multi-GB IPinYou logs — never loads a file into RAM.
- 🎚️ **Graceful degradation** — runs on the real dataset when present, falls back to a synthetic generator otherwise (so it deploys anywhere with zero data setup).
- 🐳 **Containerised & deployed** — one Dockerfile, one `render.yaml`, live on the public internet.

---

## 🏗️ Architecture

```mermaid
flowchart LR
    A["🌐 Ad Exchange<br/>bid request"] --> B{{"ADPULSE DSP"}}
    B --> C["Feature<br/>engineering"]
    C --> D["CTR model<br/>(LightGBM)"]
    C --> E["CVR model<br/>(LightGBM)"]
    D --> F["💰 Bid pricing<br/>base · CTR · (1 + N·CVR)"]
    E --> F
    F --> G{{"Second-price<br/>auction"}}
    G -->|win| H["✅ Impression<br/>(pay 2nd price)"]
    G -->|lose| I["❌ No spend"]
    B -. "Socket.IO live stream" .-> J["📊 Dashboard<br/>+ 🌍 3D Globe"]

    style B fill:#2f6bff,color:#fff
    style F fill:#0e1321,color:#38bdf8
    style J fill:#0e1321,color:#2fd39a
```

**Stack at a glance**

| Layer | Tech |
|------|------|
| **Bidding engine** | Python · LightGBM (CTR + CVR) · scikit-learn scalers |
| **API & realtime** | Flask · Flask-SocketIO (live auction feed) |
| **Data layer** | O(1) streaming reader for IPinYou logs · synthetic fallback |
| **Frontend** | Vanilla JS · Three.js / globe.gl · GSAP · Chart.js · dark "HUD" design system |
| **Delivery** | Docker (`python:3.9-slim` + `libgomp1`) · Render Blueprint |

---

## 🔁 How a single bid is made

```mermaid
sequenceDiagram
    participant X as Ad Exchange
    participant D as ADPULSE  /api/bid
    participant M as LightGBM models
    participant U as Dashboard (WebSocket)

    X->>D: bid request (user · ad slot · advertiser)
    D->>M: engineered features
    M-->>D: p(click), p(convert)
    D->>D: bid = base · CTR · (1 + N·CVR)
    Note over D: capped to budget & floor price
    D-->>X: bid price  (≈ 5 ms)
    D->>U: stream decision live 🌍
```

### The bidding formula

```
bid = base_bid × CTR × (1 + N × CVR)
```

- **CTR** is the primary gate — if click probability is low, the entire bid shrinks proportionally.
- **(1 + N × CVR)** scales the bid up for advertisers where conversions matter more.
- **N** is an advertiser-specific weight: the score we maximise is `Clicks + N × Conversions`.

---

## 🏷️ The 5 advertiser campaigns

Each advertiser has a different **N**, producing a distinct bidding personality:

| Advertiser | N | Strategy | Behaviour |
|-----------:|:-:|----------|-----------|
| `1458` | 0 | Clicks only | Bids purely on click probability |
| `3358` | 2 | Balanced | A conversion is worth 2× a click |
| `3386` | 0 | Clicks only | Conversions ignored |
| `3427` | 0 | Clicks only | Conversions ignored |
| `3476` | 10 | Conversion-focused | A likely converter can multiply the bid by up to **11×** |

---

## 🎓 Real data vs. modeled outcomes (and why)

ADPULSE streams **real [IPinYou](https://contest.ipinyou.com/) impressions** and resolves **wins/losses
against the real historical market price** (`Payingprice`, a true second-price auction).

Clicks and conversions, however, are **modeled from the predicted probabilities** for live visibility —
because real display CTR is **~0.06 %** (≈1,159 clicks in 1.82 M impressions), far too sparse to render
in a live view. A single environment flag (`OUTCOME_MODE=real`) switches to **ground-truth labels** for
offline validation.

> This mirrors how production DSP dashboards actually work: **live modeled performance, reconciled with sparse actuals in batch.**

---

## 📁 Project structure

```
ADPULSE/
├── bidder.submission.code/python/
│   ├── app.py            # Flask + Socket.IO server · REST API · live auction stream
│   ├── Bid.py            # core bidding strategy  (getBidPrice → the formula)
│   ├── data_source.py    # O(1)-memory IPinYou log streamer (+ synthetic fallback)
│   ├── BidRequest.py     # bid-request model
│   ├── model_ctr.pkl     # LightGBM click model       ├── scaler_ctr.pkl
│   ├── model_cvr.pkl     # LightGBM conversion model   └── scaler_cvr.pkl
│   └── requirements.txt
├── frontend/
│   ├── landing.html      # 3D RTB globe hero + scroll storytelling
│   ├── dashboard.html    # live auction dashboard (Chart.js + WebSocket)
│   ├── bidtester.html    # interactive single-bid tester
│   └── theme.css         # cinematic dark "HUD" design system
├── dataset/              # IPinYou logs (git-ignored — see dataset/README.md)
├── Dockerfile            # python:3.9-slim + libgomp1
├── render.yaml           # Render Blueprint (Docker, free plan, health check)
└── DEPLOY.md             # full deploy guide (Render / Hugging Face / Railway)
```

---

## 🚀 Getting started

### Run locally

```bash
cd bidder.submission.code/python
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
PORT=5050 ./venv/bin/python app.py
# open http://localhost:5050
```

> Apple Silicon: LightGBM needs OpenMP → `brew install libomp`.

### Run with Docker

```bash
docker build -t adpulse .
docker run --rm -p 5050:7860 -e PORT=7860 adpulse
# open http://localhost:5050
```

### Use the real dataset (optional)

The app auto-detects IPinYou logs placed in `dataset/` and switches from synthetic to **real** mode.
See [`dataset/README.md`](dataset/README.md) for the one-file download (`imp.06` + `clk.06` + `conv.06`).

---

## ⚙️ Configuration

| Variable | Default | Purpose |
|----------|:-------:|---------|
| `PORT` | `7860` | Bind port (platform-injected on deploy) |
| `DEMO_BID_SCALE` | `8000` | Scales bids to be competitive with real market prices (`1` = faithful submission bidder) |
| `DEMO_OUTCOME_SCALE` | `1000` | Amplifies modeled click/conv rates for live visibility |
| `OUTCOME_MODE` | `model` | `model` (lively) or `real` (ground-truth labels, needs dataset) |
| `DATASET_DAYS` | `06` | Which day(s) of logs to stream when the dataset is present |

---

## 📡 API

| Endpoint | Method | Description |
|----------|:------:|-------------|
| `/api/health` | GET | Service status, model + data-source info |
| `/api/bid` | POST | Price a single bid request (real model inference) |
| `/api/stats` | GET | Aggregate auction stats |
| `/api/reset` | POST | Reset the live simulation |
| `/` · `/dashboard` · `/bidtester` | GET | The three frontends |
| Socket.IO `bid_result` / `stats_update` | WS | Live auction event stream |

---

## 🧠 Engineering notes (the interesting bits)

- **Sub-5 ms hot path** — models are loaded once at boot; `getBidPrice()` does pure in-memory inference with no I/O.
- **O(1) memory at multi-GB scale** — logs are read line-by-line through a generator that loops forever; the full week of data never enters RAM.
- **Auto-detecting parser** — handles both the 20-column bid log and the 24-column impression/click/conversion log, mapping both onto a common request schema.
- **Real second-price auctions** — wins are resolved against the historical `Payingprice`, not a guess.
- **Graceful degradation** — no dataset? It transparently falls back to a synthetic generator, so the live demo works on a fresh cloud box with zero setup.
- **Single-process realtime** — one background producer thread feeds the in-memory stats and the Socket.IO broadcast, keeping the model and dashboard perfectly in sync.

---

## 🙏 Acknowledgments

- **[IPinYou Global RTB Bidding Algorithm Competition](https://contest.ipinyou.com/)** dataset.
- Built on a hackathon bidding-submission framework, extended into a full-stack, deployed product.

---

## 📜 License & ownership

**© 2026 Ali Husain Rizvi ([@alirizzzv](https://github.com/alirizzzv)) — All Rights Reserved.**

This repository is shared publicly for **demonstration, evaluation, and portfolio purposes only**.
It is **not** licensed for reuse, copying, modification, or redistribution. Please don't repackage or
submit this work as your own. See [`LICENSE`](LICENSE) for the full terms.

---

<div align="center">

**[▶ Open the live demo](https://adpulse-p8oo.onrender.com)** &nbsp;·&nbsp; built by [**@alirizzzv**](https://github.com/alirizzzv)

© 2026 Ali Husain Rizvi · All Rights Reserved · not for redistribution

</div>
