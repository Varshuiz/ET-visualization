"""
Environment Canada Weather Forecast Scraper - MSC Datamart XML Version
Fetches precipitation forecasts using official Environment Canada citypage XML feeds.

NOTE: The old RSS feed (weather.gc.ca/rss/city/ab-XX_e.xml) was discontinued in 2025.
This version uses the MSC Datamart at dd.weather.gc.ca which is the official replacement.

Two URL strategies are attempted in order:
  1. Stable legacy path: dd.weather.gc.ca/citypage_weather/xml/{PROV}/s0000XXX_e.xml
  2. New timestamped path: dd.weather.gc.ca/today/citypage_weather/{PROV}/{HH}/ (directory listing)
"""

import requests
import xml.etree.ElementTree as ET
import pandas as pd
import re
from datetime import datetime, timedelta, timezone
import time
import numpy as np

from .eccc_forecast_registry import (
    FORECAST_PROVINCE_ECC_CODE,
    FORECAST_SITE_META_BY_PROVINCE,
    get_lat_lon,
)
from .location_services import ALBERTA_LOCATIONS
from .weather_ingestion import kmh_max_wind_to_u2_ms


def _location_codes_by_eccc_province(ab_location_codes):
    """Merge Alberta manual codes with BC/SK/MB sites from the official ECCC site list."""
    out = {"AB": dict(ab_location_codes)}
    for pname, cities in FORECAST_SITE_META_BY_PROVINCE.items():
        ecc = FORECAST_PROVINCE_ECC_CODE[pname]
        out[ecc] = {c: d["code"] for c, d in cities.items()}
    return out


_DEFAULT_CITY_BY_PROVINCE = {
    "AB": "Calgary",
    "BC": "Vancouver",
    "SK": "Saskatoon",
    "MB": "Winnipeg",
}

_ECC_TO_FORECAST_PROV_NAME = {v: k for k, v in FORECAST_PROVINCE_ECC_CODE.items()}


