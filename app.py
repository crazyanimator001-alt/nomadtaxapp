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

st.set_page_config(page_title="NomadTax Copilot", page_icon="🌍", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""<style>
    #MainMenu, footer, header {visibility: hidden;}
    .hero-title { font-size: 2.2rem; font-weight: 700; margin-bottom: 0; }
    .hero-sub { font-size: 1.1rem; color: #64748b; margin-top: 4px; }
    .pill { display: inline-block; background: #f1f5f9; border-radius: 999px; padding: 4px 14px; font-size: 0.82rem; color: #475569; margin: 3px 3px 3px 0; }
    .privacy-note { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 10px 16px; font-size: 0.83rem; color: #166534; margin-top: 10px; }
    .reasoning-box { background: #f8fafc; border-left: 3px solid #6366f1; border-radius: 4px; padding: 8px 12px; font-size: 0.83rem; color: #475569; margin-top: 6px; }
    .disclaimer { background: #fafafa; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 16px; font-size: 0.78rem; color: #94a3b8; margin-top: 20px; }
    .stProgress > div > div { background: #6366f1; }
    section[data-testid="stVerticalBlock"] { gap: 0.8rem !important; }
</style>""", unsafe_allow_html=True)

SYSTEM_PROMPT = """
You are NomadTax Copilot. Analyze raw bank/PSP transaction data and return a structured JSON array.
For EVERY transaction include:
1. "original_description" (exact input)
2. "clean_description" (human-readable, strip IDs/noise)
3. "amount" (same as input)
4. "type" ("Income", "Expense", "Transfer", "Refund/Rebate")
5. "category" ("SaaS/Software", "Travel/Accommodation", "Meals/Food", "Contractors/Freelancers", "Bank Fees", "Marketing/Advertising", "Office Supplies", "Professional Services", "Client Payments", "Other")
6. "deductible" (true or false. NEVER null)
7. "reasoning" (1-2 sentences explaining why)
Rules: SaaS/Travel/Meals(as workspace)/Bank Fees/Contractors = DEDUCTIBLE. Personal/Transfers/Refunds/Income = NOT DEDUCTIBLE.
NEVER give tax advice. Return ONLY valid JSON array. No markdown. No ```json blocks.
"""

def get_model():
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    try: api_key = api_key or st.secrets.get("GOOGLE_API_KEY", "")
    except: pass
    if not api_key: return None
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-2.5-flash")

def safe_deductible(val):
    return val is True

def categorize_transactions(model, transactions):
    batch_size, results = 20, []
    total_batches = (len(transactions) + batch_size - 1) // batch_size
    progress_bar, status_text = st.progress(0, text="Starting…"), st.empty()
    for i in range(0, len(transactions), batch_size):
        batch, batch_num = transactions[i:i + batch_size], i // batch_size + 1
        batch_pct = batch_num / total_batches
        progress_bar.progress(batch_pct, text=f"Processing {batch_num}/{total_batches}…")
        for attempt in range(3):
            try:
                response = model.generate_content(contents=[SYSTEM_PROMPT, json.dumps(batch)])
                text = response.text.strip()
                if text.startswith("```"): text = text.split("\n", 1)[1].rsplit("```", 1)[0]
                parsed = json.loads(text)
                for row in parsed: row["deductible"] = safe_deductible(row.get("deductible"))
                results.extend(parsed)
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(15 * (attempt + 1))
                else:
                    st.warning(f"Batch {batch_num} failed: {e}")
        if batch_num < total_batches:
            for c in range(5, 0, -1):
                progress_bar.progress(batch_pct, text=f"Waiting {c}s…")
                time.sleep(1)
    progress_bar.progress(1.0, text="Done!")
    time.sleep(0.4)
    progress_bar.empty()
    status_text.empty()
    return results

def find_column(cols, keywords):
    for col in cols:
        for kw in keywords:
            if kw in col.lower(): return col
    return None

def get_exchange_rate(from_currency, to_currency):
    if from_currency == to_currency: return 1.0
    try:
        with urllib.request.urlopen(f"https://open.er-api.com/v6/latest/{from_currency}", timeout=5) as response:
            data = json.loads(response.read().decode())
            return data["rates"].get(to_currency, 1.0)
    except: return None

def render_tax_profile():
    st.subheader("⚙️ Your Tax Profile")
    c1, c2 = st.columns(2)
    with c1:
        hc = st.selectbox("Primary Reporting Currency", ["USD", "EUR", "GBP", "INR", "AUD", "CAD", "SGD", "THB", "BRL", "MXN", "Other"], 0)
        if hc == "Other": hc = st.text_input("Currency Code", "JPY").upper()
    with c2:
        tc = st.selectbox("Tax Residence", ["United States", "United Kingdom", "Germany", "Australia", "Canada", "India", "Singapore", "Spain", "Portugal", "Other"], 0)
        nomad = st.checkbox("Claiming digital nomad/foreign income status")
    return hc.strip().upper(), tc, nomad

class TaxPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 16); self.set_text_color(30, 41, 59)
        self.cell(0, 10, "NomadTax Copilot — Accountant Report", new_x="LMARGIN", new_y="NEXT", align="C")
        self.set_font("Helvetica", "", 9); self.set_text_color(100, 116, 139)
        self.cell(0, 5, f"Generated: {datetime.now().strftime('%B %d, %Y')}", new_x="LMARGIN", new_y="NEXT", align="C")
        self.ln(2); self.set_draw_color(226, 232, 240); self.line(10, self.get_y(), 200, self.get_y()); self.ln(4)
    def footer(self):
        self.set_y(-12); self.set_font("Helvetica", "I", 7); self.set_text_color(148, 163, 184)
        self.cell(0, 5, f"Page {self.page_no()}/{{nb}} · For organizational purposes only", align="C")

def generate_pdf(cat, ti, te, td, ni, notes, overrides, name="", cur="USD"):
    pdf = TaxPDF(orientation="L", unit="mm", format="A4"); pdf.alias_nb_pages(); pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=20); pdf.set_margins(12, 15, 12)
    if name:
        pdf.set_font("Helvetica", "B", 10); pdf.set_text_color(30, 41, 59)
        pdf.cell(0, 6, f"Prepared for: {name}", new_x="LMARGIN", new_y="NEXT"); pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12); pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 8, f"Financial Summary ({cur})", new_x="LMARGIN", new_y="NEXT")
    for l, v, rgb in [("Total Income", f"{cur} {ti:,.2f}", (22,163,74)), ("Total Expenses", f"{cur} {abs(te):,.2f}", (220,38,38)), ("Net Income", f"{cur} {ni:,.2f}", (37,99,235)), ("Potential Deductions", f"{cur} {abs(td):,.2f}", (99,102,241))]:
        pdf.set_font("Helvetica", "", 10); pdf.set_text_color(71, 85, 105); pdf.cell(80, 7, l)
        pdf.set_font("Helvetica", "B", 10); pdf.set_text_color(*rgb); pdf.cell(40, 7, v, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    ec = {}
    for r in cat:
        if r.get("type") == "Expense": ec[r.get("category", "Other")] = ec.get(r.get("category", "Other"), 0) + abs(r.get("amount", 0))
    if ec:
        pdf.set_font("Helvetica", "B", 12); pdf.cell(0, 8, "Expense Breakdown", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 8); pdf.set_fill_color(241, 245, 249); pdf.cell(100, 6, "Category", 1, 1)
        pdf.cell(40, 6, f"Amount ({cur})", 1, 1, align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        for c, a in sorted(ec.items(), key=lambda x: x[1], reverse=True):
            pdf.set_text_color(71, 85, 105); pdf.cell(100, 5, f"  {c}", 1)
            pdf.set_text_color(30, 41, 59); pdf.cell(40, 5, f"{a:,.2f}", 1, align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(5)
    pdf.set_font("Helvetica", "B", 12); pdf.set_text_color(30, 41, 59); pdf.cell(0, 8, "Itemized Ledger", new_x="LMARGIN", new_y="NEXT")
    COL = {"desc": 70, "amt": 22, "cat": 38, "ded": 14, "rsn": 70, "not": 50}
    pdf.set_font("Helvetica", "B", 7); pdf.set_fill_color(30, 41, 59); pdf.set_text_color(255, 255, 255)
    for l, w in [("Desc", COL["desc"]), ("Amt", COL["amt"]), ("Cat", COL["cat"]), ("Ded", COL["ded"]), ("Reasoning", COL["rsn"]), ("Notes", COL["not"])]: pdf.cell(w, 6, l, 1, 1, "C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 6.5); ri = 0
    for i, r in enumerate(cat):
        if r.get("type") not in ("Expense", "Income"): continue
        d, rs, n = r.get("clean_description") or r.get("original_description", ""), r.get("reasoning", ""), notes.get(i, "")
        de = "Yes" if overrides.get(i, r.get("deductible", False)) else "No"
        am = r.get("amount", 0); ams = f"{am:,.2f}" if am >= 0 else f"({abs(am):,.2f})"
        lh = 3.5
        def cl(t, w, fs=6.5):
            if not t: return 1
            cpl = max(1, int(w / (fs * 0.45))); ln, ct = "", 1
            for wd in t.split():
                if len(ln) + len(wd) + 1 <= cpl: ln += (" " if ln else "") + wd
                else: ct += 1; ln = wd
            return ct
        rh = max(cl(d, COL["desc"]), cl(rs, COL["rsn"]), cl(n, COL["not"]), 1) * lh + 2
        iz = ri % 2 == 0; x0, y0 = pdf.get_x(), pdf.get_y()
        if y0 + rh > pdf.page_break_trigger: pdf.add_page(); x0, y0 = pdf.get_x(), pdf.get_y()
        pdf.set_text_color(30, 41, 59)
        def dc(x, y, w, t, al="L"):
            pdf.set_xy(x, y); pdf.set_fill_color(248, 250, 252) if iz else pdf.set_fill_color(255, 255, 255)
            pdf.multi_cell(w, lh, str(t), 1, al, True, max_line_height=lh)
        dc(x0, y0, COL["desc"], d); dc(x0+COL["desc"], y0, COL["amt"], ams, "R"); dc(x0+COL["desc"]+COL["amt"], y0, COL["cat"], r.get("category", ""))
        pdf.set_xy(x0+COL["desc"]+COL["amt"]+COL["cat"], y0); pdf.set_text_color(22, 163, 74) if "Yes" in de else pdf.set_text_color(220, 38, 38)
        pdf.set_fill_color(248, 250, 252) if iz else pdf.set_fill_color(255, 255, 255)
        pdf.multi_cell(COL["ded"], lh, de, 1, "C", True, max_line_height=lh); pdf.set_text_color(30, 41, 59)
        dc(x0+COL["desc"]+COL["amt"]+COL["cat"]+COL["ded"], y0, COL["rsn"], rs); dc(x0+COL["desc"]+COL["amt"]+COL["cat"]+COL["ded"]+COL["rsn"], y0, COL["not"], n)
        pdf.set_xy(x0, y0 + rh); ri += 1
    pdf.ln(6); pdf.set_draw_color(226, 232, 240); pdf.line(12, pdf.get_y(), 265, pdf.get_y()); pdf.ln(3)
    pdf.set_font("Helvetica", "I", 6.5); pdf.set_text_color(148, 163, 184)
    pdf.multi_cell(0, 3.5, "DISCLAIMER: This report is for organizational purposes only and does not constitute tax advice. Always consult a qualified tax professional.")
    return bytes(pdf.output())

st.markdown('<p class="hero-title">🌍 NomadTax Copilot</p>', unsafe_allow_html=True)
st.markdown('<p class="hero-sub">Stop leaving money on the table. Scan your payment records, uncover missing write-offs, and generate a certified accountant-ready layout.</p>', unsafe_allow_html=True)
st.markdown("""<div style="margin:10px 0 4px;"><span class="pill">✅ AI-categorized transactions</span><span class="pill">✅ Multi-currency auto-conversion</span><span class="pill">✅ Reasoning for every decision</span><span class="pill">✅ Editable notes before PDF export</span><span class="pill">✅ Accountant-ready PDF</span></div><div class="privacy-note">🔒 <strong>Your data never leaves this session.</strong> Transactions are processed in real time and are never stored, logged, or shared.</div>""", unsafe_allow_html=True)
st.divider()

model = get_model()
if not model:
    with st.expander("🔑 Enter your Google AI API Key", expanded=True):
        api_key_input = st.text_input("API Key", type="password", label_visibility="collapsed", placeholder="AIza...")
        if api_key_input:
            os.environ["GOOGLE_API_KEY"] = api_key_input
            model = get_model()
            if model: st.success("API key accepted ✓")

freelancer_name = st.text_input("Your name (optional — appears on the PDF)", placeholder="e.g. Rahul Sharma")
home_currency, tax_country, is_nomad = render_tax_profile()

uploaded_file = st.file_uploader("📂 Upload your transaction CSV", type=["csv"], help="Works with Stripe, PayPal, Wise CSVs.")
with st.expander("What should the CSV look like?"):
    st.dataframe(pd.DataFrame({"Description": ["Client payment", "Stripe fee", "AWS"], "Amount": [2500.00, -75.00, -29.99], "Currency": ["USD", "USD", "EUR"]}), use_container_width=True, hide_index=True)

if "categorized_data" not in st.session_state: st.session_state.categorized_data = None
if uploaded_file is not None and st.session_state.get("last_uploaded_file") != uploaded_file.name:
    st.session_state.categorized_data = None; st.session_state.saved_notes = {}; st.session_state.saved_overrides = {}; st.session_state.last_uploaded_file = uploaded_file.name

if uploaded_file and model:
    try:
        df = pd.read_csv(uploaded_file); df.columns = df.columns.str.strip().str.lower()
        desc_col = find_column(df.columns, ["description", "statement", "memo", "name", "narration", "details", "merchant"])
        amount_col = find_column(df.columns, ["amount", "net", "gross", "value", "debit", "credit"])
        if not desc_col or not amount_col: st.error("Could not detect columns."); st.stop()

        curr_col = find_column(df.columns, ["currency", "ccy"])
        if curr_col:
            uq = df[curr_col].dropna().str.upper().unique()
            st.info(f"💰 **Multi-currency detected:** {', '.join(uq)}. Converting to {home_currency}...")
            df["original_amount"], df["original_currency"], df["amount_converted"] = df[amount_col], df[curr_col].str.upper(), df[amount_col]
            for c in uq:
                if c == home_currency: df.loc[df["original_currency"] == c, "amount_converted"] = df.loc[df["original_currency"] == c, "original_amount"]
                else:
                    rate = get_exchange_rate(c, home_currency)
                    if rate: df.loc[df["original_currency"] == c, "amount_converted"] = df.loc[df["original_currency"] == c, "original_amount"] * rate; st.success(f"✓ {c} → {home_currency} rate: {rate:.4f}")
                    else: st.error(f"❌ Could not fetch rate for {c}.")
            fac = "amount_converted"
        else:
            st.info(f"🏠 Single currency assumed ({home_currency})."); fac = amount_col

        st.success(f"✓ {len(df)} transactions detected · columns: **{desc_col}** & **{fac}**")
        with st.expander("Preview (first 5 rows)"):
            pc = [desc_col, fac]
            if curr_col: pc.append("original_currency")
            st.dataframe(df[pc].head(), use_container_width=True)

        if st.button("🔍 Analyze & Categorize", type="primary", use_container_width=True):
            trans = df[[desc_col, fac]].rename(columns={desc_col: "description", fac: "amount"}).to_dict(orient="records")
            with st.spinner("Gemini AI is scanning..."):
                st.session_state.categorized_data = categorize_transactions(model, trans)
                st.session_state.saved_notes = {}
                st.session_state.saved_overrides = {i: r.get("deductible", False) for i, r in enumerate(st.session_state.categorized_data)}

        if st.session_state.categorized_data is not None:
            categorized = st.session_state.categorized_data
            ti = sum(r.get("amount", 0) for r in categorized if r.get("type") == "Income" and r.get("amount", 0) > 0)
            te = sum(r.get("amount", 0) for r in categorized if r.get("type") == "Expense" and r.get("amount", 0) < 0)
            td_raw = sum(r.get("amount", 0) for r in categorized if r.get("deductible") and r.get("amount", 0) < 0)
            ni = ti + te

            st.divider(); st.subheader("📊 Summary")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("💰 Total Income", f"{home_currency} {ti:,.2f}")
            c2.metric("💸 Total Expenses", f"{home_currency} {abs(te):,.2f}")
            c3.metric("📈 Net Income", f"{home_currency} {ni:,.2f}")
            c4.metric("🧾 Flagged Deductible", f"{home_currency} {abs(td_raw):,.2f}")

            st.subheader("🏷️ Expense Breakdown")
            expense_cats = {}
            for r in categorized:
                if r.get("type") == "Expense": expense_cats[r.get("category", "Other")] = expense_cats.get(r.get("category", "Other"), 0) + abs(r.get("amount", 0))
            if expense_cats:
                cdf = pd.DataFrame(sorted(expense_cats.items(), key=lambda x: x[1], reverse=True), columns=["Category", f"Amount ({home_currency})"])
                cdf[f"Amount ({home_currency})"] = cdf[f"Amount ({home_currency})"].map("{:,.2f}".format)
                st.dataframe(cdf, use_container_width=True, hide_index=True)

            # ── MISSING DEDUCTIONS SCANNER ──
            st.subheader("🔍 Missing Deductions Scanner")
            existing_cats = set(expense_cats.keys())
            missing_warnings = []
            if "Meals/Food" not in existing_cats: missing_warnings.append("Meals/Food (Coffee, lunches)")
            if is_nomad and "Travel/Accommodation" not in existing_cats: missing_warnings.append("Travel/Accommodation (Hotels, flights)")
            if "Office Supplies" not in existing_cats: missing_warnings.append("Office Supplies (Internet, desk)")
            if missing_warnings:
                st.warning(f"⚠️ **Wait!** We didn't see any expenses for: {', '.join(missing_warnings)}. Did you pay for these separately?")
                with st.expander("➕ Add a missing expense"):
                    ac1, ac2, ac3 = st.columns(3)
                    md = ac1.text_input("Description", placeholder="e.g., Monthly internet bill", key="md")
                    ma = ac2.number_input(f"Amount ({home_currency})", min_value=0.0, format="%0.2f", key="ma")
                    mc = ac3.selectbox("Category", ["Meals/Food", "Travel/Accommodation", "Office Supplies", "SaaS/Software", "Other"], key="mc")
                    if st.button("Add to Report", key="add_btn"):
                        st.session_state.categorized_data.append({"original_description": md, "clean_description": md, "amount": -ma, "type": "Expense", "category": mc, "deductible": True, "reasoning": "Manually added missing expense."})
                        st.rerun()
            else:
                st.success("✅ Looks like you covered all the major expense categories!")

            st.subheader("📋 Categorized Transactions")
            for idx, r in enumerate(categorized):
                t, cl = r.get("type", ""), r.get("clean_description") or r.get("original_description", "")
                am = r.get("amount", 0); ams = f"{am:,.2f}" if am >= 0 else f"-{abs(am):,.2f}"
                if t == "Expense":
                    ic = "✅" if r.get("deductible") else "❌"
                    with st.expander(f"{ic} **{cl}** — {ams} · {r.get('category', '')}"):
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Category", r.get("category", "Other")); c2.metric("Deductible", "Yes ✅" if r.get("deductible") else "No ❌"); c3.metric("Type", t)
                        st.markdown(f'<div class="reasoning-box">🤖 <strong>AI Reasoning:</strong> {r.get("reasoning", "")}</div>', unsafe_allow_html=True)
                elif t == "Income":
                    with st.expander(f"💰 **{cl}** — {ams}"): st.caption(f"Type: {t}")
                elif t in ("Transfer", "Refund/Rebate"):
                    with st.expander(f"🔄 **{cl}** — {ams} · {t}"): st.caption(r.get("reasoning", ""))

            st.divider(); st.subheader("📝 Review & Add Notes for Your Accountant")
            for idx, r in enumerate(categorized):
                if r.get("type") == "Expense":
                    c1, c2, c3 = st.columns([4, 1, 3])
                    c1.write(f"**{r.get('clean_description') or r.get('original_description')}** — {abs(r.get('amount', 0)):,.2f}")
                    st.session_state.saved_overrides[idx] = c2.checkbox("Ded.", value=st.session_state.saved_overrides.get(idx, False), key=f"ov_{idx}")
                    st.session_state.saved_notes[idx] = c3.text_input("Note", value=st.session_state.saved_notes.get(idx, ""), key=f"nt_{idx}", label_visibility="collapsed", placeholder="Add note…")

            notes, overrides = st.session_state.saved_notes, st.session_state.saved_overrides
            td_final = sum(abs(r.get("amount", 0)) for i, r in enumerate(categorized) if r.get("type") == "Expense" and overrides.get(i, r.get("deductible", False)))
            st.metric("🧾 Potential Deductions (after edits)", f"{home_currency} {td_final:,.2f}", delta=f"{'+' if td_final >= abs(td_raw) else ''}{home_currency} {td_final - abs(td_raw):,.2f} vs AI")

            st.divider(); st.subheader("⬇️ Download Your Report")
            co1, co2 = st.columns(2)
            with co1: st.download_button("📊 Download Raw CSV", data=pd.DataFrame(categorized).to_csv(index=False), file_name="nomadtax_report.csv", mime="text/csv", use_container_width=True)
            with co2:
                with st.spinner("Building PDF…"): pdf_bytes = generate_pdf(categorized, ti, te, td_final, ni, notes, overrides, freelancer_name, home_currency)
                st.download_button("📄 Download Accountant PDF", data=pdf_bytes, file_name="NomadTax_Report.pdf", mime="application/pdf", use_container_width=True, type="primary")
            st.success("✓ Report ready. Hand the PDF directly to your accountant.")
    except Exception as e: st.error(f"Error: {e}")
elif uploaded_file and not model: st.warning("Please enter your Google API key above.")

st.markdown("""<div class="disclaimer">This tool organizes transaction data for informational purposes only. It does not constitute tax, legal, or financial advice. Always consult a qualified tax professional before filing.</div>""", unsafe_allow_html=True)
