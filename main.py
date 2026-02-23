#!/usr/bin/env python
# coding: utf-8

# In[1]:


import pandas as pd
import datetime
import numpy as np
from enum import Enum
from dataclasses import dataclass

class Seasons(Enum):
    WINTER = ("Wtr", "datasets/mip2024dec.csv")
    SPRING = ("Spr", "datasets/mip2025mar.csv")
    SUMMER = ("Smr", "datasets/mip2025jun.csv")
    AUTUMN = ("Aut", "datasets/mip2025sep.csv")

MAX_LOAD_kW = 300

season, mkt_prices_csv = Seasons.AUTUMN.value



# In[2]:


load_profile = pd.read_csv("datasets/ProfileClass5.csv")


# In[3]:


load_profile["SettlementPeriod"] = load_profile.index + 1


# In[4]:


load_coefficients = load_profile.columns.difference(["Time", "SettlementPeriod"])
load_multiplier = MAX_LOAD_kW / load_profile[load_coefficients].to_numpy().max()
load_profile[load_coefficients] = load_profile[load_coefficients] * load_multiplier


# In[5]:


mkt_prices = pd.read_csv(mkt_prices_csv)


# In[6]:


mkt_prices = mkt_prices[mkt_prices["DataProvider"] == "APXMIDP"]
mkt_prices["Price_p_kWh"] = mkt_prices["Price"] / 10
mkt_prices["Weekday"] = mkt_prices["SettlementDate"].map(lambda x: datetime.date.fromisoformat(x).weekday())
mkt_prices = mkt_prices[["SettlementDate", "SettlementPeriod", "Price_p_kWh", "Weekday"]]


# In[7]:


mkt_prices = mkt_prices.merge(
    load_profile[["SettlementPeriod", f"{season} Wd", f"{season} Sat", f"{season} Sun"]],
    on="SettlementPeriod",
    how="left"
)

mkt_prices["Usage_kW"] = np.select(
    [
        mkt_prices["Weekday"] < 5,
        mkt_prices["Weekday"] == 5,
        mkt_prices["Weekday"] == 6
    ],
    [
        mkt_prices[f"{season} Wd"],
        mkt_prices[f"{season} Sat"],
        mkt_prices[f"{season} Sun"]
    ]
)

mkt_prices = mkt_prices[["SettlementDate", "SettlementPeriod", "Price_p_kWh", "Usage_kW"]]
mkt_prices = mkt_prices[::-1].reset_index(drop=True)


# In[8]:


quartiles = (
    mkt_prices.groupby("SettlementDate")["Price_p_kWh"]
    .quantile([0.25, 0.75])
    .unstack()
    .rename(columns={
        0.25: "Price_p_kWh_p25_day",
        0.75: "Price_p_kWh_p75_day"
    })
)

mkt_prices = mkt_prices.merge(quartiles, on="SettlementDate", how="left")

mkt_prices = mkt_prices[["SettlementDate", "SettlementPeriod", "Price_p_kWh", "Price_p_kWh_p25_day", "Price_p_kWh_p75_day", "Usage_kW"]]


# In[9]:


@dataclass
class Battery():
    soc_kWh: float = 130
    capacity_kWh: float = 260
    soc_min_kWh: float = 26
    max_power_kW: float = 100
    efficiency: float = 0.95

    def step(self, is_charge: bool, power_kW: float, dt_hours: float = 0.5):
        # Ensure non-negative requested power and cap to inverter limit
        requested_power_kW = max(0.0, min(power_kW, self.max_power_kW))

    # Convert requested power to energy for this timestep
        requested_energy_kWh = requested_power_kW * dt_hours

        if is_charge:
        # Maximum grid energy we can accept without exceeding capacity
            headroom_kWh = self.capacity_kWh - self.soc_kWh
            max_energy_kWh = headroom_kWh / self.efficiency

        # Feasible energy we can actually charge
            actual_energy_kWh = min(requested_energy_kWh, max_energy_kWh)

        # Update SOC
            self.soc_kWh += actual_energy_kWh * self.efficiency

        else:
        # Maximum energy we can deliver without breaching minimum SOC
            available_kWh = self.soc_kWh - self.soc_min_kWh
            max_energy_kWh = available_kWh * self.efficiency

        # Feasible energy we can actually discharge
            actual_energy_kWh = min(requested_energy_kWh, max_energy_kWh)

        # Update SOC
            self.soc_kWh -= actual_energy_kWh / self.efficiency

    # Convert actual energy back to power for reporting
        actual_power_kW = actual_energy_kWh / dt_hours

        return actual_power_kW



