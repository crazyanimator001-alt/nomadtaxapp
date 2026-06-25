import json
import logging
import re
import pandas as pd
import streamlit as st
import google.generativeai as genai
from config import get_openai_key, MAX_ROWS

log = logging.getLogger(__name__)
BATCH_SIZE = 15

SYSTEM_PROMPT = (
    "You are a tax accountant for US digital nomads filing Form 2555 and Form 1116. "
    "Output strictly valid JSON. Categories MUST start with 'Business:' or 'Personal:'. "
    "Flag foreign-earned income explicitly. Be conservative; when unsure, mark Personal."
)

USER_PROMPT_TEMPLATE = 'Categorize these transactions. Return a JSON object with key "results" containing a list of objects with: id (int), category (str), reasoning (1 sentence), is_foreign_earned_income (bool), foreign_tax_paid (bool). Transactions: {data}'


def _get_gemini_client():
    key = st.secrets.get("GEMINI_API_KEY") or st.session_state.get("user_openai_key")
    if not key:
        st.error("Google Gemini API key missing. Add it in Streamlit Secrets as GEMINI_API_KEY.")
        st.stop()
    
    genai.configure(api_key=key)
    return genai.GenerativeModel(
        model_name='gemini-pro',
        system_instruction=SYSTEM_PROMPT
    )


def _call_gemini(model, prompt):
    try:
        response = model.generate_content(prompt)
        
        # Gemini sometimes wraps JSON in markdown. We strip it.
        text = response.text
        text = re.sub(r'^```json\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        
        return json.loads(text)
    except Exception as e:
        log.warning("Gemini API failed: %s", e)
        raise


def categorize_transactions(df):
    if len(df) > MAX_ROWS:
        st.error("CSV has " + str(len(df)) + " rows; max is " + str(MAX_ROWS) + ".")
        st.stop()

    model = _get_gemini_client()
    all_results = {}

    progress = st.progress(0.0, text="AI categorizing transactions via Google Gemini...")
    for i in range(0, len(df), BATCH_SIZE):
        batch = df.iloc[i : i + BATCH_SIZE]
        payload = [
            {"id": int(idx), "desc": str(row["Description"])[:200], "amount": float(row["Amount"])}
            for idx, row in batch.iterrows()
        ]
        raw = _call_gemini(model, USER_PROMPT_TEMPLATE.format(data=json.dumps(payload)))
        
        items = raw.get("results") or raw.get("data") or raw.get("transactions") or []
        if not isinstance(items, list):
            items = []
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
            "Foreign Tax Paid": bool(ai.get("foreign_tax_paid", False)),
        })
    return pd.DataFrame(rows)
