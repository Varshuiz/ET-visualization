"""Daily inputs and intermediate terms for the four ET methods on the results page.

The results page (comparison_with_acis) prepares weather and ET with
``prepare_et_results_frame``. This module uses that same frame, so the ET
column matches the results page. Intermediate columns come from the vector
functions that produce those ET values.
"""

from __future__ import annotations

import csv
import io
import zipfile
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from .et_methods import (
    calculate_extraterrestrial_radiation_vec,
    hargreaves_ET_vec,
    hargreaves_parts_vec,
    maule_ET_vec,
    maule_parts_vec,
    net_radiation_estimate_vec,
    net_radiation_parts_vec,
    penman_monteith_ET_vec,
    penman_monteith_parts_vec,
    priestley_taylor_ET_vec,
    priestley_taylor_parts_vec,
)
from .et_units import convert_units, get_unit_info

ELEVATION_M = 766.0
DEFAULT_LATITUDE = 49.7
ALBEDO = 0.23
PT_ALPHA = 1.26
PT_GAMMA = 0.066
PT_LAMBDA = 2.45
SIGMA = 4.903e-9
MAULE_K = 0.0055
MAULE_A = 17.8
HARGREAVES_C = 0.0023
HARGREAVES_OFFSET = 17.8

METHOD_ORDER = ("pt", "pm", "maule", "hargreaves")
METHOD_TO_COLUMN = {
    "pt": "ET_PT",
    "pm": "ET_PM",
    "maule": "ET_Maule",
    "hargreaves": "ET_Hargreaves",
}
METHOD_LABELS = {
    "pt": "Priestley-Taylor",
    "pm": "Penman-Monteith",
    "maule": "Maulé",
    "hargreaves": "Hargreaves-Samani",
}
SLUG_ALIASES = {
    "pt": "pt",
    "priestley": "pt",
    "priestley-taylor": "pt",
    "pm": "pm",
    "penman": "pm",
    "penman-monteith": "pm",
    "maule": "maule",
    "maulé": "maule",
    "hargreaves": "hargreaves",
    "hargreaves-samani": "hargreaves",
    "hs": "hargreaves",
}

_RA_FORMULA = (
    r"R_a = \frac{24 \times 60}{\pi} G_{sc}\, d_r "
    r"\left(\omega_s \sin\varphi \sin\delta + \cos\varphi \cos\delta \sin\omega_s\right)"
)
_RA_FORMULA_2 = (
    r"d_r = 1 + 0.033 \cos\left(\frac{2\pi J}{365}\right), \quad "
    r"\delta = 0.409 \sin\left(\frac{2\pi J}{365} - 1.39\right), \quad "
    r"\omega_s = \arccos\left(-\tan\varphi \tan\delta\right)"
)
_RN_FORMULAS = (
    r"R_{ns} = (1 - 0.23)\, R_s",
    r"R_{so} = (0.75 + 2\times 10^{-5}\, z)\, R_a",
    (
        r"\frac{R_s}{R_{so}} = \min\!\left(\frac{R_s}{R_{so}}, 1\right)"
        r"\ \text{if}\ R_{so} > 0,\ \text{else}\ 0.8"
    ),
    r"e_a = 0.6108 \exp\!\left(\frac{17.27\, T_{mean}}{T_{mean}+237.3}\right) \frac{RH}{100}",
    (
        r"R_{nl} = \sigma \frac{T_{max,K}^{4} + T_{min,K}^{4}}{2}"
        r"\left(0.34 - 0.14\sqrt{e_a}\right)"
        r"\left(1.35 \frac{R_s}{R_{so}} - 0.35\right)"
    ),
    r"R_n = \max(R_{ns} - R_{nl},\, 0)",
)


def normalize_method(raw: str | None) -> str:
    key = (raw or "").strip().lower()
    return SLUG_ALIASES.get(key, "pm")


