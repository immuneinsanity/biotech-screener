"""
UI rendering components for the Biotech Screener.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.data import (
    build_screener_row,
    get_biotech_universe,
    get_clinical_trials,
    get_market_cap_fast,
    get_price_history,
    get_stock_info,
    load_catalysts,
    load_watchlist,
    save_catalysts,
    save_watchlist,
)

# ─── Color helpers ────────────────────────────────────────────────────────────

def runway_badge(days: Optional[int]) -> str:
    """Return colored emoji badge based on runway duration."""
    if days is None:
        return "⬜ N/A"
    if days >= 180:
        return f"🟢 {days}d"
    if days >= 90:
        return f"🟡 {days}d"
    return f"🔴 {days}d"


def runway_color_css(days: Optional[int]) -> str:
    if days is None:
        return "#888888"
    if days >= 180:
        return "#00d97a"
    if days >= 90:
        return "#f5c542"
    return "#ff4b4b"


def days_badge(days: Optional[int], source: Optional[str] = None) -> str:
    if source == "Manual":
        src = " Manual"
    elif source == "ClinicalTrials":
        src = " CT.gov"
    else:
        src = ""
    if days is None:
        return "—"
    if days < 0:
        return f"⚠️ {abs(days)}d ago{src}"
    if days <= 30:
        return f"🔴 {days}d{src}"
    if days <= 90:
        return f"🟡 {days}d{src}"
    return f"🟢 {days}d{src}"


def fmt_mktcap(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    if val >= 1000:
        return f"${val/1000:.1f}B"
    return f"${val:.0f}M"


def fmt_price(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    return f"${val:.2f}"


def fmt_shares(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    if val >= 1e9:
        return f"{val/1e9:.1f}B"
    if val >= 1e6:
        return f"{val/1e6:.1f}M"
    return f"{val:,.0f}"


# ─── Screener Dashboard ───────────────────────────────────────────────────────

def render_screener() -> None:
    st.subheader("Screener Dashboard")

    catalysts = load_catalysts()
    watchlist = load_watchlist()

    # Sidebar filters (rendered from here)
    with st.sidebar:
        st.markdown("### Filters")
        cap_min = st.number_input("Min Mkt Cap ($M)", value=0, step=10, min_value=0)
        cap_max = st.number_input("Max Mkt Cap ($M)", value=2000, step=100, min_value=0)
        runway_filter = st.selectbox(
            "Runway",
            ["All", "Green (≥180d)", "Yellow (90-180d)", "Red (<90d)", "Unknown"],
        )
        sort_col = st.selectbox(
            "Sort by",
            ["Mkt Cap ($M)", "Runway (days)", "Days to Cat.", "Price"],
        )
        sort_asc = st.checkbox("Ascending", value=True)
        extra_raw = st.text_input("Add tickers (comma-sep)", placeholder="GILD, MRNA")
        st.markdown("---")
        if st.button("🔄 Refresh Data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    extra_tickers = [t.strip().upper() for t in extra_raw.split(",") if t.strip()] if extra_raw else []

    # Build universe: dynamic sources + watchlist + manually added tickers
    universe_base, dynamic_ok = get_biotech_universe()
    seen = set(universe_base)
    universe = list(universe_base)
    for t in list(watchlist.keys()) + extra_tickers:
        t = t.strip().upper()
        if t and t not in seen:
            universe.append(t)
            seen.add(t)

    # Universe info + fallback warning
    st.caption(f"Universe: **{len(universe)} tickers** (refreshed daily)")
    if not dynamic_ok:
        st.warning(
            "⚠️ Could not fetch dynamic universe (SEC EDGAR / ETF feeds unreachable). "
            "Showing static fallback list."
        )

    MAX_DISPLAY = 100

    # ── Pass 1: lightweight market-cap pre-filter ──────────────────────────────
    st.markdown(f"Screening **{len(universe)}** tickers…")
    prog1 = st.progress(0)
    passing: List[str] = []
    mc_cache: dict = {}

    for i, ticker in enumerate(universe):
        mc = get_market_cap_fast(ticker)
        mc_cache[ticker] = mc
        prog1.progress((i + 1) / len(universe))

        mc_m = (mc or 0) / 1e6
        if mc is None or (cap_min <= mc_m <= cap_max):
            passing.append(ticker)

    prog1.empty()

    # If we have more than MAX_DISPLAY, keep highest market-cap tickers
    if len(passing) > MAX_DISPLAY:
        passing.sort(key=lambda t: mc_cache.get(t) or 0, reverse=True)
        st.info(
            f"Showing top {MAX_DISPLAY} by market cap "
            f"({len(passing)} tickers passed the cap filter)."
        )
        passing = passing[:MAX_DISPLAY]

    # ── Pass 2: full data fetch for tickers that passed pre-filter ─────────────
    st.markdown(f"Loading data for **{len(passing)} tickers**…")
    prog2 = st.progress(0)
    rows = []
    for i, ticker in enumerate(passing):
        rows.append(build_screener_row(ticker, catalysts))
        prog2.progress((i + 1) / len(passing))
    prog2.empty()

    df = pd.DataFrame(rows)

    # Apply market cap filter (exact, using full .info values)
    if cap_max > 0:
        df = df[
            (df["Mkt Cap ($M)"].isna() | (df["Mkt Cap ($M)"] >= cap_min)) &
            (df["Mkt Cap ($M)"].isna() | (df["Mkt Cap ($M)"] <= cap_max))
        ]

    # Apply runway filter
    if runway_filter == "Green (≥180d)":
        df = df[df["Runway (days)"] >= 180]
    elif runway_filter == "Yellow (90-180d)":
        df = df[(df["Runway (days)"] >= 90) & (df["Runway (days)"] < 180)]
    elif runway_filter == "Red (<90d)":
        df = df[df["Runway (days)"] < 90]
    elif runway_filter == "Unknown":
        df = df[df["Runway (days)"].isna()]

    # Sort
    if sort_col in df.columns:
        df = df.sort_values(sort_col, ascending=sort_asc, na_position="last")

    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total", len(df))
    col2.metric("🟢 Runway OK", int((df["Runway (days)"] >= 180).sum()))
    col3.metric("🟡 Runway Mid", int(((df["Runway (days)"] >= 90) & (df["Runway (days)"] < 180)).sum()))
    col4.metric("🔴 Runway Low", int((df["Runway (days)"] < 90).sum()))

    st.markdown("---")

    if df.empty:
        st.info("No tickers match the current filters.")
        return

    # Build display DataFrame
    display = pd.DataFrame()
    display["Ticker"] = df["Ticker"]
    display["Name"] = df["Name"].str[:30]
    display["Price"] = df["Price"].apply(fmt_price)
    display["Mkt Cap"] = df["Mkt Cap ($M)"].apply(fmt_mktcap)
    display["Cash ($M)"] = df["Cash ($M)"].apply(
        lambda x: f"${x:.1f}M" if pd.notna(x) else "—"
    )
    display["Qtr Burn"] = df["Qtr Burn ($M)"].apply(
        lambda x: f"${x:.1f}M" if pd.notna(x) else "—"
    )
    display["Runway"] = df["Runway (days)"].apply(runway_badge)
    display["Next Catalyst"] = df["Next Catalyst"].fillna("—")
    display["Days to Cat."] = df.apply(
        lambda row: days_badge(row["Days to Cat."], row.get("Catalyst Source")),
        axis=1,
    )
    display["Last 10-Q"] = df["Last 10-Q"].fillna("—")
    display["⭐"] = df["Ticker"].apply(lambda t: "★" if t in watchlist else "")
    display["_ticker_raw"] = df["Ticker"]

    # Clickable row → detail view
    st.markdown("**Click a row to open Stock Detail →**")

    event = st.dataframe(
        display.drop(columns=["_ticker_raw"]),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        height=min(600, 55 + len(display) * 35),
    )

    selected_rows = event.selection.get("rows", []) if hasattr(event, "selection") else []
    if selected_rows:
        idx = selected_rows[0]
        selected_ticker = display.iloc[idx]["_ticker_raw"]
        st.session_state["detail_ticker"] = selected_ticker
        st.session_state["show_detail"] = True

    # Inline detail panel
    if st.session_state.get("show_detail") and st.session_state.get("detail_ticker"):
        st.markdown("---")
        render_detail(st.session_state["detail_ticker"])


# ─── Watchlist ────────────────────────────────────────────────────────────────

def render_watchlist() -> None:
    st.subheader("Watchlist")

    watchlist = load_watchlist()
    catalysts = load_catalysts()

    # Add ticker
    with st.form("add_ticker_form", clear_on_submit=True):
        cols = st.columns([2, 4, 1])
        new_ticker = cols[0].text_input("Ticker", placeholder="SAVA").upper().strip()
        new_note = cols[1].text_input("Note", placeholder="Phase 2 readout expected Q3")
        submitted = cols[2].form_submit_button("Add", use_container_width=True)
        if submitted and new_ticker:
            watchlist[new_ticker] = {"note": new_note, "added": datetime.now().strftime("%Y-%m-%d")}
            save_watchlist(watchlist)
            st.success(f"Added {new_ticker}")
            st.rerun()

    if not watchlist:
        st.info("Your watchlist is empty. Add tickers above.")
        return

    st.markdown(f"**{len(watchlist)} stocks tracked**")

    for ticker, meta in list(watchlist.items()):
        cat = catalysts.get(ticker, {})
        stock = get_stock_info(ticker)
        with st.expander(f"**{ticker}** — {stock['name']}", expanded=False):
            c1, c2, c3 = st.columns(3)
            c1.metric("Price", fmt_price(stock["price"]))
            c2.metric("Mkt Cap", fmt_mktcap((stock["market_cap"] or 0) / 1e6))
            c3.metric("Added", meta.get("added", "—"))

            # Notes
            note = st.text_area(
                "Notes", value=meta.get("note", ""), key=f"note_{ticker}",
                height=80
            )

            # Catalyst entry
            st.markdown("**Catalyst**")
            cat_cols = st.columns([2, 3, 1])
            cat_date = cat_cols[0].text_input(
                "Date (YYYY-MM-DD)", value=cat.get("next_catalyst_date", ""),
                key=f"catdate_{ticker}", placeholder="2025-06-01"
            )
            cat_label = cat_cols[1].text_input(
                "Label", value=cat.get("catalyst_label", ""),
                key=f"catlabel_{ticker}", placeholder="Phase 2 top-line data"
            )

            btn_cols = st.columns([1, 1, 4])
            if btn_cols[0].button("Save", key=f"save_{ticker}", use_container_width=True):
                watchlist[ticker]["note"] = note
                save_watchlist(watchlist)
                catalysts[ticker] = {
                    "next_catalyst_date": cat_date,
                    "catalyst_label": cat_label,
                }
                save_catalysts(catalysts)
                st.success("Saved")

            if btn_cols[1].button("Remove", key=f"rm_{ticker}", use_container_width=True):
                del watchlist[ticker]
                save_watchlist(watchlist)
                st.rerun()

            if btn_cols[2].button(
                "View Detail →", key=f"detail_{ticker}", use_container_width=False
            ):
                st.session_state["detail_ticker"] = ticker
                st.session_state["show_detail"] = True
                st.rerun()


# ─── Stock Detail ─────────────────────────────────────────────────────────────

def render_detail(ticker: str) -> None:
    ticker = ticker.upper()
    stock = get_stock_info(ticker)
    catalysts = load_catalysts()
    watchlist = load_watchlist()

    # Header row
    hcols = st.columns([6, 2])
    hcols[0].markdown(f"## {ticker} — {stock['name']}")
    in_watchlist = ticker in watchlist
    if hcols[1].button(
        "★ Remove from Watchlist" if in_watchlist else "☆ Add to Watchlist",
        use_container_width=True
    ):
        if in_watchlist:
            del watchlist[ticker]
        else:
            watchlist[ticker] = {"note": "", "added": datetime.now().strftime("%Y-%m-%d")}
        save_watchlist(watchlist)
        st.rerun()

    # Key metrics
    from src.data import get_edgar_financials
    edgar = get_edgar_financials(ticker)

    m = st.columns(5)
    m[0].metric("Price", fmt_price(stock["price"]))
    m[1].metric("Mkt Cap", fmt_mktcap((stock["market_cap"] or 0) / 1e6))
    m[2].metric("Float", fmt_shares(stock["float_shares"]))
    m[3].metric("Avg Volume", fmt_shares(stock["avg_volume"]))
    m[4].metric("52W High/Low",
        f"{fmt_price(stock['week52_high'])} / {fmt_price(stock['week52_low'])}")

    st.markdown("---")

    # Two-column layout: financials + catalyst
    left, right = st.columns(2)

    with left:
        st.markdown("#### Cash & Runway")
        if edgar["source"] == "edgar":
            runway_days = edgar["runway_days"]
            color = runway_color_css(runway_days)
            st.markdown(
                f"<div style='background:{color}22;border-left:4px solid {color};"
                f"padding:12px;border-radius:6px;margin-bottom:8px'>"
                f"<b style='color:{color}'>{runway_badge(runway_days)}</b> estimated runway"
                f"</div>",
                unsafe_allow_html=True,
            )
            rc1, rc2 = st.columns(2)
            rc1.metric("Cash Position", f"${edgar['cash']:.1f}M" if edgar["cash"] else "—")
            rc2.metric("Qtr Burn", f"${edgar['quarterly_burn']:.1f}M" if edgar["quarterly_burn"] else "—")
            st.caption(f"Source: SEC EDGAR · Last 10-Q: {edgar.get('last_filing_date', '—')}")
        else:
            st.warning("EDGAR data unavailable for this ticker.")

    with right:
        st.markdown("#### Catalyst")
        cat = catalysts.get(ticker, {})
        with st.form(f"catalyst_form_{ticker}"):
            cat_date = st.text_input(
                "Date (YYYY-MM-DD)", value=cat.get("next_catalyst_date", ""),
                placeholder="2025-09-15"
            )
            cat_label = st.text_input(
                "Event", value=cat.get("catalyst_label", ""),
                placeholder="Phase 3 interim data readout"
            )
            cat_notes = st.text_area(
                "Notes", value=cat.get("notes", ""), height=80
            )
            if st.form_submit_button("Save Catalyst", use_container_width=True):
                catalysts[ticker] = {
                    "next_catalyst_date": cat_date,
                    "catalyst_label": cat_label,
                    "notes": cat_notes,
                }
                save_catalysts(catalysts)
                st.success("Catalyst saved.")
                st.rerun()

        if cat.get("next_catalyst_date"):
            try:
                dt = datetime.strptime(cat["next_catalyst_date"], "%Y-%m-%d")
                d = (dt - datetime.now()).days
                st.info(f"{days_badge(d)} → **{cat.get('catalyst_label', 'Catalyst')}**")
            except ValueError:
                pass

    st.markdown("---")
    st.markdown("#### Price Chart (1 Year)")
    hist = get_price_history(ticker, "1y")
    if hist is not None and not hist.empty:
        _render_price_chart(ticker, hist)
    else:
        st.info("Price history unavailable.")

    # Clinical trials
    st.markdown("---")
    st.markdown("#### ClinicalTrials.gov")
    with st.spinner("Fetching clinical trials…"):
        trials = get_clinical_trials(stock["name"], ticker)

    if trials:
        trial_df = pd.DataFrame(trials)[
            ["nct_id", "title", "phase", "status", "start_date", "primary_completion"]
        ]
        trial_df.columns = ["NCT ID", "Title", "Phase", "Status", "Start", "Primary Completion"]
        st.dataframe(trial_df, use_container_width=True, hide_index=True)
    else:
        st.info("No trial data found. Try adding a catalyst manually above.")


# ─── Price chart ──────────────────────────────────────────────────────────────

def _render_price_chart(ticker: str, hist: pd.DataFrame) -> None:
    """Render a candlestick / OHLC chart with volume."""
    # Flatten MultiIndex columns if present (yfinance sometimes returns them)
    if isinstance(hist.columns, pd.MultiIndex):
        hist.columns = [col[0] for col in hist.columns]

    needed = {"Open", "High", "Low", "Close", "Volume"}
    if not needed.issubset(set(hist.columns)):
        st.info("Price chart data incomplete.")
        return

    fig = go.Figure()

    # Candlestick
    fig.add_trace(go.Candlestick(
        x=hist.index,
        open=hist["Open"],
        high=hist["High"],
        low=hist["Low"],
        close=hist["Close"],
        name=ticker,
        increasing_line_color="#00d97a",
        decreasing_line_color="#ff4b4b",
    ))

    # Volume bars on secondary y-axis
    colors = [
        "#00d97a" if c >= o else "#ff4b4b"
        for c, o in zip(hist["Close"], hist["Open"])
    ]
    fig.add_trace(go.Bar(
        x=hist.index,
        y=hist["Volume"],
        name="Volume",
        marker_color=colors,
        opacity=0.3,
        yaxis="y2",
    ))

    fig.update_layout(
        height=400,
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
        font=dict(color="#e8eaf0"),
        xaxis=dict(
            gridcolor="#1a1d2e",
            rangeslider=dict(visible=False),
        ),
        yaxis=dict(
            gridcolor="#1a1d2e",
            tickprefix="$",
            side="left",
        ),
        yaxis2=dict(
            overlaying="y",
            side="right",
            showgrid=False,
            showticklabels=False,
        ),
        legend=dict(x=0.01, y=0.99),
        margin=dict(l=0, r=0, t=20, b=0),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)
