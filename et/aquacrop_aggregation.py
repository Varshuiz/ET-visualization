import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import io
import base64


# ─── 1. AGGREGATE DAILY → WEEKLY / BIWEEKLY ────────────────────────────────

def _fmt_month_day(ts: pd.Timestamp) -> str:
    return ts.strftime("%b %d").replace(" 0", " ")

def aggregate_aquacrop_timeseries(daily_df: pd.DataFrame, timestep: str = "weekly") -> pd.DataFrame:
    """
    Resample AquaCrop daily output to weekly or biweekly averages.

    Args:
        daily_df  : DataFrame with a 'Date' column (or DatetimeIndex) plus
                    any numeric columns (ET, biomass, soil_water, canopy_cover, etc.)
        timestep  : "weekly" (7-day) or "biweekly" (14-day)

    Returns:
        Resampled DataFrame with period label, mean values, and cumulative ET.
    """
    df = daily_df.copy()

    # Ensure Date column is index
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date")
    if df.index.name != "Date":
        df.index = pd.to_datetime(df.index, errors="coerce")
        df.index.name = "Date"
    df = df.sort_index()
    df = df[~df.index.isna()]
    if df.empty:
        return pd.DataFrame(columns=["Period", "Period_Start", "Period_End"])

    # Drop non-numeric columns for aggregation
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

    period_days = 7 if timestep == "weekly" else 14

    # Mean of state variables, sum of fluxes
    flux_cols   = [c for c in numeric_cols if any(k in c.lower() for k in
                   ["et", "precip", "irr", "rain", "runoff", "drain"])]
    state_cols  = [c for c in numeric_cols if c not in flux_cols]

    agg_dict = {c: "sum" for c in flux_cols}
    agg_dict.update({c: "mean" for c in state_cols})

    sim_start = df.index.min().normalize()
    sim_end = df.index.max().normalize()
    period_index = ((df.index.normalize() - sim_start).days // period_days).astype(int)
    work = df[numeric_cols].copy()
    work["period_index"] = period_index

    resampled = (
        work.groupby("period_index", as_index=False)
        .agg({c: agg_dict.get(c, "mean") for c in numeric_cols})
        .sort_values("period_index")
        .reset_index(drop=True)
    )

    resampled["Period_Start"] = resampled["period_index"].apply(
        lambda idx: sim_start + pd.Timedelta(days=int(idx) * period_days)
    )
    resampled["Period_End"] = resampled["Period_Start"].apply(
        lambda d: min(d + pd.Timedelta(days=period_days - 1), sim_end)
    )

    label_prefix = "Week" if timestep == "weekly" else "Biweek"
    resampled["Period"] = [
        f"{label_prefix} {i + 1} ({_fmt_month_day(row['Period_Start'])} - {_fmt_month_day(row['Period_End'])})"
        for i, (_, row) in enumerate(resampled.iterrows())
    ]
    resampled = resampled[["Period", "Period_Start", "Period_End"] + numeric_cols]

    return resampled


def plot_aquacrop_timeseries(resampled_df: pd.DataFrame,
                              y_col: str = "ET",
                              title: str = "Weekly ET₀",
                              color: str = "#087f8c",
                              timestep: str = "weekly") -> str:
    """
    Generates a bar chart of a resampled AquaCrop variable.
    Returns base64-encoded PNG string for embedding in templates.
    """
    fig, ax = plt.subplots(figsize=(12, 5))
    fig.patch.set_facecolor("#f8fbf8")
    ax.set_facecolor("#f8fbf8")

    x = np.arange(len(resampled_df))
    bars = ax.bar(x, resampled_df[y_col].fillna(0),
                  color=color, alpha=0.85, edgecolor="white", linewidth=0.5)

    # Rolling average line
    if len(resampled_df) >= 3:
        rolling = resampled_df[y_col].rolling(3, center=True, min_periods=1).mean()
        ax.plot(x, rolling, color="#1a3a3a", linewidth=2, linestyle="--",
                label="3-period moving avg", zorder=3)
        ax.legend(fontsize=9)

    # Keep x-axis readable for long runs by showing only evenly spaced labels.
    period_labels = resampled_df["Period"].astype(str).tolist()
    tick_step = max(1, int(np.ceil(len(period_labels) / 12)))
    tick_positions = x[::tick_step]
    tick_labels = [period_labels[i] for i in range(0, len(period_labels), tick_step)]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, rotation=35, ha="right", fontsize=8)
    ax.set_xlabel(f"{'Weekly' if timestep == 'weekly' else 'Biweekly'} Period", fontsize=11)
    ax.set_ylabel(y_col, fontsize=11)
    period_text = ""
    if {"Period_Start", "Period_End"}.issubset(resampled_df.columns) and not resampled_df.empty:
        sim_start = pd.to_datetime(resampled_df["Period_Start"].min(), errors="coerce")
        sim_end = pd.to_datetime(resampled_df["Period_End"].max(), errors="coerce")
        if pd.notna(sim_start) and pd.notna(sim_end):
            period_text = f"{_fmt_month_day(sim_start)} - {_fmt_month_day(sim_end)} {sim_end.year}"
    title_text = f"{title}\n({period_text})" if period_text else title
    ax.set_title(title_text, fontsize=13, fontweight="bold", color="#1a3a3a")
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.1f"))
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.spines[["top", "right"]].set_visible(False)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


