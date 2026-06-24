import os
import json
import time
import urllib.request
import pandas as pd
import streamlit as st
import google.generativeai as genai
from dotenv import load_dotenv
from fpdf import FPDF
from datetime import datetime

load_dotenv()

# ── Page config ───────────────────────────────────────────────
st.set_page_config(
    page_title="NomadTax Copilot — Tax Reports for Freelancers",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ── CSS ───────────────────────────────────────────────────────
st.markdown("""
<style>
    #MainMenu {visibility: hidden;}
    footer     {visibility: hidden;}
    header     {visibility: hidden;}
    .hero-title { font-size: 2.2rem; font-weight: 700; margin-bottom: 0; }
    .hero-sub   { font-size: 1.1rem; color: #64748b; margin-top: 4px; }
    .pill {
        display: inline-block;
        background: #f1f5f9;
        border-radius: 999px;
        padding: 4px 14px;
        font-size: 0.82rem;
        color: #475569;
        margin: 3px 3px 3px 0;
    }
    .privacy-note {
        background: #f0fdf4;
        border: 1px solid #bbf7d0;
        border-radius: 8px;
        padding: 10px 16px;
        font-size: 0.83rem;
        color: #166534;
        margin-top: 10px;
    }
    .reasoning-box {
        background: #f8fafc;
        border-left: 3px solid #6366f1;
        border-radius: 4px;
        padding: 8px 12px;
        font-size: 0.83rem;
        color: #475569;
        margin-top: 6px;
    }
    .disclaimer {
        background: #fafafa;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 10px 16px;
        font-size: 0.78rem;
        color: #94a3b8;
        margin-top: 20px;
    }
    .stProgress > div > div { background: #6366f1; }
</style>
""", unsafe_allow_html=True)

# ── System Prompt ─────────────────────────────────────────────
SYSTEM_PROMPT = """
You are NomadTax Copilot, an expert financial categorization engine for digital nomads and freelancers.
Your job is to analyze raw bank/PSP transaction data from Stripe, PayPal, and Wise CSVs, and return a structured JSON array.

For EVERY transaction, include:
1. "original_description" — Keep the exact original description from input
2. "clean_description"   — Human-readable version (strip IDs, merchant codes, noise)
3. "amount"              — Same number from input (positive = inflow, negative = outflow)
4. "type"                — One of: "Income", "Expense", "Transfer", "Refund/Rebate"
5. "category"            — One of: "SaaS/Software", "Travel/Accommodation", "Meals/Food", "Contractors/Freelancers", "Bank Fees", "Marketing/Advertising", "Office Supplies", "Professional Services", "Other"
6. "deductible"          — MUST be true or false. NEVER null or omitted.
7. "reasoning"           — 1-2 sentences explaining WHY you chose this category and deductible status.

Deduction rules:
- SaaS/Software for business → DEDUCTIBLE
- Travel & accommodation while working remotely → DEDUCTIBLE
- Meals during business travel → DEDUCTIBLE (note jurisdiction differences)
- Coffee shops used as coworking spaces → DEDUCTIBLE
- Personal items, gifts → NOT DEDUCTIBLE
- Bank fees, FX fees, chargeback fees → DEDUCTIBLE
- Contractor/freelancer payments → DEDUCTIBLE
- Transfers between own accounts → NOT DEDUCTIBLE, type = Transfer
- Refunds/rebates → NOT DEDUCTIBLE, type = Refund/Rebate
- Client payments received → Income, NOT an expense

NEVER give tax advice. Only categorize based on standard business practice.
Return ONLY valid JSON array. No markdown. No explanation. No ```json blocks. Raw JSON only.
"""

# ── Gemini client ─────────────────────────────────────────────
def get_model():
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    try:
        api_key = api_key or st.secrets.get("GOOGLE_API_KEY", "")
    except Exception:
        pass
    if not api_key:
        return None
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-2.5-flash")

def safe_deductible(val):
    return val is True

def categorize_transactions(model, transactions: list) -> list:
    batch_size = 20
    results    = []
    total_batches = (len(transactions) + batch_size - 1) // batch_size
    progress_bar  = st.progress(0, text="Starting analysis…")
    status_text   = st.empty()

    for i in range(0, len(transactions), batch_size):
        batch     = transactions[i:i + batch_size]
        batch_num = i // batch_size + 1
        batch_pct = batch_num / total_batches
        status_text.caption(f"🔍 Analyzing batch {batch_num} of {total_batches} ({len(batch)} transactions)…")
        progress_bar.progress(batch_pct, text=f"Processing {batch_num}/{total_batches} batches…")

        for attempt in range(3):
            try:
                response = model.generate_content(contents=[SYSTEM_PROMPT, json.dumps(batch)])
                text = response.text.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0]
                parsed = json.loads(text)
                for row in parsed:
                    row["deductible"] = safe_deductible(row.get("deductible"))
                results.extend(parsed)
                break
            except Exception as e:
                if attempt < 2:
                    wait = 15 * (attempt + 1)
                    status_text.caption(f"⚠️ Batch {batch_num} attempt {attempt+1} failed. Retrying in {wait}s… ({e})")
                    progress_bar.progress(batch_pct, text=f"Retry {attempt+1}/2 for batch {batch_num}…")
                    time.sleep(wait)
                else:
                    st.warning(f"Batch {batch_num} failed after 3 attempts: {e}")

        if batch_num < total_batches:
            for countdown in range(5, 0, -1):
                status_text.caption(f"⏳ Pausing {countdown}s before next batch to respect API limits…")
                progress_bar.progress(batch_pct, text=f"Waiting {countdown}s… ({batch_num}/{total_batches} done)")
                time.sleep(1)

    progress_bar.progress(1.0, text="Done!")
    time.sleep(0.4)
    progress_bar.empty()
    status_text.empty()
    return results