class EnvironmentCanadaScraper:
    """
    Scraper for Environment Canada weather forecasts using MSC Datamart XML feeds.
    """

    # Updated site codes from: dd.weather.gc.ca/today/citypage_weather/docs/site_list_provinces_en.csv
    # Format: s0000XXX  (replaces old ab-XX RSS codes)
    LOCATION_CODES = {
        # Major Cities
        'Calgary':              's0000047',
        'Edmonton':             's0000045',
        'Lethbridge':           's0000652',
        'Red Deer':             's0000645',
        'Medicine Hat':         's0000745',
        'Grande Prairie':       's0000661',
        'Fort McMurray':        's0000595',

        # Medium Cities
        'Airdrie':              's0000768',
        'St. Albert':           's0000045',   # uses Edmonton
        'Spruce Grove':         's0000453',   # uses Stony Plain
        'Lloydminster':         's0000590',
        'Camrose':              's0000311',
        'Brooks':               's0000093',
        'Okotoks':              's0000767',
        'Cochrane':             's0000493',
        'Strathmore':           's0000068',
        'Stony Plain':          's0000453',
        'Wetaskiwin':           's0000312',
        'Leduc':                's0000045',   # uses Edmonton
        'Beaumont':             's0000045',   # uses Edmonton
        'Fort Saskatchewan':    's0000045',   # uses Edmonton
        'Cold Lake':            's0000619',
        'High River':           's0000383',
        'Sylvan Lake':          's0000645',   # uses Red Deer
        'Lacombe':              's0000297',

        # Mountain / Resort Towns
        'Banff':                's0000404',
        'Jasper':               's0000218',
        'Canmore':              's0000403',

        # Northern Alberta
        'Peace River':          's0000625',
        'Slave Lake':           's0000794',
        'Whitecourt':           's0000477',
        'Hinton':               's0000217',
        'High Level':           's0000621',
        'Fort Chipewyan':       's0000636',
        'Rainbow Lake':         's0000643',
        'Athabasca':            's0000001',

        # Central Alberta
        'Drumheller':           's0000129',
        'Innisfail':            's0000645',   # uses Red Deer
        'Ponoka':               's0000645',   # uses Red Deer
        'Rocky Mountain House': 's0000306',
        'Westlock':             's0000427',
        'Barrhead':             's0000413',
        'Drayton Valley':       's0000468',
        'Edson':                's0000518',
        'Lac La Biche':         's0000229',
        'Stettler':             's0000829',

        # Southern Alberta
        'Taber':                's0000359',
        'Vauxhall':             's0000358',
        'Claresholm':           's0000120',
        'Pincher Creek':        's0000824',
        'Cardston':             's0000159',
        'Crowsnest Pass':       's0000310',

        # Eastern Alberta
        'Vegreville':           's0000356',
        'Wainwright':           's0000833',
        'Coronation':           's0000111',
        'Hanna':                's0000129',   # uses Drumheller

        # Northeast Alberta
        'Bonnyville':           's0000619',   # uses Cold Lake
        'St. Paul':             's0000001',   # uses Athabasca
    }

    BASE_URL_STABLE = "https://dd.weather.gc.ca/citypage_weather/xml/{prov}/{code}_e.xml"
    BASE_URL_TODAY = "https://dd.weather.gc.ca/today/citypage_weather/{prov}/{hour}/"
    ECCC_CLIMATE_DAILY_URL = "https://api.weather.gc.ca/collections/climate-daily/items"
    CACHE_TTL_SECONDS = 600  # 10 minutes
    _FORECAST_CACHE = {}
    _LOCATION_CODES_BY_PROVINCE = None

    @classmethod
    def _codes_for_province(cls, province_code):
        if cls._LOCATION_CODES_BY_PROVINCE is None:
            cls._LOCATION_CODES_BY_PROVINCE = _location_codes_by_eccc_province(cls.LOCATION_CODES)
        prov = (province_code or "AB").upper()
        return cls._LOCATION_CODES_BY_PROVINCE.get(prov, cls._LOCATION_CODES_BY_PROVINCE["AB"])

    def get_location_code(self, city_name, province_code="AB"):
        """Return the s0000XXX site code for a city in the given MSC province (AB, BC, SK, MB)."""
        pmap = self._codes_for_province(province_code)
        if city_name in pmap:
            return pmap[city_name]
        for city, code in pmap.items():
            if city.lower() == city_name.lower():
                return code
        prov = (province_code or "AB").upper()
        fallback_city = _DEFAULT_CITY_BY_PROVINCE.get(prov, "Calgary")
        print(f"Warning: Location '{city_name}' not found in {prov}, defaulting to {fallback_city}")
        return pmap.get(fallback_city) or pmap.get("Calgary") or next(iter(pmap.values()))

    # ------------------------------------------------------------------
    # URL resolution
    # ------------------------------------------------------------------

    def _fetch_xml_content(self, site_code, province_code="AB"):
        """
        Try two strategies to get the XML content for a site code.
        Strategy 1: stable /citypage_weather/xml/ path
        Strategy 2: timestamped /today/citypage_weather/ directory listing
        Returns (url, content_bytes) or raises ValueError.
        """
        prov = (province_code or "AB").upper()
        # Strategy 1: stable path
        stable_url = self.BASE_URL_STABLE.format(prov=prov, code=site_code)
        try:
            r = requests.get(stable_url, timeout=10,
                             headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200:
                print(f"   URL: {stable_url}")
                return stable_url, r.content
        except requests.RequestException:
            pass

        # Strategy 2: new timestamped path (check current + previous 3 hours)
        now_utc = datetime.now(timezone.utc)
        for offset in range(4):
            hour = (now_utc.hour - offset) % 24
            dir_url = self.BASE_URL_TODAY.format(prov=prov, hour=f"{hour:02d}")
            try:
                r = requests.get(dir_url, timeout=10,
                                 headers={'User-Agent': 'Mozilla/5.0'})
                if r.status_code != 200:
                    continue
                pattern = rf'(\d{{8}}T\d{{6}}\.\d+Z_MSC_CitypageWeather_{re.escape(site_code)}_en\.xml)'
                matches = re.findall(pattern, r.text)
                if not matches:
                    continue
                filename = sorted(matches)[-1]
                file_url = dir_url + filename
                fr = requests.get(file_url, timeout=15,
                                  headers={'User-Agent': 'Mozilla/5.0'})
                if fr.status_code == 200:
                    print(f"   URL: {file_url}")
                    return file_url, fr.content
            except requests.RequestException:
                continue

        raise ValueError(
            f"Could not retrieve XML for site {site_code} ({prov}). "
            "Both stable and timestamped URL strategies failed."
        )

    # ------------------------------------------------------------------
    # Main forecast fetch
    # ------------------------------------------------------------------

    def fetch_forecast(self, city_name='Calgary', days=None, province_code='AB'):
        """
        Fetch weather forecast from Environment Canada MSC Datamart XML.

        Parameters
        ----------
        city_name : str   e.g. 'Calgary', 'Edmonton'
        days      : int|None   Number of daily forecast periods to return.
                               None returns full available range.
        province_code : str   MSC two-letter province (AB, BC, SK, MB).

        Returns
        -------
        pandas.DataFrame with columns:
            Date, Period, Temp_High, Temp_Low, RH_percent, Wind_kmh_max, u2_ms,
            Rs_mjm2 (filled downstream via Open-Meteo merge),
            Precipitation_mm, Forecast
        """
        try:
            prov = (province_code or "AB").upper()
            site_code = self.get_location_code(city_name, prov)
            cache_key = f"{prov}:{site_code}"
            now_ts = time.time()

            cached = self._FORECAST_CACHE.get(cache_key)
            if cached and (now_ts - cached["ts"] <= self.CACHE_TTL_SECONDS):
                full_df = cached["df"]
            else:
                url, content = self._fetch_xml_content(site_code, prov)
                _ = url  # keep for debugging hooks
                root = ET.fromstring(content)

                forecast_group = root.find('.//forecastGroup')
                if forecast_group is None:
                    raise ValueError("No <forecastGroup> element found in XML response")

                forecasts_raw = forecast_group.findall('forecast')
                forecast_data = []
                for fc in forecasts_raw:
                    period_el = fc.find('period')
                    if period_el is None:
                        continue
                    period_name = period_el.get('textForecastName', '').strip()
                    if not period_name:
                        continue

                    text_el = fc.find('textSummary')
                    forecast_text = (text_el.text or '').strip() if text_el is not None else ''
                    abbrev_el = fc.find('.//abbreviatedForecast/textSummary')
                    abbrev_text = (abbrev_el.text or '').strip() if abbrev_el is not None else ''
                    full_text = forecast_text or abbrev_text

                    temp_high = self._xml_temperature(fc, 'high')
                    temp_low = self._xml_temperature(fc, 'low')
                    if temp_high is None and temp_low is None:
                        temp_high, temp_low = self._extract_temperatures(full_text)

                    precip = self._xml_precipitation(fc)
                    if precip == 0.0:
                        precip = self._extract_precipitation(full_text)

                    rh = self._xml_relative_humidity(fc)
                    if rh is None:
                        rh = self._extract_relative_humidity(full_text)

                    wind_kmh = self._xml_wind_kmh_max(fc)
                    if wind_kmh is None:
                        wind_kmh = self._extract_wind_kmh_max(full_text)

                    forecast_data.append({
                        'Period': period_name,
                        'Temp_High': temp_high,
                        'Temp_Low': temp_low,
                        'RH_percent': rh,
                        'Wind_kmh_max': wind_kmh,
                        'Precipitation_mm': precip,
                        'Forecast': full_text,
                    })

                if not forecast_data:
                    raise ValueError("No forecast periods could be parsed from XML")

                full_df = self._group_by_day(pd.DataFrame(forecast_data), None)
                self._FORECAST_CACHE[cache_key] = {"ts": now_ts, "df": full_df}

            if days is None:
                return full_df.copy()

            target_days = max(1, int(days))
            if len(full_df) >= target_days:
                return full_df.head(target_days).copy()

            # Extend beyond citypage horizon using ECCC climate-daily baseline.
            extra_days = target_days - len(full_df)
            extended_df = self._build_extended_outlook(
                city_name=city_name,
                start_date=full_df["Date"].max() + timedelta(days=1),
                extra_days=extra_days,
                province_code=prov,
            )
            if extended_df is None or extended_df.empty:
                return full_df.copy()
            return pd.concat([full_df, extended_df], ignore_index=True)

        except Exception as e:
            print(f"   Error: {e}\n")
            raise ValueError(f"Failed to fetch forecast: {e}")

    def _resolve_city_coords(self, city_name, province_code="AB"):
        prov = (province_code or "AB").upper()
        if prov == "AB":
            loc = ALBERTA_LOCATIONS.get(city_name)
            if loc:
                return float(loc["lat"]), float(loc["lon"])
            calg = ALBERTA_LOCATIONS.get("Calgary", {"lat": 51.05, "lon": -114.07})
            return float(calg["lat"]), float(calg["lon"])
        disp = _ECC_TO_FORECAST_PROV_NAME.get(prov)
        if disp:
            lat, lon = get_lat_lon(disp, city_name)
            if lat is not None and lon is not None:
                return lat, lon
            fb = _DEFAULT_CITY_BY_PROVINCE.get(prov, "Vancouver")
            lat, lon = get_lat_lon(disp, fb)
            if lat is not None and lon is not None:
                return lat, lon
        return 53.0, -114.0

    def _build_extended_outlook(self, city_name, start_date, extra_days, province_code="AB"):
        """
        Build extended day-level outlook from ECCC climate-daily recent observations.
        This is a climate-based estimate when citypage forecast horizon is exceeded.
        """
        empty_cols = [
            "Date", "Period", "Temp_High", "Temp_Low", "RH_percent",
            "Wind_kmh_max", "u2_ms", "Rs_mjm2", "Precipitation_mm", "Forecast",
        ]
        if extra_days <= 0:
            return pd.DataFrame(columns=empty_cols)

        lat, lon = self._resolve_city_coords(city_name, province_code)
        bbox = f"{lon - 0.3},{lat - 0.3},{lon + 0.3},{lat + 0.3}"
        hist_end = pd.Timestamp.utcnow().normalize() - pd.Timedelta(days=1)
        hist_start = hist_end - pd.Timedelta(days=59)
        params = {
            "bbox": bbox,
            "datetime": f"{hist_start.date()}/{hist_end.date()}",
            "f": "json",
            "limit": 5000,
        }
        try:
            r = requests.get(self.ECCC_CLIMATE_DAILY_URL, params=params, timeout=8)
            r.raise_for_status()
            features = r.json().get("features", [])
        except Exception:
            return pd.DataFrame(columns=empty_cols)

        rows = []
        for feat in features:
            props = feat.get("properties", {})
            geom = feat.get("geometry", {})
            coords = geom.get("coordinates", [None, None])
            date_val = pd.to_datetime(props.get("LOCAL_DATE"), errors="coerce")
            tmax = pd.to_numeric(props.get("MAX_TEMPERATURE"), errors="coerce")
            tmin = pd.to_numeric(props.get("MIN_TEMPERATURE"), errors="coerce")
            precip = pd.to_numeric(props.get("TOTAL_PRECIPITATION"), errors="coerce")
            lon_s = pd.to_numeric(coords[0], errors="coerce")
            lat_s = pd.to_numeric(coords[1], errors="coerce")
            if pd.isna(date_val) or pd.isna(lon_s) or pd.isna(lat_s):
                continue
            rows.append(
                {
                    "Date": date_val.normalize(),
                    "Temp_High": tmax,
                    "Temp_Low": tmin,
                    "Precipitation_mm": max(float(precip), 0.0) if not pd.isna(precip) else 0.0,
                    "distance_sq": (lat_s - lat) ** 2 + (lon_s - lon) ** 2,
                }
            )

        if not rows:
            return pd.DataFrame(columns=empty_cols)

        hist_df = pd.DataFrame(rows).sort_values(["Date", "distance_sq"]).groupby("Date", as_index=False).first()
        if hist_df.empty:
            return pd.DataFrame(columns=empty_cols)

        hist_df["dow"] = pd.to_datetime(hist_df["Date"]).dt.dayofweek
        dow_stats = hist_df.groupby("dow").agg(
            Temp_High=("Temp_High", "mean"),
            Temp_Low=("Temp_Low", "mean"),
            Precipitation_mm=("Precipitation_mm", "mean"),
        )
        global_stats = {
            "Temp_High": float(hist_df["Temp_High"].mean()) if not hist_df["Temp_High"].isna().all() else np.nan,
            "Temp_Low": float(hist_df["Temp_Low"].mean()) if not hist_df["Temp_Low"].isna().all() else np.nan,
            "Precipitation_mm": float(hist_df["Precipitation_mm"].mean()) if not hist_df["Precipitation_mm"].isna().all() else 0.0,
        }

        out = []
        for i in range(extra_days):
            d = (pd.to_datetime(start_date) + pd.Timedelta(days=i)).normalize()
            dow = d.dayofweek
            if dow in dow_stats.index:
                tmax = dow_stats.loc[dow, "Temp_High"]
                tmin = dow_stats.loc[dow, "Temp_Low"]
                precip = dow_stats.loc[dow, "Precipitation_mm"]
            else:
                tmax = global_stats["Temp_High"]
                tmin = global_stats["Temp_Low"]
                precip = global_stats["Precipitation_mm"]
            out.append(
                {
                    "Date": d,
                    "Period": f"{d.strftime('%A')} (Extended)",
                    "Temp_High": None if pd.isna(tmax) else float(tmax),
                    "Temp_Low": None if pd.isna(tmin) else float(tmin),
                    # Climate-daily extension does not carry RH or wind; filled via Open-Meteo merge in the view.
                    "RH_percent": None,
                    "Wind_kmh_max": None,
                    "u2_ms": None,
                    "Rs_mjm2": None,
                    "Precipitation_mm": max(float(precip), 0.0) if not pd.isna(precip) else 0.0,
                    "Forecast": "Extended outlook derived from recent ECCC climate-daily station patterns.",
                }
            )
        return pd.DataFrame(out)

    # ------------------------------------------------------------------
    # XML helpers
    # ------------------------------------------------------------------

    def _xml_temperature(self, forecast_el, cls):
        """Extract a temperature by class='high' or 'low' from the forecast XML."""
        for t in forecast_el.findall('.//temperature'):
            if t.get('class', '').lower() == cls:
                try:
                    return float(t.text)
                except (TypeError, ValueError):
                    pass
        return None

    def _xml_precipitation(self, forecast_el):
        """Extract total precipitation (mm) from the XML <precipitation> block."""
        total = 0.0
        for el in forecast_el.findall('.//precipTotalAmount'):
            try:
                total += float(el.text)
            except (TypeError, ValueError):
                pass
        # snowLevel is in cm, convert to mm
        for el in forecast_el.findall('.//snowLevel'):
            try:
                total += float(el.text) * 10
            except (TypeError, ValueError):
                pass
        return total

    def _xml_relative_humidity(self, forecast_el):
        """
        Best-effort relative humidity (%) extraction from MSC citypage XML.

        Citypage schemas vary; we scan descendant elements for plausible humidity fields.
        """
        candidates = []
        for el in forecast_el.iter():
            tag = (el.tag or "").lower()
            if "humid" not in tag and "rh" not in tag:
                continue
            # Common patterns: <humidity units="%">65</humidity> or attributes
            txt = (el.text or "").strip()
            if txt:
                try:
                    candidates.append(float(txt))
                except (TypeError, ValueError):
                    pass
            for attr in ("value", "percent", "humidity", "rh"):
                val = el.get(attr)
                if val is None:
                    continue
                try:
                    candidates.append(float(val))
                except (TypeError, ValueError):
                    pass
        if not candidates:
            return None
        # If multiple values exist (e.g., daytime/nighttime), use a simple mean.
        rh = float(np.mean(candidates))
        if rh > 1.0:  # assume percent
            rh = max(0.0, min(rh, 100.0))
        else:  # rare fractional form
            rh = max(0.0, min(rh * 100.0, 100.0))
        return rh

    def _xml_wind_kmh_max(self, forecast_el):
        """
        Maximum wind speed (km/h) from MSC citypage <wind> blocks in this period.

        Uses the greater of reported speed and gust when both exist (conservative for ET).
        """
        vals = []
        for wnd in forecast_el.findall(".//wind"):
            for child in list(wnd):
                tag = (child.tag or "").rsplit("}", 1)[-1].lower()
                if tag not in ("speed", "gust"):
                    continue
                units = (child.get("units") or "").lower()
                try:
                    v = float((child.text or "").strip())
                except (TypeError, ValueError):
                    continue
                if "m/s" in units or units == "m_s":
                    vals.append(v * 3.6)
                else:
                    vals.append(v)
        if not vals:
            return None
        return float(max(vals))

    def _extract_wind_kmh_max(self, text):
        """Extract a representative max wind speed (km/h) from English forecast text."""
        if not text:
            return None
        t = text.lower()
        vals = []
        for m in re.finditer(r"gust(?:ing)?\s+to\s+(\d+)\s*km/h", t):
            vals.append(float(m.group(1)))
        for m in re.finditer(r"(\d+)\s*km/h", t):
            vals.append(float(m.group(1)))
        if not vals:
            return None
        return float(max(vals))

    # ------------------------------------------------------------------
    # Day grouping
    # ------------------------------------------------------------------

    def _group_by_day(self, df, max_days=None):
        """
        Merge daytime and nighttime periods into single daily rows.
        Daytime period provides Temp_High; nighttime provides Temp_Low.
        Precipitation is summed across both halves.
        """
        today = datetime.now()
        daily_data = {}

        for _, row in df.iterrows():
            period = row['Period']
            period_lower = period.lower()

            is_night = 'night' in period_lower or period_lower == 'tonight'

            day_match = re.search(
                r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday)',
                period_lower
            )

            if 'tonight' in period_lower:
                day_name = 'Tonight'
            elif day_match:
                day_name = day_match.group(1).title()
            else:
                day_name = period.split()[0].title()

            if day_name not in daily_data:
                daily_data[day_name] = {
                    'day_name':       day_name,
                    'temp_high':      None,
                    'temp_low':       None,
                    'rh_values':      [],
                    'wind_kmh_values': [],
                    'precipitation':  0.0,
                    'forecast_parts': [],
                }

            if is_night:
                if row['Temp_Low'] is not None:
                    daily_data[day_name]['temp_low'] = row['Temp_Low']
            else:
                if row['Temp_High'] is not None:
                    daily_data[day_name]['temp_high'] = row['Temp_High']

            daily_data[day_name]['precipitation'] += row['Precipitation_mm']
            rh = row.get('RH_percent', None)
            if rh is not None and not pd.isna(rh):
                try:
                    daily_data[day_name]['rh_values'].append(float(rh))
                except (TypeError, ValueError):
                    pass
            wk = row.get("Wind_kmh_max", None)
            if wk is not None and not pd.isna(wk):
                try:
                    daily_data[day_name]["wind_kmh_values"].append(float(wk))
                except (TypeError, ValueError):
                    pass
            if row['Forecast']:
                daily_data[day_name]['forecast_parts'].append(row['Forecast'])

        day_values = list(daily_data.values())
        if max_days is not None:
            day_values = day_values[:max_days]

        result_data = []
        for i, day_data in enumerate(day_values):
            rh_vals = day_data.get('rh_values') or []
            rh_day = float(np.mean(rh_vals)) if rh_vals else None
            wk_vals = day_data.get("wind_kmh_values") or []
            wind_kmh_day = float(max(wk_vals)) if wk_vals else None
            u2_day = kmh_max_wind_to_u2_ms(wind_kmh_day)
            result_data.append({
                'Date':             today + timedelta(days=i),
                'Period':           day_data['day_name'],
                'Temp_High':        day_data['temp_high'],
                'Temp_Low':         day_data['temp_low'],
                'RH_percent':       rh_day,
                'Wind_kmh_max':     wind_kmh_day,
                'u2_ms':            u2_day,
                'Rs_mjm2':          None,
                'Precipitation_mm': day_data['precipitation'],
                'Forecast':         ' '.join(day_data['forecast_parts']),
            })

        return pd.DataFrame(result_data)

    # ------------------------------------------------------------------
    # Text-based fallback parsers
    # ------------------------------------------------------------------

    def _extract_precipitation(self, text):
        """Extract precipitation (mm) from forecast text as a fallback."""
        if not text:
            return 0.0

        mm_matches = re.findall(r'(\d+(?:\.\d+)?)\s*(?:to\s*(\d+(?:\.\d+)?))?\s*mm', text, re.I)
        if mm_matches:
            amounts = [float(b) if b else float(a) for a, b in mm_matches]
            return max(amounts)

        percent_matches = re.findall(r'(\d+)\s*percent', text, re.I)
        if percent_matches:
            prob = int(percent_matches[0])
            if prob >= 80:   return 10.0
            elif prob >= 60: return 5.0
            elif prob >= 40: return 2.0
            elif prob >= 20: return 0.5

        t = text.lower()
        if any(w in t for w in ['rain', 'showers', 'drizzle']):
            return 15.0 if 'heavy' in t else (2.0 if 'light' in t else 5.0)
        if 'snow' in t:
            return 20.0 if 'heavy' in t else (3.0 if 'light' in t else 8.0)

        return 0.0

    def _extract_relative_humidity(self, text):
        """Extract relative humidity (%) from forecast text as a fallback."""
        if not text:
            return None
        patterns = [
            r"relative\s+humidity[^0-9]{0,12}(\d{1,3})\s*%",
            r"humidity[^0-9]{0,12}(\d{1,3})\s*%",
            r"\brh\b[^0-9]{0,12}(\d{1,3})\s*%",
            r"\b(\d{1,3})\s*%\s*relative\s+humidity",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                try:
                    rh = float(m.group(1))
                    return max(0.0, min(rh, 100.0))
                except (TypeError, ValueError):
                    continue
        return None

    def _extract_temperatures(self, text):
        """Extract high/low temperatures from forecast text as a fallback."""
        if not text:
            return None, None

        word_map = {
            'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
            'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
            'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13,
            'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'seventeen': 17,
            'eighteen': 18, 'nineteen': 19, 'twenty': 20,
        }

        def parse_temp(label):
            t = text.lower()
            words_re = label + r'\s+(' + '|'.join(word_map) + r')'
            m = re.search(words_re, t)
            if m:
                return float(word_map[m.group(1)])
            m = re.search(label + r'\s+plus\s+(\d+)', t)
            if m:
                return float(m.group(1))
            m = re.search(label + r'\s+minus\s+(\d+)', t)
            if m:
                return -float(m.group(1))
            m = re.search(label + r'\s+(\d+)', t)
            if m:
                return float(m.group(1))
            return None

        return parse_temp('high'), parse_temp('low')

    # ------------------------------------------------------------------
    # Debug helper
    # ------------------------------------------------------------------

    def debug_forecast(self, city_name='Calgary', days=5, province_code='AB'):
        """Print raw XML structure for debugging."""
        prov = (province_code or "AB").upper()
        site_code = self.get_location_code(city_name, prov)
        print(f"\n{'='*80}")
        print(f"DEBUG: site_code={site_code}  city='{city_name}'  prov='{prov}'")
        print(f"{'='*80}\n")
        try:
            url, content = self._fetch_xml_content(site_code, prov)
            root = ET.fromstring(content)
            fg = root.find('.//forecastGroup')
            if fg is None:
                print("No <forecastGroup> found in XML!")
                return False

            for i, fc in enumerate(fg.findall('forecast'), 1):
                period_el = fc.find('period')
                period_name = period_el.get('textForecastName', '?') if period_el is not None else '?'
                temp_high = self._xml_temperature(fc, 'high')
                temp_low  = self._xml_temperature(fc, 'low')
                precip    = self._xml_precipitation(fc)
                text_el   = fc.find('textSummary')
                text      = (text_el.text or '')[:120] if text_el is not None else ''

                print(f"Period #{i}: {period_name}")
                print(f"  High={temp_high}  Low={temp_low}  Precip={precip}mm")
                print(f"  Text: {text}")
                print()
            return True
        except Exception as e:
            print(f"Error: {e}")
            return False


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def fetch_env_canada_forecast(city_name='Calgary', days=None, province_code='AB'):
    """Drop-in replacement for the old RSS-based function."""
    from .weather_cache import (
        dataframe_from_cache_payload,
        dataframe_to_cache_payload,
        get_cached,
        set_cached,
        weather_cache_key,
    )

    days_key = days if days is not None else "all"
    cache_key = weather_cache_key(
        "eccc_forecast",
        city=(city_name or "").strip().lower(),
        province=(province_code or "AB").upper(),
        days=days_key,
    )
    cached = get_cached(cache_key)
    if cached is not None:
        return dataframe_from_cache_payload(cached)

    scraper = EnvironmentCanadaScraper()
    df = scraper.fetch_forecast(city_name, days, province_code)
    if df is not None and not df.empty:
        set_cached(cache_key, dataframe_to_cache_payload(df))
    return df


def print_precipitation_forecast(city_name='Calgary', days=5, province_code='AB'):
    """Pretty-print a precipitation forecast to stdout."""
    print(f"\n{'='*70}")
    print(f"Environment Canada Precipitation Forecast")
    print(f"Location: {city_name}, province={province_code or 'AB'}")
    print(f"{'='*70}")

    try:
        df = fetch_env_canada_forecast(city_name, days, province_code)
        for _, row in df.iterrows():
            date_str = row['Date'].strftime('%A, %B %d, %Y')
            high_str = f"{row['Temp_High']:.0f}C" if row['Temp_High'] is not None else "N/A"
            low_str  = f"{row['Temp_Low']:.0f}C"  if row['Temp_Low']  is not None else "N/A"
            forecast = row['Forecast'][:150] + "..." if len(row['Forecast']) > 150 else row['Forecast']

            print(f"\n{date_str}  ({row['Period']})")
            print(f"  High: {high_str}  |  Low: {low_str}")
            print(f"  Precipitation: {row['Precipitation_mm']:.1f} mm")
            print(f"  {forecast}")

        total = df['Precipitation_mm'].sum()
        print(f"\n{'='*70}")
        print(f"Total Projected Precipitation: {total:.1f} mm over {len(df)} periods")
        print(f"{'='*70}\n")
        return df

    except Exception as e:
        print(f"\nError fetching forecast: {e}\n")
        return None


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    scraper = EnvironmentCanadaScraper()

    print("\n" + "="*80)
    print("TESTING CALGARY FORECAST")
    print("="*80)
    scraper.debug_forecast('Calgary', days=7)

    print("\n" + "="*80)
    print("TESTING LETHBRIDGE FORECAST")
    print("="*80)
    scraper.debug_forecast('Lethbridge', days=5)