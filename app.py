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

# ─────────────────────────────────────────────────────────────────────────────
# PAGE CONFIG  (must be first Streamlit call)
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NomadTax Copilot",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    #MainMenu, footer, header { visibility: hidden; }
    .hero-title  { font-size: 2.4rem; font-weight: 800; margin-bottom: 0; letter-spacing: -0.5px; }
    .hero-sub    { font-size: 1.05rem; color: #64748b; margin-top: 6px; line-height: 1.6; }
    .pill {
        display: inline-block; background: #f1f5f9; border-radius: 999px;
        padding: 4px 14px; font-size: 0.80rem; color: #475569;
        margin: 3px 3px 3px 0; border: 1px solid #e2e8f0;
    }
    .privacy-note {
        background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px;
        padding: 10px 16px; font-size: 0.83rem; color: #166534; margin-top: 12px;
    }
    .reasoning-box {
        background: #f8fafc; border-left: 3px solid #6366f1; border-radius: 4px;
        padding: 8px 14px; font-size: 0.83rem; color: #475569; margin-top: 8px;
    }
    .disclaimer {
        background: #fafafa; border: 1px solid #e2e8f0; border-radius: 8px;
        padding: 10px 16px; font-size: 0.78rem; color: #94a3b8; margin-top: 24px;
    }
    .paywall-box {
        background: #eef2ff; border: 1px solid #c7d2fe; border-radius: 10px;
        padding: 16px 20px; font-size: 0.92rem; color: #3730a3; margin: 6px 0; line-height: 1.6;
    }
    .stProgress > div > div { background: #6366f1; }
    section[data-testid="stVerticalBlock"] { gap: 0.75rem !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
GEMINI_MODEL   = "gemini-2.5-flash"
BATCH_SIZE     = 20
BATCH_WAIT_SEC = 5

CATEGORIES = [
    "SaaS/Software", "Travel/Accommodation", "Meals/Food",
    "Contractors/Freelancers", "Bank Fees", "Marketing/Advertising",
    "Office Supplies", "Professional Services", "Client Payments", "Other",
]

CURRENCIES = ["USD", "EUR", "GBP", "INR", "AUD", "CAD", "SGD", "THB", "BRL", "MXN", "Other"]
TAX_RESIDENCES = [
    "United States", "United Kingdom", "Germany", "Australia", "Canada",
    "India", "Singapore", "Spain", "Portugal", "Other",
]

SYSTEM_PROMPT = """You are NomadTax Copilot. Analyse raw bank/PSP transaction data and return a structured JSON array.
For EVERY transaction include ALL of these fields:
1. "original_description" - exact input string
2. "clean_description"    - human-readable, strip IDs, reference numbers, noise
3. "amount"               - same numeric value as input (do not convert)
4. "type"                 - exactly one of: "Income", "Expense", "Transfer", "Refund/Rebate"
5. "category"             - exactly one of: "SaaS/Software", "Travel/Accommodation", "Meals/Food",
                            "Contractors/Freelancers", "Bank Fees", "Marketing/Advertising",
                            "Office Supplies", "Professional Services", "Client Payments", "Other"
6. "deductible"           - boolean true or false. NEVER null. NEVER a string.
7. "reasoning"            - 1-2 sentences explaining why this is or is not deductible.

Classification rules:
- DEDUCTIBLE (true):  SaaS/Software, Travel/Accommodation, Meals/Food used as workspace, Bank Fees,
                      Contractors/Freelancers, Marketing/Advertising, Office Supplies, Professional Services
- NOT DEDUCTIBLE (false): Personal expenses, Transfers between own accounts, Refunds, Income entries

IMPORTANT:
- Return ONLY a valid JSON array. No markdown. No triple-backticks. No preamble.
- Every row in the input must produce exactly one row in the output.
- "deductible" must be a JSON boolean (true/false), never the string "true" or "false".
"""

# ─────────────────────────────────────────────────────────────────────────────
# SESSION STATE - initialise once, never overwrite on rerun
# ─────────────────────────────────────────────────────────────────────────────
def _init_session():
    api_key_default = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key_default:
        try:
            api_key_default = st.secrets.get("GOOGLE_API_KEY", "")
        except Exception:
            pass

    defaults = {
        "api_key":          api_key_default,
        "is_premium":       False,
        "travel_log":       [],
        "categorized_data": None,
        "saved_notes":      {},
        "saved_overrides":  {},
        "last_file":        None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_session()

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def get_model():
    key = st.session_state.get("api_key", "").strip()
    if not key:
        return None
    try:
        genai.configure(api_key=key)
        return genai.GenerativeModel(GEMINI_MODEL)
    except Exception:
        return None


def safe_deductible(val) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() == "true"
    return False


def find_column(cols, keywords):
    for col in cols:
        for kw in keywords:
            if kw in col.lower():
                return col
    return None


def get_exchange_rate(from_cur: str, to_cur: str):
    if from_cur == to_cur:
        return 1.0
    try:
        url = f"https://open.er-api.com/v6/latest/{from_cur}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        return data["rates"].get(to_cur)
    except Exception:
        return None


def _count_lines(text: str, col_width_mm: float, font_size: float = 6.5) -> int:
    if not text:
        return 1
    chars_per_line = max(1, int(col_width_mm / (font_size * 0.45)))
    line, count = "", 1
    for word in str(text).split():
        if len(line) + len(word) + 1 <= chars_per_line:
            line += (" " if line else "") + word
        else:
            count += 1
            line = word
    return count

# ─────────────────────────────────────────────────────────────────────────────
# AI CATEGORISATION
# ─────────────────────────────────────────────────────────────────────────────
def categorize_transactions(model, transactions: list) -> list:
    results = []
    total_batches = (len(transactions) + BATCH_SIZE - 1) // BATCH_SIZE
    bar  = st.progress(0, text="Starting...")
    note = st.empty()

    for batch_idx in range(total_batches):
        batch     = transactions[batch_idx * BATCH_SIZE : (batch_idx + 1) * BATCH_SIZE]
        batch_num = batch_idx + 1
        pct       = batch_num / total_batches

        bar.progress(pct, text=f"Processing batch {batch_num} of {total_batches}...")

        for attempt in range(3):
            try:
                response = model.generate_content(
                    contents=[SYSTEM_PROMPT, json.dumps(batch, default=str)]
                )
                text = response.text.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                parsed = json.loads(text)
                for row in parsed:
                    row["deductible"] = safe_deductible(row.get("deductible"))
                results.extend(parsed)
                break
            except Exception as exc:
                if attempt < 2:
                    wait = 15 * (attempt + 1)
                    for remaining in range(wait, 0, -1):
                        bar.progress(pct, text=f"Retrying in {remaining}s...")
                        time.sleep(1)
                else:
                    st.warning(f"Batch {batch_num} failed after 3 attempts: {exc}")

        if batch_num < total_batches:
            for remaining in range(BATCH_WAIT_SEC, 0, -1):
                bar.progress(pct, text=f"Next batch in {remaining}s...")
                time.sleep(1)

    bar.progress(1.0, text="Done!")
    time.sleep(0.3)
    bar.empty()
    note.empty()
    return results

# ─────────────────────────────────────────────────────────────────────────────
# PDF GENERATOR
# ─────────────────────────────────────────────────────────────────────────────
class TaxPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(30, 41, 59)
        self.cell(0, 10, "NomadTax Copilot - Accountant Report",
                  new_x="LMARGIN", new_y="NEXT", align="C")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(100, 116, 139)
        self.cell(0, 5, f"Generated: {datetime.now().strftime('%B %d, %Y')}",
                  new_x="LMARGIN", new_y="NEXT", align="C")
        self.ln(2)
        self.set_draw_color(226, 232, 240)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(148, 163, 184)
        self.cell(0, 5,
                  f"Page {self.page_no()}/{{nb}} - For organisational purposes only - not tax advice",
                  align="C")


def generate_pdf(
    categorized: list,
    total_income: float,
    total_expenses: float,
    total_deductible: float,
    net_income: float,
    notes: dict,
    overrides: dict,
    name: str = "",
    currency: str = "USD",
    travel_log=None,
) -> bytes:
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

    # Financial summary
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 8, f"Financial Summary ({currency})", new_x="LMARGIN", new_y="NEXT")

    for label, value, rgb in [
        ("Total Income",         f"{currency} {total_income:,.2f}",         (22, 163,  74)),
        ("Total Expenses",       f"{currency} {abs(total_expenses):,.2f}",  (220,  38,  38)),
        ("Net Income",           f"{currency} {net_income:,.2f}",            (37,  99, 235)),
        ("Potential Deductions", f"{currency} {abs(total_deductible):,.2f}",(99, 102, 241)),
    ]:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(80, 7, label)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*rgb)
        pdf.cell(60, 7, value, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Travel day summary
    if travel_log:
        country_days = {}
        country_periods = {}
        for trip in travel_log:
            c = trip.get("country", "Unknown")
            country_days[c] = country_days.get(c, 0) + trip.get("days", 0)
            start = trip.get("start")
            end   = trip.get("end")
            start_str = start.strftime("%d %b %y") if hasattr(start, "strftime") else str(start)
            end_str   = end.strftime("%d %b %y")   if hasattr(end,   "strftime") else str(end)
            country_periods.setdefault(c, []).append(f"{start_str}-{end_str}")

        total_days = sum(country_days.values())

        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(0, 8, "Travel Day Summary (183-Day Tracker)", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(241, 245, 249)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(90, 6, "Country", border=1, fill=True)
        pdf.cell(30, 6, "Days", border=1, align="R", fill=True)
        pdf.cell(80, 6, "Period(s)", border=1, fill=True, new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(71, 85, 105)
        for c, d in sorted(country_days.items(), key=lambda x: x[1], reverse=True):
            periods_str = ", ".join(country_periods.get(c, []))
            pdf.cell(90, 6, f"  {c}", border=1)
            pdf.cell(30, 6, str(d), border=1, align="R")
            pdf.cell(80, 6, f"  {periods_str}", border=1, new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(30, 41, 59)
        pdf.set_fill_color(241, 245, 249)
        pdf.cell(90, 6, "  TOTAL DAYS ABROAD", border=1, fill=True)
        pdf.cell(30, 6, str(total_days), border=1, align="R", fill=True)
        pdf.cell(80, 6, "", border=1, fill=True, new_x="LMARGIN", new_y="NEXT")

        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 7.5)
        pdf.set_text_color(100, 116, 139)
        if total_days >= 330:
            note_text = (f"{total_days} days abroad - meets the IRS 330-day Physical Presence Test "
                         "threshold for potential FEIE eligibility. Confirm with a CPA.")
        elif total_days >= 183:
            note_text = (f"{total_days} days abroad - exceeds the common 183-day threshold. "
                         "Tax residency in host country(ies) may apply. Verify local rules.")
        else:
            note_text = (f"{total_days} days abroad - below the 183-day threshold. "
                         "Foreign income exclusions may not apply. Consult a tax professional.")
        pdf.multi_cell(0, 4, note_text)
        pdf.ln(4)

    # Expense breakdown
    ec = {}
    for r in categorized:
        if r.get("type") == "Expense":
            cat = r.get("category", "Other")
            ec[cat] = ec.get(cat, 0) + abs(r.get("amount", 0))

    if ec:
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(0, 8, "Expense Breakdown by Category", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(241, 245, 249)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(100, 6, "Category", border=1, fill=True)
        pdf.cell(40,  6, f"Amount ({currency})", border=1, align="R", fill=True,
                 new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "", 8)
        for cat, amt in sorted(ec.items(), key=lambda x: x[1], reverse=True):
            pdf.set_text_color(71, 85, 105)
            pdf.cell(100, 5, f"  {cat}", border=1)
            pdf.set_text_color(30, 41, 59)
            pdf.cell(40, 5, f"{amt:,.2f}", border=1, align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(5)

    # Itemized ledger
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 8, "Itemized Transaction Ledger", new_x="LMARGIN", new_y="NEXT")

    COL = {"desc": 68, "amt": 22, "cat": 36, "ded": 14, "rsn": 68, "notes": 50}

    pdf.set_font("Helvetica", "B", 7)
    pdf.set_fill_color(30, 41, 59)
    pdf.set_text_color(255, 255, 255)
    for label, width in [
        ("Description",      COL["desc"]),
        ("Amount",           COL["amt"]),
        ("Category",         COL["cat"]),
        ("Ded?",             COL["ded"]),
        ("AI Reasoning",     COL["rsn"]),
        ("Accountant Notes", COL["notes"]),
    ]:
        pdf.cell(width, 6, label, border=1, align="C", fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 6.5)
    LINE_H = 3.5
    row_idx = 0

    for i, r in enumerate(categorized):
        if r.get("type") not in ("Expense", "Income"):
            continue

        desc    = r.get("clean_description") or r.get("original_description", "")
        reason  = r.get("reasoning", "")
        note_t  = notes.get(i, "")
        is_ded  = overrides.get(i, r.get("deductible", False))
        ded_str = "Yes" if is_ded else "No"
        amt     = r.get("amount", 0)
        amt_str = f"{amt:,.2f}" if amt >= 0 else f"({abs(amt):,.2f})"

        row_h = max(
            _count_lines(desc,   COL["desc"]),
            _count_lines(reason, COL["rsn"]),
            _count_lines(note_t, COL["notes"]),
            1,
        ) * LINE_H + 2

        if pdf.get_y() + row_h > pdf.page_break_trigger:
            pdf.add_page()

        x0, y0  = pdf.get_x(), pdf.get_y()
        is_even  = (row_idx % 2 == 0)
        fill_rgb = (248, 250, 252) if is_even else (255, 255, 255)

        def _cell(x, y, w, text, align="L", rgb=fill_rgb):
            pdf.set_xy(x, y)
            pdf.set_fill_color(*rgb)
            pdf.set_text_color(30, 41, 59)
            pdf.multi_cell(w, LINE_H, str(text), border=1, align=align,
                           fill=True, max_line_height=LINE_H)

        _cell(x0,                                                    y0, COL["desc"],  desc)
        _cell(x0 + COL["desc"],                                      y0, COL["amt"],   amt_str, "R")
        _cell(x0 + COL["desc"] + COL["amt"],                         y0, COL["cat"],   r.get("category", ""))

        # Deductible cell with colour
        dx = x0 + COL["desc"] + COL["amt"] + COL["cat"]
        pdf.set_xy(dx, y0)
        pdf.set_fill_color(*fill_rgb)
        pdf.set_text_color(22, 163, 74) if is_ded else pdf.set_text_color(220, 38, 38)
        pdf.multi_cell(COL["ded"], LINE_H, ded_str, border=1, align="C",
                       fill=True, max_line_height=LINE_H)

        rx = dx + COL["ded"]
        _cell(rx,                 y0, COL["rsn"],   reason)
        _cell(rx + COL["rsn"],    y0, COL["notes"], note_t)

        pdf.set_xy(x0, y0 + row_h)
        row_idx += 1

    pdf.ln(6)
    pdf.set_draw_color(226, 232, 240)
    pdf.line(12, pdf.get_y(), 265, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 6.5)
    pdf.set_text_color(148, 163, 184)
    pdf.multi_cell(
        0, 3.5,
        "DISCLAIMER: This report is generated by NomadTax Copilot for organisational purposes only. "
        "It does not constitute tax, legal, or financial advice. AI-generated deductibility "
        "classifications are estimates and have not been reviewed by a tax professional. "
        "Always consult a qualified accountant, CPA, or enrolled agent before filing.",
    )
    return bytes(pdf.output())

# ─────────────────────────────────────────────────────────────────────────────
# UI SECTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _render_api_key_section():
    has_key = bool(st.session_state.api_key)
    with st.expander("🔑 Google AI API Key", expanded=not has_key):
        if has_key:
            st.success("API key loaded and active for this session.")
            if st.button("Clear API key", key="clear_key"):
                st.session_state.api_key = ""
                st.rerun()
        else:
            typed = st.text_input(
                "Paste your API key",
                type="password",
                label_visibility="collapsed",
                placeholder="AIza...",
            )
            if typed.strip():
                st.session_state.api_key = typed.strip()
                st.rerun()
            st.caption(
                "Tip: add GOOGLE_API_KEY to .streamlit/secrets.toml or a .env file "
                "and this prompt disappears permanently."
            )


def _render_tax_profile():
    st.subheader("Your Tax Profile")
    c1, c2, c3 = st.columns(3)

    with c1:
        hc = st.selectbox("Reporting Currency", CURRENCIES, index=0, key="prof_currency")
        if hc == "Other":
            hc = st.text_input("Enter currency code (e.g. JPY)", key="prof_currency_other").upper().strip()
            if not hc:
                hc = "USD"

    with c2:
        tc = st.selectbox("Tax Residence", TAX_RESIDENCES, index=0, key="prof_country")

    with c3:
        name = st.text_input(
            "Your name (appears on PDF cover)",
            placeholder="Jane Doe",
            key="prof_name",
        ).strip()

    nomad = st.checkbox(
        "I am claiming digital nomad / foreign-earned income status",
        key="prof_nomad",
    )
    return hc.strip().upper(), tc, nomad, name


def _render_forms_wizard():
    st.subheader("Which US Tax Forms Do I Need?")
    st.caption("Answer five questions to get a personalised form checklist. Informational only - always confirm with a CPA.")

    with st.expander("Open the 5-step wizard"):
        citizenship = st.radio(
            "Are you a US citizen or green card holder?",
            ["- Select -", "Yes", "No"],
            key="wiz_citizenship",
            horizontal=True,
        )

        if citizenship == "- Select -":
            st.info("Select your status above to continue.")
            return

        if citizenship == "No":
            st.info(
                "This wizard covers US filer forms only. "
                "Please consult your country's tax authority for the relevant forms."
            )
            return

        st.markdown("**Tell us about your situation:**")
        income_abroad  = st.checkbox("I earned income while physically outside the US", key="wiz_income")
        self_employed  = st.checkbox("I am self-employed, freelance, or received 1099 income", key="wiz_se")
        foreign_bank   = st.checkbox(
            "I held a foreign bank/financial account that exceeded $10,000 USD at any point in the year",
            key="wiz_fbar",
        )
        foreign_assets = st.checkbox(
            "I hold foreign financial assets > $50,000 (single filer) or $100,000 (joint) at year-end",
            key="wiz_fatca",
        )
        foreign_corp   = st.checkbox(
            "I own 10% or more of a foreign corporation or foreign partnership",
            key="wiz_corp",
        )

        forms = [("Form 1040", "US Individual Income Tax Return - always required for US persons.")]
        if income_abroad:
            forms += [
                ("Form 2555", "Foreign Earned Income Exclusion - exclude up to the annual limit of foreign-earned income."),
                ("Form 1116", "Foreign Tax Credit - claim a credit for income taxes paid to a foreign country."),
            ]
        if self_employed:
            forms += [
                ("Schedule C", "Profit or Loss from Business - report self-employment income and deductions."),
                ("Schedule SE", "Self-Employment Tax - calculate Social Security and Medicare obligations."),
            ]
        if foreign_bank:
            forms.append(
                ("FinCEN 114 (FBAR)",
                 "Report of Foreign Bank Accounts - filed separately via BSA E-Filing (not with your tax return). Deadline: April 15, auto-extended to Oct 15.")
            )
        if foreign_assets:
            forms.append(
                ("Form 8938 (FATCA)",
                 "Statement of Specified Foreign Financial Assets - filed with Form 1040.")
            )
        if foreign_corp:
            forms.append(
                ("Form 5471",
                 "Information Return for US Persons with Respect to Certain Foreign Corporations - complex; engage a CPA with international experience.")
            )

        st.markdown("---")
        st.markdown("**Forms checklist based on your answers:**")
        for form_name, form_desc in forms:
            st.markdown(f"- **{form_name}** - {form_desc}")

        st.markdown(
            '<div class="disclaimer">This checklist is informational only and not tax advice. '
            'Form requirements depend on your complete individual circumstances. '
            'Always confirm with a qualified US tax professional or enrolled agent.</div>',
            unsafe_allow_html=True,
        )


def _render_travel_tracker():
    st.subheader("Travel Day Tracker")
    st.caption(
        "Track days spent outside your home country. "
        "The summary prints on your PDF so your accountant has the full picture."
    )

    with st.expander("Add a travel period"):
        a1, a2, a3 = st.columns(3)
        t_start   = a1.date_input("Start date", key="t_start")
        t_end     = a2.date_input("End date",   key="t_end")
        t_country = a3.text_input("Country", placeholder="e.g. Thailand", key="t_country")

        if st.button("Add period", key="t_add"):
            if not t_country.strip():
                st.error("Please enter a country name.")
            elif t_end < t_start:
                st.error("End date must be on or after the start date.")
            else:
                days = (t_end - t_start).days + 1
                st.session_state.travel_log.append({
                    "start":   t_start,
                    "end":     t_end,
                    "country": t_country.strip(),
                    "days":    days,
                })
                st.rerun()

    if not st.session_state.travel_log:
        st.caption("No travel periods added yet.")
        return

    # Aggregate by country
    country_days = {}
    for trip in st.session_state.travel_log:
        c = trip["country"]
        country_days[c] = country_days.get(c, 0) + trip["days"]

    # Individual entry list with per-row delete buttons
    st.markdown("**Logged trips:**")
    for idx, trip in enumerate(list(st.session_state.travel_log)):
        r1, r2 = st.columns([10, 1])
        start_str = trip["start"].strftime("%d %b %Y") if hasattr(trip["start"], "strftime") else str(trip["start"])
        end_str   = trip["end"].strftime("%d %b %Y")   if hasattr(trip["end"],   "strftime") else str(trip["end"])
        r1.markdown(f"- **{trip['country']}** | {start_str} to {end_str} ({trip['days']} days)")
        if r2.button("x", key=f"del_{idx}", help="Remove this entry"):
            st.session_state.travel_log.pop(idx)
            st.rerun()

    st.markdown("---")
    st.markdown("**Country totals:**")
    for country, days in sorted(country_days.items(), key=lambda x: x[1], reverse=True):
        if 170 <= days < 183:
            st.error(
                f"WARNING: {country}: {days} days - only {183 - days} days away from the "
                "common 183-day tax-residency trigger. Consult a local tax advisor before travelling further."
            )
        elif days >= 183:
            st.warning(
                f"{country}: {days} days - you have exceeded the common 183-day threshold. "
                "Tax residency in this country may apply. Verify with a local tax professional."
            )
        else:
            st.write(f"- **{country}:** {days} days")

    total = sum(country_days.values())
    st.metric("Total days abroad", f"{total} days")

    if total >= 330:
        st.success(
            "You meet the 330-day Physical Presence Test commonly required for the "
            "US IRS Foreign Earned Income Exclusion. Confirm eligibility with a CPA."
        )
    elif total >= 183:
        st.info(
            "You are over 183 days abroad. You may qualify as a tax resident "
            "in a host country under standard rules. Check local laws."
        )
    else:
        st.warning(f"{total} days abroad - below the 183-day threshold.")

    if st.button("Clear all travel entries", key="t_clear"):
        st.session_state.travel_log = []
        st.rerun()


def _render_footer():
    st.markdown(
        '<div class="disclaimer">'
        'NomadTax Copilot organises transaction data for informational purposes only. '
        'It does not constitute tax, legal, or financial advice. '
        'AI-generated deductibility classifications are estimates and may be incorrect. '
        'Always consult a qualified tax professional or enrolled agent before filing.'
        '</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    # Hero
    st.markdown('<p class="hero-title">NomadTax Copilot</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hero-sub">Stop leaving money on the table. Upload your payment records, '
        'uncover missing write-offs, and export an accountant-ready PDF in minutes.</p>',
        unsafe_allow_html=True,
    )
    st.markdown("""
    <div style="margin: 10px 0 4px;">
        <span class="pill">AI-categorized transactions</span>
        <span class="pill">Multi-currency auto-conversion</span>
        <span class="pill">Reasoning for every line item</span>
        <span class="pill">Editable notes and overrides</span>
        <span class="pill">Accountant-ready PDF</span>
        <span class="pill">183-Day Travel Tracker</span>
        <span class="pill">US Tax Forms Wizard</span>
    </div>
    <div class="privacy-note">
        Your data never leaves this session.
        Transactions are sent directly to the Gemini API and are never stored or shared with NomadTax.
    </div>
    """, unsafe_allow_html=True)
    st.divider()

    _render_api_key_section()
    model = get_model()

    home_currency, tax_country, is_nomad, freelancer_name = _render_tax_profile()
    st.divider()

    _render_forms_wizard()
    st.divider()

    _render_travel_tracker()
    st.divider()

    # CSV upload
    st.subheader("Upload Transactions")
    uploaded = st.file_uploader(
        "Upload a CSV export from Stripe, PayPal, Wise, Revolut, or your bank",
        type=["csv"],
        label_visibility="collapsed",
    )
    with st.expander("What columns does the CSV need?"):
        st.dataframe(
            pd.DataFrame({
                "Description": ["Client project payment", "Stripe processing fee", "AWS compute"],
                "Amount":      [2500.00, -75.00, -29.99],
                "Currency":    ["USD", "USD", "EUR"],
            }),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "The app auto-detects common column names for description and amount. "
            "A Currency column enables automatic multi-currency conversion."
        )

    if uploaded and st.session_state.last_file != uploaded.name:
        st.session_state.categorized_data = None
        st.session_state.saved_notes      = {}
        st.session_state.saved_overrides  = {}
        st.session_state.last_file        = uploaded.name

    if not uploaded:
        st.info("Upload a CSV above to get started.")
        _render_footer()
        return

    if not model:
        st.warning("Please enter your Google AI API key above before analysing transactions.")
        _render_footer()
        return

    # Parse CSV
    try:
        df = pd.read_csv(uploaded)
        df.columns = df.columns.str.strip().str.lower()

        desc_col   = find_column(df.columns, ["description", "statement", "memo", "name",
                                               "narration", "details", "merchant"])
        amount_col = find_column(df.columns, ["amount", "net", "gross", "value", "debit", "credit"])

        if not desc_col or not amount_col:
            st.error(
                "Could not detect the required Description and Amount columns in your CSV. "
                "Please rename them or check the sample format above."
            )
            _render_footer()
            return

        curr_col = find_column(df.columns, ["currency", "ccy"])

        if curr_col:
            unique_currencies = df[curr_col].dropna().str.upper().unique()
            st.info(f"Multi-currency CSV detected: {', '.join(unique_currencies)}. Converting to {home_currency}...")

            df["_orig_amount"]   = df[amount_col]
            df["_orig_currency"] = df[curr_col].str.upper()
            df["_converted"]     = df[amount_col].astype(float)

            for cur in unique_currencies:
                mask = df["_orig_currency"] == cur
                if cur == home_currency:
                    df.loc[mask, "_converted"] = df.loc[mask, "_orig_amount"]
                else:
                    rate = get_exchange_rate(cur, home_currency)
                    if rate:
                        df.loc[mask, "_converted"] = df.loc[mask, "_orig_amount"] * rate
                        st.success(f"{cur} to {home_currency}: rate {rate:.4f}")
                    else:
                        st.error(
                            f"Could not fetch live rate for {cur} to {home_currency}. "
                            "Those rows will use unconverted amounts."
                        )
            amount_field = "_converted"
        else:
            st.info(f"No currency column found - treating all amounts as {home_currency}.")
            amount_field = amount_col

        st.success(
            f"{len(df)} transaction(s) detected - "
            f"columns: {desc_col} (description) and {amount_field} (amount)"
        )

        with st.expander("Preview first 5 rows"):
            preview_cols = [desc_col, amount_field]
            if curr_col:
                preview_cols.append("_orig_currency")
            preview_df = df[preview_cols].head().copy()
            preview_df.columns = (
                ["Description", f"Amount ({home_currency})", "Original Currency"]
                if curr_col else
                ["Description", f"Amount ({home_currency})"]
            )
            st.dataframe(preview_df, use_container_width=True, hide_index=True)

    except Exception as exc:
        st.error(f"Error reading CSV: {exc}")
        _render_footer()
        return

    if st.button("Analyse and Categorize Transactions", type="primary", use_container_width=True):
        raw = (
            df[[desc_col, amount_field]]
            .rename(columns={desc_col: "description", amount_field: "amount"})
            .to_dict(orient="records")
        )
        with st.spinner("Sending to Gemini AI..."):
            result = categorize_transactions(model, raw)
        st.session_state.categorized_data = result
        st.session_state.saved_notes      = {}
        st.session_state.saved_overrides  = {
            i: r.get("deductible", False) for i, r in enumerate(result)
        }

    if st.session_state.categorized_data is None:
        _render_footer()
        return

    categorized = st.session_state.categorized_data

    total_income   = sum(r["amount"] for r in categorized if r.get("type") == "Income"  and r.get("amount", 0) > 0)
    total_expenses = sum(r["amount"] for r in categorized if r.get("type") == "Expense" and r.get("amount", 0) < 0)
    total_ded_raw  = sum(r["amount"] for r in categorized if r.get("deductible")         and r.get("amount", 0) < 0)
    net_income     = total_income + total_expenses

    st.divider()
    st.subheader("Summary")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Income",            f"{home_currency} {total_income:,.2f}")
    m2.metric("Total Expenses",          f"{home_currency} {abs(total_expenses):,.2f}")
    m3.metric("Net Income",              f"{home_currency} {net_income:,.2f}")
    m4.metric("AI-flagged Deductible",   f"{home_currency} {abs(total_ded_raw):,.2f}")

    expense_cats = {}
    for r in categorized:
        if r.get("type") == "Expense":
            cat = r.get("category", "Other")
            expense_cats[cat] = expense_cats.get(cat, 0) + abs(r.get("amount", 0))

    if expense_cats:
        st.subheader("Expense Breakdown")
        cat_df = pd.DataFrame(
            sorted(expense_cats.items(), key=lambda x: x[1], reverse=True),
            columns=["Category", f"Amount ({home_currency})"],
        )
        cat_df[f"Amount ({home_currency})"] = cat_df[f"Amount ({home_currency})"].map("{:,.2f}".format)
        st.dataframe(cat_df, use_container_width=True, hide_index=True)

    # Missing deductions scanner - only shown when there are expenses to compare against
    if expense_cats:
        st.subheader("Missing Deductions Scanner")
        missing = []
        if "Meals/Food" not in expense_cats:
            missing.append("Meals/Food (coffee shops, client lunches)")
        if is_nomad and "Travel/Accommodation" not in expense_cats:
            missing.append("Travel/Accommodation (hotels, flights, Airbnb)")
        if "Office Supplies" not in expense_cats:
            missing.append("Office Supplies (internet, home office, equipment)")

        if missing:
            st.warning(
                f"Did you miss some expenses? We did not see: {', '.join(missing)}. "
                "If you paid for these from a different account, add them below."
            )
            with st.expander("Manually add a missing expense"):
                mc1, mc2, mc3 = st.columns(3)
                m_desc = mc1.text_input("Description", placeholder="Monthly internet bill", key="man_desc")
                m_amt  = mc2.number_input(f"Amount ({home_currency})", min_value=0.0, format="%.2f", key="man_amt")
                m_cat  = mc3.selectbox("Category", CATEGORIES, key="man_cat")
                if st.button("Add to report", key="man_add"):
                    if m_desc.strip():
                        new_idx = len(st.session_state.categorized_data)
                        st.session_state.categorized_data.append({
                            "original_description": m_desc.strip(),
                            "clean_description":    m_desc.strip(),
                            "amount":       -abs(m_amt),
                            "type":         "Expense",
                            "category":     m_cat,
                            "deductible":   True,
                            "reasoning":    "Manually added by user.",
                        })
                        st.session_state.saved_overrides[new_idx] = True
                        st.rerun()
                    else:
                        st.error("Please enter a description.")
        else:
            st.success("All major expense categories are covered.")

    # Transaction list
    st.subheader("Categorized Transactions")
    for idx, r in enumerate(categorized):
        t_type = r.get("type", "")
        label  = r.get("clean_description") or r.get("original_description", "")
        amt    = r.get("amount", 0)
        amt_str = f"{amt:,.2f}" if amt >= 0 else f"-{abs(amt):,.2f}"

        if t_type == "Expense":
            icon = "Yes" if r.get("deductible") else "No"
            with st.expander(f"[{icon}] {label} - {amt_str} | {r.get('category', '')}"):
                col_a, col_b, col_c = st.columns(3)
                col_a.metric("Category",   r.get("category", "Other"))
                col_b.metric("Deductible", "Yes" if r.get("deductible") else "No")
                col_c.metric("Type",       t_type)
                st.markdown(
                    f'<div class="reasoning-box">AI Reasoning: {r.get("reasoning", "")}</div>',
                    unsafe_allow_html=True,
                )
        elif t_type == "Income":
            with st.expander(f"[Income] {label} - {amt_str}"):
                st.caption("Income - not deductible.")
        elif t_type in ("Transfer", "Refund/Rebate"):
            with st.expander(f"[{t_type}] {label} - {amt_str}"):
                st.caption(r.get("reasoning", ""))

    # Notes and overrides
    st.divider()
    st.subheader("Review, Override and Add Accountant Notes")
    st.caption("Tick Ded. to override the AI decision. Add notes for your accountant in the right column.")

    for idx, r in enumerate(categorized):
        if r.get("type") != "Expense":
            continue
        e1, e2, e3 = st.columns([4, 1, 3])
        e1.write(
            f"**{r.get('clean_description') or r.get('original_description', '')}** "
            f"- {abs(r.get('amount', 0)):,.2f}"
        )
        st.session_state.saved_overrides[idx] = e2.checkbox(
            "Ded.",
            value=st.session_state.saved_overrides.get(idx, r.get("deductible", False)),
            key=f"ov_{idx}",
        )
        st.session_state.saved_notes[idx] = e3.text_input(
            "Note",
            value=st.session_state.saved_notes.get(idx, ""),
            key=f"nt_{idx}",
            label_visibility="collapsed",
            placeholder="Add a note for your accountant...",
        )

    notes     = st.session_state.saved_notes
    overrides = st.session_state.saved_overrides

    total_ded_final = sum(
        abs(r.get("amount", 0))
        for i, r in enumerate(categorized)
        if r.get("type") == "Expense" and overrides.get(i, r.get("deductible", False))
    )
    delta_vs_ai = total_ded_final - abs(total_ded_raw)
    delta_sign  = "+" if delta_vs_ai >= 0 else ""
    st.metric(
        "Potential Deductions (after your overrides)",
        f"{home_currency} {total_ded_final:,.2f}",
        delta=f"{delta_sign}{home_currency} {delta_vs_ai:,.2f} vs AI estimate",
    )

    # Download
    st.divider()
    st.subheader("Download")
    dl1, dl2 = st.columns(2)

    with dl1:
        st.download_button(
            "Download Raw CSV",
            data=pd.DataFrame(categorized).to_csv(index=False),
            file_name="nomadtax_transactions.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with dl2:
        if st.session_state.is_premium:
            try:
                with st.spinner("Generating PDF..."):
                    pdf_bytes = generate_pdf(
                        categorized,
                        total_income,
                        total_expenses,
                        total_ded_final,
                        net_income,
                        notes,
                        overrides,
                        name=freelancer_name,
                        currency=home_currency,
                        travel_log=st.session_state.travel_log if st.session_state.travel_log else None,
                    )
                st.download_button(
                    "Download Accountant PDF",
                    data=pdf_bytes,
                    file_name="NomadTax_Report.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    type="primary",
                )
                st.success("PDF ready - hand this directly to your accountant.")
            except Exception as exc:
                st.error(f"PDF generation failed: {exc}. Please try again or contact support.")
        else:
            st.markdown(
                '<div class="paywall-box">'
                'PDF export is a Premium feature. '
                'Upgrade once for $29 to unlock the full accountant-ready PDF - '
                'includes travel-day summary, itemized ledger with AI reasoning, your notes, and deduction totals.'
                '</div>',
                unsafe_allow_html=True,
            )
            st.link_button(
                "Upgrade to Premium - $29",
                url="https://buy.stripe.com/YOUR_LINK_HERE",
                use_container_width=True,
                type="primary",
            )
            st.caption("After payment you will receive an activation code. Enter it below to unlock.")
            activation = st.text_input("Activation code", key="activation_code", placeholder="NOMAD-XXXX-XXXX")
            if st.button("Activate", key="activate_btn"):
                VALID_CODES = {"NOMAD-DEMO-2024"}  # Replace with real validation logic
                if activation.strip().upper() in VALID_CODES:
                    st.session_state.is_premium = True
                    st.success("Premium activated! You can now download the PDF.")
                    st.rerun()
                else:
                    st.error("Invalid activation code. Please check your email or contact support.")

    _render_footer()


main()
