"""
Data fetching layer: yfinance, SEC EDGAR, ClinicalTrials.gov, and SQLite storage.
"""

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

from src.db import (
    get_catalysts as _db_get_catalysts,
    get_watchlist as _db_get_watchlist,
    remove_from_watchlist as _db_remove_watchlist,
    save_catalyst as _db_save_catalyst,
    save_to_watchlist as _db_save_watchlist,
    delete_catalyst as _db_delete_catalyst,
)

# ─── Curated small/micro-cap biotech universe ─────────────────────────────────

BIOTECH_UNIVERSE: List[str] = [
    # CNS / Neuro
    "SAVA", "AVXL", "PRAX", "XENE", "ALEC", "DNLI", "ACMR", "ACAD", "SAGE",
    # Oncology / Immunotherapy
    "AGEN", "MGNX", "IMTX", "NKTX", "JANX", "FATE", "IOVA", "RCUS", "RAPT", "TGTX",
    "EXEL", "KRTX", "RXRX", "ATXS", "IOVA",
    # Gene Editing / Cell Therapy
    "NTLA", "BEAM", "EDIT", "VYGR", "GRPH", "CRSP",
    # Rare Disease / Hematology
    "KROS", "PTGX", "AGIO", "FOLD", "ACRS", "RARE", "BLUE", "SRPT", "BMRN", "ALNY",
    # Autoimmune / Inflammation
    "IMVT", "KYMR", "VKTX",
    # Cardiovascular / Metabolic
    "VERV", "LXRX", "IONS",
    # Ophthalmology / Dermatology
    "TARS", "ARQT", "GKOS",
    # Infectious Disease / mRNA / Other
    "OCGN", "NVAX", "CDTX", "MRNA",
    # Drug Delivery / Platform
    "HALO", "FULC", "ATOS", "PHAT", "RVNC",
    # Surgery / MedTech-adjacent
    "TMDX", "NRIX", "NBIX", "SGEN",
]

# ─── SEC EDGAR ────────────────────────────────────────────────────────────────

SEC_BASE = "https://data.sec.gov"
EDGAR_HEADERS = {
    "User-Agent": "BiotechScreener/1.0 research@biotechscreener.local",
    "Accept-Encoding": "gzip, deflate",
}

# Cash concepts to try, in preference order
CASH_CONCEPTS = [
    "CashCashEquivalentsAndShortTermInvestments",
    "CashAndCashEquivalentsAtCarryingValue",
    "CashAndCashEquivalentsAndRestrictedCashAndRestrictedCashEquivalents",
]
BURN_CONCEPTS = [
    "NetCashProvidedByUsedInOperatingActivities",
]

# ─── Local storage (SQLite-backed) ───────────────────────────────────────────

def load_watchlist() -> dict:
    return _db_get_watchlist()


def save_watchlist(data: dict) -> None:
    existing = _db_get_watchlist()
    for ticker in existing:
        if ticker not in data:
            _db_remove_watchlist(ticker)
    for ticker, meta in data.items():
        _db_save_watchlist(ticker, meta.get("note", ""), meta.get("added", ""))


def load_catalysts() -> dict:
    return _db_get_catalysts()


def save_catalysts(data: dict) -> None:
    existing = _db_get_catalysts()
    for ticker in existing:
        if ticker not in data:
            _db_delete_catalyst(ticker)
    for ticker, cat in data.items():
        _db_save_catalyst(
            ticker,
            cat.get("next_catalyst_date", ""),
            cat.get("catalyst_label", ""),
            cat.get("notes", ""),
        )


# ─── SEC EDGAR: CIK lookup ────────────────────────────────────────────────────

@st.cache_data(ttl=86400, show_spinner=False)
def get_ticker_cik_map() -> Dict[str, str]:
    """Fetch the full ticker→CIK mapping from SEC (cached 24 h)."""
    try:
        url = "https://www.sec.gov/files/company_tickers.json"
        r = requests.get(url, headers=EDGAR_HEADERS, timeout=15)
        r.raise_for_status()
        raw = r.json()
        return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in raw.values()}
    except Exception:
        return {}


def get_cik(ticker: str) -> Optional[str]:
    cik_map = get_ticker_cik_map()
    return cik_map.get(ticker.upper())


# ─── SEC EDGAR: financials ────────────────────────────────────────────────────