def prepare_et_results_frame(df: pd.DataFrame, latitude=DEFAULT_LATITUDE) -> pd.DataFrame:
    """Same weather cleanup and ET columns as the ET Results page."""
    df = df.copy()
    try:
        latitude = float(latitude)
        if not np.isfinite(latitude):
            latitude = DEFAULT_LATITUDE
    except (TypeError, ValueError):
        latitude = DEFAULT_LATITUDE

    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"])

    if "Tmax" in df.columns:
        df["Tmax"] = pd.to_numeric(df["Tmax"], errors="coerce").clip(lower=0)
        df["Tmax_clamped"] = df["Tmax"].clip(lower=0)
    if "Tmin" in df.columns:
        df["Tmin"] = pd.to_numeric(df["Tmin"], errors="coerce")
    if "Tavg" in df.columns:
        df["Tavg"] = pd.to_numeric(df["Tavg"], errors="coerce").clip(lower=0)

    if "Date" in df.columns:
        df["day_of_year"] = df["Date"].dt.dayofyear

    existing_et_columns = ["ET_PT", "ET_PM", "ET_Maule", "ET_Hargreaves"]
    has_existing_et = any(col in df.columns for col in existing_et_columns)
    if ("Tmax" not in df.columns or "Tmin" not in df.columns) and not has_existing_et:
        raise ValueError("Missing temperature data")

    if not has_existing_et:
        if "Tavg" not in df.columns:
            df["Tavg"] = (df["Tmax"] + df["Tmin"]) / 2
        if "Solar_Radiation" in df.columns:
            df["Rs"] = df["Solar_Radiation"]
        elif "Rs" not in df.columns:
            df["Rs"] = (df["Tmax"] - df["Tmin"]) * 0.16 * np.sqrt(12)
        if "Wind_Speed" in df.columns:
            df["u2"] = df["Wind_Speed"]
        elif "u2" not in df.columns:
            df["u2"] = 2.0
        if "RH" not in df.columns:
            df["RH"] = 65.0
        else:
            df["RH"] = pd.to_numeric(df["RH"], errors="coerce").fillna(65.0)
        df["Ra"] = calculate_extraterrestrial_radiation_vec(latitude, df["day_of_year"].to_numpy())
    else:
        if "Tavg" not in df.columns and {"Tmax", "Tmin"}.issubset(df.columns):
            df["Tavg"] = (df["Tmax"] + df["Tmin"]) / 2
        if "Rs" not in df.columns and {"Tmax", "Tmin"}.issubset(df.columns):
            df["Rs"] = (df["Tmax"] - df["Tmin"]) * 0.16 * np.sqrt(12)
        if "u2" not in df.columns:
            if "Wind_Speed" in df.columns:
                df["u2"] = df["Wind_Speed"]
            else:
                df["u2"] = 2.0
        if "RH" not in df.columns:
            df["RH"] = 65.0
        else:
            df["RH"] = pd.to_numeric(df["RH"], errors="coerce").fillna(65.0)
        if "Ra" not in df.columns and "day_of_year" in df.columns:
            df["Ra"] = calculate_extraterrestrial_radiation_vec(latitude, df["day_of_year"].to_numpy())

    if "ET_PT" not in df.columns:
        try:
            df["Rn"] = net_radiation_estimate_vec(
                df["Rs"], df["Tmax"], df["Tmin"], df["Ra"], df["RH"], elevation=ELEVATION_M
            )
            df["ET_PT"] = priestley_taylor_ET_vec(df["Tavg"], df["Rn"])
        except Exception as exc:
            print(f"PT calculation failed: {exc}")
            df["ET_PT"] = np.nan

    need_pm = "ET_PM" not in df.columns
    if not need_pm:
        need_pm = bool(df["ET_PM"].isna().all())
    if need_pm:
        try:
            if "Tmax" in df.columns and "Tmax_clamped" not in df.columns:
                df["Tmax_clamped"] = pd.to_numeric(df["Tmax"], errors="coerce").clip(lower=0)
            df["ET_PM"] = penman_monteith_ET_vec(
                df["Tmax_clamped"], df["Tmin"], df["RH"], df["u2"], df["Rs"], df["Ra"], elevation=ELEVATION_M
            )
        except Exception as exc:
            print(f"PM calculation failed: {exc}")
            df["ET_PM"] = np.nan

    if "ET_Maule" not in df.columns:
        try:
            df["ET_Maule"] = maule_ET_vec(
                df["Tmax_clamped"], df["Tmin"], df["Rs"], df["RH"], latitude=latitude
            )
        except Exception as exc:
            print(f"Maule calculation failed: {exc}")
            df["ET_Maule"] = np.nan

    if "ET_Hargreaves" not in df.columns:
        try:
            df["ET_Hargreaves"] = hargreaves_ET_vec(
                df["Tmax_clamped"], df["Tmin"], df["Ra"], latitude=latitude
            )
        except Exception as exc:
            print(f"Hargreaves calculation failed: {exc}")
            df["ET_Hargreaves"] = np.nan

    et_columns = ["ET_PT", "ET_PM", "ET_Maule", "ET_Hargreaves"]
    return df.dropna(subset=et_columns, how="all")


