"""Bulletproof FX Engine: US Treasury (Primary) with Open-ER Fallback."""
import pandas as pd
import requests
import streamlit as st
import logging

log = logging.getLogger(__name__)

TREASURY_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/rates_of_exchange"

@st.cache_data(ttl=86_400, show_spinner=False)
def _get_treasury_rate(date_str, currency):
    if pd.isna(date_str) or currency == "USD":
        return 1.0
    try:
        dt = pd.to_datetime(date_str).date()
        mapping = {
            "EUR": "Euro Zone-Euro", "GBP": "United Kingdom-Pound", 
            "JPY": "Japan-Yen", "THB": "Thailand-Baht"
        }
        treasury_name = mapping.get(currency.upper(), f"Unknown-{currency}")
        
        params = {
            "fields": "exchange_rate,record_date",
            "filter": f"record_date:lte:{dt.isoformat()},country_currency_desc:like:{treasury_name}",
            "sort": "-record_date",
            "page[size]": "1",
        }
        r = requests.get(TREASURY_URL, params=params, timeout=5)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data:
                rate = float(data[0]["exchange_rate"])
                return 1.0 / rate if rate else None
    except Exception:
        pass
    return None

@st.cache_data(ttl=86_400, show_spinner=False)
def _get_fallback_rate(date_str, currency):
    if currency == "USD":
        return 1.0
    try:
        dt = pd.to_datetime(date_str).date().isoformat()
        url = f"https://open.er-api.com/v6/historical/{dt}?base=USD&symbols={currency}"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get("result") == "success" and "rates" in data:
                return data["rates"].get(currency)
    except Exception:
        pass
    return None

def apply_fx_to_dataframe(df):
    df = df.copy()
    df["Original Amount"] = pd.to_numeric(df["Original Amount"], errors="coerce").fillna(0)

    unique_pairs = df[["Date", "Currency"]].drop_duplicates()
    rate_lookup = {}

    progress = st.progress(0.0, text="Fetching historical FX rates (Treasury + Fallback)...")
    for i, (_, row) in enumerate(unique_pairs.iterrows()):
        key = (str(row["Date"]), str(row["Currency"]))
        
        # Try Treasury first for IRS compliance
        rate = _get_treasury_rate(row["Date"], row["Currency"])
        
        # If Treasury fails, use Fallback API
        if rate is None and row["Currency"] != "USD":
            rate = _get_fallback_rate(row["Date"], row["Currency"])
            
        rate_lookup[key] = rate
        progress.progress((i + 1) / len(unique_pairs))
    progress.empty()

    df["FX Rate"] = df.apply(lambda r: rate_lookup.get((str(r["Date"]), str(r["Currency"]))), axis=1)
    df["USD Amount"] = df.apply(lambda r: r["Original Amount"] * r["FX Rate"] if r["FX Rate"] is not None else None, axis=1)
    return df
