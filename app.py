import streamlit as st
import pandas as pd
import plotly.express as px
from fpdf import FPDF
import datetime
import io
import requests
import json
import openai

# ==========================================
# 1. PAGE CONFIG & CUSTOM CSS
# ==========================================
st.set_page_config(page_title="NomadTax OS", page_icon="✈️", layout="wide")

hide_st_style = """
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            header {visibility: hidden;}
            .block-container{padding-top: 2rem;}
            h1, h2, h3 {color: #1e293b;}
            .stButton>button {background-color: #6366f1; color: white; border-radius: 8px; border: none; padding: 0.5em 1em; font-weight: 600;}
            .stButton>button:hover {background-color: #4f46e5;}
            .wow-metric {font-size: 2.5rem; font-weight: 800; color: #6366f1; margin-bottom: 0px;}
            .wow-label {font-size: 1rem; color: #64748b; margin-top: 0px;}
            </style>
            """
st.markdown(hide_st_style, unsafe_allow_html=True)

# ==========================================
# 2. HELPER FUNCTIONS (THE ENGINE)
# ==========================================

def get_historical_fx(date_str, from_currency):
    """Fetches historical FX rate from frankfurter.app (ECB rates, free, no key needed)"""
    if from_currency == "USD" or pd.isna(date_str):
        return 1.0
    
    try:
        # Format date for API (YYYY-MM-DD)
        dt = pd.to_datetime(date_str)
        url = f"https://api.frankfurter.app/{dt.strftime('%Y-%m-%d')}?from={from_currency}&to=USD"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return response.json()['rates']['USD']
        return None # API failed
    except:
        return None

def run_ai_categorization(df):
    """Real AI logic using batched OpenAI calls for speed/cost"""
    try:
        openai.api_key = st.secrets["OPENAI_API_KEY"]
    except KeyError:
        st.error("OpenAI API key not found. Please add it to .streamlit/secrets.toml")
        st.stop()
        
    results = []
    batch_size = 15 # Process 15 rows at a time
    
    for i in range(0, len(df), batch_size):
        batch = df.iloc[i:i+batch_size]
        prompt_data = []
        for idx, row in batch.iterrows():
            prompt_data.append({"id": idx, "desc": str(row['Description']), "amount": float(row['Amount'])})
            
        prompt = f"""
        You are a tax accountant for US digital nomads. Categorize these transactions.
        Categories MUST start with either "Business:" or "Personal:" (e.g., "Business: Travel", "Personal: Dining").
        Provide a 1-sentence reasoning for each.
        Return ONLY valid JSON as a list of objects with keys: "id", "category", "reasoning".
        Data: {json.dumps(prompt_data)}
        """
        
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            response_format={ "type": "json_object" },
            messages=[{"role": "system", "content": "You output strictly valid JSON."},
                      {"role": "user", "content": prompt}]
        )
        
        batch_results = json.loads(response.choices[0].message.content)
        # Handle if API returns a dict wrapper instead of a list
        if isinstance(batch_results, dict): 
            batch_results = batch_results.get('data', batch_results.get('transactions', list(batch_results.values())[0]))
            
        results.extend(batch_results)
        
    # Map AI results back to dataframe
    ai_map = {str(item['id']): item for item in results}
    final_data = []
    for idx, row in df.iterrows():
        ai_res = ai_map.get(str(idx), {"category": "Personal: Uncategorised", "reasoning": "AI skipped"})
        final_data.append({
            "Date": row.get('Date', 'Unknown'),
            "Description": row.get('Description', 'Unknown'),
            "Original Amount": row.get('Amount', 0),
            "Currency": row.get('Currency', 'USD'),
            "Category": ai_res['category'],
            "Reasoning": ai_res['reasoning']
        })
    return pd.DataFrame(final_data)

def generate_premium_pdf(df):
    """Generates the high-quality, audit-ready PDF"""
    # [KEEPING THE EXACT SAME PDF FUNCTION FROM PREVIOUS VERSION FOR BREVITY]
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.ln(60)
    pdf.set_font("Helvetica", "B", 32)
    pdf.cell(0, 15, "NomadTax OS", ln=True, align="C")
    pdf.set_font("Helvetica", "", 16)
    pdf.cell(0, 10, "Financial & Tax Summary Report", ln=True, align="C")
    pdf.ln(20)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 10, f"Generated: {datetime.datetime.now().strftime('%B %d, %Y')}", ln=True, align="C")
    pdf.ln(40)
    pdf.set_text_color(150, 150, 150)
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(0, 10, "DISCLAIMER: This document is for informational purposes only.", ln=True, align="C")
    pdf.set_text_color(0, 0, 0)
    
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 15, "Executive Summary", ln=True)
    pdf.ln(5)
    
    # Use USD Amount for summary if available
    amount_col = 'USD Amount' if 'USD Amount' in df.columns else 'Original Amount'
    total_spent = df[amount_col].astype(float).sum()
    business_df = df[df['Category'].str.contains('Business')]
    total_business = business_df[amount_col].astype(float).sum()

    pdf.set_font("Helvetica", "", 12)
    pdf.cell(90, 10, f"Total Transactions: {len(df)}", ln=False)
    pdf.cell(0, 10, f"Total Volume: ${total_spent:,.2f}", ln=True)
    pdf.cell(90, 10, f"Potential Deductions: ${total_business:,.2f}", ln=False)
    pdf.cell(0, 10, f"Personal Expenses: ${total_spent - total_business:,.2f}", ln=True)
    return pdf.output(dest="S").encode("latin-1")

