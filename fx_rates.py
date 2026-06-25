"""IRS-approved FX rates via US Treasury Fiscal Data API."""
import pandas as pd
import requests
import streamlit as st
import logging

log = logging.getLogger(__name__)

TREASURY_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
    "/v1/accounting/od/rates_of_exchange"
)


@st.cache_data(ttl=86_400, show_spinner=False)
def get_treasury_rate(date_str: str, from_currency: str) -> float | None:
    if pd.isna(date_str) or from_currency == "USD":
        return 1.0

    try:
        dt = pd.to_datetime(date_str).date()
    except (ValueError, TypeError):
        return None

    params = {
        "fields": "country_currency_desc,exchange_rate,record_date",
        "filter": (
            f"record_date:lte:{dt.isoformat()},"
            f"country_currency_desc:like:{_currency_to_treasury_name(from_currency)}"
        ),
        "sort": "-record_date",
        "page[size]": "1",
    }

    try:
        r = requests.get(TREASURY_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return None
        rate_foreign_per_usd = float(data[0]["exchange_rate"])
        return 1.0 / rate_foreign_per_usd if rate_foreign_per_usd else None
    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning("Treasury FX fetch failed for %s %s: %s", dt, from_currency, e)
        return None


def _currency_to_treasury_name(code: str) -> str:
    mapping = {
        "EUR": "Euro Zone-Euro", "GBP": "United Kingdom-Pound", "JPY": "Japan-Yen",
        "CAD": "Canada-Dollar", "AUD": "Australia-Dollar", "MXN": "Mexico-Peso",
        "THB": "Thailand-Baht", "PHP": "Philippines-Peso", "IDR": "Indonesia-Rupiah",
        "VND": "Vietnam-Dong", "BRL": "Brazil-Real", "ARS": "Argentina-Peso",
        "COP": "Colombia-Peso", "CRC": "Costa Rica-Colon",
    }
    return mapping.get(code.upper(), f"Unknown-{code}")


def apply_fx_to_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Original Amount"] = pd.to_numeric(df["Original Amount"], errors="coerce").fillna(0)

    unique_pairs = df[["Date", "Currency"]].drop_duplicates()
    rate_lookup: dict[tuple, float | None] = {}

    progress = st.progress(0.0, text="Fetching IRS-approved exchange rates...")
    for i, (_, row) in enumerate(unique_pairs.iterrows()):
        key = (str(row["Date"]), str(row["Currency"]))
        rate_lookup[key] = get_treasury_rate(row["Date"], row["Currency"])
        progress.progress((i + 1) / len(unique_pairs))
    progress.empty()

    df["FX Rate"] = df.apply(
        lambda r: rate_lookup.get((str(r["Date"]), str(r["Currency"]))), axis=1
    )
    df["USD Amount"] = df.apply(
        lambda r: r["Original Amount"] * r["FX Rate"] if r["FX Rate"] is not None else None, axis=1
    )
    return df
