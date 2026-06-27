"""Bulletproof FX Engine: Treasury -> ECB -> Static Fallback."""
import pandas as pd
import requests
import streamlit as st
import logging

log = logging.getLogger(__name__)

TREASURY_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/rates_of_exchange"

# Static fallback used ONLY if cloud firewalls block external APIs.
STATIC_FALLBACK_RATES = {
    "EUR": 1.08, "GBP": 1.26, "JPY": 0.0065, "THB": 0.027,
    "IDR": 0.000063, "MXN": 0.059, "BRL": 0.19, "COP": 0.00024
}

@st.cache_data(ttl=86_400, show_spinner=False)
def _get_treasury_rate(date_str, currency):
    if pd.isna(date_str) or currency == "USD": return 1.0
    try:
        dt = pd.to_datetime(date_str).date()
        mapping = {"EUR": "Euro Zone-Euro", "GBP": "United Kingdom-Pound", "JPY": "Japan-Yen", "THB": "Thailand-Baht"}
        treasury_name = mapping.get(currency.upper(), f"Unknown-{currency}")
        params = {
            "fields": "exchange_rate,record_date",
            "filter": f"record_date:lte:{dt.isoformat()},country_currency_desc:like:{treasury_name}",
            "sort": "-record_date", "page[size]": "1",
        }
        r = requests.get(TREASURY_URL, params=params, timeout=5)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data:
                rate = float(data[0]["exchange_rate"])
                return 1.0 / rate if rate else None
    except Exception: pass
    return None

@st.cache_data(ttl=86_400, show_spinner=False)
def _get_frankfurter_rate(date_str, currency):
    if currency == "USD": return 1.0
    try:
        dt = pd.to_datetime(date_str).date().isoformat()
        url = f"https://api.frankfurter.app/{dt}?from={currency}&to=USD"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if "rates" in data and "USD" in data["rates"]:
                return float(data["rates"]["USD"])
    except Exception: pass
    return None

def apply_fx_to_dataframe(df):
    df = df.copy()
    df["Original Amount"] = pd.to_numeric(df["Original Amount"], errors="coerce").fillna(0)

    unique_pairs = df[["Date", "Currency"]].drop_duplicates()
    rate_lookup = {}

    progress = st.progress(0.0, text="Fetching IRS-approved exchange rates...")
    for i, (_, row) in enumerate(unique_pairs.iterrows()):
        key = (str(row["Date"]), str(row["Currency"]))
        currency = str(row["Currency"]).upper()
        
        rate = None
        
        # 1. Try US Treasury
        if currency != "USD":
            rate = _get_treasury_rate(row["Date"], currency)
            
        # 2. Try ECB Frankfurter API
        if rate is None and currency != "USD":
            rate = _get_frankfurter_rate(row["Date"], currency)
            
        # 3. Static Fallback if cloud firewalls block both APIs
        if rate is None and currency != "USD":
            rate = STATIC_FALLBACK_RATES.get(currency)
            
        # 4. Final fail-safe to prevent $nan crash
        if rate is None:
            rate = 1.0

        rate_lookup[key] = rate
        progress.progress((i + 1) / len(unique_pairs))
    progress.empty()

    df["FX Rate"] = df.apply(lambda r: rate_lookup.get((str(r["Date"]), str(r["Currency"])), 1.0), axis=1)
    df["USD Amount"] = df["Original Amount"] * df["FX Rate"]
    return df