# ==========================================
# 3. SESSION STATE
# ==========================================
if 'processed_data' not in st.session_state:
    st.session_state.processed_data = None

# ==========================================
# 4. MAIN APP UI
# ==========================================
st.title("✈️ NomadTax OS")
st.markdown("*The tax operating system for location-independent earners.*")

tab1, tab2, tab3, tab4 = st.tabs(["1. Upload & Categorize", "2. Analytics & FX", "3. Residency Predictor", "4. Tax Wizard"])

# ------------------------------------------
# TAB 1: UPLOAD
# ------------------------------------------
with tab1:
    st.header("Upload Bank Statement (CSV)")
    uploaded_file = st.file_uploader("Drag and drop your Wise, Revolut, or bank CSV here", type=["csv"])
    
    if uploaded_file is not None:
        try:
            df_raw = pd.read_csv(uploaded_file)
            df_raw.columns = df_raw.columns.str.strip().str.lower()
            col_map = {'date': 'Date', 'description': 'Description', 'amount': 'Amount', 'currency': 'Currency'}
            df_raw.rename(columns={k: v for k, v in col_map.items() if k in df_raw.columns}, inplace=True)
            
            with st.spinner("Running AI categorization engine..."):
                processed_df = run_ai_categorization(df_raw)
                st.session_state.processed_data = processed_df
                
            st.success("AI Categorization Complete!")
            st.dataframe(processed_df, use_container_width=True)
            
        except Exception as e:
            st.error(f"Error: {e}")

# ------------------------------------------
# TAB 2: ANALYTICS, FX & WOW MOMENT
# ------------------------------------------
with tab2:
    if st.session_state.processed_data is not None:
        df = st.session_state.processed_data.copy()
        df['Original Amount'] = df['Original Amount'].astype(float)
        
        st.header("Financial Analytics & FX Conversion")
        
        # --- THE FX CONVERSION ENGINE ---
        with st.spinner("Fetching historical ECB exchange rates for your transactions..."):
            df['FX Rate'] = df.apply(lambda row: get_historical_fx(row['Date'], row['Currency']), axis=1)
            df['USD Amount'] = df.apply(lambda row: row['Original Amount'] * row['FX Rate'] if row['FX Rate'] else row['Original Amount'], axis=1)
        
        # --- THE "WOW" MOMENT METRICS ---
        total_business_usd = df[df['Category'].str.contains('Business')]['USD Amount'].sum()
        currencies_used = df['Currency'].nunique()
        
        col1, col2, col3 = st.columns(3)
        col1.metric(label="Potential Deductions Found", value=f"${total_business_usd:,.2f}")
        col2.metric(label="Currencies Processed", value=f"{currencies_used}")
        col3.metric(label="Transactions Analyzed", value=f"{len(df)}")
        
        st.markdown("---")
        
        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("Business vs. Personal (USD)")
            type_split = df['Category'].str.split(": ", expand=True)
            df['Type'] = type_split[0]
            type_totals = df.groupby('Type')['USD Amount'].sum().reset_index()
            fig_donut = px.pie(type_totals, values='USD Amount', names='Type', hole=0.6, 
                               color_discrete_map={'Business': '#10b981', 'Personal': '#94a3b8'})
            st.plotly_chart(fig_donut, use_container_width=True)
            
        with col_b:
            st.subheader("Top Deduction Categories")
            biz_df = df[df['Type'] == 'Business']
            cat_totals = biz_df.groupby('Category')['USD Amount'].sum().sort_values(ascending=False).head(5).reset_index()
            fig_bar = px.bar(cat_totals, x='USD Amount', y='Category', orientation='h', color_discrete_sequence=['#6366f1'])
            st.plotly_chart(fig_bar, use_container_width=True)
            
        st.divider()
        
        # Show the data with FX applied
        st.subheader("Audit Trail with Historical FX")
        st.dataframe(df[['Date', 'Description', 'Original Amount', 'Currency', 'FX Rate', 'USD Amount', 'Category']], use_container_width=True)
        
        if st.button("📄 Generate Audit-Ready PDF (with FX)", type="primary", use_container_width=True):
            pdf_bytes = generate_premium_pdf(df)
            st.download_button(label="Download PDF", data=pdf_bytes, file_name="NomadTax_Report_FX.pdf", mime="application/pdf")
    else:
        st.warning("Please upload a CSV in Tab 1 first.")