@st.cache_data(ttl=43200, show_spinner=False)
def get_edgar_financials(ticker: str) -> Dict[str, Any]:
    """
    Pull the most recent 10-Q cash position and quarterly operating burn
    from SEC EDGAR XBRL facts API.

    Returns:
        {
            "cash": float | None,          # in millions
            "quarterly_burn": float | None, # in millions (absolute value)
            "runway_days": int | None,
            "last_filing_date": str | None,
            "source": "edgar" | "unavailable",
        }
    """
    empty = {"cash": None, "quarterly_burn": None, "runway_days": None,
             "last_filing_date": None, "source": "unavailable"}

    cik = get_cik(ticker)
    if not cik:
        return empty

    try:
        url = f"{SEC_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
        r = requests.get(url, headers=EDGAR_HEADERS, timeout=20)
        r.raise_for_status()
        facts = r.json().get("facts", {}).get("us-gaap", {})
    except Exception:
        return empty

    def latest_10q(concept: str) -> Optional[Tuple[float, str]]:
        """Return (value_in_millions, end_date) for the most recent 10-Q filing."""
        data = facts.get(concept, {}).get("units", {}).get("USD", [])
        if not data:
            return None
        quarterly = [
            e for e in data
            if e.get("form") in ("10-Q", "10-K")
            and e.get("val") is not None
        ]
        if not quarterly:
            return None
        # Sort by end date descending, prefer 10-Q
        quarterly.sort(key=lambda e: e.get("end", ""), reverse=True)
        best = quarterly[0]
        return best["val"] / 1e6, best["end"]

    # Cash
    cash_val: Optional[float] = None
    filing_date: Optional[str] = None
    for concept in CASH_CONCEPTS:
        result = latest_10q(concept)
        if result is not None:
            cash_val, filing_date = result
            break

    # Quarterly burn (operating cash flow – typically negative for clinical-stage)
    burn_val: Optional[float] = None
    for concept in BURN_CONCEPTS:
        data = facts.get(concept, {}).get("units", {}).get("USD", [])
        if not data:
            continue
        # Get last two quarterly filings to average burn
        quarterly = [
            e for e in data
            if e.get("form") in ("10-Q",) and e.get("val") is not None
        ]
        quarterly.sort(key=lambda e: e.get("end", ""), reverse=True)
        recent = quarterly[:2]
        if recent:
            avg_ocf = sum(e["val"] for e in recent) / len(recent) / 1e6
            burn_val = abs(avg_ocf) if avg_ocf < 0 else None
        break

    runway_days: Optional[int] = None
    if cash_val is not None and burn_val and burn_val > 0:
        quarters = cash_val / burn_val
        runway_days = int(quarters * 91)

    return {
        "cash": round(cash_val, 1) if cash_val is not None else None,
        "quarterly_burn": round(burn_val, 1) if burn_val is not None else None,
        "runway_days": runway_days,
        "last_filing_date": filing_date,
        "source": "edgar" if cash_val is not None else "unavailable",
    }


