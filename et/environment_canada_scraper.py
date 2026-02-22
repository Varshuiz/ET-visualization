"""
Environment Canada Weather Forecast Scraper - MSC Datamart XML Version
Fetches precipitation forecasts using official Environment Canada citypage XML feeds.

NOTE: The old RSS feed (weather.gc.ca/rss/city/ab-XX_e.xml) was discontinued in 2025.
This version uses the MSC Datamart at dd.weather.gc.ca which is the official replacement.

Two URL strategies are attempted in order:
  1. Stable legacy path: dd.weather.gc.ca/citypage_weather/xml/AB/s0000XXX_e.xml
  2. New timestamped path: dd.weather.gc.ca/today/citypage_weather/AB/{HH}/ (directory listing)
"""

import requests
import xml.etree.ElementTree as ET
import pandas as pd
import re
from datetime import datetime, timedelta, timezone


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

    BASE_URL_STABLE = "https://dd.weather.gc.ca/citypage_weather/xml/AB/{code}_e.xml"
    BASE_URL_TODAY  = "https://dd.weather.gc.ca/today/citypage_weather/AB/{hour}/"

    def get_location_code(self, city_name):
        """Return the s0000XXX site code for a city."""
        if city_name in self.LOCATION_CODES:
            return self.LOCATION_CODES[city_name]
        for city, code in self.LOCATION_CODES.items():
            if city.lower() == city_name.lower():
                return code
        print(f"Warning: Location '{city_name}' not found, defaulting to Calgary")
        return self.LOCATION_CODES['Calgary']

    # ------------------------------------------------------------------
    # URL resolution
    # ------------------------------------------------------------------

    def _fetch_xml_content(self, site_code):
        """
        Try two strategies to get the XML content for a site code.
        Strategy 1: stable /citypage_weather/xml/ path
        Strategy 2: timestamped /today/citypage_weather/ directory listing
        Returns (url, content_bytes) or raises ValueError.
        """
        # Strategy 1: stable path
        stable_url = self.BASE_URL_STABLE.format(code=site_code)
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
            dir_url = self.BASE_URL_TODAY.format(hour=f"{hour:02d}")
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
            f"Could not retrieve XML for site {site_code}. "
            "Both stable and timestamped URL strategies failed."
        )

    # ------------------------------------------------------------------
    # Main forecast fetch
    # ------------------------------------------------------------------

    def fetch_forecast(self, city_name='Calgary', days=5):
        """
        Fetch weather forecast from Environment Canada MSC Datamart XML.

        Parameters
        ----------
        city_name : str   e.g. 'Calgary', 'Edmonton'
        days      : int   Number of daily forecast periods to return (default 5)

        Returns
        -------
        pandas.DataFrame with columns:
            Date, Period, Temp_High, Temp_Low, Precipitation_mm, Forecast
        """
        try:
            print(f"\n Fetching forecast from Environment Canada RSS...")
            print(f"   Location: {city_name}")
            print(f"   Forecast periods: {days}")

            site_code = self.get_location_code(city_name)
            url, content = self._fetch_xml_content(site_code)

            root = ET.fromstring(content)

            forecast_group = root.find('.//forecastGroup')
            if forecast_group is None:
                raise ValueError("No <forecastGroup> element found in XML response")

            forecasts_raw = forecast_group.findall('forecast')
            print(f"   Found {len(forecasts_raw)} forecast periods")

            forecast_data = []
            for fc in forecasts_raw:
                period_el = fc.find('period')
                if period_el is None:
                    continue
                period_name = period_el.get('textForecastName', '').strip()
                if not period_name:
                    continue

                # Full text summary
                text_el = fc.find('textSummary')
                forecast_text = (text_el.text or '').strip() if text_el is not None else ''

                # Abbreviated summary as fallback
                abbrev_el = fc.find('.//abbreviatedForecast/textSummary')
                abbrev_text = (abbrev_el.text or '').strip() if abbrev_el is not None else ''

                full_text = forecast_text or abbrev_text

                # Temperatures: prefer XML values, fall back to text parsing
                temp_high = self._xml_temperature(fc, 'high')
                temp_low  = self._xml_temperature(fc, 'low')
                if temp_high is None and temp_low is None:
                    temp_high, temp_low = self._extract_temperatures(full_text)

                # Precipitation: prefer XML values, fall back to text parsing
                precip = self._xml_precipitation(fc)
                if precip == 0.0:
                    precip = self._extract_precipitation(full_text)

                print(f"   ✓ {period_name}: {precip:.1f}mm precip")

                forecast_data.append({
                    'Period':           period_name,
                    'Temp_High':        temp_high,
                    'Temp_Low':         temp_low,
                    'Precipitation_mm': precip,
                    'Forecast':         full_text,
                })

            if not forecast_data:
                raise ValueError("No forecast periods could be parsed from XML")

            df = pd.DataFrame(forecast_data)
            df = self._group_by_day(df, days)

            print(f"\n   Successfully fetched {len(df)} daily forecasts\n")
            return df

        except Exception as e:
            print(f"   Error: {e}\n")
            raise ValueError(f"Failed to fetch forecast: {e}")

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

    # ------------------------------------------------------------------
    # Day grouping
    # ------------------------------------------------------------------

    def _group_by_day(self, df, max_days=5):
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
            if row['Forecast']:
                daily_data[day_name]['forecast_parts'].append(row['Forecast'])

        result_data = []
        for i, day_data in enumerate(list(daily_data.values())[:max_days]):
            result_data.append({
                'Date':             today + timedelta(days=i),
                'Period':           day_data['day_name'],
                'Temp_High':        day_data['temp_high'],
                'Temp_Low':         day_data['temp_low'],
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

    def debug_forecast(self, city_name='Calgary', days=5):
        """Print raw XML structure for debugging."""
        site_code = self.get_location_code(city_name)
        print(f"\n{'='*80}")
        print(f"DEBUG: site_code={site_code}  city='{city_name}'")
        print(f"{'='*80}\n")
        try:
            url, content = self._fetch_xml_content(site_code)
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
# Convenience functions — same API as before so views.py needs NO changes
# ---------------------------------------------------------------------------

def fetch_env_canada_forecast(city_name='Calgary', days=5):
    """Drop-in replacement for the old RSS-based function."""
    scraper = EnvironmentCanadaScraper()
    return scraper.fetch_forecast(city_name, days)


def print_precipitation_forecast(city_name='Calgary', days=5):
    """Pretty-print a precipitation forecast to stdout."""
    print(f"\n{'='*70}")
    print(f"Environment Canada Precipitation Forecast")
    print(f"Location: {city_name}, Alberta")
    print(f"{'='*70}")

    try:
        df = fetch_env_canada_forecast(city_name, days)
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