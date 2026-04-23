from math import exp

import numpy as np
import pandas as pd


def saturation_vapor_pressure(T):
    """Compute saturation vapor pressure es from temperature T (C)."""
    return 0.6108 * exp((17.27 * T) / (T + 237.3))


def delta_svp(T):
    """Slope of saturation vapor pressure curve (kPa/C)."""
    es = saturation_vapor_pressure(T)
    return 4098 * es / (T + 237.3) ** 2


def actual_vapor_pressure(T, RH):
    """Calculate actual vapor pressure from temperature and relative humidity."""
    if pd.isna(T) or pd.isna(RH):
        return np.nan
    es = saturation_vapor_pressure(T)
    return es * (RH / 100)


def psychrometric_constant(elevation=0):
    """Calculate psychrometric constant (kPa/C)."""
    pressure = 101.3 * ((293 - 0.0065 * elevation) / 293) ** 5.26
    return 0.000665 * pressure


def net_radiation_estimate(Rs, Tmax, Tmin, Ra, RH=None, elevation=766):
    """Estimate net radiation from solar radiation and temperature data."""
    if pd.isna(Rs) or pd.isna(Tmax) or pd.isna(Tmin) or pd.isna(Ra):
        return np.nan

    rns = (1 - 0.23) * Rs
    sigma = 4.903e-9
    tmaxk = Tmax + 273.16
    tmink = Tmin + 273.16

    rso = (0.75 + 2e-5 * elevation) * Ra
    rs_rso = min(Rs / rso, 1.0) if rso > 0 else 0.8

    if RH is not None and not pd.isna(RH):
        ea = actual_vapor_pressure((Tmax + Tmin) / 2, RH)
        rnl = sigma * (tmaxk**4 + tmink**4) / 2 * (0.34 - 0.14 * np.sqrt(ea)) * (1.35 * rs_rso - 0.35)
    else:
        rnl = sigma * (tmaxk**4 + tmink**4) / 2 * 0.2 * (1.35 * rs_rso - 0.35)

    rn = rns - rnl
    return max(rn, 0)


def priestley_taylor_ET(Tavg, Rn, alpha=1.26, gamma=0.066, lambda_val=2.45):
    """Priestley-Taylor ET estimation."""
    if pd.isna(Tavg) or pd.isna(Rn):
        return np.nan
    delta = delta_svp(Tavg)
    return alpha * (delta / (delta + gamma)) * (Rn / lambda_val)


def penman_monteith_ET(Tmax, Tmin, RH, u2, Rs, Ra, elevation=766):
    """Penman-Monteith ET0 calculation (FAO-56)."""
    if any(pd.isna(val) for val in [Tmax, Tmin, RH, u2, Rs, Ra]):
        return np.nan

    tmean = (Tmax + Tmin) / 2
    delta = delta_svp(tmean)
    gamma = psychrometric_constant(elevation)
    es = (saturation_vapor_pressure(Tmax) + saturation_vapor_pressure(Tmin)) / 2
    ea = actual_vapor_pressure(tmean, RH)
    rn = net_radiation_estimate(Rs, Tmax, Tmin, Ra, RH, elevation)

    wind_term = 900 / (tmean + 273) * u2 * (es - ea)
    numerator = 0.408 * delta * rn + gamma * wind_term
    denominator = delta + gamma * (1 + 0.34 * u2)
    et0 = numerator / denominator
    return max(et0, 0)


def maule_ET(Tmax, Tmin, Rs, RH=None, latitude=49.7):
    """Maule ET estimation method."""
    if any(pd.isna(val) for val in [Tmax, Tmin, Rs]):
        return np.nan

    _ = latitude
    tmean = (Tmax + Tmin) / 2
    trange = Tmax - Tmin
    # Alberta-tuned defaults (higher than original Chile-centric coefficients)
    # to keep Maulé magnitude aligned with continental Prairie conditions.
    k = 0.0055
    a = 17.8
    et_base = k * (tmean + a) * Rs

    if RH is not None and not pd.isna(RH):
        humidity_factor = 1.0 - (RH - 50) / 200
        humidity_factor = max(0.7, min(1.3, humidity_factor))
    else:
        humidity_factor = 1.0

    range_factor = 1.0 + (trange - 10) / 100
    range_factor = max(0.8, min(1.2, range_factor))

    et_maule = et_base * humidity_factor * range_factor
    return max(et_maule, 0)


def hargreaves_ET(Tmax, Tmin, Ra=None, latitude=49.7):
    """Hargreaves-Samani ET estimation."""
    if any(pd.isna(val) for val in [Tmax, Tmin]):
        return np.nan

    tmean = (Tmax + Tmin) / 2
    trange = Tmax - Tmin

    if Ra is None:
        day_of_year = 200
        solar_declination = 23.45 * np.sin(np.radians(360 * (284 + day_of_year) / 365))
        lat_rad = np.radians(latitude)
        decl_rad = np.radians(solar_declination)
        ws = np.arccos(-np.tan(lat_rad) * np.tan(decl_rad))
        Ra = 37.6 * (
            ws * np.sin(lat_rad) * np.sin(decl_rad) + np.cos(lat_rad) * np.cos(decl_rad) * np.sin(ws)
        )

    # Convert Ra from MJ/m2/day to equivalent mm/day energy units
    # for the standard Hargreaves coefficient.
    ra_mm_equivalent = Ra / 2.45
    c_h = 0.0023
    et_hargreaves = c_h * (tmean + 17.8) * np.sqrt(trange) * ra_mm_equivalent
    return max(et_hargreaves, 0)


def calculate_extraterrestrial_radiation(latitude, day_of_year):
    """Calculate extraterrestrial radiation for latitude and day of year."""
    gsc = 0.0820
    lat_rad = np.radians(latitude)
    dr = 1 + 0.033 * np.cos(2 * np.pi * day_of_year / 365)
    delta = 0.409 * np.sin(2 * np.pi * day_of_year / 365 - 1.39)
    ws = np.arccos(-np.tan(lat_rad) * np.tan(delta))
    ra = (24 * 60 / np.pi) * gsc * dr * (ws * np.sin(lat_rad) * np.sin(delta) + np.cos(lat_rad) * np.cos(delta) * np.sin(ws))
    return ra
