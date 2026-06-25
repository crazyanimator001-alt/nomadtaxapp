"""OpenAI-powered transaction categorizer with batching and retry."""
import json
import logging
import time
import pandas as pd
import streamlit as st
from openai import OpenAI, OpenAIError
from config import get_openai_key, MAX_ROWS

log = logging.getLogger(__name__)
BATCH_SIZE = 20
MAX_RETRIES = 3

SYSTEM_PROMPT = (
    "You are a tax accountant for US digital nomads filing Form 2555 and Form 1116. "
    "Output strictly valid JSON. Categories MUST start with 'Business:' or 'Personal:'. "
    "Flag foreign-earned income explicitly. Be conservative; when unsure, mark Personal."
)

USER_PROMPT_TEMPLATE = """Categorize these transactions. Return a JSON object with key "results" \
containing a list of objects with: id (int), category (str), reasoning (1 sentence), \
is_foreign_earned_income (bool), foreign_tax_paid (bool).

Transactions:
{data}"""


def _get_client():
    key = get_openai_key()
    if not key:
        st.error("OpenAI API key missing. Add it in the sidebar or .streamlit/secrets.toml.")
        st.stop()
    return OpenAI(api_key=key)


def _call_with_retry(client, prompt):
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
                temperature=0.1,
            )
            return json.loads(resp.choices[0].message.content)
        except (OpenAIError, json.JSONDecodeError) as e:
            log.warning("OpenAI attempt %d failed: %s", attempt + 1, e)
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(2 ** attempt)
    return {}


def categorize_transactions(df):
    if len(df) > MAX_ROWS:
        st.error(f"CSV has {len(df)} rows; max is {MAX_ROWS}. Split your file.")
        st.stop()

    client = _get_client()
    all_results = {}

    progress = st.progress(0.0, text="AI categorizing transactions...")
    for i in range(0, len(df), BATCH_SIZE):
        batch = df.iloc[i : i + BATCH_SIZE]
        payload = [
            {"id": int(idx), "desc": str(row["Description"])[:200], "amount": float(row["Amount"])}
            for idx, row in batch.iterrows()
        ]
        raw = _call_with_retry(client, USER_PROMPT_TEMPLATE.format(data=json.dumps(payload)))
        items = raw.get("results") or raw.get("data") or raw.get("transactions") or []
        if not isinstance(items, list): items = []
        for item in items:
            if isinstance(item, dict) and "id" in item:
                all_results[str(item["id"])] = item
        progress.progress(min(1.0, (i + BATCH_SIZE) / len(df)))
    progress.empty()

    return _merge_results(df, all_results)


def _merge_results(df, ai_map):
    rows = []
    for idx, row in df.iterrows():
        ai = ai_map.get(str(idx), {})
        rows.append({
            "Date": row.get("Date", ""),
            "Description": row.get("Description", ""),
            "Original Amount": row.get("Amount", 0),
            "Currency": str(row.get("Currency", "USD")).upper(),
            "Category": ai.get("category", "Personal: Uncategorized"),
            "Reasoning": ai.get("reasoning", "AI skipped this row"),
            "Foreign Earned Income": bool(ai.get("is_foreign_earned_income", False)),
            "Foreign Tax Paid": bool(ai.get("foreign
