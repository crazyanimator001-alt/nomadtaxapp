"""NomadTax OS - main Streamlit app."""
import streamlit as st
import pandas as pd
import plotly.express as px

from config import CUSTOM_CSS, REQUIRED_CSV_COLUMNS, MAX_ROWS
from ai_categorizer import categorize_transactions
from fx_rates import apply_fx_to_dataframe
from residency import render_predictor, render_us_spt, render_heatmap
from tax_forms import calculate_form_2555, calculate_form_1116, wow_moment_text
from pdf_report import generate_report

st.set_page_config(page_title="NomadTax OS", page_icon="✈️", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

for k, v in {"raw_df": None, "categorized_df": None, "fx_df": None}.items():
    st.session_state.setdefault(k, v)

with st.sidebar:
    st.markdown("### Setup")
    if "OPENAI_API_KEY" not in st.secrets:
        st.session_state["user_openai_key"] = st.text_input("OpenAI API Key", type="password", help="Stored only in this session.")
    st.markdown("---")
    st.caption("Row limit: " + str(MAX_ROWS))
    st.caption("FX source: US Treasury")

st.title("✈️ NomadTax OS")
st.markdown("*The tax operating system for location-independent earners.*")

tab1, tab2, tab3, tab4 = st.tabs(["1. Upload & Categorize", "2. FX & Analytics", "3. Residency Engine", "4. Tax Wizard"])

# ============ TAB 1 ============
with tab1:
    st.header("Upload Bank Statement")
    uploaded = st.file_uploader("Wise, Revolut, or any CSV with Date/Description/Amount/Currency", type=["csv"])
    
    # DEMO DATA BUTTON
    if st.button("🎯 Try with Demo Data (No upload needed)", type="secondary"):
        demo_data = {
            "Date": ["2024-03-01", "2024-03-05", "2024-03-10", "2024-03-15", "2024-03-20"],
            "Description": ["Airbnb Lisbon", "Transfer to Savings", "Dojo Coworking Bali", "Chillout Bar", "AWS Hosting"],
            "Amount": [1200.00, -500.00, 150000.00, 25.00, 29.00],
            "Currency": ["EUR", "USD", "IDR", "THB", "USD"]
        }
        st.session_state.raw_df = pd.DataFrame(demo_data)
        st.success("Demo data loaded! Click 'Run AI Categorization' below.")
        st.rerun()

    # Process uploaded file if it exists
    if uploaded:
        try:
            df_raw = pd.read_csv(uploaded)
            df_raw.columns = [c.strip() for c in df_raw.columns]
            canonical = {c.lower(): c for c in ["Date", "Description", "Amount", "Currency"]}
            df_raw.rename(columns={c: canonical[c.lower()] for c in df_raw.columns if c.lower() in canonical}, inplace=True)

            missing = REQUIRED_CSV_COLUMNS - set(df_raw.columns)
            if missing:
                st.error("Missing required columns: " + str(missing))
                st.stop()
                
            st.session_state.raw_df = df_raw

    # Show data preview if it exists (from upload OR demo button)
    if st.session_state.raw_df is not None:
        st.write("Loaded **" + str(len(st.session_state.raw_df)) + "** rows.")
        st.dataframe(st.session_state.raw_df.head(10), use_container_width=True)

        if st.button("🤖 Run AI Categorization", type="primary"):
            st.session_state.categorized_df = categorize_transactions(st.session_state.raw_df)
            st.session_state.fx_df = None
            st.success("Categorization complete. See Tab 2.")

    if st.session_state.categorized_df is not None:
        st.dataframe(st.session_state.categorized_df, use_container_width=True)


# ============ TAB 2 ============
with tab2:
    if st.session_state.categorized_df is None:
        st.info("Upload and categorize a CSV in Tab 1 first.")
    else:
        if st.session_state.fx_df is None:
            if st.button("💱 Apply IRS-Approved FX Rates", type="primary"):
                st.session_state.fx_df = apply_fx_to_dataframe(st.session_state.categorized_df)

        if st.session_state.fx_df is not None:
            df = st.session_state.fx_df
            feie = calculate_form_2555(df)
            ftc = calculate_form_1116(df)

            st.markdown(wow_moment_text(feie, ftc))
            st.divider()

            total_biz = float(df[df["Category"].str.contains("Business", na=False)]["USD Amount"].fillna(0).sum())
            c1, c2, c3 = st.columns(3)
            c1.metric("Deductions Found", "$" + f"{total_biz:,.0f}")
            c2.metric("Form 2555 Eligible", "$" + f"{feie['eligible_income']:,.0f}")
            c3.metric("Form 1116 FTC", "$" + f"{ftc['foreign_tax_paid']:,.0f}")

            st.divider()
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

            st.subheader("Audit Trail")
            st.dataframe(df[["Date", "Description", "Original Amount", "Currency", "FX Rate", "USD Amount", "Category"]], use_container_width=True)

            pdf_bytes = generate_report(df, feie, ftc)
            st.download_button("📄 Download Audit-Ready PDF", data=pdf_bytes, file_name="NomadTax_Report.pdf", mime="application/pdf", type="primary")

# ============ TAB 3 ============
with tab3:
    st.header("Multi-Jurisdiction Residency Engine")
    st.info("⚠️ Standard day-count rules only. Tax treaties not modeled. Consult a CPA.")

    render_us_spt()
    st.divider()
    
    projections = []
    c1, c2 = st.columns(2)
    with c1:
        projections.append(render_predictor("Spain", "🇪🇸", "es_days"))
    with c2:
        projections.append(render_predictor("Portugal", "🇵🇹", "pt_days"))

    st.divider()
    render_heatmap(projections)

# ============ TAB 4 ============
with tab4:
    st.header("US Expat Tax Wizard")
    if not st.checkbox("I understand this is informational only, not legal advice."):
        st.stop()

    if st.radio("US Citizen / Green Card Holder?", ["Yes", "No"]) == "No":
        st.info("FEIE/FTC do not apply. Consult a CPA for your home-country obligations.")
        st.stop()

    default_foreign = 0.0
    feie_data = {"eligible_income": 0, "exclusion": 0, "row_count": 0, "feie_limit": 126500}
    ftc_data = {"foreign_tax_paid": 0, "credit_available": 0, "row_count": 0}

    if st.session_state.fx_df is not None:
        feie_data = calculate_form_2555(st.session_state.fx_df)
        ftc_data = calculate_form_1116(st.session_state.fx_df)
        default_foreign = feie_data["eligible_income"]
        st.success("✅ Pre-filled from your CSV: $" + f"{default_foreign:,.0f}" + " foreign income detected.")

    foreign_income = st.number_input("Total foreign-earned income (USD)", min_value=0.0, value=float(default_foreign), step=1000.0)
    has_fbar = st.radio(">$10,000 in foreign accounts at any time?", ["Yes", "No"], index=1)

    if st.button("Calculate Impact", type="primary"):
        st.divider()
        manual_feie = {
            "eligible_income": foreign_income,
            "exclusion": min(foreign_income, feie_data.get("feie_limit", 126500)),
            "row_count": feie_data["row_count"],
            "feie_limit": 126500,
        }
        st.markdown(wow_moment_text(manual_feie, ftc_data))
        st.divider()

        c1, c2 = st.columns(2)
        c1.metric("FEIE Exclusion Cap", "$" + f"{manual_feie['exclusion']:,.0f}")
        c2.metric("FTC Available", "$" + f"{ftc_data['credit_available']:,.0f}")

        st.subheader("Required Forms")
        st.checkbox("Form 1040 (Base Return)", value=True, disabled=True)
        if manual_feie["exclusion"] > 0:
            st.checkbox("Form 2555 — claim $" + f"{manual_feie['exclusion']:,.0f}" + " exclusion", value=True, disabled=True)
        if ftc_data["credit_available"] > 0:
            st.checkbox("Form 1116 — claim $" + f"{ftc_data['credit_available']:,.0f}" + " credit", value=True, disabled=True)
        if has_fbar == "Yes":
            st.checkbox("FBAR (FinCEN 114) — DUE APRIL 15", value=True, disabled=True)
