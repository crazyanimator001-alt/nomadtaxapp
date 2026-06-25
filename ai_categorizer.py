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


def _get_client() -> OpenAI:
    key = get_openai_key()
    if not key:
        st.error("OpenAI API key missing. Add it in the sidebar or .streamlit/secrets.toml.")
        st.stop()
    return OpenAI(api_key=key)


def _call_with_retry(client: OpenAI, prompt: str) -> dict:
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role
