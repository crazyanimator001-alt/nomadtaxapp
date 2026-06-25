"""Form 2555 (FEIE) and Form 1116 (FTC) calculations."""
import pandas as pd
from config import FEIE_LIMIT_2024


def calculate_form_2555(df: pd.DataFrame) -> dict:
    if df.empty or "Foreign Earned Income" not in df.columns:
        return {"eligible_income": 0, "exclusion": 0, "row_count": 0, "feie_limit": FEIE_LIMIT_2024}

    foreign_df = df[df["Foreign Earned Income"] & (df["USD Amount"] > 0)]
    eligible = float(foreign_df["USD Amount"].sum())
    exclusion = min(eligible, FEIE_LIMIT_2024)
    return {"eligible_income": eligible, "exclusion": exclusion, "feie_limit": FEIE_LIMIT_2024, "row_count": len(foreign_df)}


def calculate_form_1116(df: pd.DataFrame) -> dict:
    if df.empty or "Foreign Tax Paid" not in df.columns:
        return {"foreign_tax_paid": 0, "credit_available": 0, "row_count": 0}

    tax_df = df[df["Foreign Tax Paid"]]
    paid = float(tax_df["USD Amount"].abs().sum())
    return {"foreign_tax_paid": paid, "credit_available": paid, "row_count": len(tax_df)}


def wow_moment_text(feie: dict, ftc: dict) -> str:
    parts = []
    if feie["eligible_income"] > 0:
        parts.append(f"💡 We found **${feie['eligible_income']:,.0f}** of eligible foreign income across **{feie['row_count']} transactions** and mapped it directly to your **Form 2555** requirements (Up to the ${feie['feie_limit']:,} limit).")
    if ftc["foreign_tax_paid"] > 0:
        parts.append(f"💰 We identified **${ftc['foreign_tax_paid']:,.0f}** in foreign taxes paid, eligible for **Form 1116** Foreign Tax Credit.")
    return "\n\n".join(parts) if parts else "No foreign income detected. Upload more transactions."
