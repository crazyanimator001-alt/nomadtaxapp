import pandas as pd
import streamlit as st
from config import MAX_ROWS

def categorize_transactions(df):
    """100% Free, instant rules-based categorization. No API keys needed."""
    if len(df) > MAX_ROWS:
        st.error("CSV has " + str(len(df)) + " rows; max is " + str(MAX_ROWS) + ".")
        st.stop()

    results = {}
    progress = st.progress(0.0, text="Categorizing transactions (Instant & Free)...")
    
    for i, (idx, row) in enumerate(df.iterrows()):
        desc = str(row["Description"]).lower()
        amount = float(row["Amount"])
        
        category = "Personal: Uncategorized"
        reasoning = "No specific keywords matched."
        is_foreign = False
        
        # --- RULES ENGINE ---
        if any(word in desc for word in ["airbnb", "hotel", "hostel", "flight", "ryanair", "emirates", "booking.com"]):
            category = "Business: Travel (IRC §274)"
            reasoning = f"Matched travel keyword in '{row['Description']}'."
            is_foreign = True
        elif any(word in desc for word in ["aws", "github", "google cloud", "netflix", "spotify", "chatgpt", "domain", "hostinger"]):
            category = "Business: Software & Subscriptions (IRC §162)"
            reasoning = "Matched software/service keyword."
            is_foreign = True
        elif any(word in desc for word in ["coworking", "we work", "cafe", "coffee shop", "dojo"]):
            category = "Business: Co-working / Office"
            reasoning = "Matched workspace keyword."
            is_foreign = True
        elif any(word in desc for word in ["transfer", "withdrawal", "atm", "wise", "revolut"]):
            category = "Personal: Transfer / Withdrawal"
            reasoning = "Identified as a fund transfer or ATM withdrawal."
        elif any(word in desc for word in ["restaurant", "bar", "pub", "mcdonalds", "starbucks", "ubereats"]):
            category = "Personal: Dining & Food"
            reasoning = "Identified as a food/dining expense."
        elif any(word in desc for word in ["supermarket", "grocery", "7-eleven", "circle k", "tesco"]):
            category = "Personal: Groceries"
            reasoning = "Identified as a grocery store."
            
        # If it's a positive income amount, flag it
        if amount > 0:
            is_foreign = True
            if category == "Personal: Uncategorized":
                category = "Business: Income"
                reasoning = "Positive amount identified as foreign income."

        results[str(idx)] = {
            "category": category,
            "reasoning": reasoning,
            "is_foreign_earned_income": is_foreign,
            "foreign_tax_paid": False
        }
        
        progress.progress((i + 1) / len(df))
        
    progress.empty()
    return _merge_results(df, results)


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
            "Reasoning": ai.get("reasoning", "Rule engine skipped."),
            "Foreign Earned Income": bool(ai.get("is_foreign_earned_income", False)),
            "Foreign Tax Paid": bool(ai.get("foreign_tax_paid", False)),
        })
    return pd.DataFrame(rows)
