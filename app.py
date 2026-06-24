import os
import json
import pandas as pd
import streamlit as st
import google.generativeai as genai
from dotenv import load_dotenv
from fpdf import FPDF

load_dotenv()

# ── Page config ──────────────────────────────────────────────
st.set_page_config(
    page_title="NomadTax Copilot",
    page_icon="💼",
    layout="centered"
)

# ── Minimal CSS ──────────────────────────────────────────────
st.markdown("""
<style>
    .disclaimer {
        background: #fefce8;
        border: 1px solid #fde68a;
        border-radius: 8px;
        padding: 12px 16px;
        font-size: 0.85rem;
        color: #78350f;
    }
</style>
""", unsafe_allow_html=True)

# ── Google Gemini setup ─────────────────────────────────────
SYSTEM_PROMPT = """
You are an expert freelance bookkeeper. I will give you a list of financial transactions from payment processors (Stripe, PayPal, Wise) in JSON format.

Your job is to categorize each transaction accurately.

Rules:
1. Determine if money is coming in (Income) or going out (Expense/Transfer).
2. Mark pure transfers between accounts (e.g. Stripe payout to bank) as "Transfer".
3. Categorize expenses logically: SaaS/Software, Advertising, Hardware, Contractors, Bank Fees, Other.
4. Flag if an expense is likely tax-deductible for a standard freelance business.
   - Laptop, software, domains, ads = Yes. Coffee, personal shopping = No. Ambiguous = null.
5. NEVER give tax advice. Only categorize based on standard business practice.

Respond ONLY with a valid JSON array. No markdown. No explanation. No ```json blocks. Raw JSON array only.

Each object must have exactly:
{
  "original_description": "string",
  "amount": number (positive = inflow, negative = outflow),
  "type": "Income | Expense | Transfer | Skip",
  "category": "string",
  "is_likely_deductible": true | false | null
}
"""

def get_google_client():
    api_key = os.environ.get("GOOGLE_API_KEY") or st.secrets.get("GOOGLE_API_KEY", "")
    if not api_key:
        return None
    genai.configure(api_key=api_key)
    return genai.GenerativeModel('gemini-1.5-flash')

def categorize_transactions(model, transactions: list) -> list:
    batch_size = 20
    results = []

    for i in range(0, len(transactions), batch_size):
        batch = transactions[i:i + batch_size]
        try:
            response = model.generate_content(
                contents=[SYSTEM_PROMPT, json.dumps(batch)]
            )
            text = response.text.strip()
            # Strip any accidental markdown fences just in case
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            parsed = json.loads(text)
            results.extend(parsed)
        except Exception as e:
            st.warning(f"Batch {i//batch_size + 1} failed: {e}")

    return results

def find_column(df_cols, keywords):
    for col in df_cols:
        for kw in keywords:
            if kw in col.lower():
                return col
    return None

# ── PDF Generator ──────────────────────────────────────────
class TaxPDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 16)
        self.cell(0, 10, 'NomadTax Copilot - Accountant Summary', new_x="LMARGIN", new_y="NEXT", align='C')
        self.ln(5)

    def footer(self):
        self.set_y(-20)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(128, 128, 128)
        self.multi_cell(0, 4, "DISCLAIMER: This report is for organizational purposes only and does not constitute tax advice. Please consult a qualified tax professional.", align='C')

