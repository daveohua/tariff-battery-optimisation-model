import streamlit as st
import pandas as pd
from main import process_all_seasons

st.set_page_config(page_title="SME Battery Savings Simulator", layout="wide")

# ---------- Helpers ----------
def sp_to_time_str(sp: int) -> str:
    # SP 1 = 00:00-00:30. We'll label the *start* time.
    minutes = (sp - 1) * 30
    hh = minutes // 60
    mm = minutes % 60
    return f"{hh:02d}:{mm:02d}"

def add_time_labels(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["Time"] = out["SettlementPeriod"].astype(int).map(sp_to_time_str)
    return out

def annualise_from_4_weeks(season_weeks_gbp: dict) -> float:
    # dict like {"winter": 50080, "spring": 35294, ...} in £ per representative week
    return 13 * sum(season_weeks_gbp.values())

def gbp(x_pence: float) -> float:
    return x_pence / 100.0

# ---------- Load data ----------
# Replace this with however you store your seasonal results.
# Expect a dict: season -> df_results (one week of 48*7 rows)
# Example:
# dfs = {
#   "winter": pd.read_parquet("winter.parquet"),
#   "spring": pd.read_parquet("spring.parquet"),
#   "summer": pd.read_parquet("summer.parquet"),
#   "autumn": pd.read_parquet("autumn.parquet"),
# }
dfs = process_all_seasons()

st.title("SME Battery Savings Simulator")
st.caption("Fixed tariff → Dynamic tariff → Dynamic + battery (site-first dispatch)")

if not dfs:
    st.warning("Hook up your seasonal results dataframes (winter/spring/summer/autumn) in the `dfs` dict.")
    st.stop()

# ---------- Sidebar controls ----------
season = st.sidebar.selectbox("Season", list(dfs.keys()))
df = add_time_labels(dfs[season])

available_days = sorted(df["SettlementDate"].astype(str).unique())
day = st.sidebar.selectbox("Day", available_days)
day_df = df[df["SettlementDate"].astype(str) == day].copy()

# ---------- Compute weekly totals (for selected season) ----------
# Assumes your *_Cost_p columns are per settlement period
week_fixed_gbp = gbp(df["TariffNoBatteryCost_p"].sum())
week_dynamic_gbp = gbp(df["WholesaleNoBatteryCost_p"].sum())
week_dynamic_batt_gbp = gbp(df["NetCost_p"].sum())

# ---------- Top KPIs ----------
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Fixed tariff (week)", f"£{week_fixed_gbp:,.0f}")
c2.metric("Dynamic tariff (week)", f"£{week_dynamic_gbp:,.0f}", delta=f"£{(week_fixed_gbp-week_dynamic_gbp):,.0f}")
c3.metric("Dynamic + battery (week)", f"£{week_dynamic_batt_gbp:,.0f}", delta=f"£{(week_dynamic_gbp-week_dynamic_batt_gbp):,.0f}")
c4.metric("Total savings (week)", f"£{(week_fixed_gbp-week_dynamic_batt_gbp):,.0f}")
c5.metric("Assumed peak usage", "300kW")
c6.metric("Battery size", "260kWh")

st.divider()

# ---------- Daily trace ----------
st.subheader(f"Daily behaviour ({season.title()} – {day})")

# Build a compact trace table for charting
trace = day_df[[
    "Time",
    "Usage_kW",
    "GridImport_kW",
    "Charge_kW",
    "Discharge_kW",
    "SOC_kWh",
    "SupplierImportPrice_p_kWh",
]].copy()

# Convert discharge to negative for a single “battery power” line if desired
trace["BatteryPower_kW"] = trace["Charge_kW"] - trace["Discharge_kW"]

left, right = st.columns([2, 1])

with left:
    st.line_chart(
        trace.set_index("Time")[["Usage_kW", "GridImport_kW", "BatteryPower_kW"]]
    )

with right:
    st.write("Battery stats (day)")
    st.metric("Min SOC (kWh)", f"{trace['SOC_kWh'].min():.1f}")
    st.metric("Max SOC (kWh)", f"{trace['SOC_kWh'].max():.1f}")
    st.metric("Total charge (kWh)", f"{(trace['Charge_kW'].sum() * 0.5):.0f}")
    st.metric("Total discharge (kWh)", f"{(trace['Discharge_kW'].sum() * 0.5):.0f}")
    st.metric("Peak import (kW)", f"{trace['GridImport_kW'].max():.0f}")

with st.expander("Show raw day data"):
    st.dataframe(day_df, use_container_width=True)

st.divider()

# ---------- Assumptions ----------
st.subheader("Assumptions")
st.markdown(
    """
- Battery: **260 kWh**, **100 kW** (charge/discharge), SOC min **10%**, round-trip eff. ~**90%**
- Dynamic tariff: **MIP + adder** (import), export at **MIP**
- Dispatch: **site-first discharge** (primarily avoids grid import), charge in low-price periods
- Excludes: **local flexibility revenues**, capacity/ancillary services, degradation costs
"""
)