# ─── yfinance: stock info ─────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def get_stock_info(ticker: str) -> Dict[str, Any]:
    """
    Fetch price, market cap, float, volume, and 52-week range from yfinance.
    Returns a dict with None for unavailable fields – never raises.
    """
    empty = {
        "ticker": ticker,
        "name": ticker,
        "price": None,
        "market_cap": None,
        "float_shares": None,
        "avg_volume": None,
        "week52_high": None,
        "week52_low": None,
        "sector": None,
        "industry": None,
    }
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}

        price = (
            info.get("currentPrice")
            or info.get("regularMarketPrice")
            or info.get("previousClose")
        )
        return {
            "ticker": ticker,
            "name": info.get("longName") or info.get("shortName") or ticker,
            "price": price,
            "market_cap": info.get("marketCap"),
            "float_shares": info.get("floatShares"),
            "avg_volume": info.get("averageVolume"),
            "week52_high": info.get("fiftyTwoWeekHigh"),
            "week52_low": info.get("fiftyTwoWeekLow"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
        }
    except Exception:
        return empty


@st.cache_data(ttl=3600, show_spinner=False)
def get_price_history(ticker: str, period: str = "1y") -> Optional[pd.DataFrame]:
    """Return OHLCV DataFrame or None on failure."""
    try:
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if df.empty:
            return None
        return df
    except Exception:
        return None


# ─── ClinicalTrials.gov ───────────────────────────────────────────────────────

@st.cache_data(ttl=43200, show_spinner=False)
def get_clinical_trials(company_name: str, ticker: str = "") -> List[Dict[str, Any]]:
    """
    Query ClinicalTrials.gov v2 API for studies sponsored by a company.
    Returns list of simplified trial dicts.
    """
    query = ticker if ticker else company_name.split()[0]
    try:
        url = "https://clinicaltrials.gov/api/v2/studies"
        params = {
            "query.spons": query,
            "filter.overallStatus": "RECRUITING,NOT_YET_RECRUITING,ACTIVE_NOT_RECRUITING,COMPLETED",
            "pageSize": 10,
            "sort": "LastUpdatePostDate:desc",
            "fields": "NCTId,BriefTitle,Phase,OverallStatus,StartDate,PrimaryCompletionDate,LeadSponsorName",
        }
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        studies = r.json().get("studies", [])
        result = []
        for s in studies:
            proto = s.get("protocolSection", {})
            id_mod = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            design_mod = proto.get("designModule", {})
            result.append({
                "nct_id": id_mod.get("nctId", ""),
                "title": id_mod.get("briefTitle", "")[:80],
                "phase": ", ".join(design_mod.get("phases", [])) or "N/A",
                "status": status_mod.get("overallStatus", ""),
                "start_date": status_mod.get("startDateStruct", {}).get("date", ""),
                "primary_completion": status_mod.get("primaryCompletionDateStruct", {}).get("date", ""),
            })
        return result
    except Exception:
        return []


# ─── Catalyst date resolution ─────────────────────────────────────────────────

@st.cache_data(ttl=43200, show_spinner=False)
def _get_ct_primary_completion(ticker: str, company_name: str) -> Optional[str]:
    """
    Query ClinicalTrials.gov for the earliest future Phase 2/3 primary completion
    date from active trials sponsored by this company.
    Returns a date string "YYYY-MM-DD" or None.
    """
    today = datetime.now().date()
    query = ticker if ticker else company_name.split()[0]
    try:
        url = "https://clinicaltrials.gov/api/v2/studies"
        params = {
            "query.spons": query,
            "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING",
            "pageSize": 20,
            "fields": "NCTId,Phase,OverallStatus,PrimaryCompletionDate",
        }
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        studies = r.json().get("studies", [])

        earliest = None
        for s in studies:
            proto = s.get("protocolSection", {})
            design_mod = proto.get("designModule", {})
            status_mod = proto.get("statusModule", {})
            phases = design_mod.get("phases", [])

            if not any(p in ("PHASE2", "PHASE3") for p in phases):
                continue

            pcd = status_mod.get("primaryCompletionDateStruct", {}).get("date", "")
            if not pcd:
                continue

            try:
                fmt = "%Y-%m" if len(pcd) == 7 else "%Y-%m-%d"
                pcd_date = datetime.strptime(pcd, fmt).date()
                if pcd_date >= today and (earliest is None or pcd_date < earliest):
                    earliest = pcd_date
            except ValueError:
                continue

        return earliest.strftime("%Y-%m-%d") if earliest else None
    except Exception:
        return None


def get_next_catalyst_date(
    ticker: str, company_name: str, catalysts: dict
) -> Tuple[Optional[str], Optional[str]]:
    """
    Return (date_str, source) for the nearest upcoming catalyst.
    source is 'Manual', 'ClinicalTrials', or None.
    Checks manually entered DB dates first; falls back to CT.gov completion dates.
    """
    today = datetime.now().date()

    cat_entry = catalysts.get(ticker.upper(), {})
    manual_date = cat_entry.get("next_catalyst_date", "")
    if manual_date:
        try:
            d = datetime.strptime(manual_date, "%Y-%m-%d").date()
            if d >= today:
                return manual_date, "Manual"
        except ValueError:
            pass

    ct_date = _get_ct_primary_completion(ticker, company_name)
    if ct_date:
        return ct_date, "ClinicalTrials"

    return None, None


# ─── Screener DataFrame builder ───────────────────────────────────────────────

def build_screener_row(ticker: str, catalysts: dict) -> Dict[str, Any]:
    """Build a single screener row (stock info + EDGAR financials + catalyst)."""
    stock = get_stock_info(ticker)
    edgar = get_edgar_financials(ticker)

    next_catalyst, catalyst_source = get_next_catalyst_date(ticker, stock["name"], catalysts)
    cat_label = catalysts.get(ticker.upper(), {}).get("catalyst_label", "")

    days_to_cat: Optional[int] = None
    if next_catalyst:
        try:
            cat_date = datetime.strptime(next_catalyst, "%Y-%m-%d")
            days_to_cat = (cat_date - datetime.now()).days
        except ValueError:
            pass

    mkt_cap_m = (stock["market_cap"] or 0) / 1e6  # in millions

    return {
        "Ticker": ticker,
        "Name": stock["name"],
        "Price": stock["price"],
        "Mkt Cap ($M)": round(mkt_cap_m, 1) if mkt_cap_m else None,
        "Cash ($M)": edgar["cash"],
        "Qtr Burn ($M)": edgar["quarterly_burn"],
        "Runway (days)": edgar["runway_days"],
        "Last 10-Q": edgar["last_filing_date"],
        "Next Catalyst": cat_label or next_catalyst or "",
        "Catalyst Date": next_catalyst or "",
        "Days to Cat.": days_to_cat,
        "Catalyst Source": catalyst_source,
        "_float": stock["float_shares"],
        "_avg_vol": stock["avg_volume"],
        "_52wk_high": stock["week52_high"],
        "_52wk_low": stock["week52_low"],
        "_edgar_source": edgar["source"],
    }


def get_full_universe(extra_tickers: Optional[List[str]] = None) -> List[str]:
    """Return deduplicated universe + any watchlist/extra tickers."""
    base = list(BIOTECH_UNIVERSE)
    if extra_tickers:
        for t in extra_tickers:
            t = t.strip().upper()
            if t and t not in base:
                base.append(t)
    return base