def generate_pdf(categorized_data, total_income, total_expenses, total_deductible, net_income):
    pdf = TaxPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=25)
    
    pdf.set_font('Helvetica', 'B', 12)
    pdf.cell(0, 10, 'Financial Summary', new_x="LMARGIN", new_y="NEXT")
    pdf.set_font('Helvetica', '', 11)
    
    pdf.cell(95, 8, f'Total Gross Income: ${total_income:,.2f}', new_x="RIGHT", new_y="TOP")
    pdf.cell(95, 8, f'Total Expenses: ${abs(total_expenses):,.2f}', new_x="LMARGIN", new_y="NEXT")
    pdf.cell(95, 8, f'Net Income: ${net_income:,.2f}', new_x="RIGHT", new_y="TOP")
    pdf.cell(95, 8, f'Flagged Deductible: ${abs(total_deductible):,.2f}', new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    pdf.set_font('Helvetica', 'B', 12)
    pdf.cell(0, 10, 'Expense Breakdown by Category', new_x="LMARGIN", new_y="NEXT")
    
    expense_cats = {}
    for r in categorized_data:
        if r["type"] == "Expense":
            cat = r.get("category", "Other")
            expense_cats[cat] = expense_cats.get(cat, 0) + abs(r["amount"])
            
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(120, 7, 'Category', border=1, fill=True)
    pdf.cell(60, 7, 'Amount ($)', border=1, align='R', fill=True, new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_font('Helvetica', '', 10)
    for cat, amount in sorted(expense_cats.items(), key=lambda x: x[1], reverse=True):
        pdf.cell(120, 6, cat, border=1)
        pdf.cell(60, 6, f'${amount:,.2f}', border=1, align='R', new_x="LMARGIN", new_y="NEXT")
        
    pdf.ln(10)
    
    pdf.set_font('Helvetica', 'B', 12)
    pdf.cell(0, 10, 'Full Transaction Log', new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_font('Helvetica', 'B', 8)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(70, 6, 'Description', border=1, fill=True)
    pdf.cell(25, 6, 'Amount', border=1, align='R', fill=True)
    pdf.cell(25, 6, 'Type', border=1, align='C', fill=True)
    pdf.cell(30, 6, 'Category', border=1, align='C', fill=True)
    pdf.cell(20, 6, 'Deductible', border=1, align='C', fill=True, new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_font('Helvetica', '', 8)
    for r in categorized_data:
        desc = r["original_description"][:35] + "..." if len(r["original_description"]) > 35 else r["original_description"]
        ded = "Yes" if r.get("is_likely_deductible") == True else "No" if r.get("is_likely_deductible") == False else "-"
        
        pdf.cell(70, 5, desc, border=1)
        pdf.cell(25, 5, f'${r["amount"]:,.2f}', border=1, align='R')
        pdf.cell(25, 5, r["type"], border=1, align='C')
        pdf.cell(30, 5, r.get("category", ""), border=1, align='C')
        pdf.cell(20, 5, ded, border=1, align='C', new_x="LMARGIN", new_y="NEXT")

    pdf_bytes = pdf.output()
    return bytes(pdf_bytes)

# ─────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────

st.title("NomadTax Copilot")
st.caption("Upload your Stripe, PayPal, or Wise CSV  —  get a clean categorized report AND an Accountant-Ready PDF.")

st.markdown("""
<div class="disclaimer">
WARNING: This tool organizes your transaction data only. 
It does not provide tax advice. Always consult a qualified tax professional before filing.
</div>
""", unsafe_allow_html=True)

st.divider()

model = get_google_client()
if not model:
    api_key_input = st.text_input("Enter your Google AI API Key", type="password", help="Get your FREE key at aistudio.google.com")
    if api_key_input:
        os.environ["GOOGLE_API_KEY"] = api_key_input
        model = get_google_client()

uploaded_file = st.file_uploader("Upload your transaction CSV", type=["csv"], help="Works with Stripe, PayPal, Wise, or any CSV with description + amount columns.")

if uploaded_file and model:
    try:
        df = pd.read_csv(uploaded_file)
        df.columns = df.columns.str.strip().str.lower()

        desc_col = find_column(df.columns, ["description", "statement", "memo", "name", "narration", "details"])
        amount_col = find_column(df.columns, ["amount", "net", "gross", "value"])

        if not desc_col or not amount_col:
            st.error(f"Could not auto-detect columns. Found: {list(df.columns)}")
            st.info("Rename your columns to include 'description' and 'amount' and re-upload.")
            st.stop()

        st.success(f"Found {len(df)} transactions. Using columns: **{desc_col}** and **{amount_col}**")

        with st.expander("Preview raw data (first 5 rows)"):
            st.dataframe(df[[desc_col, amount_col]].head(), use_container_width=True)

        if st.button("Categorize with AI", type="primary", use_container_width=True):
            transactions = df[[desc_col, amount_col]].rename(
                columns={desc_col: "description", amount_col: "amount"}
            ).to_dict(orient="records")

            with st.spinner("Gemini AI is categorizing your transactions..."):
                categorized = categorize_transactions(model, transactions)

            if not categorized:
                st.error("Something went wrong. No results returned.")
                st.stop()

            total_income    = sum(r["amount"] for r in categorized if r["type"] == "Income" and r["amount"] > 0)
            total_expenses  = sum(r["amount"] for r in categorized if r["type"] == "Expense" and r["amount"] < 0)
            total_deductible= sum(r["amount"] for r in categorized if r.get("is_likely_deductible") and r["amount"] < 0)
            net_income      = total_income + total_expenses

            with st.spinner("Generating Accountant PDF..."):
                pdf_bytes = generate_pdf(categorized, total_income, total_expenses, total_deductible, net_income)

            st.divider()
            st.subheader("Dashboard Summary")

            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("Total Income", f"${total_income:,.2f}")
            with c2:
                st.metric("Total Expenses", f"${abs(total_expenses):,.2f}")
            with c3:
                st.metric("Net Income", f"${net_income:,.2f}", delta=f"${total_deductible:,.2f} flagged deductible")

            st.subheader("Expense Breakdown")
            expense_cats = {}
            for r in categorized:
                if r["type"] == "Expense":
                    cat = r.get("category", "Other")
                    expense_cats[cat] = expense_cats.get(cat, 0) + abs(r["amount"])

            if expense_cats:
                expense_df = pd.DataFrame(
                    sorted(expense_cats.items(), key=lambda x: x[1], reverse=True),
                    columns=["Category", "Amount ($)"]
                )
                expense_df["Amount ($)"] = expense_df["Amount ($)"].map("${:,.2f}".format)
                st.dataframe(expense_df, use_container_width=True, hide_index=True)
            else:
                st.info("No expenses found.")

            with st.expander("View All Categorized Transactions"):
                result_df = pd.DataFrame(categorized)
                result_df["amount"] = result_df["amount"].map("${:,.2f}".format)
                result_df["is_likely_deductible"] = result_df["is_likely_deductible"].map(
                    {True: "Yes", False: "No", None: "-"}
                )
                result_df.columns = [c.replace("_", " ").title() for c in result_df.columns]
                st.dataframe(result_df, use_container_width=True, hide_index=True)

            st.divider()
            
            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    label="DOWNLOAD ACCOUNTANT PDF",
                    data=pdf_bytes,
                    file_name="NomadTax_Accountant_Report.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    type="primary"
                )
            with col2:
                csv_out = pd.DataFrame(categorized).to_csv(index=False)
                st.download_button(
                    label="Download Raw CSV",
                    data=csv_out,
                    file_name="nomadtax_report.csv",
                    mime="text/csv",
                    use_container_width=True
                )

    except Exception as e:
        st.error(f"Error reading file: {e}")

elif uploaded_file and not model:
    st.warning("Please enter your Google API key above to process the file.")

elif not uploaded_file:
    st.info("Upload a CSV to get started. Works with any file that has a description column and an amount column.")

    with st.expander("What does a valid CSV look like?"):
        sample = pd.DataFrame({
            "Description": ["Client payment - Website", "Stripe fee", "AWS subscription", "Transfer to bank"],
            "Amount": [1500.00, -45.00, -29.99, -1455.00]
        })
        st.dataframe(sample, use_container_width=True, hide_index=True)
        st.caption("Column names don't have to match exactly — the app auto-detects them.")