# ─── 2. YIELD VS CROPS BAR CHART (t/ha) ────────────────────────────────────

# AquaCrop-OSPy returns dry biomass in t/ha. Harvest Index (HI) converts to yield.
# Reference: FAO AquaCrop manual, typical HI values.
CROP_HARVEST_INDEX = {
    "Wheat":   0.45,
    "Maize":   0.50,
    "Barley":  0.45,
    "Canola":  0.35,
    "Potato":  0.75,  # storage organ fraction
    "Cotton":  0.35,
    "Soybean": 0.42,
    "Sugar beet": 0.70,
}

def compute_yield_tha(dry_biomass_tha: float, crop_name: str) -> float:
    """
    Convert AquaCrop dry aboveground biomass (t/ha) → marketable yield (t/ha).
    Uses crop-specific Harvest Index.
    """
    hi = CROP_HARVEST_INDEX.get(crop_name, 0.45)
    return round(dry_biomass_tha * hi, 2)


def build_yield_comparison_chart(yield_results: dict,
                                  irrigation_label: str = "Rainfed") -> str:
    """
    Create a horizontal bar chart comparing yield (t/ha) across crops.

    Args:
        yield_results: dict of {crop_name: yield_t_ha}
                       e.g. {"Wheat": 3.2, "Barley": 2.8, "Canola": 1.9}
        irrigation_label: label shown in chart subtitle

    Returns:
        base64 PNG string
    """
    crops  = list(yield_results.keys())
    yields = [yield_results[c] for c in crops]

    # Color-code by yield level
    palette = ["#087f8c" if y >= max(yields) * 0.8
               else "#5aaa95" if y >= max(yields) * 0.5
               else "#BB9F06"
               for y in yields]

    fig, ax = plt.subplots(figsize=(10, max(4, len(crops) * 0.8)))
    fig.patch.set_facecolor("#f8fbf8")
    ax.set_facecolor("#f8fbf8")

    bars = ax.barh(crops, yields, color=palette, edgecolor="white", linewidth=0.5)

    # Value labels inside / outside bars
    for bar, val in zip(bars, yields):
        ax.text(bar.get_width() + 0.05, bar.get_y() + bar.get_height() / 2,
                f"{val:.2f} t/ha", va="center", ha="left", fontsize=10,
                fontweight="bold", color="#1a3a3a")

    ax.set_xlabel("Yield (t/ha)", fontsize=12)
    ax.set_title(f"Simulated Crop Yield — {irrigation_label}",
                 fontsize=13, fontweight="bold", color="#1a3a3a")
    ax.set_xlim(0, max(yields) * 1.25)
    ax.grid(axis="x", linestyle="--", alpha=0.4)
    ax.spines[["top", "right"]].set_visible(False)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def build_multi_irrigation_yield_chart(crop_yield_data: dict) -> str:
    """
    Grouped bar chart: yield (t/ha) per crop, grouped by irrigation strategy.

    Args:
        crop_yield_data: {
            "Rainfed":      {"Wheat": 2.1, "Barley": 1.8, ...},
            "Full Irrig.":  {"Wheat": 4.2, "Barley": 3.6, ...},
            "Deficit 60%":  {"Wheat": 3.5, "Barley": 3.0, ...},
        }
    """
    strategies = list(crop_yield_data.keys())
    crops      = list(next(iter(crop_yield_data.values())).keys())
    x          = np.arange(len(crops))
    width      = 0.25
    colors     = ["#BB9F06", "#087f8c", "#5aaa95"]

    fig, ax = plt.subplots(figsize=(12, 5))
    fig.patch.set_facecolor("#f8fbf8")
    ax.set_facecolor("#f8fbf8")

    for i, (strategy, color) in enumerate(zip(strategies, colors)):
        vals = [crop_yield_data[strategy].get(c, 0) for c in crops]
        offset = (i - len(strategies) / 2 + 0.5) * width
        bars = ax.bar(x + offset, vals, width, label=strategy,
                      color=color, edgecolor="white", linewidth=0.5, alpha=0.9)

    ax.set_xticks(x)
    ax.set_xticklabels(crops, fontsize=10)
    ax.set_ylabel("Yield (t/ha)", fontsize=12)
    ax.set_title("Yield Comparison: Crops × Irrigation Strategies",
                 fontsize=13, fontweight="bold", color="#1a3a3a")
    ax.legend(fontsize=10)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.spines[["top", "right"]].set_visible(False)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def format_yield_table(yield_results: dict) -> list:
    """
    Format yield results for Django template rendering.
    Returns list of dicts: [{"crop": ..., "yield_tha": ..., "category": ...}]
    """
    rows = []
    if not yield_results:
        return rows
    max_y = max(yield_results.values()) if yield_results else 1
    for crop, y in sorted(yield_results.items(), key=lambda x: x[1], reverse=True):
        if max_y > 0:
            pct = (y / max_y) * 100
        else:
            pct = 0
        category = "High" if pct >= 80 else "Medium" if pct >= 50 else "Low"
        rows.append({
            "crop":      crop,
            "yield_tha": round(y, 2),
            "category":  category,
            "pct_bar":   round(pct, 1),
        })
    return rows


