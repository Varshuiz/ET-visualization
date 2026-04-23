import base64
from io import BytesIO

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .et_units import convert_units, get_unit_info

METHOD_CONFIG = {
    "PT": {"column": "ET_PT", "label": "Priestley-Taylor", "color": "#1F77B4"},
    "PM": {"column": "ET_PM", "label": "Penman-Monteith", "color": "#D62728"},
    "Maule": {"column": "ET_Maule", "label": "Maule", "color": "#2CA02C"},
    "Hargreaves": {"column": "ET_Hargreaves", "label": "Hargreaves-Samani", "color": "#9467BD"},
}


def _available_methods_for_plot(df, selected_methods=None):
    method_order = ["PT", "PM", "Maule", "Hargreaves"]
    if selected_methods:
        method_order = [m for m in method_order if m in selected_methods]
    return [m for m in method_order if METHOD_CONFIG[m]["column"] in df.columns]


def calculate_growing_season_stats(df, et_method="ET_PM", unit="mm"):
    """Calculate growing season cumulative ET and statistics with unit conversion."""
    growing_season_stats = {}
    if "Date" not in df.columns or et_method not in df.columns:
        return growing_season_stats

    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df["Month"] = df["Date"].dt.month
    df["Year"] = df["Date"].dt.year
    growing_season = df[df["Month"].isin([5, 6, 7, 8, 9, 10])].copy()

    if len(growing_season) == 0:
        return growing_season_stats

    if unit == "inches":
        converted_col = et_method + "_converted"
        growing_season[converted_col] = growing_season[et_method].apply(lambda x: convert_units(x, "mm", "inches"))
        et_col = converted_col
    else:
        et_col = et_method

    yearly_stats = []
    for year in sorted(growing_season["Year"].unique()):
        year_data = growing_season[growing_season["Year"] == year].copy()
        if len(year_data) > 0:
            year_data = year_data.sort_values("Date")
            year_data["Cumulative_ET"] = year_data[et_col].cumsum()
            yearly_stats.append(
                {
                    "year": year,
                    "total_et": year_data[et_col].sum(),
                    "avg_daily_et": year_data[et_col].mean(),
                    "max_daily_et": year_data[et_col].max(),
                    "min_daily_et": year_data[et_col].min(),
                    "days_recorded": len(year_data),
                    "final_cumulative": year_data["Cumulative_ET"].iloc[-1] if len(year_data) > 0 else 0,
                }
            )

    if len(yearly_stats) > 0:
        growing_season_stats = {
            "years_analyzed": len(yearly_stats),
            "yearly_stats": yearly_stats,
            "multi_year_avg_total": np.mean([y["total_et"] for y in yearly_stats]),
            "multi_year_avg_daily": np.mean([y["avg_daily_et"] for y in yearly_stats]),
            "highest_season_total": max([y["total_et"] for y in yearly_stats]),
            "lowest_season_total": min([y["total_et"] for y in yearly_stats]),
            "total_days_analyzed": sum([y["days_recorded"] for y in yearly_stats]),
        }

    return growing_season_stats