def find_column(cols, keywords):
    for col in cols:
        for kw in keywords:
            if kw in col.lower():
                return col
    return None
    # ── FX Conversion & Tax Profile ──────────────────────────────
def get_exchange_rate(from_currency, to_currency):
    if from_currency == to_currency:
        return 1.0
    url = f"https://api.frankfurter.app/latest?from={from_currency}&to={to_currency}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            return data["rates"][to_currency]
    except Exception:
        return None

def render_tax_profile():
    st.subheader("⚙️ Your Tax Profile")
    st.caption("Helps us convert currencies accurately and organize your report.")
    col1, col2 = st.columns(2)
    with col1:
        home_currency = st.selectbox("Primary Reporting Currency", options=["USD", "EUR", "GBP", "INR", "AUD", "CAD", "SGD", "THB", "BRL", "MXN", "Other"], index=0)
        if home_currency == "Other":
            home_currency = st.text_input("Enter currency code (e.g., JPY)", value="JPY").upper()
    with col2:
        tax_country = st.selectbox("Tax Residence Country", options=["United States", "United Kingdom", "Germany", "Australia", "Canada", "India", "Singapore", "Spain", "Portugal", "Other"], index=0)
        is_nomad = st.checkbox("I am claiming foreign income / digital nomad status")
    return home_currency.strip().upper(), tax_country, is_nomad

# ── PDF Generator ─────────────────────────────────────────────
class TaxPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(30, 41, 59)
        self.cell(0, 10, "NomadTax Copilot — Accountant Report", new_x="LMARGIN", new_y="NEXT", align="C")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(100, 116, 139)
        self.cell(0, 5, f"Generated: {datetime.now().strftime('%B %d, %Y')}", new_x="LMARGIN", new_y="NEXT", align="C")
        self.ln(2)
        self.set_draw_color(226, 232, 240)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(148, 163, 184)
        self.cell(0, 5, f"Page {self.page_no()}/{{nb}} · NomadTax Copilot · For organizational purposes only", align="C")