def _weekly_yield_rows_from_daily(
    daily_df: pd.DataFrame, crop_name: str, start_date_str: str
) -> dict[int, dict]:
    """Map week_idx → peak biomass and marketable yield for that week."""
    if daily_df is None or daily_df.empty or "biomass" not in daily_df.columns:
        return {}

    df = daily_df.copy()
    if "Date" not in df.columns:
        start_dt = pd.to_datetime(start_date_str.replace("/", "-"), errors="coerce")
        if pd.isna(start_dt):
            return {}
        df["Date"] = pd.date_range(start_dt, periods=len(df), freq="D")

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    if df.empty:
        return {}

    start_dt = df["Date"].min().normalize()
    df["week_idx"] = ((df["Date"] - start_dt).dt.days // 7).astype(int)

    by_week: dict[int, dict] = {}
    for wk, grp in df.groupby("week_idx", sort=True):
        bmax = float(grp["biomass"].max())
        y = compute_yield_tha(bmax, crop_name)
        by_week[int(wk)] = {
            "week_after_planting": int(wk) + 1,
            "period_label": f"Week {int(wk) + 1}",
            "yield_tha": round(float(y), 3),
            "biomass_peak_tha": round(bmax, 3),
        }
    return by_week


def build_weekly_yield_projection(
    daily_df: pd.DataFrame, crop_name: str, start_date_str: str
) -> list:
    """
    End-of-week potential marketable yield (t/ha) from peak simulated dry biomass
    within each calendar week after simulation start (indicative trajectory).
    """
    return build_weekly_yield_comparison(daily_df, crop_name, start_date_str, actual_daily_df=None)


def build_weekly_yield_comparison(
    optimal_daily_df: pd.DataFrame,
    crop_name: str,
    start_date_str: str,
    actual_daily_df: pd.DataFrame | None = None,
) -> list:
    """
    Weekly rows with optimal vs farmer-irrigation marketable yield (t/ha).
    Peak biomass within each week × harvest index.
    """
    optimal_by_week = _weekly_yield_rows_from_daily(optimal_daily_df, crop_name, start_date_str)
    if not optimal_by_week:
        return []

    actual_by_week = (
        _weekly_yield_rows_from_daily(actual_daily_df, crop_name, start_date_str)
        if actual_daily_df is not None
        else {}
    )

    out = []
    for wk in sorted(optimal_by_week.keys()):
        opt = optimal_by_week[wk]
        act = actual_by_week.get(wk)
        out.append(
            {
                "week_after_planting": opt["week_after_planting"],
                "period_label": opt["period_label"],
                "yield_tha": opt["yield_tha"],
                "biomass_peak_tha": opt["biomass_peak_tha"],
                "optimal_yield_tha": opt["yield_tha"],
                "your_yield_tha": act["yield_tha"] if act else None,
                "biomass_peak_optimal_tha": opt["biomass_peak_tha"],
                "biomass_peak_actual_tha": act["biomass_peak_tha"] if act else None,
            }
        )
    return out


def build_simulation_results_tables(
    results: dict,
    start_date_str: str,
) -> tuple[list[dict], list[dict]]:
    """
    Daily and weekly result rows for the AquaCrop results data table.

    Weekly aggregation: sum ET, precipitation, irrigation; mean soil water & biomass.
  """
    crop_growth = results.get("daily_output")
    water_flux = results.get("water_flux")
    if crop_growth is None or (isinstance(crop_growth, pd.DataFrame) and crop_growth.empty):
        return [], []

    cg = crop_growth.copy() if isinstance(crop_growth, pd.DataFrame) else pd.DataFrame(crop_growth)
    wf = (
        water_flux.copy()
        if isinstance(water_flux, pd.DataFrame) and water_flux is not None and not water_flux.empty
        else pd.DataFrame()
    )

    n = len(cg)
    start_dt = pd.to_datetime(str(start_date_str).replace("/", "-"), errors="coerce")
    if pd.isna(start_dt):
        start_dt = pd.Timestamp.today().normalize()

    if "Date" in cg.columns:
        dates = pd.to_datetime(cg["Date"], errors="coerce")
    else:
        dates = pd.date_range(start_dt, periods=n, freq="D")

    tr = pd.to_numeric(
        wf.get("Tr", pd.Series(0.0, index=range(n))), errors="coerce"
    ).fillna(0.0)
    es = pd.to_numeric(
        wf.get("Es", pd.Series(0.0, index=range(n))), errors="coerce"
    ).fillna(0.0)
    if len(tr) < n:
        tr = pd.concat([tr, pd.Series([0.0] * (n - len(tr)))], ignore_index=True)
    if len(es) < n:
        es = pd.concat([es, pd.Series([0.0] * (n - len(es)))], ignore_index=True)
    tr, es = tr.iloc[:n], es.iloc[:n]

    precip = pd.to_numeric(
        wf.get("Infl", pd.Series(0.0, index=range(n))), errors="coerce"
    ).fillna(0.0).iloc[:n]
    irrigation = pd.to_numeric(
        wf.get("IrrDay", pd.Series(0.0, index=range(n))), errors="coerce"
    ).fillna(0.0).iloc[:n]
    biomass = pd.to_numeric(
        cg.get("biomass", pd.Series(0.0, index=range(n))), errors="coerce"
    ).fillna(0.0).iloc[:n]
    # Root-zone water (Wr, mm) is in water_flux, not crop_growth (aquacrop-OSPy output layout).
    soil_water = pd.to_numeric(
        wf.get("Wr", wf.get("soil_water", pd.Series(np.nan, index=range(n)))),
        errors="coerce",
    )
    if len(soil_water) < n:
        soil_water = pd.concat(
            [soil_water, pd.Series([np.nan] * (n - len(soil_water)))],
            ignore_index=True,
        )
    soil_water = soil_water.iloc[:n]

    daily_df = pd.DataFrame(
        {
            "Date": dates,
            "et_mm": (tr + es).values,
            "precipitation_mm": precip.values,
            "irrigation_mm": irrigation.values,
            "soil_moisture": soil_water.values,
            "biomass_tha": biomass.values,
        }
    )
    daily_df["Date"] = pd.to_datetime(daily_df["Date"], errors="coerce")
    daily_df = daily_df.dropna(subset=["Date"]).reset_index(drop=True)
    if daily_df.empty:
        return [], []

    daily_rows = []
    for _, row in daily_df.iterrows():
        sm = row["soil_moisture"]
        daily_rows.append(
            {
                "period": row["Date"].strftime("%Y-%m-%d"),
                "et_mm": round(float(row["et_mm"]), 2),
                "precipitation_mm": round(float(row["precipitation_mm"]), 2),
                "irrigation_mm": round(float(row["irrigation_mm"]), 2),
                "soil_moisture": round(float(sm), 2) if pd.notna(sm) else None,
                "biomass_tha": round(float(row["biomass_tha"]), 3),
            }
        )

    week_start = daily_df["Date"].min().normalize()
    daily_df["week_idx"] = ((daily_df["Date"] - week_start).dt.days // 7).astype(int)
    weekly_rows = []
    for wk, grp in daily_df.groupby("week_idx", sort=True):
        ws = grp["Date"].min()
        we = grp["Date"].max()
        sm_mean = grp["soil_moisture"].mean()
        weekly_rows.append(
            {
                "period": f"Week {int(wk) + 1} ({_fmt_month_day(ws)} – {_fmt_month_day(we)})",
                "et_mm": round(float(grp["et_mm"].sum()), 2),
                "precipitation_mm": round(float(grp["precipitation_mm"].sum()), 2),
                "irrigation_mm": round(float(grp["irrigation_mm"].sum()), 2),
                "soil_moisture": round(float(sm_mean), 2) if pd.notna(sm_mean) else None,
                "biomass_tha": round(float(grp["biomass_tha"].mean()), 3),
            }
        )

    return daily_rows, weekly_rows