# ------------------------------------------
# TAB 3: SMART RESIDENCY PREDICTOR
# ------------------------------------------
with tab3:
    st.header("Smart Residency Predictor")
    st.info("⚠️ **Disclaimer:** This calculates based on standard 183-day rules. It does not account for tax treaties. Consult a professional.")
    
    col_es, col_pt = st.columns(2)
    
    with col_es:
        st.subheader("🇪🇸 Spain Predictor")
        es_days = st.number_input("Days spent in Spain so far this year:", min_value=0, max_value=365, value=0, key="es")
        
        if es_days > 0:
            today = datetime.date.today()
            start_of_year = datetime.date(today.year, 1, 1)
            days_elapsed_year = (today - start_of_year).days
            
            # Calculate velocity (days per month)
            months_elapsed = max(days_elapsed_year / 30.44, 1) # prevent div by 0
            velocity = es_days / months_elapsed
            
            remaining_safe_days = 183 - es_days
            if remaining_safe_days > 0:
                months_left_safe = remaining_safe_days / velocity
                projected_trigger_date = today + datetime.timedelta(days=months_left_safe * 30.44)
                
                st.success(f"**Status:** Safe (For now)")
                st.metric(label="Projected Trigger Date", value=projected_trigger_date.strftime('%B %d, %Y'))
                st.write(f"*At your current rate of {velocity:.1f} days/month, you will cross the 183-day threshold on this date.*")
                st.warning(f"**Action:** You must leave Spain before this date to avoid becoming a tax resident.")
            else:
                st.error("**Status:** Danger - Threshold Crossed")

    with col_pt:
        st.subheader("🇵🇹 Portugal Predictor")
        pt_days = st.number_input("Days spent in Portugal so far this year:", min_value=0, max_value=365, value=0, key="pt")
        
        if pt_days > 0:
            today = datetime.date.today()
            start_of_year = datetime.date(today.year, 1, 1)
            days_elapsed_year = (today - start_of_year).days
            
            months_elapsed = max(days_elapsed_year / 30.44, 1)
            velocity = pt_days / months_elapsed
            
            remaining_safe_days = 183 - pt_days
            if remaining_safe_days > 0:
                months_left_safe = remaining_safe_days / velocity
                projected_trigger_date = today + datetime.timedelta(days=months_left_safe * 30.44)
                
                st.success(f"**Status:** Safe (For now)")
                st.metric(label="Projected Trigger Date", value=projected_trigger_date.strftime('%B %d, %Y'))
                st.write(f"*At your current rate of {velocity:.1f} days/month, you will cross the 183-day threshold on this date.*")
                st.warning(f"**Action:** You must leave Portugal before this date to avoid becoming a tax resident.")
            else:
                st.error("**Status:** Danger - Threshold Crossed")

# ------------------------------------------
# TAB 4: TAX WIZARD (IMPACT FOCUSED)
# ------------------------------------------
with tab4:
    st.header("US Expat Tax Impact Wizard")
    agree = st.checkbox("I understand this is for informational purposes only and does not constitute legal tax advice.", value=False)
    if not agree:
        st.stop()
        
    q1 = st.radio("Are you a US Citizen or Green Card Holder?", ["Yes", "No"], index=0)
    
    if q1 == "Yes":
        foreign_income = st.number_input("What is your total estimated foreign-earned income (in USD) this year?", min_value=0, value=85000, step=1000)
        q3 = st.radio("Did you have >$10,000 in foreign financial accounts at any time?", ["Yes", "No"], index=1)
        
        if st.button("Calculate Potential Impact", type="primary"):
            st.divider()
            feie_limit = 126500 # 2024 limit
            
            if foreign_income > 0:
                potential_exclusion = min(foreign_income, feie_limit)
                # Rough estimate of tax saved (assuming ~24% average marginal rate for simplicity, NOT advice)
                estimated_savings = potential_exclusion * 0.24 
                
                st.subheader("Potential FEIE Impact")
                col1, col2 = st.columns(2)
                col1.metric("Income Eligible for Exclusion", f"${potential_exclusion:,.2f}")
                col2.metric("Estimated Tax Savings*", f"${estimated_savings:,.2f}")
                st.caption("*Based on a rough 24% marginal tax rate. Actual savings depend on your specific tax bracket.")
                
            st.subheader("Required Forms Checklist")
            st.checkbox("Form 1040 (Base Return)", value=True, disabled=True)
            st.checkbox(f"Form 2555 (To claim the ${potential_exclusion:,.0f} exclusion)", value=True, disabled=True)
            if q3 == "Yes":
                st.checkbox("FBAR (FinCEN 114) - CRITICAL DUE APRIL 15", value=True, disabled=True)