def _fmt(value, places=3) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(number):
        return ""
    return f"{number:.{places}f}"


def _series(df: pd.DataFrame, column: str) -> np.ndarray:
    if column not in df.columns:
        return np.full(len(df), np.nan, dtype=np.float64)
    return pd.to_numeric(df[column], errors="coerce").to_numpy(dtype=np.float64)


def _as_array(value, length: int) -> np.ndarray:
    if np.isscalar(value):
        return np.full(length, float(value), dtype=np.float64)
    array = np.asarray(value, dtype=np.float64)
    if array.shape == ():
        return np.full(length, float(array), dtype=np.float64)
    return array


def _et_display(series_mm: np.ndarray, unit: str, places: int) -> list[str]:
    out = []
    for value in series_mm:
        if not np.isfinite(value):
            out.append("")
            continue
        shown = convert_units(float(value), "mm", "inches") if unit == "inches" else float(value)
        out.append(f"{round(shown, places):.{places}f}")
    return out


def _columns_to_rows(df: pd.DataFrame, columns: list[tuple[str, np.ndarray]], et_text: list[str]) -> list[list[str]]:
    dates = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d").tolist()
    rendered = []
    for _key, values in columns:
        values = _as_array(values, len(df))
        rendered.append([_fmt(v, 3) for v in values])
    rows = []
    for i, day in enumerate(dates):
        rows.append([day] + [col[i] for col in rendered] + [et_text[i]])
    return rows


def _gamma_from_elevation(elevation: float) -> float:
    pressure = 101.3 * ((293 - 0.0065 * elevation) / 293) ** 5.26
    return 0.000665 * pressure


def _site_constants(latitude: float, elevation: float) -> list[dict]:
    return [
        {"name": "Latitude φ", "value": _fmt(latitude, 4), "units": "degrees"},
        {"name": "Elevation z", "value": _fmt(elevation, 1), "units": "m"},
        {"name": "Albedo", "value": _fmt(ALBEDO, 2), "units": "—"},
        {"name": "Soil heat flux G", "value": "0", "units": "MJ/m²/day (not subtracted)"},
    ]


def _ra_legend() -> list[dict]:
    return [
        {"symbol": "J", "meaning": "Day of year", "units": "1–366"},
        {"symbol": "Gsc", "meaning": "Solar constant used in the Ra function", "units": "0.0820 MJ/m²/min"},
        {"symbol": "φ", "meaning": "Latitude", "units": "radians inside the function"},
        {"symbol": "Ra", "meaning": "Extraterrestrial radiation", "units": "MJ/m²/day"},
    ]


