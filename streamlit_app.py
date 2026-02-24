import streamlit as st
import pandas as pd
import datetime
import plotly.graph_objects as go
from main import process_all_seasons

st.set_page_config(page_title="Battery Savings Simulator", layout="wide")

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

def gbp(x_pence: float) -> float:
    return x_pence / 100.0

st.title("Flexibility-native Electricity Savings Simulator")
st.caption("How much could you save when you switch from a fixed tariff to a dynamic tariff with battery optimisation? (For information purposes only)")

# ---------- Sidebar controls ----------

fixed_tariff_px = st.sidebar.number_input(
    "Fixed tariff price (p/kWh)", value=21
)

yearly_peak_site_demand = st.sidebar.number_input(
    "Yearly peak site demand (kW)", value=150
)

battery_size = st.sidebar.number_input(
    "Battery size (kWh)", value=260
)

dfs = process_all_seasons(fixed_tariff_px=fixed_tariff_px, peak_load=yearly_peak_site_demand, battery_size=battery_size)

season = st.sidebar.segmented_control(
    "Select season",
    ["Winter", "Spring", "Summer", "Autumn"],
    default="Winter",
)
df = add_time_labels(dfs[season.upper()])

day = st.sidebar.segmented_control(
    "Select day",
    ["Monday", "Saturday", "Sunday"],
    default="Monday"
)
df["SettlementDate"] = pd.to_datetime(df["SettlementDate"])
day_df = df[df["SettlementDate"].dt.day_name() == day].copy()
plan = st.sidebar.segmented_control(
    "Select plan",
    ["Fixed", "Dynamic", "Dynamic+Battery"],
    default="Fixed"
)
# compute year totals
year_fixed_gbp = gbp(sum(df["TariffNoBatteryCost_p"].sum() * 13 for df in dfs.values()))
year_dynamic_gbp = gbp(sum(df["WholesaleNoBatteryCost_p"].sum() * 13 for df in dfs.values()))
year_dynamic_batt_gbp = gbp(sum(df["NetCost_p"].sum() * 13 for df in dfs.values()))


# ---------- Compute weekly totals (for selected season) ----------
# Assumes your *_Cost_p columns are per settlement period
week_fixed_gbp = gbp(df["TariffNoBatteryCost_p"].sum())
week_dynamic_gbp = gbp(df["WholesaleNoBatteryCost_p"].sum())
week_dynamic_batt_gbp = gbp(df["NetCost_p"].sum())

# ---------- Top KPIs ----------
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Fixed tariff (year)", f"£{year_fixed_gbp:,.0f}")
c2.metric("Dynamic tariff (year)", f"£{year_dynamic_gbp:,.0f}", delta=f"-£{(year_fixed_gbp-year_dynamic_gbp):,.0f}", delta_color="inverse")
c3.metric("Dynamic + battery (year)", f"£{year_dynamic_batt_gbp:,.0f}", delta=f"-£{(year_fixed_gbp-year_dynamic_batt_gbp):,.0f}", delta_color="inverse")
c4.metric("Total savings (year)", f"£{(year_fixed_gbp-year_dynamic_batt_gbp):,.0f}")
c5.metric("Yearly peak site demand", f"{yearly_peak_site_demand}kW")
c6.metric("Battery size", f"{battery_size}kWh")

st.divider()

c7, c8, c9, c10 = st.columns(4)
c7.metric(f"Fixed tariff (typical {season.lower()} week)", f"£{week_fixed_gbp:,.0f}")
c8.metric(f"Dynamic tariff (typical {season.lower()} week)", f"£{week_dynamic_gbp:,.0f}", delta=f"-£{(week_fixed_gbp-week_dynamic_gbp):,.0f}", delta_color="inverse")
c9.metric(f"Dynamic + battery (typical {season.lower()} week)", f"£{week_dynamic_batt_gbp:,.0f}", delta=f"-£{(week_fixed_gbp-week_dynamic_batt_gbp):,.0f}", delta_color="inverse")
c10.metric(f"Total savings (typical {season.lower()} week)", f"£{(week_fixed_gbp-week_dynamic_batt_gbp):,.0f}")