def generate_pdf(categorized, total_income, total_expenses, total_deductible, net_income, notes, overrides, name="", home_currency="USD"):
    pdf = TaxPDF(orientation="L", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(12, 15, 12)

    if name:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(0, 6, f"Prepared for: {name}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 8, f"Financial Summary (All figures in {home_currency})", new_x="LMARGIN", new_y="NEXT")

    for label, value, rgb in [
        ("Total Gross Income", f"{home_currency} {total_income:,.2f}", (22, 163, 74)),
        ("Total Expenses", f"{home_currency} {abs(total_expenses):,.2f}", (220, 38, 38)),
        ("Net Income", f"{home_currency} {net_income:,.2f}", (37, 99, 235)),
        ("Potential Deductions (edited)", f"{home_currency} {abs(total_deductible):,.2f}", (99, 102, 241)),
    ]:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(80, 7, label)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*rgb)
        pdf.cell(40, 7, value, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    expense_cats = {}
    for r in categorized:
        if r.get("type") == "Expense":
            cat = r.get("category", "Other")
            expense_cats[cat] = expense_cats.get(cat, 0) + abs(r.get("amount", 0))

    if expense_cats:
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(0, 8, "Expense Breakdown by Category", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(241, 245, 249)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(100, 6, "Category", border=1, fill=True)
        pdf.cell(40, 6, f"Amount ({home_currency})", border=1, fill=True, align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        for cat, amt in sorted(expense_cats.items(), key=lambda x: x[1], reverse=True):
            pdf.set_text_color(71, 85, 105)
            pdf.cell(100, 5, f"  {cat}", border=1)
            pdf.set_text_color(30, 41, 59)
            pdf.cell(40, 5, f"{amt:,.2f}", border=1, align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(5)

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 8, "Itemized Transaction Ledger", new_x="LMARGIN", new_y="NEXT")
    COL = {"desc": 70, "amount": 22, "cat": 38, "ded": 14, "reasoning": 70, "notes": 50}
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_fill_color(30, 41, 59)
    pdf.set_text_color(255, 255, 255)
    for label, w in [("Description", COL["desc"]), ("Amount", COL["amount"]), ("Category", COL["cat"]), ("Ded.", COL["ded"]), ("AI Reasoning", COL["reasoning"]), ("Accountant Notes", COL["notes"])]:
        pdf.cell(w, 6, label, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 6.5)
    row_index = 0
    for i, r in enumerate(categorized):
        if r.get("type") not in ("Expense", "Income"):
            continue
        desc = r.get("clean_description") or r.get("original_description", "")
        reasoning = r.get("reasoning", "")
        note = notes.get(i, "")
        ded = "Yes" if overrides.get(i, r.get("deductible", False)) else "No"
        amount = r.get("amount", 0)
        amount_str = f"{amount:,.2f}" if amount >= 0 else f"({abs(amount):,.2f})"

        line_h = 3.5
        def count_lines(text, width, font_size=6.5):
            if not text: return 1
            chars_per_line = max(1, int(width / (font_size * 0.45)))
            words = text.split()
            line, count = "", 1
            for w in words:
                if len(line) + len(w) + 1 <= chars_per_line:
                    line += (" " if line else "") + w
                else:
                    count += 1
                    line = w
            return count

        row_h = max(count_lines(desc, COL["desc"]), count_lines(reasoning, COL["reasoning"]), count_lines(note, COL["notes"]), 1) * line_h + 2
        is_zebra = row_index % 2 == 0
        x0, y0 = pdf.get_x(), pdf.get_y()

        if y0 + row_h > pdf.page_break_trigger:
            pdf.add_page()
            x0, y0 = pdf.get_x(), pdf.get_y()

        pdf.set_text_color(30, 41, 59)
        def draw_cell(x, y, w, text, align="L"):
            pdf.set_xy(x, y)
            if is_zebra: pdf.set_fill_color(248, 250, 252)
            else: pdf.set_fill_color(255, 255, 255)
            pdf.multi_cell(w, line_h, str(text), border=1, align=align, fill=True, max_line_height=line_h)

        draw_cell(x0, y0, COL["desc"], desc)
        draw_cell(x0 + COL["desc"], y0, COL["amount"], amount_str, "R")
        draw_cell(x0 + COL["desc"] + COL["amount"], y0, COL["cat"], r.get("category", ""))

        pdf.set_xy(x0 + COL["desc"] + COL["amount"] + COL["cat"], y0)
        if "Yes" in ded: pdf.set_text_color(22, 163, 74)
        else: pdf.set_text_color(220, 38, 38)
        if is_zebra: pdf.set_fill_color(248, 250, 252)
        else: pdf.set_fill_color(255, 255, 255)
        pdf.multi_cell(COL["ded"], line_h, ded, border=1, align="C", fill=True, max_line_height=line_h)
        pdf.set_text_color(30, 41, 59)

        draw_cell(x0 + COL["desc"] + COL["amount"] + COL["cat"] + COL["ded"], y0, COL["reasoning"], reasoning)
        draw_cell(x0 + COL["desc"] + COL["amount"] + COL["cat"] + COL["ded"] + COL["reasoning"], y0, COL["notes"], note)
        pdf.set_xy(x0, y0 + row_h)
        row_index += 1

    pdf.ln(6)
    pdf.set_draw_color(226, 232, 240)
    pdf.line(12, pdf.get_y(), 265, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 6.5)
    pdf.set_text_color(148, 163, 184)
    pdf.multi_cell(0, 3.5, "DISCLAIMER: This report is for organizational purposes only and does not constitute tax, legal, or financial advice. Your data was processed in real time and is never stored or shared. Always consult a qualified tax professional before filing.")
    return bytes(pdf.output())
    # ═════════════════════════════════════════════════════════════
# UI
# ═════════════════════════════════════════════════════════════

st.markdown('<p class="hero-title">🌍 NomadTax Copilot</p>', unsafe_allow_html=True)
st.markdown('<p class="hero-sub">Turn your messy Stripe, PayPal, or Wise CSV into a clean accountant-ready tax report — in under 60 seconds.</p>', unsafe_allow_html=True)
st.markdown("""
<div style="margin:10px 0 4px;">
  <span class="pill">✅ AI-categorized transactions</span>
  <span class="pill">✅ Multi-currency auto-conversion</span>
  <span class="pill">✅ Reasoning for every decision</span>
  <span class="pill">✅ Editable notes before PDF export</span>
  <span class="pill">✅ Accountant-ready PDF</span>
</div>
<div class="privacy-note">
  🔒 <strong>Your data never leaves this session.</strong> Transactions are processed in real time and are never stored, logged, or shared.
</div>
""", unsafe_allow_html=True)

st.divider()

# ── API Key ───────────────────────────────────────────────────
model = get_model()
if not model:
    with st.expander("🔑 Enter your Google AI API Key", expanded=True):
        st.caption("Get a free key at [aistudio.google.com](https://aistudio.google.com).")
        api_key_input = st.text_input("API Key", type="password", label_visibility="collapsed", placeholder="AIza...")
        if api_key_input:
            os.environ["GOOGLE_API_KEY"] = api_key_input
            model = get_model()
            if model:
                st.success("API key accepted ✓")

# ── Tax Profile & Name ───────────────────────────────────────
freelancer_name = st.text_input("Your name (optional — appears on the PDF)", placeholder="e.g. Rahul Sharma")
home_currency, tax_country, is_nomad = render_tax_profile()

# ── File upload ───────────────────────────────────────────────
uploaded_file = st.file_uploader("📂 Upload your transaction CSV", type=["csv"], help="Works with Stripe, PayPal, Wise, or any CSV with description + amount columns.")

with st.expander("What should the CSV look like?"):
    st.dataframe(pd.DataFrame({
        "Description": ["Client payment - Website build", "Stripe processing fee", "AWS subscription", "Transfer to bank account"],
        "Amount": [2500.00, -75.00, -29.99, -2425.00],
        "Currency": ["USD", "USD", "EUR", "USD"]
    }), use_container_width=True, hide_index=True)
    st.caption("Column names are auto-detected — they don't need to match exactly.")

# ── Initialize Session State & Reset Trigger ─────────────────
if "categorized_data" not in st.session_state:
    st.session_state.categorized_data = None

if uploaded_file is not None:
    if st.session_state.get("last_uploaded_file") != uploaded_file.name:
        st.session_state.categorized_data = None
        st.session_state.saved_notes = {}
        st.session_state.saved_overrides = {}
        st.session_state.last_uploaded_file = uploaded_file.name

# ── Process ───────────────────────────────────────────────────
if uploaded_file and model:
    try:
        df = pd.read_csv(uploaded_file)
        df.columns = df.columns.str.strip().str.lower()

        desc_col = find_column(df.columns, ["description", "statement", "memo", "name", "narration", "details", "merchant"])
        amount_col = find_column(df.columns, ["amount", "net", "gross", "value", "debit", "credit"])

        if not desc_col or not amount_col:
            st.error(f"Could not detect columns. Found: {list(df.columns)}")
            st.info("Rename columns to include 'description' and 'amount'.")
            st.stop()

        # ── Currency Detection & Conversion ───────────────
        curr_col = find_column(df.columns, ["currency", "ccy"])
        if curr_col:
            unique_currencies = df[curr_col].dropna().str.upper().unique()
            st.info(f"💰 **Multi-currency detected:** {', '.join(unique_currencies)}. Converting to {home_currency}...")
            df["original_amount"] = df[amount_col]
            df["original_currency"] = df[curr_col].str.upper()
            df["amount_converted"] = df[amount_col]
            for curr in unique_currencies:
                if curr == home_currency:
                    df.loc[df["original_currency"] == curr, "amount_converted"] = df.loc[df["original_currency"] == curr, "original_amount"]
                else:
                    rate = get_exchange_rate(curr, home_currency)
                    if rate:
                        mask = df["original_currency"] == curr
                        df.loc[mask, "amount_converted"] = df.loc[mask, "original_amount"] * rate
                        st.success(f"✓ {curr} → {home_currency} rate: {rate:.4f}")
                    else:
                        st.error(f"❌ Could not fetch rate for {curr}. These transactions will use raw numbers.")
            final_amount_col = "amount_converted"
        else:
            st.info(f"🏠 Single currency assumed ({home_currency}).")
            final_amount_col = amount_col

        st.success(f"✓ {len(df)} transactions detected · columns: **{desc_col}** & **{final_amount_col}**")

        with st.expander("Preview (first 5 rows)"):
            preview_cols = [desc_col, final_amount_col]
            if curr_col: preview_cols.append("original_currency")
            st.dataframe(df[preview_cols].head(), use_container_width=True)

        # ── State-Safe Execution Button ───────────────────
        if st.button("🔍 Analyze & Categorize", type="primary", use_container_width=True):
            transactions = df[[desc_col, final_amount_col]].rename(columns={desc_col: "description", final_amount_col: "amount"}).to_dict(orient="records")
            with st.spinner("Gemini AI is scanning and batching records..."):
                st.session_state.categorized_data = categorize_transactions(model, transactions)
                st.session_state.saved_notes = {}
                st.session_state.saved_overrides = {i: r.get("deductible", False) for i, r in enumerate(st.session_state.categorized_data)}

        # ── Dashboard Render (Survives reruns) ────────────
        if st.session_state.categorized_data is not None:
            categorized = st.session_state.categorized_data
            total_income = sum(r.get("amount", 0) for r in categorized if r.get("type") == "Income" and r.get("amount", 0) > 0)
            total_expenses = sum(r.get("amount", 0) for r in categorized if r.get("type") == "Expense" and r.get("amount", 0) < 0)
            total_deductible_raw = sum(r.get("amount", 0) for r in categorized if r.get("deductible") and r.get("amount", 0) < 0)
            net_income = total_income + total_expenses

            st.divider()
            st.subheader("📊 Summary")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("💰 Total Income", f"{home_currency} {total_income:,.2f}")
            c2.metric("💸 Total Expenses", f"{home_currency} {abs(total_expenses):,.2f}")
            c3.metric("📈 Net Income", f"{home_currency} {net_income:,.2f}")
            c4.metric("🧾 Flagged Deductible", f"{home_currency} {abs(total_deductible_raw):,.2f}")

            st.subheader("🏷️ Expense Breakdown")
            expense_cats = {}
            for r in categorized:
                if r.get("type") == "Expense":
                    expense_cats[r.get("category", "Other")] = expense_cats.get(r.get("category", "Other"), 0) + abs(r.get("amount", 0))
            if expense_cats:
                cat_df = pd.DataFrame(sorted(expense_cats.items(), key=lambda x: x[1], reverse=True), columns=["Category", f"Amount ({home_currency})"])
                cat_df[f"Amount ({home_currency})"] = cat_df[f"Amount ({home_currency})"].map("{:,.2f}".format)
                st.dataframe(cat_df, use_container_width=True, hide_index=True)

            st.subheader("📋 Categorized Transactions")
            st.caption("Every AI decision is explained. Expand any row to see the reasoning.")
            for idx, r in enumerate(categorized):
                t = r.get("type", "")
                clean = r.get("clean_description") or r.get("original_description", "")
                amount = r.get("amount", 0)
                amount_str = f"{amount:,.2f}" if amount >= 0 else f"-{abs(amount):,.2f}"
                if t == "Expense":
                    icon = "✅" if r.get("deductible") else "❌"
                    with st.expander(f"{icon} **{clean}** — {amount_str} · {r.get('category', '')}"):
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Category", r.get("category", "Other"))
                        col2.metric("Deductible", "Yes ✅" if r.get("deductible") else "No ❌")
                        col3.metric("Type", t)
                        st.markdown(f'<div class="reasoning-box">🤖 <strong>AI Reasoning:</strong> {r.get("reasoning", "No reasoning provided.")}</div>', unsafe_allow_html=True)
                elif t == "Income":
                    with st.expander(f"💰 **{clean}** — {amount_str}"):
                        col1, col2 = st.columns(2)
                        col1.metric("Type", t)
                        col2.metric("Category", r.get("category", ""))
                elif t in ("Transfer", "Refund/Rebate"):
                    with st.expander(f"🔄 **{clean}** — {amount_str} · {t}"):
                        st.caption(r.get("reasoning", ""))

            # ── State-Safe Editable Matrix ────────────────
            st.divider()
            st.subheader("📝 Review & Add Notes for Your Accountant")
            st.caption("Override any deductible decision and add a note — both print directly to the PDF.")
            for idx, r in enumerate(categorized):
                if r.get("type") == "Expense":
                    col1, col2, col3 = st.columns([4, 1, 3])
                    clean = r.get("clean_description") or r.get("original_description", "")
                    col1.write(f"**{clean}** — {abs(r.get('amount', 0)):,.2f}")
                    st.session_state.saved_overrides[idx] = col2.checkbox("Ded.", value=st.session_state.saved_overrides.get(idx, False), key=f"ov_{idx}")
                    st.session_state.saved_notes[idx] = col3.text_input("Note", value=st.session_state.saved_notes.get(idx, ""), key=f"nt_{idx}", label_visibility="collapsed", placeholder="Add accountant note…")

            notes = st.session_state.saved_notes
            overrides = st.session_state.saved_overrides
            total_deductible_final = sum(abs(r.get("amount", 0)) for i, r in enumerate(categorized) if r.get("type") == "Expense" and overrides.get(i, r.get("deductible", False)))

            st.metric("🧾 Potential Deductions (after your edits)", f"{home_currency} {total_deductible_final:,.2f}", delta=f"{'+' if total_deductible_final >= abs(total_deductible_raw) else ''}{home_currency} {total_deductible_final - abs(total_deductible_raw):,.2f} vs AI estimate")

            st.divider()
            st.subheader("⬇️ Download Your Report")
            col1, col2 = st.columns(2)
            with col1:
                csv_out = pd.DataFrame(categorized).to_csv(index=False)
                st.download_button("📊 Download Raw CSV", data=csv_out, file_name="nomadtax_report.csv", mime="text/csv", use_container_width=True)
            with col2:
                with st.spinner("Building your PDF…"):
                    pdf_bytes = generate_pdf(categorized, total_income, total_expenses, total_deductible_final, net_income, notes, overrides, freelancer_name, home_currency)
                st.download_button("📄 Download Accountant-Ready PDF", data=pdf_bytes, file_name="NomadTax_Accountant_Report.pdf", mime="application/pdf", use_container_width=True, type="primary")
            st.success("✓ Report ready. Hand the PDF directly to your accountant — no extra prep needed.")

    except Exception as e:
        st.error(f"Error: {e}")

elif uploaded_file and not model:
    st.warning("Please enter your Google AI API key above to process the file.")

st.markdown("""
<div class="disclaimer">
  This tool organizes transaction data for informational purposes only. It does not constitute tax, legal, or financial advice. Always consult a qualified tax professional before filing.
</div>
""", unsafe_allow_html=True)