def create_growing_season_plots(df, et_method="ET_PM", unit="mm"):
    """Create plots specific to growing season analysis with unit conversion."""
    plots = {}
    if "Date" not in df.columns or et_method not in df.columns:
        return plots

    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df["Month"] = df["Date"].dt.month
    df["Year"] = df["Date"].dt.year
    growing_season = df[df["Month"].isin([5, 6, 7, 8, 9, 10])].copy()

    if len(growing_season) == 0:
        return plots

    if unit == "inches":
        converted_col = et_method + "_converted"
        growing_season[converted_col] = growing_season[et_method].apply(lambda x: convert_units(x, "mm", "inches"))
        et_col = converted_col
        unit_info = get_unit_info("inches")
    else:
        et_col = et_method
        unit_info = get_unit_info("mm")

    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
    fig.patch.set_facecolor("white")

    ax1.set_facecolor("#f8fffe")
    colors = ["#087F8C", "#86A873", "#BB9F06", "#5AAA95"]
    for i, year in enumerate(sorted(growing_season["Year"].unique())):
        year_data = growing_season[growing_season["Year"] == year].copy()
        year_data = year_data.sort_values("Date")
        year_data["Cumulative_ET"] = year_data[et_col].cumsum()
        year_data["Day_of_Year"] = year_data["Date"].dt.dayofyear
        color = colors[i % len(colors)]
        ax1.plot(year_data["Day_of_Year"], year_data["Cumulative_ET"], label=f"{year}", color=color, linewidth=2.5)

    ax1.set_title("Cumulative ET During Growing Season", fontsize=14, fontweight="bold", color="#095256")
    ax1.set_xlabel("Day of Year", fontsize=12, color="#095256")
    ax1.set_ylabel(f'Cumulative ET ({unit_info["total_label"]})', fontsize=12, color="#095256")
    ax1.grid(True, alpha=0.3, color="#5AAA95")
    ax1.legend()

    ax2.set_facecolor("#f8fffe")
    monthly_avg = growing_season.groupby("Month")[et_col].mean()
    months = ["May", "Jun", "Jul", "Aug", "Sep", "Oct"]
    month_numbers = [5, 6, 7, 8, 9, 10]
    bars = ax2.bar(months, [monthly_avg.get(m, 0) for m in month_numbers], color=["#86A873", "#87B374", "#88BD75", "#89C776", "#8AD177", "#8BDB78"])
    ax2.set_title("Average Daily ET by Month", fontsize=14, fontweight="bold", color="#095256")
    ax2.set_xlabel("Month", fontsize=12, color="#095256")
    ax2.set_ylabel(f'Average Daily ET ({unit_info["daily_label"]})', fontsize=12, color="#095256")
    ax2.grid(True, alpha=0.3, color="#5AAA95", axis="y")

    for bar in bars:
        height = bar.get_height()
        ax2.annotate(
            f'{height:.{unit_info["daily_decimal_places"]}f}',
            xy=(bar.get_x() + bar.get_width() / 2, height),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=10,
            color="#095256",
        )

    ax3.set_facecolor("#f8fffe")
    growing_season_sorted = growing_season.sort_values("Date")
    ax3.plot(growing_season_sorted["Date"], growing_season_sorted[et_col], color="#087F8C", alpha=0.7, linewidth=1)
    ax3.plot(
        growing_season_sorted["Date"],
        growing_season_sorted[et_col].rolling(window=7, min_periods=1).mean(),
        color="#095256",
        linewidth=2,
        label="7-day average",
    )
    ax3.set_title("Daily ET During Growing Season", fontsize=14, fontweight="bold", color="#095256")
    ax3.set_xlabel("Date", fontsize=12, color="#095256")
    ax3.set_ylabel(f'Daily ET ({unit_info["daily_label"]})', fontsize=12, color="#095256")
    ax3.grid(True, alpha=0.3, color="#5AAA95")
    ax3.legend()

    ax4.set_facecolor("#f8fffe")
    yearly_totals = growing_season.groupby("Year")[et_col].sum()
    bars = ax4.bar(yearly_totals.index.astype(str), yearly_totals.values, color="#5AAA95", alpha=0.8)
    ax4.set_title("Total Growing Season ET by Year", fontsize=14, fontweight="bold", color="#095256")
    ax4.set_xlabel("Year", fontsize=12, color="#095256")
    ax4.set_ylabel(f'Total ET ({unit_info["total_label"]})', fontsize=12, color="#095256")
    ax4.grid(True, alpha=0.3, color="#5AAA95", axis="y")

    for bar in bars:
        height = bar.get_height()
        ax4.annotate(
            f'{height:.{unit_info["total_decimal_places"]}f}',
            xy=(bar.get_x() + bar.get_width() / 2, height),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=10,
            color="#095256",
        )

    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white", edgecolor="none")
    buf.seek(0)
    plots["growing_season_analysis"] = base64.b64encode(buf.read()).decode("utf-8")
    buf.close()
    plt.close()
    return plots