st.divider()
# ---------- Daily trace ----------
st.subheader(f"Half-hourly consumption, typical {day} in {season.lower()}")

trace = day_df[[
    "Time",
    "Usage_kW",
    "GridImport_kW",
    "Charge_kW",
    "Discharge_kW",
    "SOC_kWh",
    "SupplierImportPrice_p_kWh",
]].copy()

trace["BatteryPower_kW"] = trace["Charge_kW"] - trace["Discharge_kW"]
trace["fixedTariffPrice_p_kWh"] = fixed_tariff_px
trace["consumerPrice_p_kWh"] = trace["fixedTariffPrice_p_kWh"] if plan == "Fixed" else trace["SupplierImportPrice_p_kWh"]

price_cols = [
    "SupplierImportPrice_p_kWh",
    "fixedTariffPrice_p_kWh"
]

y2_min = trace[price_cols].min().min()
y2_max = trace[price_cols].max().max()

padding = (y2_max - y2_min) * 0.05
y2_range = [y2_min - padding, y2_max + padding]

consumption_cols = [
    "Usage_kW",
    "GridImport_kW"
]

y_min = trace[consumption_cols].min().min()
y_max = trace[consumption_cols].max().max()

padding = (y_max - y_min) * 0.05
y_range = [y_min - padding, y_max + padding]

trace = trace.set_index("Time")

# Create figure
fig = go.Figure()

# Usage bars (left axis)
fig.add_trace(go.Bar(
    x=trace.index,
    y=trace["Usage_kW"],
    name="Usage (kW)",
    yaxis="y"
))

if plan == "Dynamic+Battery":
    fig.add_trace(go.Bar(
        x=trace.index,
        y=trace["GridImport_kW"],
        name="Grid Import (kW)",
        yaxis="y"
    ))


# Price line (right axis)
fig.add_trace(go.Scatter(
    x=trace.index,
    y=trace["consumerPrice_p_kWh"],
    name="Price (p/kWh)",
    mode="lines",
    yaxis="y2"
))

if plan != "Fixed":
    fig.add_trace(go.Scatter(
        x=trace.index,
        y=trace["fixedTariffPrice_p_kWh"],
        name="Fixed tariff price (p/kWh)",
        mode="lines",
        yaxis="y2",
        line=dict(dash="dash")
    ))

# Layout with dual axes
fig.update_layout(
    xaxis=dict(title="Time"),
    yaxis=dict(title="Usage (kW)", range = y_range, showgrid=False),
    yaxis2=dict(
        title="Price (p/kWh)",
        overlaying="y",
        side="right",
        range=y2_range,
        showgrid=False
    ),
    legend=dict(orientation="h")
)

st.plotly_chart(fig, use_container_width=True)

with st.expander("Show raw data"):
    st.dataframe(day_df, use_container_width=True)

st.subheader("Notes and Assumptions")
st.markdown(""" 
    - This dashboard is powered by a model that models the electricity demand and battery usage of a single site, for an indicative week in each season. These are extrapolated to produce yearly figures.
    - Electricity prices for the dynamic tariff track Elexon Market Index Prices for the settlement periods the model is ran on, with a premium of 10 pence representing the supplier's margin.
    - The battery optimisation model is a very simple rule based system. Optimisation models in real life are very sophisticated and will produce better savings.
    - The modelled demand pattern is based on Elexon Load Profile 5, which most closely matches businesses with maximum demand above 100kW who have high demand peaks as opposed to sustained demand. 
    - The model for the dynamic tariff with battery optimisation focusses on tariff arbitrage with site-first dispatch. Notably it does not take into account distribution network costs. More savings could be made from exporting electricity to wholesale markets, further demand shaving to avoid distribution network costs, and revenue gained from participation in flexibility and capacity markets.
""")