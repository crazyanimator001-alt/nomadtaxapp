"""Multi-jurisdiction residency engine: US SPT, Spain, Portugal."""
import datetime
import plotly.graph_objects as go
import streamlit as st
from config import RESIDENCY_DAY_THRESHOLD


def substantial_presence_test(current: int, prior: int, two_years_ago: int) -> dict:
    weighted = current + (prior / 3) + (two_years_ago / 6)
    triggered = weighted >= 183 and current >= 31
    return {"weighted_days": round(weighted, 1), "threshold": 183, "triggered": triggered, "current_year_days": current}


def project_residency_trigger(days_so_far: int, threshold: int = RESIDENCY_DAY_THRESHOLD) -> dict:
    today = datetime.date.today()
    days_elapsed = max((today - datetime.date(today.year, 1, 1)).days, 1)
    velocity = days_so_far / days_elapsed

    if days_so_far >= threshold:
        return {"status": "triggered", "trigger_date": None, "days_remaining": 0, "velocity": velocity}
    remaining = threshold - days_so_far
    if velocity <= 0:
        return {"status": "safe", "trigger_date": None, "days_remaining": remaining, "velocity": 0}

    days_until = remaining / velocity
    trigger_date = today + datetime.timedelta(days=days_until)
    if trigger_date.year > today.year:
        return {"status": "safe_through_year", "trigger_date": None, "days_remaining": remaining, "velocity": velocity}

    return {"status": "warning", "trigger_date": trigger_date, "days_remaining": remaining, "velocity": velocity}


def render_predictor(country: str, flag: str, key: str) -> dict:
    st.subheader(f"{flag} {country}")
    days = st.number_input(f"Days in {country} this year:", min_value=0, max_value=365, value=0, key=key)
    if days == 0:
        return {"country": country, "days": 0, "status": "no_data"}

    proj = project_residency_trigger(days)
    proj["country"] = country
    proj["days"] = days

    if proj["status"] == "triggered":
        st.error(f"**Threshold crossed.** You have exceeded the 183-day limit. This may trigger potential tax residency obligations. Consult a cross-border CPA immediately.")
    elif proj["status"] == "warning":
        st.warning(f"**Trigger date: {proj['trigger_date'].strftime('%b %d, %Y')}**  \nAt {proj['velocity']*30.44:.1f} days/month you'll cross 183. Staying past this date could trigger foreign tax residency.")
    else:
        st.success(f"Safe — {proj['days_remaining']} days remaining this year.")
    return proj


def render_us_spt() -> dict:
    st.subheader("🇺🇸 United States (Substantial Presence)")
    col1, col2, col3 = st.columns(3)
    cur = col1.number_input("Days this year", 0, 365, 0, key="us_cur")
    pri = col2.number_input("Days last year", 0, 365, 0, key="us_pri")
    two = col3.number_input("Days 2 years ago", 0, 365, 0, key="us_two")

    result = substantial_presence_test(cur, pri, two)
    if result["triggered"]:
        st.error(f"**SPT triggered.** Weighted days: {result['weighted_days']}/183. You are considered a US tax resident for the year under this test.")
    elif cur > 0:
        st.success(f"Below threshold: {result['weighted_days']}/183 weighted days. ({cur} this year + {pri/3:.1f} + {two/6:.1f})")
    return result


def render_heatmap(projections: list[dict]) -> None:
    months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    today = datetime.date.today()
    z, countries = [], []

    for p in projections:
        if p.get("status") == "no_data": continue
        countries.append(p["country"])
        row = []
        for m in range(1, 13):
            month_end = datetime.date(today.year, m, 28)
            days_into = max((month_end - datetime.date(today.year, 1, 1)).days, 1)
            projected = p.get("velocity", 0) * days_into
            risk = min(projected / 183, 1.5)
            row.append(risk)
        z.append(row)

    if not z:
        st.info("Enter days for at least one country to see the risk heatmap.")
        return

    fig = go.Figure(go.Heatmap(
        z=z, x=months, y=countries,
        colorscale=[[0, "#10b981"], [0.6, "#fbbf24"], [1.0, "#ef4444"]],
        zmin=0, zmax=1.5,
        colorbar=dict(title="Risk", tickvals=[0, 0.5, 1.0, 1.5], ticktext=["Safe", "Watch", "Trigger", "Over"]),
    ))
    fig.update_layout(title="Residency Risk Forecast", height=300)
    st.plotly_chart(fig, use_container_width=True)