# In[10]:


battery = Battery()
battery_state = []

FIXED_TARIFF_p = 21
WHOLESALE_SUPPLIER_PREMIUM_p = 10
SETTLEMENT_PERIOD_LENGTH_h = 0.5


for index, row in mkt_prices.iterrows():
    charge_kW = 0
    discharge_kW = 0

    settlement_period = row["SettlementPeriod"]
    market_price = row["Price_p_kWh"]
    market_price_p25_day = row["Price_p_kWh_p25_day"]
    market_price_p75_day = row["Price_p_kWh_p75_day"]
    supplier_import_price = market_price + WHOLESALE_SUPPLIER_PREMIUM_p
    supplier_export_price = market_price

    usage_kW = row["Usage_kW"]

    if settlement_period <= 14 and market_price < market_price_p25_day:
        action = "charge"
        reason = "settlement_period <= 14 and market_price < market_price_p25_day"
        charge_kW = battery.step(True, 100)
    elif market_price < market_price_p25_day:
        action = "charge"
        reason = "market_price < market_price_p25_day"
        charge_kW = battery.step(True, 100)
    elif market_price > market_price_p75_day and settlement_period >= 15:
        action = "discharge"
        reason = "market_price > market_price_p75_day and settlement_period >= 15"
        discharge_request_kW = min(100, usage_kW)
        discharge_kW = battery.step(False, discharge_request_kW)
    else:
        action = "idle"
        reason = "N/A"

    grid_net_kW = usage_kW + charge_kW - discharge_kW
    grid_import_kW = max(grid_net_kW, 0)
    grid_export_kW = max(-grid_net_kW, 0)
    grid_import_kWh = grid_import_kW * SETTLEMENT_PERIOD_LENGTH_h
    grid_export_kWh = grid_export_kW * SETTLEMENT_PERIOD_LENGTH_h
    import_cost_p = grid_import_kWh * supplier_import_price
    export_revenue_p = grid_export_kWh * supplier_export_price

    net_cost_p = import_cost_p - export_revenue_p
    tariff_baseline_cost_p = usage_kW * SETTLEMENT_PERIOD_LENGTH_h * FIXED_TARIFF_p
    wholesale_baseline_cost_p = usage_kW * SETTLEMENT_PERIOD_LENGTH_h * supplier_import_price

    battery_state.append({
    "SettlementDate": row["SettlementDate"],
    "SettlementPeriod": row["SettlementPeriod"],
    "Price_p_kWh": row["Price_p_kWh"],
    "SupplierImportPrice_p_kWh": supplier_import_price,
    "SupplierExportPrice_p_kWh": supplier_export_price,
    "Usage_kW": usage_kW,

    # Battery behaviour
    "Charge_kW": charge_kW,
    "Discharge_kW": discharge_kW,
    "SOC_kWh": battery.soc_kWh,
    "BatteryAction": action,
    "BatteryActionReason": reason,

    # Grid interaction
    "GridNet_kW": grid_net_kW,
    "GridImport_kW": grid_import_kW,
    "GridExport_kW": grid_export_kW,

    # Economics
    "ImportCost_p": import_cost_p,
    "ExportRevenue_p": export_revenue_p,
    "NetCost_p": net_cost_p,
    "WholesaleNoBatteryCost_p": wholesale_baseline_cost_p,
    "TariffNoBatteryCost_p": tariff_baseline_cost_p,

    })

battery_state_df = pd.DataFrame(battery_state) 



# In[11]:


battery_state_df["NetCost_p"].sum() / 100 * 13


# In[12]:


battery_state_df["TariffNoBatteryCost_p"].sum() / 100 * 13


# In[13]:


battery_state_df["WholesaleNoBatteryCost_p"].sum() / 100 * 13


# In[14]:


battery_state_df


# In[15]:


battery_state_df.to_csv("temp/battery_state.csv")


