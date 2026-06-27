"""NomadTax Copilot - The 1-Page Magic Flow."""
import streamlit as st
import pandas as pd
import plotly.express as px

from config import CUSTOM_CSS, REQUIRED_CSV_COLUMNS, MAX_ROWS
from ai_categorizer import categorize_transactions
from fx_rates import apply_fx_to_dataframe
from residency import render_predictor, render_us_spt, render_heatmap
from tax_forms import calculate_form_2555, calculate_form_1116, wow_moment_text
from pdf_report import generate_report

st.set_page_config(page_title="NomadTax Copilot", page_icon="✈️", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

for k, v in {"raw_df": None, "categorized_df": None, "fx_df": None}.items():
    st.session_state.setdefault(k, v)

st.title("✈️ NomadTax Copilot")
st.markdown("*Upload your bank statement. We handle the IRS math.*")

# ==========================================
# STEP 1: FRICTIONLESS UPLOAD & SMART MAPPER
# ==========================================
with st.container():
    col1, col2 = st.columns([3, 1])
    col1.subheader("Step 1: Upload Bank Statement")
    col2.markdown("### [?] Supported\nWise, Revolut, PayPal, Monzo")
    
    uploaded = st.file_uploader("Drop your raw CSV file here (Do not edit it)", type=["csv"])

    if uploaded:
        # File size guard
        if uploaded.size > 5 * 1024 * 1024:
            st.error("File too large. Maximum size is 5 MB.")
            st.stop()
        
        try:
            df_raw = pd.read_csv(uploaded)
            if len(df_raw) > MAX_ROWS:
                st.error("Too many rows (" + str(len(df_raw)) + "). Max is " + str(MAX_ROWS) + ".")
                st.stop()
                
            df_raw.columns = [c.strip() for c in df_raw.columns]
            
            # THE SMART MAPPER: Handles raw bank exports automatically
            column_map = {
                "Date": ["transaction date", "date", "completed date", "booking date", "value date"],
                "Description": ["description", "reference", "merchant", "memo", "details", "transaction description"],
                "Amount": ["amount", "base amount", "original amount", "transaction amount", "fx amount"],
                "Currency": ["currency", "billing currency", "original currency", "fx currency"]
            }
            
            for target, variations in column_map.items():
                if target not in df_raw.columns:
                    for var in variations:
                        if var in df_raw.columns:
                            df_raw.rename(columns={var: target}, inplace=True)
                            break
                            
            missing = REQUIRED_CSV_COLUMNS - set(df_raw.columns)
            if missing:
                st.error("Could not auto-detect columns. Missing: " + str(missing) + ". Found: " + str(list(df_raw.columns)))
                st.stop()
                
            # Auto-run categorization instantly upon upload
            with st.spinner("Analyzing transactions and categorizing deductions..."):
                st.session_state.categorized_df = categorize_transactions(df_raw)
            st.session_state.fx_df = None
            st.success("Upload successful! Proceed to Step 2.")
            
        except Exception as e:
            st.error("Could not read CSV: " + str(e))

# ==========================================
# STEP 2: HUMAN-IN-THE-LOOP REVIEW
# ==========================================
if st.session_state.categorized_df is not None:
    st.divider()
    st.subheader("Step 2: Review & Fix Categories")
    st.caption("The AI caught most of them. Click any category dropdown to fix mistakes (e.g., changing 'Uncategorized' to 'Business: Software').")
    
    # The powerful Data Editor with Dropdowns
    edited_df = st.data_editor(
        st.session_state.categorized_df, 
        use_container_width=True,
        column_config={
            "Category": st.column_config.SelectboxColumn(
                options=[
                    "Business: Travel (IRC §274)",
                    "Business: Lodging (IRC §274)",
                    "Business: Co-working / Office",
                    "Business: Software & Subscriptions (IRC §162)",
                    "Business: Internet & Phone",
                    "Business: Income",
                    "Personal: Dining & Food",
                    "Personal: Groceries",
                    "Personal: Transfer / Withdrawal",
                    "Personal: Uncategorized"
                ],
                required=True,
            )
        },
        hide_columns=["Reasoning", "Foreign Earned Income", "Foreign Tax Paid"]
    )
    
    # Save user fixes back to session state
    st.session_state.categorized_df = edited_df

    # ==========================================
    # STEP 3: FX CONVERSION & THE PAYWALL
    # ==========================================
    st.divider()
    st.subheader("Step 3: Generate IRS-Compliant Report")
    
    if st.button("💱 Apply IRS-Approved FX Rates", type="primary", use_container_width=True):
        with st.spinner("Fetching exact daily Treasury rates for your transactions..."):
            st.session_state.fx_df = apply_fx_to_dataframe(st.session_state.categorized_df)

    if st.session_state.fx_df is not None:
        df = st.session_state.fx_df
        feie = calculate_form_2555(df)
        ftc = calculate_form_1116(df)

        # THE WOW MOMENT METRICS
        st.markdown(wow_moment_text(feie, ftc))
        st.divider()

        total_biz = float(df[df["Category"].str.contains("Business", na=False)]["USD Amount"].fillna(0).sum())
        c1, c2, c3 = st.columns(3)
        c1.metric("Potential Deductions Found", "$" + f"{total_biz:,.0f}")
        c2.metric("Form 2555 Eligible Income", "$" + f"{feie['eligible_income']:,.0f}")
        c3.metric("Currencies Processed", df["Currency"].nunique())

        st.divider()
        
        # VISUAL CHARTS
        col_a, col_b = st.columns(2)
        with col_a:
            df["Type"] = df["Category"].str.split(":", n=1).str[0]
            tot = df.groupby("Type")["USD Amount"].sum().reset_index()
            fig = px.pie(tot, values="USD Amount", names="Type", hole=0.6, color_discrete_map={"Business": "#10b981", "Personal": "#94a3b8"})
            st.plotly_chart(fig, use_container_width=True)
        with col_b:
            biz = df[df["Type"] == "Business"].groupby("Category")["USD Amount"].sum().sort_values(ascending=False).head(5).reset_index()
            fig = px.bar(biz, x="USD Amount", y="Category", orientation="h", color_discrete_sequence=["#6366f1"])
            st.plotly_chart(fig, use_container_width=True)

        # THE AUDIT TRAIL
        with st.expander("🔍 View Detailed Audit Trail"):
            st.dataframe(df[["Date", "Description", "Original Amount", "Currency", "FX Rate", "USD Amount", "Category"]], use_container_width=True)

        # THE PAYWALL ILLUSION
        st.divider()
        st.markdown("---")
        col_pay1, col_pay2, col_pay3 = st.columns([1,1,1])
        with col_pay2:
            # This is a dummy button to test conversion desire
            pay_button = st.button("🔒 Unlock Audit-Ready PDF ($29 One-Time)", type="primary", use_container_width=True)
            if pay_button:
                st.info("Payment gateway integrating soon. For early access, DM us on Twitter/X @NomadTaxCopilot.")
                
        # Hidden actual download for founders/testing (Remove in production)
        with st.expander("⚙️ Developer: Download Test PDF"):
            pdf_bytes = generate_report(df, feie, ftc)
            st.download_button("📄 Download Raw PDF", data=pdf_bytes, file_name="NomadTax_Report.pdf", mime="application/pdf")

# ==========================================
# APPENDIX: ADVANCED TOOLS (Hidden from main flow)
# ==========================================
st.markdown("---")
with st.expander("🧮 Advanced Tools: Residency Tracker & Tax Wizard"):
    tab_res, tab_wiz = st.tabs(["Residency Engine", "Tax Wizard"])
    
    with tab_res:
        st.info("⚠️ Standard day-count rules only. Tax treaties not modeled.")
        render_us_spt()
        st.divider()
        projections = []
        c1, c2 = st.columns(2)
        with c1: projections.append(render_predictor("Spain", "🇪🇸", "es_days"))
        with c2: projections.append(render_predictor("Portugal", "🇵🇹", "pt_days"))
        render_heatmap(projections)

    with tab_wiz:
        st.header("US Expat Tax Wizard")
        if not st.checkbox("I understand this is informational only, not legal advice."):
            st.stop()
        if st.radio("US Citizen / Green Card Holder?", ["Yes", "No"]) == "No":
            st.info("FEIE/FTC do not apply. Consult a CPA.")
            st.stop()

        default_foreign = 0.0
        feie_data = {"eligible_income": 0, "exclusion": 0, "row_count": 0, "feie_limit": 126500}
        ftc_data = {"foreign_tax_paid": 0, "credit_available": 0, "row_count": 0}

        if st.session_state.fx_df is not None:
            feie_data = calculate_form_2555(st.session_state.fx_df)
            ftc_data = calculate_form_1116(st.session_state.fx_df)
            default_foreign = feie_data["eligible_income"]
            st.success("✅ Pre-filled from your CSV: $" + f"{default_foreign:,.0f}" + " detected.")

        foreign_income = st.number_input("Total foreign-earned income (USD)", min_value=0.0, value=float(default_foreign), step=1000.0)
        has_fbar = st.radio(">$10,000 in foreign accounts?", ["Yes", "No"], index=1)

        if st.button("Calculate Impact", type="primary"):
            st.divider()
            manual_feie = {
                "eligible_income": foreign_income,
                "exclusion": min(foreign_income, feie_data.get("feie_limit", 126500)),
                "row_count": feie_data["row_count"],
                "feie_limit": 126500,
            }
            st.markdown(wow_moment_text(manual_feie, ftc_data))
            c1, c2 = st.columns(2)
            c1.metric("FEIE Exclusion Cap", "$" + f"{manual_feie['exclusion']:,.0f}")
            c2.metric("FTC Available", "$" + f"{ftc_data['credit_available']:,.0f}")
            st.subheader("Required Forms")
            st.checkbox("Form 1040", value=True, disabled=True)
            if manual_feie["exclusion"] > 0: st.checkbox("Form 2555", value=True, disabled=True)
            if ftc_data["credit_available"] > 0: st.checkbox("Form 1116", value=True, disabled=True)
            if has_fbar == "Yes": st.checkbox("FBAR (FinCEN 114)", value=True, disabled=True)