def method_spec(method: str, latitude: float, elevation: float, unit_label: str) -> dict:
    gamma = _gamma_from_elevation(elevation)
    shared_site = _site_constants(latitude, elevation)
    if method == "pt":
        return {
            "code": "pt",
            "label": METHOD_LABELS["pt"],
            "formulas": [
                r"ET = \alpha \frac{\Delta}{\Delta + \gamma} \frac{R_n}{\lambda}",
                r"\Delta = \frac{4098 \cdot e_s(T)}{(T + 237.3)^{2}}, \quad e_s(T) = 0.6108 \exp\!\left(\frac{17.27\, T}{T + 237.3}\right)",
                *_RN_FORMULAS,
                _RA_FORMULA,
                _RA_FORMULA_2,
            ],
            "legend": [
                {"symbol": "ET", "meaning": "Priestley-Taylor reference evapotranspiration", "units": unit_label},
                {"symbol": "α", "meaning": "Priestley-Taylor coefficient", "units": "1.26"},
                {"symbol": "T", "meaning": "Average temperature passed in as Tavg. Values below 0 °C were set to 0.", "units": "°C"},
                {"symbol": "Δ", "meaning": "Slope of saturation vapour pressure at T", "units": "kPa/°C"},
                {"symbol": "γ", "meaning": "Psychrometric constant fixed in this function (not computed from elevation)", "units": "kPa/°C"},
                {"symbol": "λ", "meaning": "Latent heat of vaporization", "units": "MJ/kg"},
                {"symbol": "Rn", "meaning": "Net radiation, floored at 0", "units": "MJ/m²/day"},
                {"symbol": "Rs", "meaning": "Solar radiation", "units": "MJ/m²/day"},
                {"symbol": "Rns", "meaning": "Net shortwave radiation, (1 − 0.23) Rs", "units": "MJ/m²/day"},
                {"symbol": "Rnl", "meaning": "Net longwave radiation", "units": "MJ/m²/day"},
                {"symbol": "ea", "meaning": "Actual vapour pressure at (Tmax + Tmin) / 2", "units": "kPa"},
                {"symbol": "σ", "meaning": "Stefan-Boltzmann constant in this function", "units": "4.903×10⁻⁹ MJ/K⁴/m²/day"},
                {"symbol": "G", "meaning": "Not included. The function uses Rn directly.", "units": "0"},
                *_ra_legend(),
            ],
            "constants": shared_site + [
                {"name": "α", "value": _fmt(PT_ALPHA, 2), "units": "—"},
                {"name": "γ", "value": _fmt(PT_GAMMA, 3), "units": "kPa/°C"},
                {"name": "λ", "value": _fmt(PT_LAMBDA, 2), "units": "MJ/kg"},
                {"name": "σ", "value": "4.903e-9", "units": "MJ/K⁴/m²/day"},
            ],
        }
    if method == "pm":
        return {
            "code": "pm",
            "label": METHOD_LABELS["pm"],
            "formulas": [
                (
                    r"ET = \max\!\left("
                    r"\frac{0.408\, \Delta R_n + \gamma \dfrac{900}{T_{mean}+273} u_2 (e_s - e_a)}"
                    r"{\Delta + \gamma (1 + 0.34\, u_2)},\, 0\right)"
                ),
                (
                    r"e_s = \frac{e_s(T_{max}) + e_s(T_{min})}{2}, \quad "
                    r"e_a = e_s(T_{mean})\, \frac{RH}{100}"
                ),
                r"e_s(T) = 0.6108 \exp\!\left(\frac{17.27\, T}{T + 237.3}\right), \quad \Delta = \frac{4098\, e_s(T_{mean})}{(T_{mean}+237.3)^{2}}",
                r"\gamma = 0.000665 \times 101.3 \left(\frac{293 - 0.0065\, z}{293}\right)^{5.26}",
                *_RN_FORMULAS,
                _RA_FORMULA,
                _RA_FORMULA_2,
            ],
            "legend": [
                {"symbol": "ET", "meaning": "Penman-Monteith reference evapotranspiration", "units": unit_label},
                {"symbol": "Tmean", "meaning": "(Tmax + Tmin) / 2. Tmax below 0 °C was set to 0.", "units": "°C"},
                {"symbol": "u2", "meaning": "Wind speed at 2 m", "units": "m/s"},
                {"symbol": "es", "meaning": "Mean of saturation vapour pressure at Tmax and at Tmin", "units": "kPa"},
                {"symbol": "ea", "meaning": "es at Tmean times RH/100. This is not es × RH/100.", "units": "kPa"},
                {"symbol": "Δ", "meaning": "Slope of saturation vapour pressure at Tmean", "units": "kPa/°C"},
                {"symbol": "γ", "meaning": "Psychrometric constant from elevation", "units": "kPa/°C"},
                {"symbol": "Rn", "meaning": "Net radiation from the net-radiation function, floored at 0", "units": "MJ/m²/day"},
                {"symbol": "G", "meaning": "Not included. The numerator uses Rn, not Rn − G.", "units": "0"},
                *_ra_legend(),
            ],
            "constants": shared_site + [
                {"name": "γ", "value": _fmt(gamma, 5), "units": "kPa/°C"},
                {"name": "σ", "value": "4.903e-9", "units": "MJ/K⁴/m²/day"},
                {"name": "Wind coefficient", "value": "0.34", "units": "s/m"},
                {"name": "Aerodynamic numerator", "value": "900", "units": "—"},
            ],
        }
    if method == "maule":
        return {
            "code": "maule",
            "label": METHOD_LABELS["maule"],
            "formulas": [
                r"ET_{base} = k (T_{mean} + a)\, R_s",
                r"f_{RH} = \mathrm{clip}\!\left(1 - \frac{RH - 50}{200},\, 0.7,\, 1.3\right)",
                r"f_{\Delta T} = \mathrm{clip}\!\left(1 + \frac{(T_{max} - T_{min}) - 10}{100},\, 0.8,\, 1.2\right)",
                r"ET = \max(ET_{base} \cdot f_{RH} \cdot f_{\Delta T},\, 0)",
            ],
            "legend": [
                {"symbol": "ET", "meaning": "Maulé evapotranspiration", "units": unit_label},
                {"symbol": "Tmean", "meaning": "(Tmax + Tmin) / 2. Tmax below 0 °C was set to 0.", "units": "°C"},
                {"symbol": "Rs", "meaning": "Solar radiation", "units": "MJ/m²/day"},
                {"symbol": "k", "meaning": "Prairie-tuned coefficient in this function", "units": "—"},
                {"symbol": "a", "meaning": "Temperature offset in this function", "units": "°C"},
                {"symbol": "fRH", "meaning": "Humidity factor. 1 when RH is missing.", "units": "—"},
                {"symbol": "fΔT", "meaning": "Temperature-range factor", "units": "—"},
                {"symbol": "latitude", "meaning": "Accepted by the function and not used", "units": "degrees"},
            ],
            "constants": [
                {"name": "Latitude φ", "value": _fmt(latitude, 4), "units": "degrees (not used)"},
                {"name": "k", "value": _fmt(MAULE_K, 4), "units": "—"},
                {"name": "a", "value": _fmt(MAULE_A, 1), "units": "°C"},
                {"name": "fRH limits", "value": "0.7 to 1.3", "units": "—"},
                {"name": "fΔT limits", "value": "0.8 to 1.2", "units": "—"},
            ],
        }
    return {
        "code": "hargreaves",
        "label": METHOD_LABELS["hargreaves"],
        "formulas": [
            (
                r"ET = \max\!\left(0.0023\, (T_{mean} + 17.8)\, "
                r"\sqrt{\max(T_{max}-T_{min},\, 0)}\, \frac{R_a}{2.45},\, 0\right)"
            ),
            _RA_FORMULA,
            _RA_FORMULA_2,
        ],
        "legend": [
            {"symbol": "ET", "meaning": "Hargreaves-Samani evapotranspiration", "units": unit_label},
            {"symbol": "Tmean", "meaning": "(Tmax + Tmin) / 2. Tmax below 0 °C was set to 0.", "units": "°C"},
            {"symbol": "Ra", "meaning": "Extraterrestrial radiation", "units": "MJ/m²/day"},
            {"symbol": "Ra/2.45", "meaning": "Ra converted to an equivalent water depth", "units": "mm/day"},
            {"symbol": "0.0023", "meaning": "Hargreaves coefficient in this function", "units": "—"},
            {"symbol": "17.8", "meaning": "Temperature offset in this function", "units": "°C"},
            *_ra_legend(),
        ],
        "constants": [
            {"name": "Latitude φ", "value": _fmt(latitude, 4), "units": "degrees"},
            {"name": "Coefficient", "value": _fmt(HARGREAVES_C, 4), "units": "—"},
            {"name": "Temperature offset", "value": _fmt(HARGREAVES_OFFSET, 1), "units": "°C"},
            {"name": "Ra divisor", "value": "2.45", "units": "MJ/kg"},
            {"name": "Gsc", "value": "0.0820", "units": "MJ/m²/min"},
        ],
    }


