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

@dataclass
class Battery:
    soc_kWh: float = 130
    capacity_kWh: float = 260
    soc_min_kWh: float = 26
    max_power_kW: float = 100
    efficiency: float = 0.95

    def step(self, is_charge: bool, power_kW: float, dt_hours: float = 0.5):
        requested_power_kW = max(0.0, min(power_kW, self.max_power_kW))
        requested_energy_kWh = requested_power_kW * dt_hours

        if is_charge:
            headroom_kWh = self.capacity_kWh - self.soc_kWh
            max_energy_kWh = headroom_kWh / self.efficiency

            actual_energy_kWh = min(requested_energy_kWh, max_energy_kWh)

            self.soc_kWh += actual_energy_kWh * self.efficiency

        else:
            available_kWh = self.soc_kWh - self.soc_min_kWh
            max_energy_kWh = available_kWh * self.efficiency

            actual_energy_kWh = min(requested_energy_kWh, max_energy_kWh)

            self.soc_kWh -= actual_energy_kWh / self.efficiency

        actual_power_kW = actual_energy_kWh / dt_hours

        return actual_power_kW

def prepare_load_profile(peak_load, dataset="datasets/ProfileClass5.csv"):
    load_profile = pd.read_csv(dataset)
    load_profile["SettlementPeriod"] = load_profile.index + 1
    load_coefficients = load_profile.columns.difference(["Time", "SettlementPeriod"])
    load_multiplier = peak_load / load_profile[load_coefficients].to_numpy().max()
    load_profile[load_coefficients] = load_profile[load_coefficients] * load_multiplier
    load_profile = load_profile[
        [
            "SettlementPeriod",
            "Aut Wd",
            "Aut Sat",
            "Aut Sun",
            "Smr Wd",
            "Smr Sat",
            "Smr Sun",
            "Spr Wd",
            "Spr Sat",
            "Spr Sun",
            "Wtr Wd",
            "Wtr Sat",
            "Wtr Sun"
        ]
    ]

    return load_profile

def prepare_mkt_prices(dataset):
    mkt_prices = pd.read_csv(dataset)

    mkt_prices = mkt_prices[mkt_prices["DataProvider"] == "APXMIDP"]

    mkt_prices["Price_p_kWh"] = mkt_prices["Price"] / 10
    mkt_prices = mkt_prices.merge(
        mkt_prices.groupby("SettlementDate")["Price_p_kWh"]
        .quantile([0.25, 0.75])
        .unstack()
        .rename(columns={0.25: "Price_p_kWh_p25_day", 0.75: "Price_p_kWh_p75_day"}),
        on="SettlementDate", how="left"
    )

    mkt_prices["SettlementWeekday"] = mkt_prices["SettlementDate"].map(
        lambda x: datetime.date.fromisoformat(x).weekday()
    )
    mkt_prices = mkt_prices.sort_values(["SettlementDate", "SettlementPeriod"]).reset_index(drop=True)
    mkt_prices = mkt_prices[
        [
            "SettlementDate",
            "SettlementPeriod",
            "SettlementWeekday",
            "Price_p_kWh",
            "Price_p_kWh_p25_day",
            "Price_p_kWh_p75_day",
        ]
    ]

    return mkt_prices

def generate_usage_per_sp(load_profile, mkt_prices, _season):
    usage_sp = mkt_prices.merge(
        load_profile[["SettlementPeriod", f"{_season} Wd", f"{_season} Sat", f"{_season} Sun"]],
        on="SettlementPeriod",
        how="left"
    )
    usage_sp["Usage_kW"] = np.select(
        [usage_sp["SettlementWeekday"] < 5, usage_sp["SettlementWeekday"] == 5, usage_sp["SettlementWeekday"] == 6],
        [
            usage_sp[f"{_season} Wd"],
            usage_sp[f"{_season} Sat"],
            usage_sp[f"{_season} Sun"],
        ],
    )

    usage_sp = usage_sp[
        [
            "SettlementDate",
            "SettlementPeriod",
            "Price_p_kWh",
            "Price_p_kWh_p25_day",
            "Price_p_kWh_p75_day",
            "Usage_kW"
        ]
    ]

    return usage_sp

def run_model(dataset, fixed_tariff_px, battery_size):
    battery = Battery(
        soc_kWh = battery_size / 2,
        capacity_kWh = battery_size,
        soc_min_kWh = battery_size * 0.1,
        max_power_kW= battery_size * 0.4
    )
    battery_state = []

    FIXED_TARIFF_p = fixed_tariff_px
    WHOLESALE_SUPPLIER_PREMIUM_p = 10
    SETTLEMENT_PERIOD_LENGTH_h = 0.5

    for index, row in dataset.iterrows():
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
        wholesale_baseline_cost_p = (
            usage_kW * SETTLEMENT_PERIOD_LENGTH_h * supplier_import_price
        )

        battery_state.append(
            {
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
            }
        )

    battery_state_df = pd.DataFrame(battery_state)
    return battery_state_df

def process_all_seasons(fixed_tariff_px=21, peak_load=150, battery_size=260):
    dfs = {}

    load_profile = prepare_load_profile(peak_load)

    for season in Seasons:
        season_abr, mkt_px_dataset = season.value

        mkt_prices = prepare_mkt_prices(mkt_px_dataset)
        px_usage_sp = generate_usage_per_sp(load_profile, mkt_prices, season_abr)
        final_df = run_model(px_usage_sp, fixed_tariff_px, battery_size)

        dfs[season.name] = final_df

    return dfs

if __name__ == "__main__":
    summary = []

    load_profile = prepare_load_profile(peak_load=300)

    for season in Seasons:
        season_abr, mkt_px_dataset = season.value

        mkt_prices = prepare_mkt_prices(mkt_px_dataset)
        px_usage_sp = generate_usage_per_sp(load_profile, mkt_prices, season_abr)
        final_df = run_model(px_usage_sp)

        summary.append({
            "season": season.name,
            "tou_tariff_battery": final_df["NetCost_p"].sum() / 100 * 13,
            "tou_tariff": final_df["WholesaleNoBatteryCost_p"].sum() / 100 * 13,
            "fixed_tariff": final_df["TariffNoBatteryCost_p"].sum() / 100 * 13
        })

    summary_df = pd.DataFrame(summary)
    print(summary_df)
    print(summary_df["fixed_tariff"].sum())
    print(summary_df["tou_tariff"].sum())
    print(summary_df["tou_tariff_battery"].sum())
    print(summary_df["fixed_tariff"].sum() - summary_df["tou_tariff_battery"].sum())

    final_df.to_csv("temp/final_df.csv")