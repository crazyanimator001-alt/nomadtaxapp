"""Audit-ready PDF generation using fpdf2 with Unicode support."""
import datetime
import pandas as pd
from fpdf import FPDF


class TaxReportPDF(FPDF):
    def header(self):
        if self.page_no() == 1: return
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(99, 102, 241)
        self.cell(0, 10, "NomadTax OS — Confidential", ln=True, align="R")
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def _safe(s) -> str:
    return str(s).encode("latin-1", errors="replace").decode("latin-1")


def generate_report(df: pd.DataFrame, feie: dict, ftc: dict) -> bytes:
    pdf = TaxReportPDF()
    pdf.set_auto_page_break(auto=True, margin=20)

    # COVER PAGE
    pdf.add_page()
    pdf.ln(70)
    pdf.set_font("Helvetica", "B", 36)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 18, "NomadTax OS", ln=True, align="C")
    pdf.set_font("Helvetica", "", 16)
    pdf.set_text_color(99, 102, 241)
    pdf.cell(0, 10, "Annual Tax Summary Report", ln=True, align="C")
    pdf.ln(30)
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(0, 8, f"Generated: {datetime.datetime.now().strftime('%B %d, %Y')}", ln=True, align="C")
    pdf.cell(0, 8, f"Transactions analyzed: {len(df)}", ln=True, align="C")
    pdf.ln(60)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(150, 150, 150)
    pdf.multi_cell(0, 5, _safe("DISCLAIMER: This document is generated for informational purposes only and does not constitute legal or tax advice. Consult a qualified CPA before filing."), align="C")

    # EXECUTIVE SUMMARY
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 12, "Executive Summary", ln=True)
    pdf.ln(4)

    total = float(df["USD Amount"].fillna(0).sum())
    business = float(df[df["Category"].str.contains("Business", na=False)]["USD Amount"].fillna(0).sum())

    pdf.set_font("Helvetica", "", 11)
    summary_rows = [
        ("Total transactions", f"{len(df)}"),
        ("Total volume (USD)", f"${total:,.2f}"),
        ("Business / deductible (USD)", f"${business:,.2f}"),
        ("Personal (USD)", f"${total - business:,.2f}"),
        ("Form 2555 eligible income", f"${feie['eligible_income']:,.2f}"),
        ("Form 2555 exclusion (capped)", f"${feie['exclusion']:,.2f}"),
        ("Form 1116 foreign tax paid", f"${ftc['foreign_tax_paid']:,.2f}"),
    ]
    for label, val in summary_rows:
        pdf.set_fill_color(248, 250, 252)
        pdf.cell(110, 9, _safe(label), border=1, fill=True)
        pdf.cell(60, 9, _safe(val), border=1, align="R")
        pdf.ln()

    # CATEGORY BREAKDOWN
    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Category Breakdown", ln=True)
    cat_totals = df.groupby("Category")["USD Amount"].sum().sort_values(ascending=False).head(15)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(99, 102, 241)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(120, 8, "Category", border=1, fill=True)
    pdf.cell(50, 8, "Amount (USD)", border=1, fill=True, align="R")
    pdf.ln()
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 10)
    for cat, amt in cat_totals.items():
        pdf.cell(120, 7, _safe(cat)[:60], border=1)
        pdf.cell(50, 7, f"${amt:,.2f}", border=1, align="R")
        pdf.ln()

    # AUDIT TRAIL
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Transaction Audit Trail (first 50)", ln=True)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(99, 102, 241)
    pdf.set_text_color(255, 255, 255)
    headers = [("Date", 22), ("Description", 60), ("Curr", 12), ("Orig", 22), ("FX", 16), ("USD", 22), ("Category", 36)]
    for h, w in headers: pdf.cell(w, 7, h, border=1, fill=True)
    pdf.ln()
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 7)
    for _, row in df.head(50).iterrows():
        pdf.cell(22, 6, _safe(str(row.get("Date",""))[:10]), border=1)
        pdf.cell(60, 6, _safe(str(row.get("Description",""))[:35]), border=1)
        pdf.cell(12, 6, _safe(row.get("Currency","")), border=1)
        pdf.cell(22, 6, f"{float(row.get('Original Amount',0)):,.2f}", border=1, align="R")
        fx = row.get("FX Rate")
        pdf.cell(16, 6, f"{fx:.4f}" if fx else "—", border=1, align="R")
        usd = row.get("USD Amount")
        pdf.cell(22, 6, f"${usd:,.2f}" if usd else "—", border=1, align="R")
        pdf.cell(36, 6, _safe(str(row.get("Category",""))[:22]), border=1)
        pdf.ln()

    out = pdf.output(dest="S")
    return bytes(out) if isinstance(out, (bytes, bytearray)) else out.encode("latin-1", errors="replace")
