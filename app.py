"""
Biotech Screener — main entry point.
Run with: streamlit run app.py
"""

import streamlit as st

st.set_page_config(
    page_title="Biotech Screener",
    page_icon="🧬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS ───────────────────────────────────────────────────────────────

st.markdown("""
<style>
/* Tighter table rows */
.stDataFrame { font-size: 13px; }

/* Metric delta colors */
[data-testid="stMetricDelta"] { font-size: 12px; }

/* Tab styling */
[data-testid="stTabs"] button {
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 0.5px;
}

/* Header */
.screener-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 8px;
}
.screener-title {
    font-size: 28px;
    font-weight: 800;
    color: #00d4aa;
    letter-spacing: -0.5px;
}
.screener-subtitle {
    font-size: 13px;
    color: #888;
    margin-top: -6px;
}

/* Sidebar */
section[data-testid="stSidebar"] {
    background-color: #111827;
}
</style>
""", unsafe_allow_html=True)

# ─── Session state defaults ───────────────────────────────────────────────────

if "show_detail" not in st.session_state:
    st.session_state["show_detail"] = False
if "detail_ticker" not in st.session_state:
    st.session_state["detail_ticker"] = None

# ─── Sidebar header ───────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown(
        """
        <div class="screener-header">
            <span style="font-size:32px">🧬</span>
            <div>
                <div class="screener-title">Biotech Screener</div>
                <div class="screener-subtitle">Small / Micro-Cap · Swing Trading</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("---")

    # Detail ticker quick-nav
    if st.session_state.get("detail_ticker"):
        st.markdown(f"**Viewing:** `{st.session_state['detail_ticker']}`")
        if st.button("✕ Close Detail", use_container_width=True):
            st.session_state["show_detail"] = False
            st.session_state["detail_ticker"] = None
            st.rerun()
        st.markdown("---")

    # Quick direct lookup
    st.markdown("### Quick Lookup")
    lookup = st.text_input("Jump to ticker", placeholder="SAVA").strip().upper()
    if lookup:
        st.session_state["detail_ticker"] = lookup
        st.session_state["show_detail"] = True

# ─── Main content ─────────────────────────────────────────────────────────────

from src.ui import render_detail, render_screener, render_watchlist

# If a detail view is requested, show it full-width first
if st.session_state.get("show_detail") and st.session_state.get("detail_ticker"):
    render_detail(st.session_state["detail_ticker"])
    st.markdown("---")
    if st.button("← Back to Screener"):
        st.session_state["show_detail"] = False
        st.session_state["detail_ticker"] = None
        st.rerun()
else:
    tab_screener, tab_watchlist, tab_about = st.tabs(
        ["📊 Screener", "⭐ Watchlist", "ℹ️ About"]
    )

    with tab_screener:
        render_screener()

    with tab_watchlist:
        render_watchlist()

    with tab_about:
        st.markdown("""
## About Biotech Screener

A lightweight swing-trading research tool for **small and micro-cap biotech stocks**.

### Data Sources
| Source | What it provides | Refresh |
|--------|-----------------|---------|
| **yfinance** | Price, market cap, float, 52-week range, volume | 1 hour |
| **SEC EDGAR** | Cash position + burn rate from latest 10-Q filings | 12 hours |
| **ClinicalTrials.gov** | Active and upcoming clinical trials | 12 hours |
| **SQLite** | Watchlist, catalyst notes | Real-time |

### Runway Color Coding
| Color | Meaning |
|-------|---------|
| 🟢 Green | ≥ 180 days estimated cash runway |
| 🟡 Yellow | 90–179 days |
| 🔴 Red | < 90 days — potential dilution risk |

### Runway Calculation
```
Cash = Most recent 10-Q cash + short-term investments
Quarterly Burn = Avg of last 2 quarters of operating cash outflow
Runway (days) = (Cash / Quarterly Burn) × 91
```

### Disclaimer
This tool is for **research and educational purposes only**. Not financial advice.
Always do your own due diligence before trading.

---
### Getting Started
1. Use the **Screener** tab to filter stocks by market cap and runway
2. Click any row to view the **Stock Detail** panel
3. Add stocks to your **Watchlist** and enter upcoming catalysts manually
4. Use **Quick Lookup** in the sidebar to jump directly to any ticker

### Adding Custom Tickers
- Type comma-separated tickers into the "Add tickers" box in the Screener sidebar
- Or add them via the Watchlist tab — they'll appear in the Screener automatically
        """)