def create_multi_method_growing_season_plots(df, selected_methods=None, unit="mm"):
    """Create 4-panel growing-season plots for multiple ET methods."""
    plots = {}
    if "Date" not in df.columns:
        return plots

    method_codes = _available_methods_for_plot(df, selected_methods=selected_methods)
    if not method_codes:
        return plots

    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df["Month"] = df["Date"].dt.month
    df["Year"] = df["Date"].dt.year
    df["Day_of_Year"] = df["Date"].dt.dayofyear
    growing_season = df[df["Month"].isin([5, 6, 7, 8, 9, 10])].copy()

    if len(growing_season) == 0:
        return plots

    unit_info = get_unit_info("inches" if unit == "inches" else "mm")

    for method in method_codes:
        col = METHOD_CONFIG[method]["column"]
        if unit == "inches":
            growing_season[col] = growing_season[col].apply(
                lambda x: convert_units(x, "mm", "inches") if not pd.isna(x) else x
            )

    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(18, 12))
    fig.patch.set_facecolor("white")

    # 1) Cumulative ET by day-of-year with CI shading (min/max envelope).
    ax1.set_facecolor("#f8fffe")
    for method in method_codes:
        col = METHOD_CONFIG[method]["column"]
        label = METHOD_CONFIG[method]["label"]
        color = METHOD_CONFIG[method]["color"]

        cumulative_curves = []
        for year in sorted(growing_season["Year"].dropna().unique()):
            year_data = growing_season[growing_season["Year"] == year].sort_values("Date")
            if len(year_data) == 0:
                continue
            year_curve = pd.DataFrame(
                {
                    "Day_of_Year": year_data["Day_of_Year"].to_numpy(),
                    "CumET": year_data[col].fillna(0).cumsum().to_numpy(),
                }
            ).drop_duplicates(subset=["Day_of_Year"], keep="last")
            cumulative_curves.append(year_curve.set_index("Day_of_Year")["CumET"])

        if not cumulative_curves:
            continue

        cumulative_matrix = pd.concat(cumulative_curves, axis=1).sort_index()
        doy = cumulative_matrix.index.to_numpy()
        y_mean = cumulative_matrix.mean(axis=1).to_numpy()
        y_min = cumulative_matrix.min(axis=1).to_numpy()
        y_max = cumulative_matrix.max(axis=1).to_numpy()

        ax1.fill_between(doy, y_min, y_max, color=color, alpha=0.12)
        ax1.plot(doy, y_mean, color=color, linewidth=2.4, label=f"{label} mean")
        ax1.plot(doy, y_min, color=color, linestyle="--", linewidth=1, alpha=0.7)
        ax1.plot(doy, y_max, color=color, linestyle="--", linewidth=1, alpha=0.7)

    ax1.set_title("Cumulative ET During Growing Season (Mean + Min/Max Range)", fontsize=13, fontweight="bold", color="#095256")
    ax1.set_xlabel("Day of Year", fontsize=11, color="#095256")
    ax1.set_ylabel(f'Cumulative ET ({unit_info["total_label"]})', fontsize=11, color="#095256")
    ax1.grid(True, alpha=0.28, color="#8BBFC4")
    ax1.legend(fontsize=9, ncol=2)

    # 2) Grouped monthly average bars by method.
    ax2.set_facecolor("#f8fffe")
    months = ["May", "Jun", "Jul", "Aug", "Sep", "Oct"]
    month_numbers = [5, 6, 7, 8, 9, 10]
    x = np.arange(len(month_numbers))
    width = 0.18 if len(method_codes) >= 4 else 0.8 / max(len(method_codes), 1)

    for idx, method in enumerate(method_codes):
        col = METHOD_CONFIG[method]["column"]
        label = METHOD_CONFIG[method]["label"]
        color = METHOD_CONFIG[method]["color"]
        monthly_avg = growing_season.groupby("Month")[col].mean()
        values = [monthly_avg.get(m, 0) for m in month_numbers]
        offsets = x + (idx - (len(method_codes) - 1) / 2) * width
        ax2.bar(offsets, values, width=width, label=label, color=color, alpha=0.9)

    ax2.set_xticks(x)
    ax2.set_xticklabels(months)
    ax2.set_title("Average Daily ET by Month (Grouped by Method)", fontsize=13, fontweight="bold", color="#095256")
    ax2.set_xlabel("Month", fontsize=11, color="#095256")
    ax2.set_ylabel(f'Average Daily ET ({unit_info["daily_label"]})', fontsize=11, color="#095256")
    ax2.grid(True, alpha=0.28, color="#8BBFC4", axis="y")
    ax2.legend(fontsize=9)

    # 3) Daily ET with CI and min/max per method.
    ax3.set_facecolor("#f8fffe")
    for method in method_codes:
        col = METHOD_CONFIG[method]["column"]
        label = METHOD_CONFIG[method]["label"]
        color = METHOD_CONFIG[method]["color"]
        grouped = growing_season.groupby("Day_of_Year")[col].agg(["mean", "min", "max"]).dropna()
        if len(grouped) == 0:
            continue
        xvals = grouped.index.to_numpy()
        y_mean = grouped["mean"].to_numpy()
        y_min = grouped["min"].to_numpy()
        y_max = grouped["max"].to_numpy()

        ax3.fill_between(xvals, y_min, y_max, color=color, alpha=0.12)
        ax3.plot(xvals, y_mean, color=color, linewidth=2.3, label=f"{label} mean")
        ax3.plot(xvals, y_min, color=color, linestyle="--", linewidth=1, alpha=0.65)
        ax3.plot(xvals, y_max, color=color, linestyle="--", linewidth=1, alpha=0.65)

    ax3.set_title("Daily ET During Growing Season (Mean + Min/Max Range)", fontsize=13, fontweight="bold", color="#095256")
    ax3.set_xlabel("Day of Year", fontsize=11, color="#095256")
    ax3.set_ylabel(f'Daily ET ({unit_info["daily_label"]})', fontsize=11, color="#095256")
    ax3.grid(True, alpha=0.28, color="#8BBFC4")
    ax3.legend(fontsize=9, ncol=2)

    # 4) Grouped yearly totals by method.
    ax4.set_facecolor("#f8fffe")
    years = sorted(growing_season["Year"].dropna().unique())
    x_year = np.arange(len(years))
    width_year = 0.18 if len(method_codes) >= 4 else 0.8 / max(len(method_codes), 1)

    for idx, method in enumerate(method_codes):
        col = METHOD_CONFIG[method]["column"]
        label = METHOD_CONFIG[method]["label"]
        color = METHOD_CONFIG[method]["color"]
        yearly_totals = growing_season.groupby("Year")[col].sum()
        values = [yearly_totals.get(y, 0) for y in years]
        offsets = x_year + (idx - (len(method_codes) - 1) / 2) * width_year
        ax4.bar(offsets, values, width=width_year, label=label, color=color, alpha=0.9)

    ax4.set_xticks(x_year)
    ax4.set_xticklabels([str(y) for y in years])
    ax4.set_title("Total Growing Season ET by Year (Grouped by Method)", fontsize=13, fontweight="bold", color="#095256")
    ax4.set_xlabel("Year", fontsize=11, color="#095256")
    ax4.set_ylabel(f'Total ET ({unit_info["total_label"]})', fontsize=11, color="#095256")
    ax4.grid(True, alpha=0.28, color="#8BBFC4", axis="y")
    ax4.legend(fontsize=9)

    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white", edgecolor="none")
    buf.seek(0)
    plots["growing_season_analysis"] = base64.b64encode(buf.read()).decode("utf-8")
    buf.close()
    plt.close()
    return plots
