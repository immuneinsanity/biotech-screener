# 🧬 Biotech Screener

A swing-trading research tool for small and micro-cap biotech stocks.

## Features

- **Screener Dashboard** — Filter stocks by market cap, runway status, and sort by catalyst proximity
- **Cash Runway** — Automatically pulled from SEC EDGAR 10-Q filings (cash + burn rate)
- **Catalyst Tracker** — Enter upcoming clinical readouts, FDA dates, or PDUFA dates per stock
- **Watchlist** — Save stocks with notes, managed in a local JSON file
- **Stock Detail** — Price chart, 52-week range, float, volume, clinical trials from ClinicalTrials.gov
- **Dark theme UI** with color-coded runway indicators (🟢🟡🔴)

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

Or with a virtual environment (recommended):

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Run the app

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501`.

## Project Structure

```
biotech-screener/
├── app.py                  # Main Streamlit entry point
├── requirements.txt
├── Dockerfile
├── .env.example
├── .dockerignore
├── README.md
├── .streamlit/
│   └── config.toml         # Dark theme configuration
├── src/
│   ├── __init__.py
│   ├── db.py               # SQLite storage layer
│   ├── data.py             # Data fetching (yfinance, EDGAR, ClinicalTrials)
│   └── ui.py               # UI rendering components
└── data/
    └── biotech.db          # SQLite database (auto-created)
```

## Data Sources

| Source | Usage | Rate Limits |
|--------|-------|-------------|
| [yfinance](https://github.com/ranaroussi/yfinance) | Price, market cap, float | Informal; cached 1h |
| [SEC EDGAR](https://data.sec.gov/) | Cash + burn from 10-Q | Free, no key needed |
| [ClinicalTrials.gov](https://clinicaltrials.gov/api/) | Clinical trial status | Free, no key needed |

All API calls are cached with Streamlit's `@st.cache_data`. Click **🔄 Refresh Data** in the sidebar to force a reload.

## Runway Calculation

```
Cash = Most recent 10-Q cash + short-term investments (SEC EDGAR XBRL)
Quarterly Burn = Average of last 2 quarters of operating cash outflow
Runway (days) = (Cash / Quarterly Burn) × 91
```

- 🟢 **Green** — ≥ 180 days
- 🟡 **Yellow** — 90–179 days
- 🔴 **Red** — < 90 days (dilution risk)

## Customization

### Adding tickers to the default universe

Edit the `BIOTECH_UNIVERSE` list in `src/data.py`.

### Adjusting cache TTL

In `src/data.py`, modify the `ttl` parameter on `@st.cache_data` decorators:
- Stock data: `ttl=3600` (1 hour)
- EDGAR data: `ttl=43200` (12 hours)

## Deployment

### Option A: Streamlit Community Cloud (easiest, free)

1. Push this repo to GitHub (public repo or free account)
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your GitHub repo and set the main file to `app.py`
4. **Note:** watchlist data won't persist between restarts on the free tier (use Railway for persistence)

### Option B: Railway (free tier, persistent storage)

1. Push repo to GitHub
2. Go to [railway.app](https://railway.app), create a new project from your GitHub repo
3. Add a Volume mounted at `/app/data`
4. Set the environment variable `DB_PATH=/app/data/biotech.db`
5. Railway auto-detects the Dockerfile and deploys

### Option C: Run locally with Docker

```bash
docker build -t biotech-screener .
docker run -p 8501:8501 -v biotech-data:/app/data biotech-screener
```

Then open http://localhost:8501

## Disclaimer

For research and educational purposes only. Not financial advice.
