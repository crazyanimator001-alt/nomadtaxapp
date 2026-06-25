"""Global config: constants, CSS, secrets."""
import streamlit as st

# 2024 IRS limits
FEIE_LIMIT_2024 = 126500
FBAR_THRESHOLD = 10000
SUBSTANTIAL_PRESENCE_THRESHOLD = 183
RESIDENCY_DAY_THRESHOLD = 183

REQUIRED_CSV_COLUMNS = {"Date", "Description", "Amount", "Currency"}
MAX_ROWS = 1000

CUSTOM_CSS = """
<style>
 footer, header {visibility: hidden;}
.block-container {padding-top: 2rem; max-width: 1200px;}
h1, h2, h3 {color: #0f172a; font-family: -apple-system, sans-serif;}
.stButton>button {
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
    color: white; border-radius: 10px; border: none;
    padding: 0.6em 1.4em; font-weight: 600; transition: all 0.2s;
}
.stButton>button:hover {transform: translateY(-1px); box-shadow: 0 4px 12px rgba(99,102,241,0.3);}
[data-testid="stMetricValue"] {color: #6366f1; font-weight: 800;}
</style>
"""


def get_openai_key():
    """Prefer st.secrets, fall back to user-supplied UI key."""
    try:
        return st.secrets["OPENAI_API_KEY"]
    except (KeyError, FileNotFoundError):
        return st.session_state.get("user_openai_key")
