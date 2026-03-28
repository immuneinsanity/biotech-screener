"""
Data fetching layer: yfinance, SEC EDGAR, ClinicalTrials.gov, and SQLite storage.
"""

import io
import os
import random
import re
import time
import xml.etree.ElementTree as ET
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

# ─── Curated small/micro-cap biotech universe (static fallback) ───────────────

BIOTECH_UNIVERSE_FALLBACK: List[str] = [
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

# Keep old name as alias so any other references don't break
BIOTECH_UNIVERSE = BIOTECH_UNIVERSE_FALLBACK


@st.cache_data(ttl=86400, show_spinner=False)
def get_biotech_universe() -> Tuple[List[str], bool]:
    """
    Build a dynamic biotech/pharma universe from SEC EDGAR SIC codes and ETF holdings.

    Returns:
        (tickers, dynamic_succeeded) where dynamic_succeeded is False when all
        external fetches failed and we are using only the static fallback list.

    Cached for 24 hours.
    """
    tickers: set = set()
    sources_ok = 0

    # ── Source 1: SEC EDGAR Atom feed – SIC 2836 (Pharma) + 8731 (Bio Research) ──
    for sic in ("2836", "8731"):
        try:
            url = (
                "https://www.sec.gov/cgi-bin/browse-edgar"
                f"?action=getcompany&SIC={sic}&dateb=&owner=include"
                "&count=400&search_text=&output=atom"
            )
            r = requests.get(url, headers=EDGAR_HEADERS, timeout=25)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            ns = {"a": "http://www.w3.org/2005/Atom"}
            found = 0
            for entry in root.findall("a:entry", ns):
                title_el = entry.find("a:title", ns)
                if title_el is None or not title_el.text:
                    continue
                # Titles are formatted: "COMPANY NAME (TICKER)"
                m = re.search(r"\(([A-Z]{1,5})\)\s*$", title_el.text.strip())
                if m:
                    tickers.add(m.group(1))
                    found += 1
            if found > 0:
                sources_ok += 1
        except Exception:
            pass

    # ── Source 2: XBI (SPDR S&P Biotech ETF) holdings XLSX ───────────────────
    try:
        xbi_url = (
            "https://www.ssga.com/us/en/intermediary/etfs/library-content"
            "/products/fund-data/etfs/us/holdings-daily-us-en-xbi.xlsx"
        )
        r = requests.get(xbi_url, headers={"User-Agent": EDGAR_HEADERS["User-Agent"]}, timeout=30)
        r.raise_for_status()
        raw = io.BytesIO(r.content)
        # Scan for the row that contains "Ticker" as a header
        probe = pd.read_excel(raw, header=None, nrows=10)
        header_row = None
        for idx, row in probe.iterrows():
            if any(str(v).strip().lower() == "ticker" for v in row.values):
                header_row = idx
                break
        if header_row is not None:
            raw.seek(0)
            xbi_df = pd.read_excel(raw, header=header_row)
            tcol = next((c for c in xbi_df.columns if str(c).strip().lower() == "ticker"), None)
            if tcol:
                for t in xbi_df[tcol].dropna():
                    t = str(t).strip().upper()
                    if t.isalpha() and 1 <= len(t) <= 5:
                        tickers.add(t)
                sources_ok += 1
    except Exception:
        pass

    # ── Source 3: IBB (iShares Nasdaq Biotechnology ETF) holdings CSV ─────────
    try:
        ibb_url = (
            "https://www.ishares.com/us/products/239699/ishares-nasdaq-biotechnology-etf"
            "/1467271812596.ajax?fileType=csv&fileName=IBB_holdings&dataType=fund"
        )
        r = requests.get(
            ibb_url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.ishares.com"},
            timeout=30,
        )
        r.raise_for_status()
        lines = r.text.splitlines()
        # iShares CSVs have fund-level metadata rows before the real header
        header_idx = next(
            (i for i, line in enumerate(lines) if "ticker" in line.lower()), None
        )
        if header_idx is not None:
            ibb_df = pd.read_csv(
                io.StringIO("\n".join(lines[header_idx:])), on_bad_lines="skip"
            )
            tcol = next((c for c in ibb_df.columns if str(c).strip().lower() == "ticker"), None)
            if tcol:
                for t in ibb_df[tcol].dropna():
                    t = str(t).strip().upper()
                    if t.isalpha() and 1 <= len(t) <= 5:
                        tickers.add(t)
                sources_ok += 1
    except Exception:
        pass

    # ── Always include the curated fallback list ──────────────────────────────
    tickers.update(BIOTECH_UNIVERSE_FALLBACK)

    cleaned = sorted({
        t.upper() for t in tickers
        if t and isinstance(t, str) and t.isalpha() and 1 <= len(t) <= 5
    })
    return cleaned, sources_ok > 0


@st.cache_data(ttl=3600, show_spinner=False)
def get_bulk_market_caps(tickers: tuple) -> dict:
    """Fetch market caps for many tickers using yf.Tickers in chunks of 50.
    Returns dict of {ticker: market_cap_float_or_None}"""
    result = {}
    chunk_size = 50
    for i in range(0, len(tickers), chunk_size):
        chunk = list(tickers[i:i + chunk_size])
        try:
            tickers_obj = yf.Tickers(" ".join(chunk))
            for ticker in chunk:
                try:
                    mc = tickers_obj.tickers[ticker].fast_info["marketCap"]
                    result[ticker] = float(mc) if mc else None
                except Exception:
                    result[ticker] = None
        except Exception:
            for t in chunk:
                result[t] = None
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def get_market_cap_fast(ticker: str) -> Optional[float]:
    """Lightweight market cap fetch via yfinance fast_info (skips heavy .info call)."""
    for attempt in range(3):
        try:
            if attempt > 0:
                time.sleep(2 ** attempt + random.uniform(0.2, 0.8))
            fi = yf.Ticker(ticker).fast_info
            try:
                mc = fi["marketCap"]
            except (KeyError, TypeError):
                mc = None
            return float(mc) if mc else None
        except Exception:
            if attempt == 2:
                return None
    return None

# ─── SEC EDGAR ────────────────────────────────────────────────────────────────

SEC_BASE = "https://data.sec.gov"
EDGAR_HEADERS = {
    "User-Agent": "BiotechScreener/1.0 research@biotechscreener.local",
    "Accept-Encoding": "gzip, deflate",
}

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

    Sums liquid capital across multiple XBRL line items to avoid understating
    cash for biotechs that hold capital in short-term treasuries/money markets.

    Returns:
        {
            "cash": float | None,             # in millions
            "quarterly_burn": float | None,   # in millions (absolute value)
            "runway_days": int | None,
            "last_filing_date": str | None,
            "cash_components": str | None,    # e.g. "cash+STI+MktSec"
            "source": "edgar" | "unavailable",
        }
    """
    empty = {"cash": None, "quarterly_burn": None, "runway_days": None,
             "last_filing_date": None, "cash_components": None, "source": "unavailable"}

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
        """Return (value_in_millions, end_date) for the most recent 10-Q/10-K filing."""
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
        quarterly.sort(key=lambda e: e.get("end", ""), reverse=True)
        best = quarterly[0]
        return best["val"] / 1e6, best["end"]

    def get_value_for_date(concept: str, end_date: str) -> Optional[float]:
        """Return value in millions for the given concept at a specific filing end date."""
        data = facts.get(concept, {}).get("units", {}).get("USD", [])
        for e in data:
            if (e.get("end") == end_date
                    and e.get("form") in ("10-Q", "10-K")
                    and e.get("val") is not None):
                return e["val"] / 1e6
        return None

    # ── Cash: try all-in-one concept first ───────────────────────────────────
    cash_val: Optional[float] = None
    filing_date: Optional[str] = None
    cash_components: Optional[str] = None

    # Long-term investment concepts (used in both branches below)
    _LT_CONCEPTS = [
        "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
        "DebtSecuritiesAvailableForSaleNoncurrent",
        "LongTermInvestments",
    ]

    all_in_one = latest_10q("CashCashEquivalentsAndShortTermInvestments")
    if all_in_one is not None:
        cash_val, filing_date = all_in_one
        cash_components = "cash+STI"

        # The all-in-one concept already covers cash + short-term; add long-term on top.
        # Deduplicate: take the largest non-None value (they likely report the same pool).
        lt_vals = [v for v in (get_value_for_date(c, filing_date) for c in _LT_CONCEPTS) if v is not None]
        if lt_vals:
            cash_val += max(lt_vals)
            cash_components += "+LTI"
    else:
        # Find the anchor filing date from the primary cash concept
        anchor = latest_10q("CashAndCashEquivalentsAtCarryingValue")
        if anchor is not None:
            _, filing_date = anchor

            cash_base = get_value_for_date("CashAndCashEquivalentsAtCarryingValue", filing_date)

            # Short-term investments – deduplicate overlapping concepts; take the largest.
            _ST_CONCEPTS = [
                "ShortTermInvestments",
                "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                "DebtSecuritiesAvailableForSaleCurrent",
                "MarketableSecuritiesCurrent",
            ]
            st_vals = [v for v in (get_value_for_date(c, filing_date) for c in _ST_CONCEPTS) if v is not None]
            sti_component = max(st_vals) if st_vals else None

            # Long-term investments – deduplicate; take the largest.
            lt_vals = [v for v in (get_value_for_date(c, filing_date) for c in _LT_CONCEPTS) if v is not None]
            lt_component = max(lt_vals) if lt_vals else None

            htm = get_value_for_date("HeldToMaturitySecuritiesCurrent", filing_date)

            total = 0.0
            labels = []
            if cash_base is not None:
                total += cash_base
                labels.append("cash")
            if sti_component is not None:
                total += sti_component
                labels.append("STI")
            if lt_component is not None:
                total += lt_component
                labels.append("LTI")
            if htm is not None:
                total += htm
                labels.append("HTM")

            if total > 0:
                cash_val = total
                cash_components = "+".join(labels)

    # ── Quarterly burn (operating cash flow) ─────────────────────────────────
    # XBRL 10-Q cash flow values are often YTD (Q2 = 6-month total, Q3 = 9-month).
    # Prefer entries whose period is ~1 quarter (70–100 days) to get a true
    # single-quarter figure; fall back to dividing YTD by the quarter number.
    burn_val: Optional[float] = None
    for concept in BURN_CONCEPTS:
        data = facts.get(concept, {}).get("units", {}).get("USD", [])
        if not data:
            continue

        entries_with_dates = [
            e for e in data
            if e.get("form") in ("10-Q", "10-K")
            and e.get("val") is not None
            and e.get("start") and e.get("end")
        ]
        if not entries_with_dates:
            continue

        # Annotate each entry with its duration in days
        for e in entries_with_dates:
            try:
                s = datetime.strptime(e["start"], "%Y-%m-%d")
                d = datetime.strptime(e["end"], "%Y-%m-%d")
                e["_days"] = (d - s).days
            except ValueError:
                e["_days"] = 0

        # Prefer genuine single-quarter entries (10-Q, ~91 days)
        single_q = [
            e for e in entries_with_dates
            if e.get("form") == "10-Q" and 70 <= e.get("_days", 0) <= 100
        ]
        if single_q:
            single_q.sort(key=lambda e: e.get("end", ""), reverse=True)
            recent = single_q[:2]
            avg_ocf = sum(e["val"] for e in recent) / len(recent) / 1e6
            burn_val = abs(avg_ocf) if avg_ocf < 0 else None
            break

        # Fallback: most-recent entry (could be YTD or annual), divide by quarter count
        entries_with_dates.sort(key=lambda e: e.get("end", ""), reverse=True)
        best = entries_with_dates[0]
        days = best.get("_days", 91)
        quarter_num = max(1, round(days / 91))
        single_q_val = best["val"] / quarter_num / 1e6
        burn_val = abs(single_q_val) if single_q_val < 0 else None
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
        "cash_components": cash_components,
        "source": "edgar" if cash_val is not None else "unavailable",
    }


# ─── yfinance: stock info ─────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def get_stock_info(ticker: str) -> Dict[str, Any]:
    """
    Fetch price, market cap, float, volume, and 52-week range from yfinance.
    Uses fast_info for price/mktcap/52wk to reduce rate-limit exposure.
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
    for attempt in range(3):
        try:
            if attempt > 0:
                time.sleep(2 ** attempt + random.uniform(0.2, 0.8))

            t = yf.Ticker(ticker)

            # fast_info for lightweight fields (less likely to be rate-limited)
            fi = t.fast_info
            try:
                price = fi["lastPrice"]
            except (KeyError, TypeError):
                price = None
            try:
                market_cap = fi["marketCap"]
            except (KeyError, TypeError):
                market_cap = None
            try:
                week52_high = fi["yearHigh"]
            except (KeyError, TypeError):
                week52_high = None
            try:
                week52_low = fi["yearLow"]
            except (KeyError, TypeError):
                week52_low = None

            # .info for fields not in fast_info (name, float, volume, industry)
            info: Dict[str, Any] = {}
            try:
                info = t.info or {}
            except Exception:
                pass

            return {
                "ticker": ticker,
                "name": info.get("longName") or info.get("shortName") or ticker,
                "price": price or info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose"),
                "market_cap": market_cap or info.get("marketCap"),
                "float_shares": info.get("floatShares"),
                "avg_volume": info.get("averageVolume"),
                "week52_high": week52_high or info.get("fiftyTwoWeekHigh"),
                "week52_low": week52_low or info.get("fiftyTwoWeekLow"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
            }
        except Exception:
            if attempt == 2:
                return empty
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
                if pcd_date > today and (earliest is None or pcd_date < earliest):
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
            if d > today:
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

    # Double-check: if days <= 0, treat as no upcoming catalyst
    if days_to_cat is not None and days_to_cat <= 0:
        days_to_cat = None
        next_catalyst = None
        catalyst_source = None

    mkt_cap_m = (stock["market_cap"] or 0) / 1e6  # in millions

    return {
        "Ticker": ticker,
        "Name": stock["name"],
        "Price": stock["price"],
        "Mkt Cap ($M)": round(mkt_cap_m, 1) if mkt_cap_m else None,
        "Liquidity ($M)": edgar["cash"],
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
    base, _ = get_biotech_universe()
    base = list(base)
    if extra_tickers:
        for t in extra_tickers:
            t = t.strip().upper()
            if t and t not in base:
                base.append(t)
    return base