def _trace_table(df: pd.DataFrame, method: str, latitude: float, unit: str) -> tuple[list[str], list[list[str]]]:
    n = len(df)
    unit_info = get_unit_info(unit)
    places = unit_info["decimal_places"]
    et_label = f"ET ({unit_info['daily_label']})"
    tmax = _series(df, "Tmax_clamped" if "Tmax_clamped" in df.columns else "Tmax")
    tmin = _series(df, "Tmin")
    rh = _series(df, "RH")
    rs = _series(df, "Rs")
    ra = _series(df, "Ra")
    column = METHOD_TO_COLUMN[method]
    et_mm = _series(df, column)
    et_text = _et_display(et_mm, unit, places)

    if method == "pt":
        rn_parts = net_radiation_parts_vec(df["Rs"], df["Tmax"], df["Tmin"], df["Ra"], df["RH"], elevation=ELEVATION_M)
        pt = priestley_taylor_parts_vec(df["Tavg"], rn_parts["Rn"])
        headers = [
            "Date",
            "Tmax (°C)",
            "Tmin (°C)",
            "Tmean (°C)",
            "RH (%)",
            "Rs (MJ/m²/day)",
            "Ra (MJ/m²/day)",
            "Rso (MJ/m²/day)",
            "Rs/Rso",
            "Rns (MJ/m²/day)",
            "ea (kPa)",
            "Rnl (MJ/m²/day)",
            "Rn (MJ/m²/day)",
            "Δ (kPa/°C)",
            et_label,
        ]
        columns = [
            ("tmax", tmax),
            ("tmin", tmin),
            ("tmean", _series(df, "Tavg")),
            ("rh", rh),
            ("rs", rs),
            ("ra", ra),
            ("rso", rn_parts["Rso"]),
            ("rs_rso", rn_parts["Rs_Rso"]),
            ("rns", rn_parts["Rns"]),
            ("ea", rn_parts["ea"]),
            ("rnl", rn_parts["Rnl"]),
            ("rn", rn_parts["Rn"]),
            ("delta", pt["delta"]),
        ]
    elif method == "pm":
        parts = penman_monteith_parts_vec(
            df["Tmax_clamped"], df["Tmin"], df["RH"], df["u2"], df["Rs"], df["Ra"], elevation=ELEVATION_M
        )
        headers = [
            "Date",
            "Tmax (°C)",
            "Tmin (°C)",
            "Tmean (°C)",
            "RH (%)",
            "u2 (m/s)",
            "Rs (MJ/m²/day)",
            "Ra (MJ/m²/day)",
            "Rso (MJ/m²/day)",
            "Rs/Rso",
            "Rns (MJ/m²/day)",
            "es (kPa)",
            "ea (kPa)",
            "es − ea (kPa)",
            "Rnl (MJ/m²/day)",
            "Rn (MJ/m²/day)",
            "Δ (kPa/°C)",
            "γ (kPa/°C)",
            "Wind term",
            et_label,
        ]
        columns = [
            ("tmax", tmax),
            ("tmin", tmin),
            ("tmean", parts["Tmean"]),
            ("rh", rh),
            ("u2", _series(df, "u2")),
            ("rs", rs),
            ("ra", ra),
            ("rso", parts["Rso"]),
            ("rs_rso", parts["Rs_Rso"]),
            ("rns", parts["Rns"]),
            ("es", parts["es"]),
            ("ea", parts["ea"]),
            ("vpd", parts["vpd"]),
            ("rnl", parts["Rnl"]),
            ("rn", parts["Rn"]),
            ("delta", parts["delta"]),
            ("gamma", np.full(n, parts["gamma"])),
            ("wind", parts["wind_term"]),
        ]
    elif method == "maule":
        parts = maule_parts_vec(df["Tmax_clamped"], df["Tmin"], df["Rs"], df["RH"], latitude=latitude)
        headers = [
            "Date",
            "Tmax (°C)",
            "Tmin (°C)",
            "Tmean (°C)",
            "Trange (°C)",
            "RH (%)",
            "Rs (MJ/m²/day)",
            "ET base",
            "fRH",
            "fΔT",
            et_label,
        ]
        columns = [
            ("tmax", tmax),
            ("tmin", tmin),
            ("tmean", parts["Tmean"]),
            ("trange", parts["Trange"]),
            ("rh", rh),
            ("rs", rs),
            ("base", parts["et_base"]),
            ("frh", _as_array(parts["humidity_factor"], n)),
            ("frange", parts["range_factor"]),
        ]
    else:
        parts = hargreaves_parts_vec(df["Tmax_clamped"], df["Tmin"], df["Ra"], latitude=latitude)
        headers = [
            "Date",
            "Tmax (°C)",
            "Tmin (°C)",
            "Tmean (°C)",
            "Trange (°C)",
            "Ra (MJ/m²/day)",
            "Ra/2.45 (mm/day)",
            et_label,
        ]
        columns = [
            ("tmax", tmax),
            ("tmin", tmin),
            ("tmean", parts["Tmean"]),
            ("trange", parts["Trange"]),
            ("ra", ra),
            ("ra_mm", parts["Ra_mm"]),
        ]
    return headers, _columns_to_rows(df, columns, et_text)


def build_method_trace(df: pd.DataFrame, method: str, latitude: float, unit: str) -> dict:
    unit_info = get_unit_info(unit if unit in ("mm", "inches") else "mm")
    spec = method_spec(method, latitude, ELEVATION_M, unit_info["daily_label"])
    headers, rows = _trace_table(df, method, latitude, unit if unit in ("mm", "inches") else "mm")
    spec["headers"] = headers
    spec["rows"] = rows
    return spec


def trace_csv(spec: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["constant", "value", "units"])
    for item in spec["constants"]:
        writer.writerow([item["name"], item["value"], item["units"]])
    writer.writerow([])
    writer.writerow(spec["headers"])
    for row in spec["rows"]:
        writer.writerow(row)
    return buffer.getvalue()


def trace_zip(df: pd.DataFrame, latitude: float, unit: str) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for method in METHOD_ORDER:
            spec = build_method_trace(df, method, latitude, unit)
            filename = f"{method}_inputs.csv"
            archive.writestr(filename, trace_csv(spec))
    return payload.getvalue()


def query_url(base: str, **params) -> str:
    clean = {key: value for key, value in params.items() if value not in (None, "")}
    encoded = urlencode(clean)
    return f"{base}?{encoded}" if encoded else base
